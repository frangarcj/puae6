"""MED01, first part: identity, validation and registration of the media of p6_load.

The probe (media_probe.cpp) links the whole A500 engine and drives p6_load with disk images generated at run time
(nothing binary is committed): an image is accepted only if its content id is the SHA-256 of its bytes, it is a plain
901120-byte ADF and it is registered by identity; every other list is refused before the engine is touched.
Set P6_TEST_SANITIZERS=1 to also run it under ASan/UBSan.
"""
import os
from pathlib import Path
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe, run_probe


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class MediaLoadTests(unittest.TestCase):
    def test_media_are_known_by_identity_and_rejected_lists_cost_nothing(self):
        result = run_probe(build_probe(ROOT / "build/link-audit", False, "p6_media_probe"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("media load ok", result.stdout)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenario_is_clean_under_asan_and_ubsan(self):
        result = run_probe(build_probe(ROOT / "build/native-debug", True, "p6_media_probe"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("runtime error", result.stderr)


if __name__ == "__main__":
    unittest.main()
