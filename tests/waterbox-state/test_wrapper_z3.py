"""T55c3/c5: all Z3 capacities, domains, real sandbox restore and measured heap margin; no commercial firmware."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_wrapper_cpu as base
from tests.state.test_z3 import program

ROOT, bw = base.ROOT, base.build_waterbox
SIZES = (16, 1, 2, 4, 8, 32, 64, 128, 256, 512)


@unittest.skipUnless(base.runtime_available(), "real Waterbox runtime/guest required")
class WrapperZ3Tests(unittest.TestCase):
    session = base.WrapperCpuTests.session
    core = staticmethod(base.WrapperCpuTests.core)
    digests = staticmethod(base.WrapperCpuTests.digests)

    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(base.RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="z3-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        base.WrapperCpuTests.count = 0
        for mib in SIZES:
            for name, z2 in (("z3", True), ("z3-only", False)):
                (cls.dir / f"{name}-{mib}.rom").write_bytes(base.builder.build(program(z2, mib)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_domains_and_same_new_sandbox_and_process_restore(self):
        for mib in SIZES:
            for fast, name in (("Auto", "z3"), ("None", "z3-only")):
                with self.subTest(mib=mib, fast=fast):
                    settings = ("accuracy=Normal", f"fast={fast}", f"z3=MB{mib}")
                    state = self.dir / f"{fast}-{mib}.state"
                    rom, end = f"{name}-{mib}", f"{(mib << 20) - 4:x}"
                    follow = 4 if mib == 16 else 1
                    lines = [self.core("A", *settings, machine="a4030"), "domains A"]
                    if mib == 16:  # Preserve c3's cut before autoconfig as a regression.
                        lines += ["run A 1", "save A early", "run A 11"]
                    else:
                        lines += ["run A 12"]
                    lines += ["peek A Z3_Fast_RAM 0 4", f"peek A Z3_Fast_RAM {end} 4", "save A s", f"savefile A {state}",
                              f"poke A Z3_Fast_RAM {end} deadbeef", "load A s", f"run A {follow}", "digest A first"]
                    lines += ["load A early", "run A 15"] if mib == 16 else ["load A s", "run A 1"]
                    other = "None" if mib == 16 else "MB16"
                    lines += ["digest A second", self.core("B", *settings, machine="a4030"), "load B s", f"run B {follow}", "digest B third",
                              self.core("C", "accuracy=Normal", f"fast={fast}", f"z3={other}", machine="a4030"),
                              "run C 1", "digest C before", "load C s", "digest C after"]
                    if mib == 16:  # Keep None rejection above and also reject another populated capacity.
                        lines += [self.core("E", "accuracy=Normal", f"fast={fast}", "z3=MB32", machine="a4030"),
                                  "run E 1", "digest E before", "load E s", "digest E after"]
                    try:
                        out = self.session(lines, rom)
                        self.assertIn(f"domain A Z3_Fast_RAM bytes={mib << 20} writable=True", out)
                        self.assertIn("peek A Z3_Fast_RAM 0 11223344", out)
                        self.assertIn(f"peek A Z3_Fast_RAM {end} 55667788", out)
                        a = self.digests(out, "A")
                        self.assertEqual(a["first"], a["second"])
                        self.assertEqual(a["first"], self.digests(out, "B")["third"])
                        units = base.WrapperCpuTests.units(out, "A")
                        future = [row[1:] for row in units[12:12+follow]]
                        self.assertEqual(future, [row[1:] for row in units[-follow:]])
                        self.assertEqual(future, [row[1:] for row in base.WrapperCpuTests.units(out, "B")])
                        self.assertRegex(out, r"rejected C s reason=OtherHeaps")
                        self.assertEqual(self.digests(out, "C")["before"], self.digests(out, "C")["after"])
                        if mib == 16:
                            self.assertRegex(out, r"rejected E s reason=OtherHeaps")
                            self.assertEqual(self.digests(out, "E")["before"], self.digests(out, "E")["after"])
                        fresh = self.session([self.core("D", *settings, machine="a4030"), f"loadfile D {state}",
                                              f"run D {follow}", "digest D fourth"], rom)
                        self.assertEqual(a["first"], self.digests(fresh, "D")["fourth"])
                        self.assertEqual(future, [row[1:] for row in base.WrapperCpuTests.units(fresh, "D")])
                    finally:
                        # Each session process disposes its sandboxes; don't retain large states between capacities.
                        state.unlink(missing_ok=True)

    def test_actual_heap_commitment_has_the_existing_two_times_margin(self):
        dotnet, env = bw.build_managed()
        result_path = self.dir / "budget.json"
        baseline = bw.run_ffi("heaps", "--machine", "A4030", "--z3", "None")
        self.assertEqual(0, baseline.returncode, baseline.stderr)
        none = int(baseline.stdout.strip().split()[-1]) * 1024
        for mib in SIZES:
            with self.subTest(mib=mib):
                command = [dotnet, str(bw.BUILD / "managed/bin/Debug/net8.0/Smoke.dll"), "--puae6", "--runtime", str(bw.runtime_receipt_path()),
                           "--guest", str(base.RECEIPT), "--manifest", str(ROOT / "manifests/waterbox-memory.json"), "--firmware", str(self.dir / f"z3-{mib}.rom"),
                           "--units", "12", "--machine", "a4030", "--z3", str(mib), "--result", str(result_path)]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
                self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-2000:])
                report = json.loads(result_path.read_text())
                result = bw.run_ffi("heaps", "--machine", "A4030", "--z3", f"MB{mib}")
                self.assertEqual(0, result.returncode, result.stderr)
                budget = int(result.stdout.strip().split()[-1]) * 1024
                self.assertEqual(mib << 21, budget - none)
                self.assertEqual(budget, report["layout"]["mmap"])
                for stage in ("after_seal", "after_first_unit", "after_last_unit"):
                    for heap, limit in report["layout"].items():
                        self.assertLessEqual(2 * report[stage][heap]["committed_bytes"], limit, (mib, stage, heap))
                self.assertIn({"name": "Z3 Fast RAM", "bytes": mib << 20}, report["areas"])
                print("Z3 measured heaps", json.dumps({"mib": mib, "guest": report["guest_sha256"], "runtime": report["runtime_sha256"],
                      "budget": report["layout"], "last": report["after_last_unit"]}, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
