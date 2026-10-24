"""Shared by the movie tests (T22): the ROMs, ADFs and input scripts the fixtures use, and a runner of programs of the session language of tests/ffi/Session.cs
in a real Waterbox host. Nothing binary is committed: every ROM and disk is built here from the micro-ROM sources and the disk generator."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.input.test_input import CAPS, ESC, FIXTURES, JOYSTICK, KEY_Q, KEY_W, LEFT, MOUSE, RIGHT, UP, B1, B2, line  # noqa: E402
from tests.scenarios.a500.display import Display, rom_source  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
LACE = Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)
AVAILABLE = runtime_available() and bool(TOOLS)


class Harness:
    """ROMs, disks and scripts in a temporary directory, and `session()`, which runs a program in a new process."""

    def __init__(self, prefix="p6-movies-"):
        self.tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.dir = Path(self.tmp.name)
        self.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        for name, source in (("disk", disk.rom_source()), ("input_all", (FIXTURES / "input_all.s").read_text(encoding="utf-8")), ("lace", rom_source(LACE))):
            (self.dir / f"{name}.rom").write_bytes(builder.build(source))
        for name, label in (("A", 0x41), ("B", 0x42)):
            (self.dir / f"{name}.adf").write_bytes(disk.make_adf(label))
        self.count = 0

    def close(self):
        self.tmp.cleanup()

    def rom(self, name):
        return str(self.dir / f"{name}.rom")

    def adfs(self, names="A,B"):
        return ",".join(str(self.dir / f"{n}.adf") for n in names.split(",") if n)

    def script(self, name, lines):
        path = self.dir / f"{name}.txt"
        path.write_text("\n".join(lines) + "\n")
        return str(path)

    def core(self, name, firmware="disk", adf="A,B", **options):
        extra = "".join(f" {k}={v}" if v is not True else f" {k}" for k, v in options.items())
        return f"core {name} firmware={self.rom(firmware)}" + (f" adf={self.adfs(adf)}" if adf else "") + extra

    def session(self, lines, env=None, expect=0, cwd=None):
        self.count += 1
        path = self.dir / f"program-{self.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", self.rom("disk"),
                                        "--program", str(path), env=env, cwd=cwd)
        if expect is not None and result.returncode != expect:
            raise AssertionError(f"exit {result.returncode}, expected {expect}\n{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    @staticmethod
    def read_movie(path):
        with zipfile.ZipFile(path) as z:
            header = dict(l.split(" ", 1) for l in z.read("Header.txt").decode().splitlines() if " " in l)
            log = z.read("Input Log.txt").decode().splitlines()
        key = next(l for l in log if l.startswith("LogKey:"))[len("LogKey:"):]
        entries = [l for l in log if l.startswith("|")]
        return header, key, entries, log

    @staticmethod
    def rewrite_movie(source, target, header_edits=None, log_edit=None):
        """A copy of a .bk2 with some header lines changed (a value, or None to drop the key)."""
        with zipfile.ZipFile(source) as z:
            header_lines = z.read("Header.txt").decode().splitlines()
            log = z.read("Input Log.txt").decode()
        out = []
        for line_ in header_lines:
            key = line_.split(" ", 1)[0]
            if header_edits and key in header_edits:
                if header_edits[key] is not None:
                    out.append(f"{key} {header_edits[key]}")
            else:
                out.append(line_)
        with zipfile.ZipFile(target, "w") as z:
            z.writestr("Header.txt", "\n".join(out) + "\n")
            z.writestr("Input Log.txt", log_edit(log) if log_edit else log)
        return target


def fixture_inputs(h):
    """What the main fixture movie presses, by frame (1-based): a script of keys, mouse and joysticks, and the buttons of the core (reset, disks)."""
    lines = []
    for u in range(1, 241):
        keys = [ESC] if 30 <= u <= 39 else [0x38] if u == 45 else [0x39, 0x3A] if u == 46 else [KEY_Q, KEY_W] if u in (50, 51) else [CAPS] if 60 <= u <= 70 else []   # 0x38 Comma, 0x39 Period, 0x3A Slash: mnemonics that are punctuation
        mouse = (MOUSE, B1 if 120 <= u <= 130 else 0, 3 if u % 5 == 0 else (-2 if u % 7 == 0 else 0), 1 if u % 11 == 0 else 0)
        joy = (JOYSTICK, (RIGHT if 90 <= u <= 100 else 0) | (UP if 100 <= u <= 110 else 0) | (B2 if u == 140 else 0), 0, 0)
        lines.append(line(keys=keys, p0=mouse, p1=joy))
    script = h.script("fixture-script", lines)
    press = "135:Power;79:Next_Slot;80:Insert_Disk;150:Eject_Disk;158:Insert_Disk"
    return script, press
