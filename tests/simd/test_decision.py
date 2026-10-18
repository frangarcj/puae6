"""T35a: measurements and the evidence behind the platform-specific follow-up."""
import json
from pathlib import Path
import statistics
import tempfile
import unittest

from tests.portability.rows import ROWS
from tests.simd import evaluate, matrix

ROOT = evaluate.ROOT
EVIDENCE = ROOT / "reports/optimizations/T35a-rgb565-matrix.json"


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
    def test_the_evidence_is_complete_current_and_does_not_relax_the_profile(self):
        r = json.loads(EVIDENCE.read_text())
        self.assertEqual(set(ROWS), set(r["rows"]))
        self.assertEqual(r["images"], matrix.IMAGES)
        signatures = set()
        for row, entry in r["rows"].items():
            self.assertEqual(entry["native_hardware"], ROWS[row]["native_hardware"])
            self.assertEqual(set(entry["results"]), {"opt", "fallback", "san"})
            for mode, result in entry["results"].items():
                signatures.add(result["rgb_digest"])
                self.assertEqual(result["verified_rgb_values"], 1 << 24)
                self.assertEqual(result["forced_fallback"], "scalar")
                self.assertEqual(result["backend"], "neon" if ROWS[row]["isa"] == "arm64" and mode != "fallback" else "scalar")
                for path, sha in result["sources"].items():
                    self.assertEqual(evaluate.digest(ROOT / path), sha, path)
                self.assertFalse(set(result["flags"]) & {"-march=native", "-Ofast", "-ffast-math", "-ffp-contract=fast"})
        self.assertEqual(len(signatures), 1)

    def test_the_decision_preserves_both_the_regression_and_the_promising_rows(self):
        r = json.loads(EVIDENCE.read_text())["rows"]
        def median(row, field):
            return statistics.median(x[field] for x in r[row]["results"]["opt"]["samples"])
        self.assertLess(median("macos-arm64-appleclang", "current_ns"), median("macos-arm64-appleclang", "candidate_ns"))
        for row in ("linux-arm64-clang", "linux-arm64-gcc"):
            self.assertLess(median(row, "candidate_ns"), median(row, "current_ns"))
        tasks = {t["id"]: t for t in json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]}
        self.assertTrue({"T35a", "T35b", "T35c"} <= set(tasks["T35"]["depends"]))
        self.assertTrue(tasks["T35b"]["required"] and tasks["T35c"]["required"])
        adr = (ROOT / "docs/adr/0004-native-optimizations.md").read_text()
        self.assertIn("T35b", adr)
        self.assertIn("T35c", adr)


if __name__ == "__main__":
    unittest.main()
