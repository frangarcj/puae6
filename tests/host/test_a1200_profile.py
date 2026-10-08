"""config/profiles/a1200-aga.json against the preferences the bridge builds for P6_MACHINE_A1200.

The checks are those of test_a500_profile (every pinned field has the stated value for PAL and NTSC; every byte of
struct uae_prefs that differs from upstream's own A1200, built_in_prefs model 4, belongs to a declared override or to
p6_config), with the probe told to build the A1200. Gayle is not compiled, so cs_ide, cs_pcmcia and cs_compatible are
declared overrides, and so is cs_ciaoverlay. waiting_blits is a declared override too: upstream turns it on for every
CPU from the 68020 up, as an emulation shortcut that no hardware has.
"""
import unittest

from tests.host.test_a500_profile import ROOT, TOOLS, ProfileChecks


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A1200ProfileTests(ProfileChecks, unittest.TestCase):
    MACHINE = "A1200"
    PROBE_ARG = "a1200"
    PROFILE = ROOT / "config/profiles/a1200-aga.json"

    def test_gayle_is_off_and_the_blitter_wait_is_declared(self):
        fields = {f["name"]: f for f in self.profile["fields"]}
        for name, value in (("cs_ide", 0), ("cs_pcmcia", 0), ("cs_compatible", 0), ("cs_ciaoverlay", 1), ("waiting_blits", 0)):
            with self.subTest(field=name):
                self.assertEqual((fields[name]["source"], fields[name]["value"]), ("override", value))

    def test_it_is_a_68ec020_with_aga_and_two_mebibytes_of_chip(self):
        for standard in ("pal", "ntsc"):
            pinned = self.pinned[standard]
            self.assertEqual((pinned["cpu_model"], pinned["address_space_24"], pinned["fpu_model"], pinned["mmu_model"]), ("68020", "1", "0", "0"))
            self.assertEqual((pinned["cpu_cycle_exact"], pinned["cpu_memory_cycle_exact"], pinned["blitter_cycle_exact"]), ("1", "1", "1"))
            self.assertEqual(pinned["chipset_mask"], "7")           # CSMASK_ECS_AGNUS | CSMASK_ECS_DENISE | CSMASK_AGA
            self.assertEqual((pinned["chipmem.size"], pinned["bogomem.size"], pinned["fastmem[0].size"]), ("2097152", "0", "0"))
            self.assertEqual(pinned["cpu_clock_multiplier"], "1024")  # 14.19 MHz: four ticks of the 3.55 MHz clock per CPU clock pair


if __name__ == "__main__":
    unittest.main()
