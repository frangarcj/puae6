"""T35 experiment: exact conversion, naturally aligned tails and protected pages."""
from pathlib import Path
import platform
import tempfile
import unittest

from tests.simd import evaluate


class PixelTests(unittest.TestCase):
    def test_all_rgb_values_alignments_tails_and_protected_pages(self):
        with tempfile.TemporaryDirectory(prefix="p6-simd-") as directory:
            report = evaluate.evaluate(Path(directory), "san")
        self.assertEqual(1 << 24, report["verified_rgb_values"])
        self.assertEqual("neon" if platform.machine().lower() in ("arm64", "aarch64") else "scalar", report["backend"])
        self.assertEqual("scalar", report["forced_fallback"])
        if platform.system() == "Darwin":
            self.assertEqual("portable", report["product_backend"])

    def test_the_fallback_needs_no_neon_or_compiler_vectorization(self):
        with tempfile.TemporaryDirectory(prefix="p6-simd-") as directory:
            report = evaluate.evaluate(Path(directory), "fallback")
        self.assertEqual("scalar", report["backend"])
        self.assertEqual("portable", report["product_backend"])
        self.assertEqual(1 << 24, report["verified_rgb_values"])

    def test_the_comparison_harness_can_fail(self):
        with tempfile.TemporaryDirectory(prefix="p6-simd-") as directory:
            exe, _ = evaluate.build(Path(directory), "opt")
            result = evaluate.run(exe, "--selftest-fail")
        self.assertEqual(2, result.returncode)
        self.assertIn("RGB domain differs", result.stderr)


if __name__ == "__main__":
    unittest.main()
