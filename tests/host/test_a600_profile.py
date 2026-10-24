"""config/profiles/a600-ecs.json against the preferences the bridge builds for P6_MACHINE_A600.

The checks are those of test_a500_profile (every pinned field has the stated value for PAL and NTSC; every byte of
struct uae_prefs that differs from upstream's own A600, built_in_prefs model 2, belongs to a declared override or to
p6_config), with the probe told to build the A600. Gayle is not compiled, so cs_ide, cs_pcmcia and cs_compatible are
declared overrides, and so is cs_ciaoverlay: a preference that changes without being declared fails here.
"""
import unittest

from tests.host.test_a500_profile import ROOT, TOOLS, ProfileChecks


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class A600ProfileTests(ProfileChecks, unittest.TestCase):
    MACHINE = "A600"
    PROBE_ARG = "a600"
    PROFILE = ROOT / "config/profiles/a600-ecs.json"

    def test_gayle_is_off_and_declared(self):
        fields = {f["name"]: f for f in self.profile["fields"]}
        for name, value in (("cs_ide", 0), ("cs_pcmcia", 0), ("cs_compatible", 0), ("cs_ciaoverlay", 1)):
            with self.subTest(field=name):
                self.assertEqual((fields[name]["source"], fields[name]["value"]), ("override", value))
        for standard in ("pal", "ntsc"):
            self.assertEqual(self.pinned[standard]["chipset_mask"], "3")
            self.assertEqual((self.pinned[standard]["chipmem.size"], self.pinned[standard]["bogomem.size"]), ("1048576", "0"))


if __name__ == "__main__":
    unittest.main()
