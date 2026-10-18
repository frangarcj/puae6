"""Synthetic timings test the comparison, never emulator speed or correctness."""
import unittest

from tests.simd.frontend_statistics import compare


def samples():
    runs = []
    for rep in range(5):
        for variant, factor, save, load in (("current", 10, 20, 10), ("candidate", 9, 21, 8)):
            cpu = factor * (rep + 1)
            runs.append({"variant": variant, "repeat": rep, "warmup": 1, "units": 3,
                         "raw": {"run_cpu_ns": [10**9, cpu, cpu, 1000], "run_wall_ns": [10**9, 2*cpu, 2*cpu, 2000],
                                 "state_save_ns": [save, save], "state_load_ns": [load, load]}})
    return runs


class StatisticsTests(unittest.TestCase):
    def test_pairs_use_repetition_ids_warmup_is_removed_and_outliers_are_retained(self):
        runs = samples()
        reordered = [r for r in runs if r["variant"] == "current"] + list(reversed([r for r in runs if r["variant"] == "candidate"]))
        result = compare(reordered)
        self.assertEqual(result["paired_wins"], 5)
        self.assertAlmostEqual(result["cpu_reduction_percent"], 10)
        for pair in result["paired_reductions_percent"]:
            self.assertAlmostEqual(pair, 10)
        before = result["variants"]["current"]
        self.assertEqual(before["cpu_medians_ns"], [10, 20, 30, 40, 50])
        self.assertEqual(before["median_cpu_ns"], 30)
        self.assertEqual((before["cpu"]["n"], before["cpu"]["max"], before["wall"]["max"]), (15, 1000, 2000))
        self.assertGreater(before["repetition_dispersion"]["stdev"], 0)
        self.assertAlmostEqual(result["state_p95_cost_percent"]["state_save_ns"], 5)
        self.assertAlmostEqual(result["state_p95_cost_percent"]["state_load_ns"], -20)

    def test_a_missing_or_duplicate_repetition_cannot_look_like_a_complete_comparison(self):
        runs = samples()
        with self.assertRaises(ValueError):
            compare(runs[:-1])
        runs[-1]["repeat"] = 3
        with self.assertRaises(ValueError):
            compare(runs)


if __name__ == "__main__":
    unittest.main()
