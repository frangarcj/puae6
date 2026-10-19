"""T52c: the CPU and its clock in BizHawk's Waterbox host: WB03 and the identity of a state with a 68010 or another multiplier (the real sandbox snapshot, never the native serializer).

CpuModel and CpuMultiplier are sync settings of the core (restart settings): the wrapper gives the guest p6_config.cpu_model and cpu_multiplier, and the config hash of the state envelope and
the one of the header of a movie carry them when they are not Auto. What is checked, running the 68010 programs of tests/state/test_det02_cpu010.py (a DBRA loop in loop mode across unit
boundaries, the vector base with MOVES and user mode traps, the format 8 address error frame) and the A500 programs:
WB03  save at N, go on M units, load, go on M again: the same units and the same digest; the state runs in a new sandbox too (WB04), with the 68010 and with other clocks.
ID    a state of a machine with a 68010 is refused by one with a 68000 and the other way round, a state of one clock is refused at another (OtherConfig, before the sandbox is touched, and
      the instance that refused it goes on as if never asked); a core that asks for a CPU the machine cannot have fails to start with the reason of the bridge.
CPU   the A500 with a 68010 is not the A500 with a 68000: the same program gives other units (the program finds a 68010), the 68010 at a faster clock runs more of its program in the
      same unit, and the clock changes the machine and nothing the frontend counts as a unit.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_cpu.py"
"""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.state import test_det02_cpu010 as c10
from tests.state.test_a4030 import PROGRAM as A4030_PROGRAM  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
PROGRAMS = ("c10-loop", "c10-vbr", "c10-address")


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperCpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-cpu-")
        cls.dir = Path(cls.tmp.name)
        for name in PROGRAMS + ("vsync", "busy"):
            source = c10.ROMS[name][0]
            fixture = ROOT / "tests/fixtures/microrom" / f"{source}.s"
            (cls.dir / f"{name}.rom").write_bytes(builder.build(fixture.read_text(encoding="utf-8") if "\n" not in source and fixture.is_file() else source))
        (cls.dir / "a4030.rom").write_bytes(builder.build(A4030_PROGRAM))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, firmware, expect=0):
        WrapperCpuTests.count += 1
        path = self.dir / f"program-{WrapperCpuTests.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.dir / f"{firmware}.rom"),
                                        "--program", str(path))
        if expect is None:      # any failure: the program is not expected to get through
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

    @staticmethod
    def core(name, *settings, machine="a500"):
        return f"core {name} machine={machine} " + " ".join(settings)

    def test_a4030_normal_restores_in_same_and_new_sandbox_with_auto_or_no_fast(self):
        heaps = {}
        for fast in ("Auto", "None", "MB8"):
            result = build_waterbox.run_ffi("heaps", "--machine", "A4030", "--fast", fast)
            self.assertEqual(0, result.returncode, result.stdout[-1500:] + result.stderr[-1500:])
            heaps[fast] = tuple(map(int, result.stdout.strip().split()[-5:]))
        self.assertEqual(heaps["Auto"], heaps["MB8"])
        self.assertEqual(8192, heaps["Auto"][-1] - heaps["None"][-1])
        for fast in ("Auto", "None"):
            with self.subTest(fast=fast):
                settings = ("accuracy=Normal", f"fast={fast}")
                out = self.session([self.core("A", *settings, machine="a4030"), "run A 3", "save A s", "run A 5", "digest A first",
                                    "load A s", "run A 5", "digest A second", self.core("B", *settings, machine="a4030"),
                                    "load B s", "run B 5", "digest B third"], "a4030")
                digests = self.digests(out, "A")
                self.assertEqual(digests["first"], digests["second"])
                self.assertEqual(digests["first"], self.digests(out, "B")["third"])

    def test_cpu_accuracy_restores_and_refuses_a_state_of_another_level(self):
        for machine, cpu in (("a500", "Auto"), ("a500", "M68010"), ("a1200", "Auto")):
            for level in ("Exact", "Compatible", "Normal"):
                with self.subTest(machine=machine, cpu=cpu, level=level):
                    out = self.session([self.core("A", f"cpu={cpu}", f"accuracy={level}", machine=machine), "run A 4", "save A s", "run A 5", "digest A first", "load A s", "run A 5", "digest A second"], "busy")
                    self.assertEqual(self.digests(out, "A")["first"], self.digests(out, "A")["second"])
        out = self.session([self.core("A", "accuracy=Normal"), "run A 4", "save A s", self.core("B"), "run B 3", "digest B before", "load B s", "digest B after"], "vsync")
        self.assertRegex(out, r"rejected B s reason=OtherConfig")
        self.assertEqual(self.digests(out, "B")["before"], self.digests(out, "B")["after"])

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_machine_with_a_68010_and_other_clocks(self):
        for settings in (("cpu=M68010",), ("cpu=M68010", "clock=X16"), ("clock=X1",), ("clock=X8",)):
            for program in PROGRAMS:
                for n in (4, 7):
                    with self.subTest(settings=settings, program=program, saved_at=n):
                        m = 6
                        out = self.session([self.core("A", *settings), f"run A {n}", "save A s", f"run A {m}", "digest A first", "load A s", f"run A {m}", "digest A second"], program)
                        units = self.units(out, "A")
                        self.assertEqual(n + 2 * m, len(units))
                        self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]])
                        digests = self.digests(out, "A")
                        self.assertEqual(digests["first"], digests["second"])

    def test_the_a1200_at_another_clock_restores_too(self):
        for clock in ("X1", "X16"):
            with self.subTest(clock=clock):
                out = self.session([self.core("A", f"clock={clock}", machine="a1200"), "run A 5", "save A s", "run A 6", "digest A first", "load A s", "run A 6", "digest A second"], "busy")
                self.assertEqual(self.digests(out, "A")["first"], self.digests(out, "A")["second"])

    def test_a_state_runs_in_a_new_sandbox_as_in_the_first(self):
        for settings in (("cpu=M68010",), ("clock=X12",), ("cpu=M68010", "clock=X4")):
            for program in ("c10-loop", "c10-vbr"):
                with self.subTest(settings=settings, program=program):
                    out = self.session([self.core("A", *settings), "run A 5", "save A s", self.core("B", *settings), "load B s", "run A 6", "run B 6", "digest A a", "digest B b"], program)
                    self.assertEqual([u[1:] for u in self.units(out, "A")][5:], [u[1:] for u in self.units(out, "B")])
                    self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    def test_a_state_of_a_core_with_another_cpu_or_clock_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        for mine, theirs in ((("cpu=M68010",), ()), ((), ("cpu=M68010",)), (("clock=X8",), ()), ((), ("clock=X8",)), (("clock=X8",), ("clock=X16",)),
                             (("cpu=M68010", "clock=X8"), ("cpu=M68010",)), (("cpu=M68000",), ("cpu=M68010",))):
            with self.subTest(core=mine, state=theirs):
                out = self.session([self.core("A", *theirs), "run A 6", "save A s", self.core("T", *mine), "run T 3", "digest T before", "load T s", "digest T after"], "c10-loop")
                refusal = re.search(r"^rejected T s reason=OtherConfig message=(.*)$", out, re.M)
                self.assertIsNotNone(refusal, out[-1500:])
                self.assertIn("other sync settings", refusal[1])
                self.assertEqual(self.digests(out, "T")["before"], self.digests(out, "T")["after"])

    def test_the_a500_with_a_68010_is_not_the_one_with_a_68000_and_a_faster_clock_runs_more_of_the_program(self):
        cpu68010 = self.session([self.core("A", "cpu=M68010"), "run A 8", "digest A d"], "c10-vbr")
        cpu68000 = self.session([self.core("A"), "run A 8", "digest A d"], "c10-vbr")
        self.assertNotEqual(self.digests(cpu68010, "A")["d"], self.digests(cpu68000, "A")["d"])
        slow = self.session([self.core("A", "cpu=M68010", "clock=X1"), "run A 8", "digest A d"], "c10-loop")
        fast = self.session([self.core("A", "cpu=M68010", "clock=X16"), "run A 8", "digest A d"], "c10-loop")
        self.assertNotEqual(self.digests(slow, "A")["d"], self.digests(fast, "A")["d"])
        # the same units for the frontend: the same number of them, the same audio frames to a frame (the instruction that ends a unit moves the end by a few colour clocks)
        self.assertEqual(len(self.units(slow, "A")), len(self.units(fast, "A")))
        for a, b in zip(self.units(slow, "A"), self.units(fast, "A")):
            self.assertLessEqual(abs(int(a[4]) - int(b[4])), 1)

    def test_a_cpu_the_machine_cannot_have_fails_to_start_with_the_reason(self):
        for settings, machine, reason in ((("cpu=M68020",), "a500", "a 68020 is the A1200's"), (("cpu=M68010",), "a1200", "the A1200 has a soldered 68EC020"),
                                          (("cpu=M68000",), "a1200", "the A1200 has a soldered 68EC020")):
            with self.subTest(settings=settings, machine=machine):
                out = self.session([self.core("A", *settings, machine=machine)], "vsync", expect=None)
                self.assertIn(reason, out)


if __name__ == "__main__":
    unittest.main()
