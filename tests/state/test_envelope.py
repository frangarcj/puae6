"""T14b: the envelope of docs/adr/0009-state-format.md, run and attacked before the native serializer exists.

The vector is what T15's C++ must reproduce byte for byte; every defect below is one the loader must refuse with its code and before it
allocates from any length it read; nothing the format cannot express is promised (no .uss, no Waterbox snapshot).
"""
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import unittest

from tests.state import envelope as e

ROOT = Path(__file__).resolve().parents[2]
VECTOR = json.loads((ROOT / "tests/state/vectors/envelope-v1.json").read_text())
ADR = (ROOT / "docs/adr/0009-state-format.md").read_text()
BOUND = 1 << 20


def label(text):
    return hashlib.sha256(text.encode()).digest()


def build(**overrides):
    inputs = VECTOR["inputs"]
    upstream = bytes.fromhex(inputs["upstream_commit"])
    series, codegen, sources = label("series"), label("codegen"), label("sources")
    media = [(kind, label(name)) for kind, name in zip(inputs["media_types"], VECTOR["labels"]["media"])]
    c = inputs["config"]
    identity = e.Identity(e.PROFILE_A500, e.core_build_id(upstream, series, codegen, sources), upstream, series, codegen,
                          e.config_hash(c["machine"], c["video_standard"], c["audio_rate_hz"], c["flags"], c["random_seed"], c["rtc_epoch_seconds"]),
                          e.media_manifest_hash(media), label("firmware"))
    identity = e.Identity(**{**identity.__dict__, **overrides})
    chunks = {tag.encode(): (v["version"], bytes.fromhex(v["body"])) for tag, v in inputs["chunks"].items()}
    return identity, chunks, e.encode(identity, inputs["unit_index"], inputs["master_ticks"], chunks)


def forge(blob, header=None, payload=None):
    """The same state with header bytes or the payload replaced and every checksum made right again: a defect that only structure can find."""
    data = bytearray(blob)
    for offset, value in (header or {}).items():
        data[offset:offset + len(value)] = value
    body = bytes(data[e.HEADER_SIZE:]) if payload is None else payload
    data[272:304] = e.domain_hash("p6/native-payload/v1", body)
    data[304:336] = e.domain_hash("p6/native-header/v1", bytes(data[:304]))
    return bytes(data[:e.HEADER_SIZE]) + body


class VectorTests(unittest.TestCase):
    def test_the_inputs_of_the_vector_give_its_bytes_and_hashes(self):
        identity, chunks, blob = build()
        self.assertEqual(VECTOR["state_hex"], blob.hex())
        self.assertEqual(VECTOR["state_sha256"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(VECTOR["state_bytes"], len(blob))
        for name in ("core_build_id", "config_hash", "media_manifest_hash"):
            self.assertEqual(VECTOR["derived"][name], getattr(identity, name).hex())

    def test_a_state_decodes_to_what_was_encoded_when_the_identity_is_the_loaded_one(self):
        identity, chunks, blob = build()
        state = e.decode(blob, identity, BOUND)
        self.assertEqual((state.unit_index, state.master_ticks, state.identity, state.chunks),
                         (VECTOR["inputs"]["unit_index"], VECTOR["inputs"]["master_ticks"], identity, chunks))

    def test_the_header_fields_are_the_ones_of_the_adr_table(self):
        rows = re.findall(r"^\s*\| (\d+) \| (\d+) \| (.+?) \|$", ADR, re.M)
        self.assertEqual([(int(o), int(s)) for o, s, _ in rows], [(o, s) for _, o, s in e.FIELDS])
        self.assertEqual(sum(size for _, _, size in e.FIELDS), e.HEADER_SIZE)


class RefusalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.identity, cls.chunks, cls.blob = build()

    def refuse(self, data, code, field, bound=BOUND, chunk_bounds=None):
        with self.assertRaises(e.StateError) as caught:
            e.decode(data, self.identity, bound, chunk_bounds)
        self.assertEqual((caught.exception.code, caught.exception.field), (code, field), str(caught.exception))

    def test_every_truncation_is_refused(self):
        for length in range(len(self.blob)):
            with self.assertRaises(e.StateError, msg=f"length {length}"):
                e.decode(self.blob[:length], self.identity, BOUND)

    def test_bytes_after_the_end_are_refused(self):
        self.refuse(self.blob + b"\0", e.CORRUPT_STATE, "payload_length")

    def test_a_flipped_bit_anywhere_is_refused_and_one_in_the_payload_as_a_corrupt_state(self):
        for index in range(len(self.blob)):
            data = bytearray(self.blob)
            data[index] ^= 0x10
            with self.assertRaises(e.StateError, msg=f"byte {index}") as caught:
                e.decode(bytes(data), self.identity, BOUND)
            if index >= e.HEADER_SIZE:
                self.assertEqual(caught.exception.code, e.CORRUPT_STATE, f"byte {index}")

    def test_the_header_checks_come_in_the_order_of_the_adr(self):
        self.refuse(b"P6WATERB" + self.blob[8:], e.INCOMPATIBLE, "magic")                       # never parsed as a native state
        self.refuse(forge(self.blob, {0: b"P6NATIVF"}), e.CORRUPT_STATE, "magic")
        self.refuse(forge(self.blob, {8: struct.pack("<I", 2)}), e.INCOMPATIBLE, "format_version")
        self.refuse(forge(self.blob, {12: struct.pack("<I", 337)}), e.CORRUPT_STATE, "header_size")
        self.refuse(forge(self.blob, {20: struct.pack("<I", 1)}), e.CORRUPT_STATE, "flags")
        self.refuse(forge(self.blob, {52: struct.pack("<I", 1)}), e.CORRUPT_STATE, "reserved")
        self.refuse(forge(self.blob, {108: struct.pack("<I", 1)}), e.CORRUPT_STATE, "reserved")

    def test_lengths_are_checked_before_anything_is_reserved_from_them(self):
        for claimed in (1 << 64) - 1, 1 << 63, BOUND + 1, len(self.blob) - e.HEADER_SIZE + 1, len(self.blob) - e.HEADER_SIZE - 1:
            with self.subTest(claimed=claimed):
                self.refuse(forge(self.blob, {40: struct.pack("<Q", claimed)}), e.CORRUPT_STATE, "payload_length")
        self.refuse(self.blob, e.CORRUPT_STATE, "payload_length", bound=len(self.blob) - e.HEADER_SIZE - 1)

    def test_a_state_of_another_machine_is_incompatible_and_the_message_names_what_differs(self):
        other = {"profile_id": 2, "core_build_id": label("other build"), "upstream_commit": bytes(20), "patch_series_hash": label("other series"),
                 "codegen_hash": label("other codegen"), "config_hash": label("other config"), "media_manifest_hash": label("other media"),
                 "firmware_sha256": label("other firmware")}
        for field, value in other.items():
            with self.subTest(field=field):
                _, _, blob = build(**{field: value})
                self.refuse(blob, e.INCOMPATIBLE, field)

    def test_an_altered_payload_with_a_right_checksum_is_refused_by_its_structure(self):
        payload = self.blob[e.HEADER_SIZE:]
        first = e.CHUNK_HEADER.size + len(self.chunks[e.TAGS[0]][1])
        cases = {
            "a chunk repeated": (payload[:first] + payload[:first] + payload[first:], e.CORRUPT_STATE, "chunk"),
            "an unknown tag": (b"XXXX" + payload[4:], e.CORRUPT_STATE, "chunk"),
            "chunks out of order": (payload[first:first + e.CHUNK_HEADER.size + len(self.chunks[e.TAGS[1]][1])] + payload[:first] +
                                    payload[first + e.CHUNK_HEADER.size + len(self.chunks[e.TAGS[1]][1]):], e.CORRUPT_STATE, "chunk"),
            "the last chunk missing": (payload[:-(e.CHUNK_HEADER.size + len(self.chunks[e.TAGS[-1]][1]))], e.CORRUPT_STATE, "chunk"),
            "a chunk longer than the payload": (payload[:8] + struct.pack("<Q", 1 << 40) + payload[16:], e.CORRUPT_STATE, "chunk_length"),
            "a chunk version this build does not know": (payload[:4] + struct.pack("<I", 2) + payload[8:], e.INCOMPATIBLE, "chunk_version"),
            "bytes after the last chunk": (payload + b"\0\0", e.CORRUPT_STATE, "payload"),
        }
        for name, (altered, code, field) in cases.items():
            with self.subTest(name):
                header = {40: struct.pack("<Q", len(altered))}
                self.refuse(forge(self.blob, header, altered), code, field)
        self.refuse(forge(self.blob, {48: struct.pack("<I", 10)}), e.CORRUPT_STATE, "chunk_count")

    def test_a_chunk_beyond_its_bound_is_refused(self):
        self.refuse(self.blob, e.CORRUPT_STATE, "chunk_length", chunk_bounds={e.TAGS[2]: len(self.chunks[e.TAGS[2]][1]) - 1})

    def test_random_damage_never_makes_the_decoder_fail_in_any_way_but_a_refusal(self):
        rng = random.Random(14)
        survived = 0
        for _ in range(4000):
            data = bytearray(self.blob)
            for _ in range(rng.choice((1, 1, 2, 5))):
                if not data:
                    break
                kind = rng.random()
                if kind < 0.6 and data:
                    data[rng.randrange(len(data))] = rng.randrange(256)
                elif kind < 0.8:
                    del data[rng.randrange(len(data)):]
                else:
                    data[rng.randrange(len(data)):rng.randrange(len(data))] = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
            try:
                state = e.decode(bytes(data), self.identity, BOUND)
            except e.StateError:
                continue
            survived += 1
            self.assertEqual(bytes(data), self.blob, "damage that decodes must not have changed a thing")
            self.assertEqual(state.chunks, self.chunks)
        self.assertLess(survived, 400)


class DomainTests(unittest.TestCase):
    def test_hashes_of_different_purposes_differ_for_the_same_bytes(self):
        data = b"same bytes"
        domains = ("p6/config/v1", "p6/media-manifest/v1", "p6/core-build/v1", "p6/native-header/v1", "p6/native-payload/v1", "p6/probe/v1/cpu",
                   "p6/probe/v1/ram", "p6/waterbox-snapshot/v1")
        self.assertEqual(len(domains), len({e.domain_hash(d, data) for d in domains}))
        self.assertNotEqual(e.domain_hash("p6/a/v1", b"bc"), e.domain_hash("p6/a/v1b", b"c"))   # the zero byte ends the domain

    def test_the_native_envelope_uses_no_waterbox_domain_and_a_probe_digest_is_not_an_envelope_hash(self):
        source = (ROOT / "tests/state/envelope.py").read_text()
        domains = set(re.findall(r'"(p6/[\w/-]+)"', source))
        self.assertTrue(domains)
        self.assertFalse([d for d in domains if "waterbox" in d])
        self.assertFalse([d for d in domains if d.startswith("p6/probe/")])

    def test_the_identity_hashes_change_with_each_of_their_inputs(self):
        base = (1, 1, 48000, 0, 1, 946684800)
        seen = {e.config_hash(*base)}
        for index, value in enumerate((2, 2, 44100, 1, 2, 946684801)):
            changed = list(base)
            changed[index] = value
            seen.add(e.config_hash(*changed))
        self.assertEqual(7, len(seen))
        a, b = label("a"), label("b")
        manifests = {e.media_manifest_hash(m) for m in ([], [(1, a)], [(1, b)], [(2, a)], [(1, a), (1, b)], [(1, b), (1, a)])}
        self.assertEqual(6, len(manifests), "count, content, type and order all matter")
        self.assertNotEqual(e.core_build_id(bytes(20), a, b, a), e.core_build_id(bytes(20), a, b, b))


if __name__ == "__main__":
    unittest.main()
