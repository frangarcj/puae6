"""T105d: A4000 motherboard Fast RAM through the public BizHawk/Waterbox wrapper."""
import json
import re
import unittest

import test_wrapper_cpu as base
from tests.state.test_motherboard_fast import PROGRAM


@unittest.skipUnless(base.runtime_available() and base.TOOLS, "Waterbox runtime or puae6.wbx not built")
class WrapperMotherboardFastTests(base.WrapperCpuTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base.WrapperCpuTests.count = 0
        (cls.dir / "motherboard-fast.rom").write_bytes(base.builder.build(PROGRAM))

    def test_domain_heap_and_restore_alone_and_with_z3(self):
        factor = json.loads((base.ROOT / "manifests/waterbox-memory.json").read_text())["memory_budget"]["mmap_extra_factor"]
        none = base.build_waterbox.run_ffi("heaps", "--machine", "A4030", "--fast", "None", "--motherboard-fast", "None")
        one = base.build_waterbox.run_ffi("heaps", "--machine", "A4030", "--fast", "None", "--motherboard-fast", "MB1")
        self.assertEqual((0, 0), (none.returncode, one.returncode), none.stderr + one.stderr)
        self.assertEqual(1024 * factor, int(one.stdout.split()[-1]) - int(none.stdout.split()[-1]))
        for z3 in ("None", "MB1"):
            with self.subTest(z3=z3):
                settings = ("accuracy=Normal", "fast=None", "motherboard-fast=MB1", f"z3={z3}")
                out = self.session([
                    self.core("A", *settings, machine="a4030"), "domains A", "run A 3",
                    "peek A Motherboard_Fast_RAM 0 4", "peek A Motherboard_Fast_RAM ffffc 4",
                    "save A s", "run A 4", "digest A first", "load A s", "run A 4", "digest A second",
                    self.core("B", *settings, machine="a4030"), "load B s", "run B 4", "digest B third",
                ], "motherboard-fast")
                self.assertIn("domain A Motherboard_Fast_RAM bytes=1048576 writable=True", out)
                self.assertRegex(out, r"peek A Motherboard_Fast_RAM 0 (?!00000000)[0-9a-f]{8}")
                self.assertIn("peek A Motherboard_Fast_RAM ffffc 55667788", out)
                a = self.digests(out, "A")
                self.assertEqual(a["first"], a["second"])
                self.assertEqual(a["first"], self.digests(out, "B")["third"])
                if z3 == "MB1":
                    self.assertIn("domain A Z3_Fast_RAM bytes=1048576 writable=True", out)


if __name__ == "__main__":
    unittest.main()
