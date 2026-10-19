"""T55c3: shared option, real bus address and map changes across native restore."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from test_memory import descriptors, PEEK, MAP
from tests.state.test_z3 import program, SIZES
from tests.build_support import build_targets

OPTIONS = ("--set", "puae6_preset=a4030_normal", "--set", "puae6_z3_memory=MB16")


class Z3FrontendTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless

    @classmethod
    def setUpClass(cls):
        built = build_targets(lr.ROOT / "build/state-opt", ["p6_libretro_host", "puae6_libretro", "p6_headless"], optimized=True)
        cls.host, cls.core, cls.headless = (built[name] for name in ("p6_libretro_host", "puae6_libretro", "p6_headless"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="z3-lr-", dir=lr.ROOT / "build")
        cls.counter = 0
        cls.roms = {}
        boot = Path(cls.tmp.name) / "boot.rom"
        boot.write_bytes(lr.builder.build((lr.FIXTURES / "boot.s").read_text()))
        cls.roms["boot"] = boot
        rom = Path(cls.tmp.name) / "z3.rom"
        rom.write_bytes(lr.builder.build(program(True)))
        cls.roms["z3"] = rom
        for mib in SIZES:
            if mib != 16:
                path = Path(cls.tmp.name) / f"z3-{mib}.rom"
                path.write_bytes(lr.builder.build(program(True, mib)))
                cls.roms[f"z3-{mib}"] = path

    def test_shared_option_real_address_and_map_withdrawal_on_restore(self):
        out = self.frontend("z3", "--runs", 12, *OPTIONS,
                            "--do", "12:peek:0x22000000:4", "--do", "12:peek:0x22fffffc:4", name="kick40068.A4000").stdout
        self.compare_with_headless(out, "z3", 12, "--machine", "a4030", "--cpu-compatibility", "normal", "--z3", "16")
        found, order = descriptors(out)
        self.assertEqual(["CHIP", "FAST", "Z3", "ROM"], order)
        self.assertEqual((0x22000000, 16 << 20), (found["Z3"]["start"], found["Z3"]["len"]))
        peeks = {m[1]: m[3] for m in PEEK.finditer(out)}
        self.assertEqual(("11223344", "55667788"), (peeks["0x22000000"], peeks["0x22fffffc"]))
        replay = self.frontend("z3", "--runs", 23, *OPTIONS,
                               "--do", "1:save:early", "--do", "14:load:early", name="kick40068.A4000").stdout
        self.assertIn("state load early exact ok=1", replay)
        self.assertEqual([3, 4, 3, 4], [int(m[2]) for m in MAP.finditer(replay)])

    def test_all_sizes_match_headless_and_expose_the_actual_extent(self):
        for mib in SIZES:
            if mib == 16:
                continue
            with self.subTest(mib=mib):
                end = 0x22000000 + (mib << 20) - 4
                options = ("--set", "puae6_preset=a4030_normal", "--set", f"puae6_z3_memory=MB{mib}")
                out = self.frontend(f"z3-{mib}", "--runs", 12, *options, "--do", "12:peek:0x22000000:4",
                                    "--do", f"12:peek:{end:#x}:4", name="kick40068.A4000").stdout
                self.compare_with_headless(out, f"z3-{mib}", 12, "--machine", "a4030", "--cpu-compatibility", "normal", "--z3", str(mib))
                found, order = descriptors(out)
                self.assertEqual(["CHIP", "FAST", "Z3", "ROM"], order)
                self.assertEqual((0x22000000, mib << 20), (found["Z3"]["start"], found["Z3"]["len"]))
                peeks = {m[1]: m[3] for m in PEEK.finditer(out)}
                self.assertEqual(("11223344", "55667788"), (peeks["0x22000000"], peeks[f"{end:#x}"]))

    def test_z3_requires_restart_and_invalid_machine_is_refused(self):
        out = self.frontend("z3", "--runs", 12, *OPTIONS, "--set-at", "3:puae6_z3_memory=None", name="kick40068.A4000").stdout
        self.assertIn("reload the content to apply it", out)
        self.assertIn("Z3", descriptors(out)[0])
        refused = self.frontend("boot", "--set", "puae6_z3_memory=MB16", expect=3).stdout
        self.assertIn("Zorro III supports one 1/2/4/8/16/32/64/128/256/512 MiB board on A4030 only", refused)


if __name__ == "__main__":
    unittest.main()
