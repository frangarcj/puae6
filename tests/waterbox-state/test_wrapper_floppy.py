"""T62 (DET02, WB03): the floppy drive speed on the three surfaces. headless takes --floppy-speed, the BizHawk core the FloppySpeed sync setting, libretro the puae6_floppy_speed option.

The option is p6_config.floppy_speed (config/schema/options.json names every spelling; this file reads the schema and has no table of its own). What is decided here:
PCM/ticks  the Waterbox core gives, unit by unit, the ticks and the samples of p6_headless with the same speed (a disk is read by DMA the whole time), for every speed;
LOAD       the speed accelerates the disk inside the sandbox as it does natively (units until a read of one track ends, tracks read in 40 units: 10/3, 5/7, 2/15, 1/30 and 0/6046);
WB03       save at N, go on, load, repeat: the same units and digest at every speed, turbo included, with states taken before, in and after the transfers (the Waterbox state is the whole
           sandbox, so the two lines a turbo transfer waits are in it by construction);
ID         a state or a movie of one speed is refused by a core with any other (OtherConfig, before the sandbox is touched) and accepted by the same one; a core at the real drive's speed has
           the identity it always had (the text of the identity names the speed only when it is off the default).

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet and the local NuGet cache for the wrapper part, and the native build for headless.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_floppy.py"
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox  # noqa: E402
import generate_options as gen  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.state.test_det02_floppy import DISK_COUNT_PLAIN  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
PEEK = re.compile(r"^peek (\S+) Chip_RAM (\w+) (\w+)$", re.M)
UNITS = 40

OPTION = next(o for o in gen.load_schema()["options"] if o["id"] == "floppy_speed")
SPEEDS = [(v["value"], v.get("wrapper_name", v["value"]), OPTION["headless"]["names"][v["value"]]) for v in OPTION["values"]]       # (schema value, wrapper member, headless spelling)
REAL = OPTION["default"]
# What the program measures at each speed, by schema value: units until the first read ends / reads ended in 40 units (tests/state/test_det02_floppy.py)
MEASURED = {"100": (10, 3), "200": (5, 7), "400": (2, 15), "800": (1, 30), "turbo": (0, 6046)}


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6)")
class WrapperFloppyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wrapper-floppy-")
        cls.dir = Path(cls.tmp.name)
        cls.rom = cls.dir / "disk_count.rom"
        cls.rom.write_bytes(builder.build(DISK_COUNT_PLAIN))
        cls.disk = cls.dir / "game.adf"
        cls.disk.write_bytes(hashlib.sha256(b"floppy speed").digest() * (901120 // 32))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, expect=0):
        WrapperFloppyTests.count += 1
        path = self.dir / f"program-{WrapperFloppyTests.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.rom), "--program", str(path))
        if expect is None:
            self.assertNotEqual(0, result.returncode, result.stdout[-1500:])
            return result.stdout + result.stderr
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    def core(self, name, member=None):
        return f"core {name} adf={self.disk} machine=a500" + (f" fspeed={member}" if member else "")

    def longs(self, out, name, *addresses):
        found = {int(m[2], 16): int(m[3], 16) for m in PEEK.finditer(out) if m[1] == name}
        return [found[a] for a in addresses]

    def measure(self, member):
        """(vertical blanks from the start of the first read to its end, reads ended in UNITS units) read from the memory of the sandbox."""
        peeks = ["peek A Chip_RAM 604 4", "peek A Chip_RAM 608 4", "peek A Chip_RAM 700 4"]
        out = self.session([self.core("A", member), f"run A {UNITS}", *peeks])
        reads, start, first = self.longs(out, "A", 0x604, 0x608, 0x700)
        return first - start, reads

    # --- the same ticks and samples as headless -----------------------------------------------------------------------------------------

    def test_every_speed_gives_the_ticks_and_the_samples_of_headless_with_the_same_flag(self):
        program = []
        for n, (_, member, _) in enumerate(SPEEDS):
            program += [self.core(f"C{n}", member), f"run C{n} 16", f"dispose C{n}"]
        out = self.session(program)
        for n, (value, member, spelling) in enumerate(SPEEDS):
            with self.subTest(speed=value):
                units = self.units(out, f"C{n}")
                self.assertEqual(16, len(units))
                result = subprocess.run([str(self.headless), "--run", str(self.rom), "--units", "16", "--disk", str(self.disk), "--floppy-speed", spelling, "--machine", "a500"],
                                        capture_output=True, text=True, timeout=900)
                self.assertEqual(0, result.returncode, result.stderr)
                ticks = [int(m[1]) for m in re.finditer(r"^unit \d+ ticks=(\d+) ", result.stdout, re.M)]
                self.assertEqual(ticks, [int(u[2]) for u in units])

    # --- the load is accelerated inside the sandbox --------------------------------------------------------------------------------------

    def test_the_faster_drives_and_the_turbo_one_read_the_disk_sooner_in_the_sandbox(self):
        found = {value: self.measure(None if value == REAL else member) for value, member, _ in SPEEDS}
        print("\nT62 floppy speed in the sandbox (units until the first read of $1900 words ends / reads ended in %d units): " % UNITS +
              ", ".join(f"{value} {found[value][0]}/{found[value][1]}" for value, _, _ in SPEEDS))
        self.assertEqual(MEASURED, found)

    # --- WB03 ---------------------------------------------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_machine_at_every_speed(self):
        for value, member, _ in SPEEDS:
            for n in (2, 3, 5):
                with self.subTest(speed=value, saved_at=n):
                    m = 6
                    out = self.session([self.core("A", member), f"run A {n}", "save A s", f"run A {m}", "digest A first", "load A s", f"run A {m}", "digest A second"])
                    units = self.units(out, "A")
                    self.assertEqual(n + 2 * m, len(units))
                    self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]])
                    digests = self.digests(out, "A")
                    self.assertEqual(digests["first"], digests["second"])

    def test_a_state_runs_in_a_new_sandbox_of_the_same_speed_as_in_the_first(self):
        out = self.session([self.core("A", "Turbo"), "run A 3", "save A s", self.core("B", "Turbo"), "load B s", "run A 6", "run B 6", "digest A a", "digest B b"])
        self.assertEqual([u[1:] for u in self.units(out, "A")][3:], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    # --- the identity -------------------------------------------------------------------------------------------------------------------

    def test_a_state_of_a_speed_loads_in_the_same_one_and_is_refused_by_any_other_before_the_sandbox_is_touched(self):
        program = []
        for value, member, _ in SPEEDS:
            program += [self.core(f"S{value}", member), f"run S{value} 4", f"save S{value} s{value}", f"digest S{value} saved", f"dispose S{value}"]
        for value, member, _ in SPEEDS:
            program += [self.core(f"T{value}", member), f"run T{value} 2", f"digest T{value} before"]
            program += [f"load T{value} s{other}" for other, _, _ in SPEEDS]
            program += [f"digest T{value} after"]
        out = self.session(program)
        for value, _, _ in SPEEDS:
            refusals = re.findall(rf"^rejected T{value} s(\w+) reason=(\w+) message=(.*)$", out, re.M)
            self.assertEqual(sorted(other for other, _, _ in SPEEDS if other != value), sorted(r[0] for r in refusals), f"{value} refuses every state of another speed and only those")
            for other, reason, message in refusals:
                self.assertEqual("OtherConfig", reason, f"{value} <- {other}")
                self.assertIn("other sync settings", message)
            self.assertEqual(self.digests(out, f"S{value}")["saved"], self.digests(out, f"T{value}")["after"], value)

    def test_a_movie_of_a_speed_is_refused_by_any_other_and_accepted_by_the_same_one(self):
        movies = {}
        for value, member, _ in SPEEDS:
            movies[value] = self.dir / f"movie-{value}.bk2"
            out = self.session([self.core("R", member), "run R 6 record", f"endmovie R {movies[value]}"])
            self.assertIn(f"movie {movies[value]} frames=6", out)
        for value, member, _ in SPEEDS:
            with self.subTest(played=value):
                others = [other for other, _, _ in SPEEDS if other != value]
                out = self.session([self.core("P", member)] + [f"moviecheck P {movies[other]}" for other in others] + [f"moviecheck P {movies[value]}"])
                self.assertEqual(len(others), len(re.findall(r"^movie rejected P reason=OtherConfig ", out, re.M)), out[-1500:])
                self.assertEqual(1, out.count("movie accepted P"))

    def test_at_the_real_drive_the_identity_is_the_one_it_always_was(self):
        source = (ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs").read_text(encoding="utf-8")
        self.assertIn('if (_syncSettings.FloppySpeed != Puae6FloppySpeed.P100) memory += ";floppyspeed=" + _syncSettings.FloppySpeed;', source)
        out = self.session([self.core("A"), "run A 4", "save A s", self.core("B", "P100"), "load B s", "run A 3", "run B 3", "digest A a", "digest B b"])
        self.assertNotIn("rejected", out)
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])


if __name__ == "__main__":
    unittest.main()
