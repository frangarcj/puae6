"""T15a: the native envelope (ports/portable/state_io) against the reference of tests/state/envelope.py.

The reference is the executable form of docs/adr/0009-state-format.md and tests/state/test_envelope.py attacks it with every defect the
loader must refuse. Here the same attacks run through the C++ validator (target p6_envelope_probe): the verdict, the code and the field it
names must be the reference's for each of them, and the bytes the C++ encoder writes must be the golden vector's. The archive
(archive.h) has its own self-checking probe, run here too.
"""
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe  # noqa: E402
from tests.state import envelope as e  # noqa: E402
from tests.state import test_envelope as reference  # noqa: E402

BUILD = ROOT / "build/link-audit"
VECTOR = reference.VECTOR
_BUILT = {}


def built(target):
    """The executable of `target`, configured and built once per process (the code generator runs at every build)."""
    if target not in _BUILT:
        _BUILT[target] = build_probe(BUILD, False, target)
    return _BUILT[target]


class Probe:
    """One long-lived p6_envelope_probe: a line in, a line out."""

    def __init__(self, exe):
        self.process = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return self.process.stdout.readline().rstrip("\n")

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=30)


def identity_words(identity):
    return " ".join([str(identity.profile_id), identity.core_build_id.hex(), identity.upstream_commit.hex(), identity.patch_series_hash.hex(),
                     identity.codegen_hash.hex(), identity.config_hash.hex(), identity.media_manifest_hash.hex(), identity.firmware_sha256.hex()])


def native_encode(probe, identity, unit_index, master_ticks, chunks):
    pieces = " ".join(f"{tag.decode()}:{version}:{body.hex() or '-'}" for tag, (version, body) in chunks.items())
    reply = probe.ask(f"encode {identity_words(identity)} {unit_index} {master_ticks} {pieces}")
    assert not reply.startswith("error"), reply
    return bytes.fromhex(reply)


def native_decode_with(probe):
    def native_decode(data, expected, bound, chunk_bounds=None):
        specs = ",".join(f"{tag.decode()}:{e.SUPPORTED_CHUNK_VERSION}" + (f":{chunk_bounds[tag]}" if chunk_bounds and tag in chunk_bounds else "")
                         for tag in e.TAGS)
        reply = probe.ask(f"decode {identity_words(expected)} {bound} {specs} {data.hex() or '-'}")
        parts = reply.split(" ")
        if parts[0] == "refused":
            raise e.StateError(int(parts[1]), parts[2], "refused by the native validator")
        assert parts[0] == "ok", reply
        chunks = {}
        for piece in parts[3:]:
            tag, version, body = piece.split(":")
            chunks[tag.encode()] = (int(version), bytes.fromhex(body))
        return e.State(int(parts[1]), int(parts[2]), expected, chunks)
    return native_decode


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class NativeEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = built("p6_envelope_probe")
        cls.probe = Probe(cls.exe)

    @classmethod
    def tearDownClass(cls):
        cls.probe.close()

    def test_the_encoder_writes_the_bytes_of_the_golden_vector(self):
        identity, chunks, blob = reference.build()
        native = native_encode(self.probe, identity, VECTOR["inputs"]["unit_index"], VECTOR["inputs"]["master_ticks"], chunks)
        self.assertEqual(VECTOR["state_hex"], native.hex())
        self.assertEqual(VECTOR["state_sha256"], hashlib.sha256(native).hexdigest())

    def test_the_identity_hashes_are_the_ones_of_the_vector_and_of_the_reference(self):
        inputs = VECTOR["inputs"]
        c = inputs["config"]
        config = self.probe.ask(f"hash config {c['machine']} {c['video_standard']} {c['audio_rate_hz']} {c['flags']} {c['random_seed']} {c['rtc_epoch_seconds']}")
        self.assertEqual(VECTOR["derived"]["config_hash"], config)
        media = [(kind, reference.label(name)) for kind, name in zip(inputs["media_types"], VECTOR["labels"]["media"])]
        words = " ".join(f"{kind}:{content.hex()}" for kind, content in media)
        self.assertEqual(VECTOR["derived"]["media_manifest_hash"], self.probe.ask(f"hash media {len(media)} {words}"))
        upstream = bytes.fromhex(inputs["upstream_commit"])
        series, codegen, sources = (reference.label(x) for x in ("series", "codegen", "sources"))
        self.assertEqual(VECTOR["derived"]["core_build_id"],
                         self.probe.ask(f"hash build {upstream.hex()} {series.hex()} {codegen.hex()} {sources.hex()} {e.PROFILE_A500}"))

    def test_random_identities_media_and_configurations_hash_as_the_reference_does(self):
        rng = random.Random(15)
        for _ in range(300):
            config = (rng.choice((1, 2, 7)), rng.choice((1, 2)), rng.choice((44100, 48000, 96000)), rng.getrandbits(32), rng.getrandbits(64),
                      rng.randrange(-(1 << 62), 1 << 62))
            self.assertEqual(e.config_hash(*config).hex(), self.probe.ask("hash config " + " ".join(map(str, config))))
            media = [(rng.choice((1, 2, 3)), rng.randbytes(32)) for _ in range(rng.randrange(0, 17))]
            words = " ".join(f"{kind}:{content.hex()}" for kind, content in media)
            self.assertEqual(e.media_manifest_hash(media).hex(), self.probe.ask(f"hash media {len(media)} {words}".rstrip()))
            parts = (rng.randbytes(20), rng.randbytes(32), rng.randbytes(32), rng.randbytes(32))
            profile = rng.choice((1, 2))
            self.assertEqual(e.core_build_id(*parts, profile_id=profile).hex(),
                             self.probe.ask(f"hash build {parts[0].hex()} {parts[1].hex()} {parts[2].hex()} {parts[3].hex()} {profile}"))

    def test_random_states_encode_to_the_bytes_of_the_reference_and_decode_back(self):
        rng = random.Random(16)
        decode = native_decode_with(self.probe)
        for _ in range(200):
            identity = e.Identity(rng.choice((1, 2)), rng.randbytes(32), rng.randbytes(20), rng.randbytes(32), rng.randbytes(32), rng.randbytes(32),
                                  rng.randbytes(32), rng.randbytes(32))
            chunks = {tag: (1, rng.randbytes(rng.choice((0, 1, 7, 64, 300)))) for tag in e.TAGS}
            unit, ticks = rng.getrandbits(64), rng.getrandbits(64)
            blob = e.encode(identity, unit, ticks, chunks)
            self.assertEqual(blob, native_encode(self.probe, identity, unit, ticks, chunks))
            state = decode(blob, identity, 1 << 20)
            self.assertEqual((state.unit_index, state.master_ticks, state.chunks), (unit, ticks, chunks))

    def test_a_state_of_a_native_encoder_is_read_by_the_reference_decoder(self):
        identity, chunks, _ = reference.build()
        native = native_encode(self.probe, identity, 7, 99, chunks)
        state = e.decode(native, identity, reference.BOUND)
        self.assertEqual((state.unit_index, state.master_ticks, state.chunks), (7, 99, chunks))


class NativeAttackMixin:
    """Every attack of tests/state/test_envelope.py, with the native validator in place of the reference one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exe = built("p6_envelope_probe")
        cls.probe = Probe(cls.exe)
        cls.patch = mock.patch.object(e, "decode", native_decode_with(cls.probe))
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.probe.close()
        super().tearDownClass()


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class NativeRefusalTests(NativeAttackMixin, reference.RefusalTests):
    pass


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ArchiveTests(unittest.TestCase):
    def test_the_archive_round_trips_every_field_kind_and_refuses_what_it_must(self):
        exe = built("p6_archive_probe")
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("archive ok", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
