"""Shared pieces of the native-state tests (T15): the p6_state_probe runner, the parser of what it prints, micro-ROMs built from the fixtures or
from inline sources, the re-sealing of a state whose bytes a test changed, and the check that a refused state leaves the machine alone.

Every chunk of the state is tested the same way (docs/adr/0009-state-format.md): what the chunk puts back is compared with the digest of the
unit the state was taken in, in the same process and in a new one; a body that Validate accepts must never fail to apply; and a bad body is
refused with its code before anything of the machine is touched.
"""
import collections
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
from tests.build_support import build_targets, state_probe_tree  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.state import envelope as e  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/microrom"
SUBSYSTEMS = ("cpu", "scheduler", "ram", "rtc", "cia", "chipset", "audio", "input", "video", "bridge")
DIGEST = re.compile(r"^digest (\d+) (\w+) ([0-9a-f]{64}) (\d+)$")
PCM = re.compile(r"^pcm (\d+) ([0-9a-f]{64}) (\d+)$")
PICTURE = re.compile(r"^picture (\d+) (none|\d+x\d+ p\d+ \d+/\d+ [0-9a-f]{64})$")
CHIP = re.compile(r"^chip (\d+) (\w+) ([0-9a-f]{64})$")
LOAD = re.compile(r"^load (\d+) (-?\d+) ?(.*)$")
APPEND = re.compile(r"^append (\d+) (-?\d+) (\d+) ?(.*)$")
EVENT = re.compile(r"^event (\d+) (-?\d+) ?(.*)$")
SAVE = re.compile(r"^save (\d+) (\d+) ([0-9a-f]{64}) bound=(\d+)$")
SAVE_ERROR = re.compile(r"^save (\d+) error (-?\d+) ?(.*)$")
PUBLIC = re.compile(r"^public (\d+) cap_state=(\d) bound=(\d+) save=(\d+) load=(\d+) written=(\d+) junk=(\d+) small=(\d+) ?(.*)$")
PUBLIC_SIZE = re.compile(r"^public-size (\d+) (\d+) (\d+) ([0-9a-f]{64})$")
FIELD = re.compile(r"^field (\d+) (\S+) (\d+) (\d+)$")
RTC = re.compile(r"^rtc (\d+) (-?\d+) (-?\d+) (\d+) (\d+) (-?\d+) (\d+) (\d+) (\d+)$")
MUTATE_ERROR = re.compile(r"^mutate (\d+) error (.*)$")
MUTATE = re.compile(r"^mutate (\d+) (\w+) bytes=(\d+) attempts=(\d+) accepted=(\d+) refused=(\d+) faulted=(\d+) changed=(\d+) verdicts=([0-9a-f]{64})$")
_BUILT = {}

# Programs for the chunks that describe a running machine: one micro-ROM per way the CPU can be at a unit boundary: running with interrupts, in STOP, halted, tracing (T1), in user mode with a stale ISP.
TRACE = """; PUAE6 test program: the vertical-blank setup of vsync.s with T1 set, so that every instruction takes a trace exception.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #trace,$24.L         ; vector 9 (trace)
    MOVE.L #handler,$6C.L       ; level-3 autovector (vector 27)
    LEA $1000.W,A0              ; log pointer
    MOVE.W #$C020,$DFF09A.L     ; INTENA: SET|INTEN|VERTB
    MOVE.W #$A000,SR            ; T1, supervisor, interrupts on
loop:
    ADDQ.L #1,D0
    BRA.S loop
trace:
    ADDQ.L #1,D2
    RTE
handler:
    MOVE.W #$0020,$DFF09C.L     ; INTREQ: clear VERTB
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
"""
USER = """; PUAE6 test program: the vertical-blank setup of vsync.s, but the main loop runs in user mode (S = 0) and the interrupt handler in supervisor mode.
; The supervisor stack pointer is left stale at $400 while the program uses the user one.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #handler,$6C.L
    LEA $1000.W,A0
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$0000,SR            ; user mode, interrupts on
loop:
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
"""
# name -> (unit the state is taken in, units that run before it is loaded back)
SCENARIOS = {"vsync": (3, 4), "stop": (3, 4), "halt": (2, 3), "input_all": (3, 4), "long_ops": (3, 4), "copper_irq": (3, 4), "trace": (3, 4),
             "user": (3, 4)}





class Run:
    """What one p6_state_probe process printed, in order."""

    def __init__(self, text):
        self.digests = {}           # (unit, subsystem) -> hex, outside a load
        self.pcm = {}               # unit -> hex of the stereo samples that unit handed over (T15i3)
        self.pictures = {}          # unit -> "none" or "WxH pFIELD CADENCE HEX": the picture that unit handed over, as the host sees it (T15j); the "video" digest is the rows of the buffer, not that
        self.chip = {}              # (unit, group) -> hex of one group of what the chipset digest hashes, outside a load (T15i3)
        self.chip_after = []        # one dict group -> hex per load
        self.dumps = {}             # (unit, subsystem) -> the canonical bytes --dump printed (first 4 KiB), outside a load
        self.after = []             # one dict subsystem -> hex per load
        self.loads = []             # (unit, code, message)
        self.saves = []             # (unit, bytes, sha256, bound)
        self.save_errors = []       # (unit, code, message)
        self.public = []            # (unit, cap_state, bound, save, load, written, junk, small, message) of --public-at, as strings
        self.public_size = []       # (unit, bound, written, sha256) of --public-at
        self.fault = []             # (event, code, message) of run-after-fault, unload and reload
        self.fields = []            # (chunk index, name, offset in the state, size)
        self.mutate = []            # dicts of the "mutate" lines
        self.rtc = []               # dicts of the "rtc" lines: the clock in the open at that moment
        self.mutate_errors = []     # (unit, reason) of a mutate that could not start, for instance a good state that was refused
        self.appends = []           # (unit, code, index, message) of --append-at (T17e): the index the manifest gave the medium
        self.events = []            # (unit, code, message) of --event-at
        current = None
        for line in text.splitlines():
            if line.startswith("after-load "):
                current = {}
                self.after.append(current)
                self.chip_after.append({})
            elif DIGEST.match(line):
                unit, subsystem, digest, _ = DIGEST.match(line).groups()
                if current is None:
                    self.digests[(int(unit), subsystem)] = digest
                else:
                    current[subsystem] = digest
                    if len(current) == len(SUBSYSTEMS):         # the block that follows a load is one set of digests, not the rest of the run
                        current = None
            elif CHIP.match(line):
                unit, group, digest = CHIP.match(line).groups()
                (self.chip if current is None else self.chip_after[-1])[(int(unit), group) if current is None else group] = digest
            elif line.startswith("bytes ") and current is None:
                _, unit, subsystem, data = line.split(" ", 3)
                self.dumps[(int(unit), subsystem)] = bytes.fromhex(data)
            elif PCM.match(line):
                self.pcm[int(PCM.match(line)[1])] = PCM.match(line)[2]
            elif PICTURE.match(line):
                self.pictures[int(PICTURE.match(line)[1])] = PICTURE.match(line)[2]
            elif LOAD.match(line):
                current = None
                unit, code, message = LOAD.match(line).groups()
                self.loads.append((int(unit), int(code), message))
            elif APPEND.match(line):
                unit, code, index, message = APPEND.match(line).groups()
                self.appends.append((int(unit), int(code), int(index), message))
            elif EVENT.match(line):
                unit, code, message = EVENT.match(line).groups()
                self.events.append((int(unit), int(code), message))
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
            elif PUBLIC_SIZE.match(line):
                unit, bound, written, digest = PUBLIC_SIZE.match(line).groups()
                self.public_size.append((int(unit), int(bound), int(written), digest))
            elif FIELD.match(line):
                chunk, name, offset, size = FIELD.match(line).groups()
                self.fields.append((int(chunk), name, int(offset), int(size)))
            elif RTC.match(line):
                g = RTC.match(line).groups()
                self.rtc.append({"unit": int(g[0]), "status": int(g[1]), "seconds": int(g[2]), "ticks": int(g[3]), "subsecond": int(g[4]),
                                 "cycle": int(g[5]), "d": int(g[6]), "e": int(g[7]), "f": int(g[8])})
            elif MUTATE_ERROR.match(line):
                self.mutate_errors.append((int(MUTATE_ERROR.match(line)[1]), MUTATE_ERROR.match(line)[2]))
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


class FieldTable:
    """The fields of one chunk of a saved state, by label ("name", or "name[k]" when the name repeats, as the slots of a table do), with their
    offsets, so that a test can read a field or write several of them and get a state with right checksums."""

    def __init__(self, blob, chunk, run_fields):
        self.blob, self.chunk = blob, chunk
        named = [(name, offset, size) for index, name, offset, size in run_fields if index == chunk]
        total = collections.Counter(name for name, _, _ in named)
        seen = collections.Counter()
        self.where, self.order = {}, []
        for name, offset, size in named:
            label = name if total[name] == 1 else f"{name}[{seen[name]}]"
            seen[name] += 1
            self.where[label] = (offset, size)
            self.order.append(label)

    def get(self, label, signed=False):
        offset, size = self.where[label]
        return int.from_bytes(self.blob[offset:offset + size], "little", signed=signed)

    def size(self, label):
        return self.where[label][1]

    def with_values(self, values):
        """The state with the given fields (label -> integer) replaced."""
        blob = self.blob
        for label, value in values.items():
            offset, size = self.where[label]
            blob = with_bytes(blob, self.chunk, offset, int(value).to_bytes(size, "little", signed=value < 0))
        return blob


def built(target, variant=None):
    """The executable of `target`, configured and built once per process (the code generator runs at every build). `variant` is "optimized" (-O2 with
    asserts, build/state-opt) or "debug" (-O0, build/link-audit); by default the one P6_STATE_PROBE_BUILD selects (optimized when unset)."""
    directory, optimized = state_probe_tree(variant)
    if (directory, target) not in _BUILT:
        _BUILT[directory, target] = build_targets(directory, [target], optimized=optimized)[target]
    return _BUILT[directory, target]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ProbeCase(unittest.TestCase):
    """A class that drives p6_state_probe. ROMS maps a name to (fixture or source text, defines, replacements)."""

    ROMS = {}
    exe = None
    MACHINE = "a500"        # the --machine of the probe (T23b): a500 is the probe's default and adds no argument

    @classmethod
    def firmware(cls):
        """The Kickstart image of the "kickstart" scenarios, or None: P6_KICKSTART_ROM names a raw .rom (the A600 classes read theirs from a zip, see tests/state/test_det02_a600.py)."""
        kickstart = os.environ.get("P6_KICKSTART_ROM")
        return Path(kickstart) if kickstart and Path(kickstart).is_file() else None

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
        kickstart = cls.firmware()
        if kickstart:
            cls.roms["kickstart"] = kickstart

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def probe(cls, rom, *arguments):
        return cls.probe_on(cls.MACHINE, rom, *arguments)

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        """A run of the probe on `machine` (a state of another machine comes from here)."""
        flag = ["--machine", machine] if machine != "a500" else []
        result = subprocess.run([str(cls.exe), str(cls.roms[rom]), *flag, *arguments], capture_output=True, text=True, timeout=1800)
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
