"""T54d: the FPU in BizHawk's Waterbox host: WB03 and the identity of a state with a 68881 or a 68882 (the real sandbox snapshot, never the native serializer).

The FPU is a sync setting of the core (Puae6SyncSettings.FpuModel, a restart setting): the wrapper gives the guest p6_config.fpu_model, and the config hash of the state envelope and
the one of the header of a movie carry it when it is not None. What is checked, running the FPU programs of tests/state/test_det02_fpu.py (arithmetic in every rounding mode and
precision, an exception posted for the next instruction, FSAVE/FRESTORE) on the A1200:
WB03  save at N, go on M units, load, go on M again: the same units and the same digest; the state runs in a new sandbox too (WB04), also with the registers of the FPU as the
      program left them.
ID    a state of an A1200 with a 68882 is refused by an A1200 without an FPU and by one with a 68881, and the other way round (OtherConfig, before the sandbox is touched, and the
      instance that refused it goes on as if never asked); a core that asks for an FPU on a 68000 machine fails to start with the reason of the bridge.
FPU   the A1200 with a 68882 is not the A1200 without one: the same program gives other units (the program finds a coprocessor), and the units are the ones p6_headless --fpu gives.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_fpu.py"
"""
import json
import os
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
from tests.state import test_det02_fpu as fpu  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
PROGRAMS = ("fpu-arith", "fpu-rounding", "fpu-trap", "fpu-frames")


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperFpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fpu-")
        cls.dir = Path(cls.tmp.name)
        for name in PROGRAMS:
            (cls.dir / f"{name}.rom").write_bytes(builder.build(fpu.ROMS[name][0]))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, firmware, expect=0):
        WrapperFpuTests.count += 1
        path = self.dir / f"program-{WrapperFpuTests.count}.txt"
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
    def core(name, fpu_model="M68882", machine="a1200"):
        return f"core {name} machine={machine} fpu={fpu_model}"

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_machine_with_a_68881_and_a_68882(self):
        for model in ("M68881", "M68882"):
            for program in PROGRAMS:
                for n in (4, 7):
                    with self.subTest(model=model, program=program, saved_at=n):
                        m = 6
                        out = self.session([self.core("A", model), f"run A {n}", "save A s", f"run A {m}", "digest A first", "load A s", f"run A {m}", "digest A second"], program)
                        units = self.units(out, "A")
                        self.assertEqual(n + 2 * m, len(units))
                        self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]])
                        digests = self.digests(out, "A")
                        self.assertEqual(digests["first"], digests["second"])

    def test_a_state_runs_in_a_new_sandbox_as_in_the_first(self):
        for program in ("fpu-trap", "fpu-arith"):
            with self.subTest(program=program):
                out = self.session([self.core("A"), "run A 5", "save A s", self.core("B"), "load B s", "run A 6", "run B 6", "digest A a", "digest B b"], program)
                self.assertEqual([u[1:] for u in self.units(out, "A")][5:], [u[1:] for u in self.units(out, "B")])
                self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    def test_a_state_of_a_core_with_another_fpu_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        for mine, theirs in (("M68882", "None"), ("None", "M68882"), ("M68881", "M68882"), ("M68882", "M68881")):
            with self.subTest(core=mine, state=theirs):
                out = self.session([self.core("A", theirs), "run A 6", "save A s", self.core("T", mine), "run T 3", "digest T before", "load T s", "digest T after"], "fpu-arith")
                refusal = re.search(r"^rejected T s reason=OtherConfig message=(.*)$", out, re.M)
                self.assertIsNotNone(refusal, out[-1500:])
                self.assertIn("other sync settings", refusal[1])
                self.assertEqual(self.digests(out, "T")["before"], self.digests(out, "T")["after"])

    def test_the_a1200_with_a_68882_is_not_the_one_without(self):
        with_fpu = self.session([self.core("A"), "run A 8", "digest A d"], "fpu-arith")
        without = self.session([self.core("A", "None"), "run A 8", "digest A d"], "fpu-arith")
        # (the picture is the same: the program draws nothing; the machine is not, its registers and its RAM hold what the coprocessor computed)
        self.assertNotEqual(self.digests(with_fpu, "A")["d"], self.digests(without, "A")["d"])

    def test_an_fpu_on_a_68000_machine_fails_to_start_with_the_reason(self):
        for machine in ("a500", "a600"):
            with self.subTest(machine=machine):
                out = self.session([self.core("A", "M68882", machine)], "fpu-arith", expect=None)
                self.assertIn("an FPU needs a 68020", out)


KICKSTART_A1200 = os.environ.get("P6_KICKSTART_A1200_ROM")


@unittest.skipUnless(KICKSTART_A1200 and Path(KICKSTART_A1200).is_file() and runtime_available() and TOOLS, "opt-in: P6_KICKSTART_A1200_ROM names the Kickstart 3.1 of the A1200")
class WrapperFpuKickstartTests(unittest.TestCase):
    def test_the_kickstart_31_boots_with_a_68882_in_the_sandbox_and_a_state_restores(self):
        # The ROM is read where it is: the session is given its path and nothing of it is copied, hashed or kept.
        sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        with tempfile.TemporaryDirectory(prefix="p6-fpu-ks-") as tmp:
            program = Path(tmp) / "program.txt"
            program.write_text("\n".join(["core A machine=a1200 fpu=M68882", "run A 80", "save A s", "run A 40", "digest A first", "load A s", "run A 40", "digest A second"]) + "\n")
            result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", sha, "--firmware", KICKSTART_A1200, "--program", str(program))
        self.assertEqual(0, result.returncode, result.stdout[-1500:] + result.stderr[-1500:])
        digests = {m[1]: m[2] for m in re.finditer(r"^digest A (\S*) sha=(\w+) ", result.stdout, re.M)}
        self.assertEqual(digests["first"], digests["second"])


if __name__ == "__main__":
    unittest.main()
