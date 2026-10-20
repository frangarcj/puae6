"""T55c10/WB03: A4040 in the real sandbox, including new sandbox/process restore."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_wrapper_cpu as base
from tests.state import test_a4040 as a4040


@unittest.skipUnless(base.runtime_available() and base.TOOLS, "real Waterbox runtime/guest required")
class WrapperA4040Tests(unittest.TestCase):
    session = base.WrapperCpuTests.session
    units = staticmethod(base.WrapperCpuTests.units)
    digests = staticmethod(base.WrapperCpuTests.digests)

    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(base.RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="a4040-wb-", dir=base.ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        base.WrapperCpuTests.count = 0
        for name, (source, _, _) in a4040.A4040Tests.ROMS.items():
            (cls.dir / (name + ".rom")).write_bytes(base.builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def core(name, cpu, fpu="None", extra=""):
        return f"core {name} machine=a4040 accuracy=Normal cpu=M{cpu} fpu={fpu} {extra}"

    def test_cpu_fpu_z3_oracles_and_same_new_sandbox_and_process_restore(self):
        for cpu in a4040.CPU:
            for rom, fpu, extra, domain, address, count, expected in (
                    ("cpu-" + cpu, "None", "", "Chip_RAM", "2010", 4, "0f967f0a"),
                    ("fpu", "M" + cpu, "", "Chip_RAM", "2020", 12, "3ffd0000aaaaaaaaaaaaaaab"),
                    ("z3", "None", "z3=MB16", "Z3_Fast_RAM", "0", 4, "11223344"),
                    ("cpu-" + cpu, "None", "fast=None", "Chip_RAM", "2010", 4, "0f967f0a")):
                with self.subTest(cpu=cpu, rom=rom, extra=extra):
                    n, m = (12, 3) if rom == "z3" else (3, 4)
                    state = self.dir / "restore.state"
                    out = self.session([self.core("A", cpu, fpu, extra), f"run A {n}", "save A s", f"savefile A {state}",
                                        f"peek A {domain} {address} {count}", f"run A {m}", "digest A first", "load A s",
                                        f"run A {m}", "digest A second", self.core("B", cpu, fpu, extra), "load B s",
                                        f"run B {m}", "digest B third"], rom)
                    self.assertIn(f"peek A {domain} {address} {expected}", out)
                    digests = self.digests(out, "A")
                    self.assertEqual(digests["first"], digests["second"])
                    self.assertEqual(digests["first"], self.digests(out, "B")["third"])
                    future = [u[1:] for u in self.units(out, "A")[n:n+m]]
                    self.assertEqual(future, [u[1:] for u in self.units(out, "A")[n+m:]])
                    self.assertEqual(future, [u[1:] for u in self.units(out, "B")])
                    fresh = self.session([self.core("C", cpu, fpu, extra), f"loadfile C {state}", f"run C {m}", "digest C fresh"], rom)
                    self.assertEqual(future, [u[1:] for u in self.units(fresh, "C")])
                    self.assertEqual(digests["first"], self.digests(fresh, "C")["fresh"])
                    state.unlink()

    def test_identity_refuses_other_cpu_fpu_and_invalid_pairs(self):
        for change in (self.core("B", "68060"), self.core("B", "68040", "M68040")):
            out = self.session([self.core("A", "68040"), "run A 3", "save A s", change, "run B 2", "digest B before", "load B s", "digest B after"], "cpu-68040")
            self.assertRegex(out, r"rejected B s reason=OtherConfig")
            self.assertEqual(self.digests(out, "B")["before"], self.digests(out, "B")["after"])
        for cpu, fpu in (("68040", "M68060"), ("68060", "M68040"), ("68040", "M68882")):
            out = self.session([self.core("A", cpu, fpu)], "cpu-68040", expect=None)
            self.assertIn("FPU" if fpu == "M68882" else "matching A4040 internal CPU", out)


if __name__ == "__main__":
    unittest.main()
