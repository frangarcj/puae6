"""T105b: validated chipset selectors through the generated libretro surface."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402


STATE_HASH = re.compile(r"^state save \S+ ok=1 size=\d+ used=\d+ hash=(\w+)", re.M)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class ChipsetOptionTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless

    def a600(self, *options, **keywords):
        return self.frontend("boot", *options, name=lr.KICKSTART_NAMES_A600[0], **keywords)

    def test_validated_overrides_reach_the_same_machine_as_headless(self):
        out = self.a600("--runs", 12,
                        "--set", "puae6_machine=A600",
                        "--set", "puae6_chipset_compatible=A500",
                        "--set", "puae6_chipset=ECS_Denise").stdout
        self.compare_with_headless(out, "boot", 12, "--machine", "a600",
                                   "--chipset-compatible", "a500", "--chipset", "ecs-denise")

    def test_auto_and_explicit_profile_values_have_the_same_state_identity(self):
        plain = self.a600("--runs", 4, "--set", "puae6_machine=A600", "--do", "4:save:plain").stdout
        named = self.a600("--runs", 4,
                          "--set", "puae6_machine=A600",
                          "--set", "puae6_chipset_compatible=A600",
                          "--set", "puae6_chipset=ECS",
                          "--do", "4:save:named").stdout
        self.assertEqual(STATE_HASH.search(plain)[1], STATE_HASH.search(named)[1])

    def test_incompatible_and_unknown_values_fail_before_the_first_unit(self):
        cases = (
            (("--set", "puae6_chipset=AGA"), "AGA requires a 68020 or later machine profile", lr.KICKSTART_NAMES[0]),
            (("--set", "puae6_machine=A1200", "--set", "puae6_chipset=OCS"), "1 MiB", lr.KICKSTART_NAMES_A1200[0]),
            (("--set", "puae6_chipset=ecs"), 'puae6_chipset has the value "ecs"', lr.KICKSTART_NAMES[0]),
        )
        for options, reason, firmware in cases:
            with self.subTest(options=options):
                out = self.frontend("boot", *options, name=firmware, expect=3).stdout
                self.assertIn(reason, out)
                self.assertNotIn("run 1 ", out)


if __name__ == "__main__":
    unittest.main()

