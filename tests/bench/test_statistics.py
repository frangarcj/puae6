"""PERF01 (T33), the part that needs no emulator: the statistics, the way a baseline is summarized and the budgets that come out of it.

tools/bench.py measures; these tests pin what it does with the numbers, with data made up here (a run is only as good as the arithmetic after it):
percentiles by linear interpolation, the dispersion measures, the "noisy run" rule, the pooled p95 over repetitions, the summary of a baseline directory (and that it
notices when two layers of the same scenario ended on different master ticks), that raw files are byte-for-byte reproducible, and that the budget file keeps the goals
apart from the observations and never marks anything as a hard threshold.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402


def load(system_load=2.0):
    return {"load": [system_load, 3.0, 4.0], "free_mib": 1, "active_mib": 1, "swapouts": 0, "swap": "", "thermal": "", "busiest": []}


def record(scenario="a500-ocs-busy-pal", runner="native", layer="native", rep=1, wall=(4_000_000, 4_100_000, 4_200_000, 4_050_000), cpu=None, ticks=1000, index=50, units=None, attempts=1, **extra):
    wall = list(wall)
    cpu = list(cpu if cpu is not None else wall)
    return {"scenario": scenario, "runner": runner, "layer": layer, "rep": rep, "units": units or len(wall), "warmup": 10, "machine": "a500", "standard": "pal", "unit_wall_ns": wall,
            "unit_cpu_ns": cpu, "state_save_ns": [5_000_000, 5_100_000], "state_load_ns": [4_000_000, 4_200_000], "first_unit_after_load_ns": [4_100_000, 4_200_000],
            "peak_rss_bytes": 50_000_000, "cadence": [3546895, 70824], "master_ticks_at_window_end": ticks, "unit_index_at_window_end": index, "facts": {"state": {"bytes": 1000, "bound_bytes": 3000}},
            "load_before": load(), "load_after": load(), "attempts": attempts, **extra}


class StatisticsTests(unittest.TestCase):
    def test_percentiles_interpolate_between_the_closest_ranks(self):
        self.assertEqual(bench.percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(bench.percentile([1, 2, 3, 4], 0), 1.0)
        self.assertEqual(bench.percentile([1, 2, 3, 4], 100), 4.0)
        self.assertAlmostEqual(bench.percentile(list(range(101)), 95), 95.0)
        self.assertAlmostEqual(bench.percentile([0, 10], 95), 9.5)
        self.assertEqual(bench.percentile([7], 99), 7.0)
        with self.assertRaises(ValueError):
            bench.percentile([], 50)

    def test_describe_reports_the_spread_as_well_as_the_centre(self):
        stats = bench.describe([10, 10, 10, 10, 50])
        self.assertEqual((stats["n"], stats["min"], stats["p50"], stats["max"]), (5, 10.0, 10.0, 50.0))
        self.assertEqual(stats["mad"], 0.0)                       # the outlier moves the mean and the p95, not the median or the MAD
        self.assertGreater(stats["mean"], stats["p50"])
        self.assertGreater(stats["p95"], 30.0)
        calm = bench.describe([100, 101, 99, 100, 102, 98])
        self.assertLess(calm["cod"], 0.02)
        self.assertEqual(bench.describe([]), {"n": 0})

    def test_a_run_whose_thread_waited_is_noisy_and_one_that_did_not_is_not(self):
        quiet = bench.run_summary(record(wall=(4_000_000,) * 20, cpu=(3_990_000,) * 20))
        self.assertEqual(quiet["noisy"], [])
        waited = bench.run_summary(record(wall=(4_000_000,) * 20, cpu=(3_000_000,) * 20))
        self.assertEqual(len(waited["noisy"]), 1)
        self.assertIn("waited", waited["noisy"][0])

    def test_the_load_average_is_recorded_but_is_not_a_verdict_by_itself(self):
        busy = record(wall=(4_000_000,) * 20)
        busy["load_before"] = load(15.0)
        summary = bench.run_summary(busy)
        self.assertEqual(summary["noisy"], [])
        self.assertEqual(summary["load1_before"], 15.0)

    def test_the_pool_of_repetitions_gives_the_p95_and_the_runs_the_dispersion(self):
        runs = []
        for rep, base in enumerate((4_000_000, 4_040_000, 3_960_000), 1):
            summary = bench.run_summary(record(rep=rep, wall=[base + i * 1000 for i in range(100)]))
            summary["_samples"] = [base + i * 1000 for i in range(100)]
            runs.append(summary)
        stats = bench.aggregate(runs)
        self.assertEqual(stats["runs"], 3)
        self.assertEqual(stats["pooled"]["n"], 300)
        self.assertAlmostEqual(stats["median_of_run_medians_ns"], 4_049_500, delta=1)
        self.assertEqual(stats["run_median_range_ns"], [runs[2]["wall_ns"]["p50"], runs[1]["wall_ns"]["p50"]])
        self.assertGreater(stats["between_run_cv"], 0.0)
        self.assertLess(stats["between_run_cv"], 0.02)

    def test_the_unit_is_what_the_engine_says_it_is(self):
        self.assertAlmostEqual(bench.unit_ms_from_cadence([3546895, 70824]), 19.968, places=3)      # PAL
        self.assertAlmostEqual(bench.unit_ms_from_cadence([60, 1]), 1000.0 / 60.0)

    def test_raw_files_depend_on_the_data_only(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.json.gz", Path(directory) / "b.json.gz"
            data = record()
            bench.write_raw(a, data)
            bench.write_raw(b, data)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(bench.read_raw(a), data)


def make_baseline(directory, records):
    (directory / "raw").mkdir(parents=True)
    (directory / "environment.json").write_text(json.dumps({
        "measurement_status": "ORIENTATIVE: test", "git": {"commit": "0" * 40, "uncommitted_changes": False}, "tools": {"clang": "clang", "native_flags": {}},
        "host": {"cpu": "test", "performance_cores": 1, "efficiency_cores": 1, "memory_bytes": 2**30, "os": "x"}, "profile": "deterministic-a500",
        "engine": {"patches": 1, "series_sha256": "0" * 64}}))
    for index, item in enumerate(records):
        bench.write_raw(directory / "raw" / f"{index:02d}.json.gz", item)


class SummaryTests(unittest.TestCase):
    def summarize(self, records):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_baseline(root / "fake", records)
            with mock.patch.object(bench, "BASELINES", root):
                return bench.summarize("fake"), (root / "fake" / "summary.md").read_text()

    def test_every_layer_that_ends_on_the_same_tick_makes_the_baseline_comparable(self):
        summary, text = self.summarize([record(runner="native", rep=1), record(runner="native", rep=2),
                                        record(runner="libretro", layer="libretro", wall=(4_100_000, 4_150_000, 4_300_000, 4_120_000)),
                                        record(runner="waterbox-guest-release-runtime", layer="waterbox-guest", wall=(6_000_000, 6_100_000, 6_300_000, 6_050_000),
                                               parts={"unit_enter_ns": [1000] * 4, "unit_guest_ns": [4_300_000] * 4, "unit_copy_ns": [1_600_000] * 4, "unit_exit_ns": [1000] * 4},
                                               facts={"snapshot_bytes": 9_000_000, "pages_after_units": {}, "heaps_after_units": {}})])
        entry = summary["scenarios"]["a500-ocs-busy-pal"]
        self.assertTrue(entry["ticks_equal_across_runners"])
        self.assertEqual(sorted(entry["runners"]), ["libretro", "native", "waterbox-guest-release-runtime"])
        self.assertEqual(entry["runners"]["native"]["reps"], 2)
        self.assertEqual(entry["runners"]["native"]["layer"], "native")
        self.assertEqual(entry["runners"]["waterbox-guest-release-runtime"]["layer"], "waterbox-guest")        # native and Waterbox are separate rows
        self.assertAlmostEqual(entry["unit_ms"], 19.968, places=3)
        self.assertIn("Estado: **ORIENTATIVE**", text)

    def test_two_layers_that_disagree_about_the_machine_are_reported_as_not_equal(self):
        summary, _ = self.summarize([record(runner="native", ticks=1000), record(runner="libretro", layer="libretro", ticks=1001)])
        self.assertFalse(summary["scenarios"]["a500-ocs-busy-pal"]["ticks_equal_across_runners"])

    def test_the_margin_against_the_video_unit_and_the_goal_are_computed_not_assumed(self):
        fast = record(runner="native", wall=(3_000_000,) * 50)
        slow = record(runner="waterbox-core-debug", layer="waterbox-core", wall=(15_000_000,) * 50)
        summary, _ = self.summarize([fast, slow])
        runners = summary["scenarios"]["a500-ocs-busy-pal"]["runners"]
        self.assertAlmostEqual(runners["native"]["p95_unit_fraction"], 3.0 / 19.968, places=3)
        self.assertTrue(runners["native"]["meets_goal_p95_le_70_percent"])
        self.assertFalse(runners["waterbox-core-debug"]["meets_goal_p95_le_70_percent"])      # 15 ms of 19.97 is 75 %
        self.assertAlmostEqual(runners["native"]["p95_headroom_x"], 19.968 / 3.0, places=2)

    def test_the_main_cost_is_named_from_the_layers_differences(self):
        summary, _ = self.summarize([record(runner="native-norender", wall=(4_000_000,) * 20), record(runner="native", wall=(4_020_000,) * 20),
                                     record(runner="libretro", layer="libretro", wall=(4_050_000,) * 20)])
        row = summary["cost_breakdown"]["a500-ocs-busy-pal"]
        self.assertEqual(row["main_cost"], "engine")
        self.assertAlmostEqual(row["output_production_ms (native render 3 - render 0)"], 0.02, places=4)
        self.assertAlmostEqual(row["libretro_adapter_ms (libretro - native)"], 0.03, places=4)


class BudgetTests(unittest.TestCase):
    def test_goals_stay_goals_and_nothing_is_a_threshold(self):
        records = [record(runner="native", rep=r, wall=(3_000_000 + r * 1000,) * 50) for r in range(1, 6)] + [record(runner="libretro", layer="libretro", wall=(3_100_000,) * 50)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_baseline(root / "fake", records)
            with mock.patch.object(bench, "BASELINES", root), mock.patch.object(bench, "BUDGETS", root / "budgets.json"):
                bench.summarize("fake")
                document = bench.write_budgets("fake")
                on_disk = json.loads((root / "budgets.json").read_text())
        self.assertEqual(document, on_disk)
        self.assertEqual(document["status"], "ORIENTATIVE")
        self.assertEqual(document["goals"]["p95_unit_fraction_max"], 0.70)
        self.assertEqual(document["goals"]["time_regression_investigate_above"], 0.05)
        self.assertEqual(document["goals"]["state_size_or_cost_regression_investigate_above"], 0.10)
        self.assertEqual({e["enforcement"] for e in document["entries"].values()}, {"advisory"})
        for entry in document["entries"].values():
            self.assertNotIn("p95_unit_fraction_max", entry["observed"])              # what was measured never carries the goal's name or value
            self.assertEqual(entry["goal"], {"p95_unit_fraction_max": 0.70})
            self.assertNotIn("native-norender", entry["runner"])
        self.assertTrue(document["entries"]["native/a500-ocs-busy-pal"]["stable"])      # five calm repetitions
        self.assertFalse(document["entries"]["libretro/a500-ocs-busy-pal"]["stable"])    # one repetition is not a baseline


if __name__ == "__main__":
    unittest.main()
