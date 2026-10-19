"""T55c3: Z3 domains, real sandbox restore and measured heap margin; no commercial firmware."""
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
        for name, z2 in (("z3", True), ("z3-only", False)):
            (cls.dir / f"{name}.rom").write_bytes(base.builder.build(program(z2)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_domains_and_same_new_sandbox_and_process_restore(self):
        for fast, rom in (("Auto", "z3"), ("None", "z3-only")):
            with self.subTest(fast=fast):
                settings = ("accuracy=Normal", f"fast={fast}", "z3=MB16")
                state = self.dir / f"{fast}.state"
                out = self.session([self.core("A", *settings, machine="a4030"), "domains A", "run A 1", "save A early",
                                    "run A 11", "peek A Z3_Fast_RAM 0 4", "peek A Z3_Fast_RAM fffffc 4", "save A s", f"savefile A {state}",
                                    "poke A Z3_Fast_RAM fffffc deadbeef", "load A s", "run A 4", "digest A first",
                                    "load A early", "run A 15", "digest A second", self.core("B", *settings, machine="a4030"),
                                    "load B s", "run B 4", "digest B third", self.core("C", "accuracy=Normal", f"fast={fast}", machine="a4030"),
                                    "run C 1", "digest C before", "load C s", "digest C after"], rom)
                self.assertIn("domain A Z3_Fast_RAM bytes=16777216 writable=True", out)
                self.assertIn("peek A Z3_Fast_RAM 0 11223344", out)
                self.assertIn("peek A Z3_Fast_RAM fffffc 55667788", out)
                a = self.digests(out, "A")
                self.assertEqual(a["first"], a["second"])
                self.assertEqual(a["first"], self.digests(out, "B")["third"])
                self.assertRegex(out, r"rejected C s reason=OtherHeaps")
                self.assertEqual(self.digests(out, "C")["before"], self.digests(out, "C")["after"])
                fresh = self.session([self.core("D", *settings, machine="a4030"), f"loadfile D {state}", "run D 4", "digest D fourth"], rom)
                self.assertEqual(a["first"], self.digests(fresh, "D")["fourth"])

    def test_actual_heap_commitment_has_the_existing_two_times_margin(self):
        dotnet, env = bw.build_managed()
        result_path = self.dir / "budget.json"
        command = [dotnet, str(bw.BUILD / "managed/bin/Debug/net8.0/Smoke.dll"), "--puae6", "--runtime", str(bw.runtime_receipt_path()),
                   "--guest", str(base.RECEIPT), "--manifest", str(ROOT / "manifests/waterbox-memory.json"), "--firmware", str(self.dir / "z3.rom"),
                   "--units", "12", "--machine", "a4030", "--z3", "16", "--result", str(result_path)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-2000:])
        report = json.loads(result_path.read_text())
        heaps = {}
        for z3 in ("None", "MB16"):
            result = bw.run_ffi("heaps", "--machine", "A4030", "--z3", z3)
            self.assertEqual(0, result.returncode, result.stderr)
            heaps[z3] = int(result.stdout.strip().split()[-1]) * 1024
        self.assertEqual(32 << 20, heaps["MB16"] - heaps["None"])
        self.assertEqual(heaps["MB16"], report["layout"]["mmap"])
        for stage in ("after_seal", "after_first_unit", "after_last_unit"):
            for heap, budget in report["layout"].items():
                self.assertLessEqual(2 * report[stage][heap]["committed_bytes"], budget, (stage, heap))
        self.assertIn({"name": "Z3 Fast RAM", "bytes": 16 << 20}, report["areas"])
        print("Z3 measured heaps", json.dumps({"guest": report["guest_sha256"], "runtime": report["runtime_sha256"],
              "budget": report["layout"], "last": report["after_last_unit"]}, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
