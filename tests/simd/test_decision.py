"""Live pixel measurement and the required platform-specific follow-up."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.simd import evaluate

ROOT = evaluate.ROOT


class PerformanceTests(unittest.TestCase):
    def test_the_actual_native_compiler_measures_verified_rows(self):
        with tempfile.TemporaryDirectory(prefix="p6-simd-perf-") as directory:
            r = evaluate.evaluate(Path(directory), "opt", "macos-arm64-appleclang")
        self.assertEqual(r["verified_rgb_values"], 1 << 24)
        self.assertEqual(r["row_pixels"], 756)
        self.assertTrue(r["row_copy_included"])
        self.assertEqual((r["warmup"], r["iterations_per_sample"], r["repetitions"]), (300, 300, 5))
        self.assertEqual(len(r["samples"]), 5)
        self.assertTrue(all(s["current_ns"] > 0 and s["candidate_ns"] > 0 for s in r["samples"]))


class DecisionTests(unittest.TestCase):

    def test_the_followup_requires_both_the_engine_flags_and_the_real_frontend(self):
        tasks = {t["id"]: t for t in json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]}
        self.assertTrue({"T35a", "T35b", "T35c"} <= set(tasks["T35"]["depends"]))
        self.assertTrue(tasks["T35b"]["required"] and tasks["T35c"]["required"])
        adr = (ROOT / "docs/adr/0004-native-optimizations.md").read_text()
        self.assertIn("T35b", adr)
        self.assertIn("T35c", adr)


if __name__ == "__main__":
    unittest.main()
