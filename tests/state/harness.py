"""Shared pieces of the native-state tests (T15): the p6_state_probe runner, the parser of what it prints, micro-ROMs built from the fixtures or
from inline sources, the re-sealing of a state whose bytes a test changed, and the check that a refused state leaves the machine alone.

Every chunk of the state is tested the same way (docs/adr/0009-state-format.md): what the chunk puts back is compared with the digest of the
unit the state was taken in, in the same process and in a new one; a body that Validate accepts must never fail to apply; and a bad body is
refused with its code before anything of the machine is touched.
"""
from pathlib import Path
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.state import envelope as e  # noqa: E402

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
SUBSYSTEMS = ("cpu", "scheduler", "ram", "rtc", "chipset", "audio", "input", "video", "bridge")
DIGEST = re.compile(r"^digest (\d+) (\w+) ([0-9a-f]{64}) (\d+)$")
LOAD = re.compile(r"^load (\d+) (-?\d+) ?(.*)$")
SAVE = re.compile(r"^save (\d+) (\d+) ([0-9a-f]{64}) bound=(\d+)$")
SAVE_ERROR = re.compile(r"^save (\d+) error (-?\d+) ?(.*)$")
PUBLIC = re.compile(r"^public (\d+) cap_state=(\d) bound=(\d+) save=(\d+) load=(\d+) written=(\d+) ?(.*)$")
FIELD = re.compile(r"^field (\d+) (\S+) (\d+) (\d+)$")
MUTATE = re.compile(r"^mutate (\d+) (\w+) bytes=(\d+) attempts=(\d+) accepted=(\d+) refused=(\d+) faulted=(\d+) changed=(\d+) verdicts=([0-9a-f]{64})$")
_BUILT = {}


class Run:
    """What one p6_state_probe process printed, in order."""

    def __init__(self, text):
        self.digests = {}           # (unit, subsystem) -> hex, outside a load
        self.after = []             # one dict subsystem -> hex per load
        self.loads = []             # (unit, code, message)
        self.saves = []             # (unit, bytes, sha256, bound)
        self.save_errors = []       # (unit, code, message)
        self.public = []
        self.fault = []             # (event, code, message) of run-after-fault, unload and reload
        self.fields = []            # (chunk index, name, offset in the state, size)
        self.mutate = []            # dicts of the "mutate" lines
        current = None
        for line in text.splitlines():
            if line.startswith("after-load "):
                current = {}
                self.after.append(current)
            elif DIGEST.match(line):
                unit, subsystem, digest, _ = DIGEST.match(line).groups()
                if current is None:
                    self.digests[(int(unit), subsystem)] = digest
                else:
                    current[subsystem] = digest
                    if len(current) == len(SUBSYSTEMS):         # the block that follows a load is one set of digests, not the rest of the run
                        current = None
            elif LOAD.match(line):
                current = None
                unit, code, message = LOAD.match(line).groups()
                self.loads.append((int(unit), int(code), message))
            elif SAVE.match(line):
                current = None
                unit, size, digest, bound = SAVE.match(line).groups()
                self.saves.append((int(unit), int(size), digest, int(bound)))
            elif SAVE_ERROR.match(line):
                current = None
                unit, code, message = SAVE_ERROR.match(line).groups()
                self.save_errors.append((int(unit), int(code), message))
            elif line.startswith(("run-after-fault ", "unload ", "reload ")):
                event, code, *message = line.split(" ", 2)
                self.fault.append((event, int(code), message[0] if message else ""))
            elif PUBLIC.match(line):
                self.public.append(PUBLIC.match(line).groups())
            elif FIELD.match(line):
                chunk, name, offset, size = FIELD.match(line).groups()
                self.fields.append((int(chunk), name, int(offset), int(size)))
            elif MUTATE.match(line):
                groups = MUTATE.match(line).groups()
                self.mutate.append({"unit": int(groups[0]), "chunk": groups[1], "bytes": int(groups[2]), "attempts": int(groups[3]),
                                    "accepted": int(groups[4]), "refused": int(groups[5]), "faulted": int(groups[6]), "changed": int(groups[7]),
                                    "verdicts": groups[8]})

    def unit(self, number):
        return {s: self.digests[(number, s)] for s in SUBSYSTEMS}


def parse_chunks(blob):
    """[[tag, version, body]] of a state."""
    chunks, at = [], e.HEADER_SIZE
    while at < len(blob):
        tag, version, length = e.CHUNK_HEADER.unpack_from(blob, at)
        chunks.append([tag, version, blob[at + 16:at + 16 + length]])
        at += 16 + length
    return chunks


def reforge(blob, edit=None, header=None, count=None, keep_length=False):
    """The state with its chunk list edited (`edit` gets a list of [tag, version, body]) and every checksum made right again: a defect that
    only the structure, and not a checksum, can find. `header` replaces header bytes by offset after the lengths are set."""
    chunks = parse_chunks(blob)
    if edit:
        edit(chunks)
    payload = b"".join(e.CHUNK_HEADER.pack(tag, version, len(body)) + bytes(body) for tag, version, body in chunks)
    data = bytearray(blob[:e.HEADER_SIZE])
    if not keep_length:
        struct.pack_into("<Q", data, 40, len(payload))
    struct.pack_into("<I", data, 48, len(chunks) if count is None else count)
    for offset, value in (header or {}).items():
        data[offset:offset + len(value)] = value
    data[272:304] = e.domain_hash("p6/native-payload/v1", payload)
    data[304:336] = e.domain_hash("p6/native-header/v1", bytes(data[:304]))
    return bytes(data) + payload


def with_bytes(blob, chunk_index, offset, value):
    """The state with `value` written at `offset` of the whole state (inside the body of chunk `chunk_index`), checksums made right again."""
    def edit(chunks):
        at = e.HEADER_SIZE
        for i in range(chunk_index):
            at += 16 + len(chunks[i][2])
        body = bytearray(chunks[chunk_index][2])
        start = offset - (at + 16)
        assert 0 <= start and start + len(value) <= len(body), (offset, len(value), len(body))
        body[start:start + len(value)] = value
        chunks[chunk_index][2] = bytes(body)
    return reforge(blob, edit)


def built(target):
    """The executable of `target`, configured and built once per process (the code generator runs at every build)."""
    if target not in _BUILT:
        _BUILT[target] = build_probe(BUILD, False, target)
    return _BUILT[target]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ProbeCase(unittest.TestCase):
    """A class that drives p6_state_probe. ROMS maps a name to (fixture or source text, defines, replacements)."""

    ROMS = {}
    exe = None

    @classmethod
    def setUpClass(cls):
        cls.exe = built("p6_state_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-state-")
        cls.dir = Path(cls.tmp.name)
        cls.roms = {}
        for name, (source, defines, replacements) in cls.ROMS.items():
            text = (FIXTURES / f"{source}.s").read_text(encoding="utf-8") if source in {p.stem for p in FIXTURES.glob("*.s")} else source
            for old, new in (replacements or {}).items():
                assert old in text, old
                text = text.replace(old, new)
            path = cls.dir / f"{name}.rom"
            path.write_bytes(builder.build(text, defines) if defines else builder.build(text))
            cls.roms[name] = path
        kickstart = os.environ.get("P6_KICKSTART_ROM")
        if kickstart and Path(kickstart).is_file():
            cls.roms["kickstart"] = Path(kickstart)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def probe(cls, rom, *arguments):
        result = subprocess.run([str(cls.exe), str(cls.roms[rom]), *arguments], capture_output=True, text=True, timeout=1800)
        assert result.returncode == 0, result.stderr[-400:]
        return Run(result.stdout)

    def state_of(self, name, rom, unit, *arguments):
        """The bytes of the state of `rom` taken after `unit` units."""
        path = self.dir / name
        self.probe(rom, "--units", str(unit), "--save-at", str(unit), str(path), *arguments)
        return path.read_bytes()

    def refusals(self, attempts, rom="vsync", extra=(), unit=6, units=12):
        """Runs every attempt as a load at `unit` of one process, and checks that each is refused as expected and that the process then goes on
        exactly as a control run that never saw them. `attempts` is a list of (name, bytes, code, text that the message must contain)."""
        control = self.probe(rom, "--units", str(units), "--every", "1", *extra)
        files = []
        arguments = ["--units", str(units), "--every", "1", *extra]
        for index, (name, data, code, text) in enumerate(attempts):
            path = self.dir / f"attempt{index}.state"
            path.write_bytes(data)
            files.append(path)
            arguments += ["--load-at", str(unit), str(path)]
        try:
            run = self.probe(rom, *arguments)
        finally:
            for path in files:
                path.unlink()
        self.assertEqual(len(attempts), len(run.loads))
        before = control.unit(unit)
        for (name, _, code, text), (at, got, message), after in zip(attempts, run.loads, run.after):
            self.assertEqual((unit, code), (at, got), f"{name}: {message}")
            self.assertIn(text, message, name)
            self.assertEqual(before, after, f"{name}: a refused state changed the machine")
        for later in range(unit + 1, units + 1):
            self.assertEqual(control.unit(later), run.unit(later), f"unit {later}: the machine did not go on as if nothing had happened")
        return run
