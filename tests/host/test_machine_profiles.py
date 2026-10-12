"""config/profiles of the T50 machines (A500 OG, A500+, A1000, A2000 OG, A2000) against the preferences the bridge builds for them.

The checks are those of test_a500_profile (every pinned field has the stated value for PAL and NTSC; every byte of struct uae_prefs that differs from upstream's own preset belongs to a
declared override or to p6_config; a field sourced "upstream" or "pin" has the value upstream has), with the probe told which machine to build. The comparator is upstream's built_in_prefs
for the machine's model and configuration, after the video standard is chosen as WinUAE does; the A2000 has no model of its own upstream (PUAE5 builds it as `chipset_compatible = A2000` on an
A500 or an A500+), so its comparator is the A500 (OG) or the A500+ and the A2000 table is a declared override. What each class adds is the fact that makes the machine what it is.
"""
import unittest

from tests.host.test_a500_profile import ROOT, TOOLS, ProfileChecks

CP_A500, CP_A500P, CP_A1000, CP_A2000 = 5, 6, 8, 10        # options.h: enum { CP_GENERIC = 1, CP_CDTV, CP_CDTVCR, CP_CD32, CP_A500, CP_A500P, CP_A600, CP_A1000, CP_A1200, CP_A2000, ...
AGNUSMODEL_A1000, DENISEMODEL_A1000 = 2, 3
MIB = 1048576


class MachineChecks(ProfileChecks):
    """A machine's facts: chipset mask, Chip and Slow RAM and the chipset table value (cs_compatible), the same for PAL and NTSC."""
    CHIPSET = 0
    CHIP = 512 * 1024
    SLOW = 0
    COMPATIBLE = 0

    def facts(self, name):
        values = {self.pinned[standard][name] for standard in ("pal", "ntsc")}
        self.assertEqual(len(values), 1, name)
        return int(values.pop())

    def test_the_machine_is_what_its_profile_says(self):
        self.assertEqual((self.facts("chipset_mask"), self.facts("chipmem.size"), self.facts("bogomem.size"), self.facts("fastmem[0].size")),
                         (self.CHIPSET, self.CHIP, self.SLOW, 0))
        self.assertEqual((self.facts("cpu_model"), self.facts("fpu_model"), self.facts("mmu_model")), (68000, 0, 0))
        self.assertEqual((self.facts("cs_ide"), self.facts("cs_pcmcia"), self.facts("cs_cdtvcd"), self.facts("cs_cd32cd")), (0, 0, 0, 0))
        self.assertEqual(self.facts("cs_compatible"), self.COMPATIBLE)
        self.assertEqual((self.facts("floppyslots[0].dfxtype"), self.facts("floppyslots[1].dfxtype")), (0, 0))      # DF0 and DF1 on every machine
        self.assertEqual(self.facts("cs_resetwarning"), 0)

    def test_the_reset_warning_is_off_in_the_machine_that_builds_a_table_with_it_on(self):
        fields = {f["name"]: f for f in self.profile["fields"]}
        self.assertEqual(fields["cs_resetwarning"]["value"], 0)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A500OGProfileTests(MachineChecks, unittest.TestCase):
    MACHINE, PROBE_ARG, PROFILE = "A500OG", "a500og", ROOT / "config/profiles/a500og-ocs.json"
    COMPATIBLE = CP_A500

    def test_it_is_the_a500_without_slow_ram_and_without_a_clock(self):
        self.assertEqual(self.facts("cs_rtc"), 0)
        fields = {f["name"]: f for f in self.profile["fields"]}
        self.assertEqual((fields["bogomem.size"]["source"], fields["bogomem.size"]["value"]), ("upstream", 0))      # configuration 3 of upstream's A500, no override
        self.assertEqual(fields["floppyslots[1].dfxtype"]["source"], "override")         # upstream's configuration 3 has one drive; the ABI keeps DF1


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A500PlusProfileTests(MachineChecks, unittest.TestCase):
    MACHINE, PROBE_ARG, PROFILE = "A500PLUS", "a500plus", ROOT / "config/profiles/a500plus-ecs.json"
    CHIPSET, CHIP, COMPATIBLE = 3, MIB, CP_A500P

    def test_it_has_the_clock_of_its_table_and_no_gayle(self):
        self.assertEqual(self.facts("cs_rtc"), 1)
        fields = {f["name"]: f for f in self.profile["fields"]}
        for name in ("cs_ide", "cs_pcmcia", "cs_compatible", "chipset_mask", "chipmem.size"):
            self.assertIn(fields[name]["source"], ("upstream", "pin"), name)       # the A500+ comes from upstream's model 1 with nothing cleared


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A1000ProfileTests(MachineChecks, unittest.TestCase):
    MACHINE, PROBE_ARG, PROFILE = "A1000", "a1000", ROOT / "config/profiles/a1000-ocs.json"
    CHIPSET = 0x20             # CSMASK_A1000

    def test_it_has_the_chips_the_ram_board_and_the_mains_tod_of_the_a1000(self):
        self.assertEqual((self.facts("cs_a1000ram"), self.facts("cs_agnusmodel"), self.facts("cs_denisemodel")), (1, AGNUSMODEL_A1000, DENISEMODEL_A1000))
        self.assertEqual(self.facts("cs_ksmirror_e0"), 0)
        self.assertEqual((self.pinned["pal"]["cs_ciaatod"], self.pinned["ntsc"]["cs_ciaatod"]), ("1", "2"))      # 50 Hz for PAL, 60 Hz for NTSC

    def test_the_table_is_cleared_after_it_is_applied_and_the_audio_filter_is_declared(self):
        fields = {f["name"]: f for f in self.profile["fields"]}
        self.assertEqual((fields["cs_compatible"]["source"], fields["cs_compatible"]["value"]), ("override", 0))
        self.assertEqual(fields["cs_resetwarning"]["source"], "override")                  # upstream's A1000 table has the reset warning on
        self.assertEqual(fields["sound_filter"]["source"], "override")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A2000OGProfileTests(MachineChecks, unittest.TestCase):
    MACHINE, PROBE_ARG, PROFILE = "A2000OG", "a2000og", ROOT / "config/profiles/a2000og-ocs.json"
    SLOW = 512 * 1024

    def test_it_is_the_a500_with_the_a2000_table(self):
        self.assertEqual((self.facts("cs_rtc"), self.facts("cs_unmapped_space"), self.facts("cs_df0idhw")), (1, 1, 1))
        self.assertEqual((self.pinned["pal"]["cs_ciaatod"], self.pinned["ntsc"]["cs_ciaatod"]), ("1", "2"))
        fields = {f["name"]: f for f in self.profile["fields"]}
        for name in ("cs_unmapped_space", "cs_df0idhw", "cs_compatible"):
            self.assertEqual(fields[name]["source"], "override", name)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A2000ProfileTests(MachineChecks, unittest.TestCase):
    MACHINE, PROBE_ARG, PROFILE = "A2000", "a2000", ROOT / "config/profiles/a2000-ecs.json"
    CHIPSET, CHIP = 1, MIB

    def test_it_is_the_a500_plus_with_the_ecs_agnus_only_and_the_a2000_table(self):
        self.assertEqual((self.facts("cs_rtc"), self.facts("cs_unmapped_space"), self.facts("cs_1mchipjumper")), (1, 1, 1))
        fields = {f["name"]: f for f in self.profile["fields"]}
        for name in ("chipset_mask", "cs_unmapped_space", "cs_compatible", "cs_1mchipjumper"):
            self.assertEqual(fields[name]["source"], "override", name)


if __name__ == "__main__":
    unittest.main()
