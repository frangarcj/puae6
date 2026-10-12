"""PERF01 (T33), the committed baseline: reports/baselines/ID and plan/performance-budgets.json.

The baseline is data (raw samples of every run, the conditions each ran under, the versions) plus a summary computed from it; the budgets file points at the summary. These tests
read what is in the repository and assert that it hangs together, not that it is fast:

  * the summary is a function of the raw files (summarizing them again gives the committed summary), so nobody edited a number;
  * every scenario compares layers that really are the same machine (they ended the timed window on the same master tick) and lists native and Waterbox as separate rows;
  * every row has its margin against the video unit and every scenario names its main cost;
  * the budgets file repeats the summary's numbers under `observed`, keeps the goals of docs/04-validation.md apart under `goal`, marks everything advisory unless the row was
    stable (five calm repetitions), and says it is ORIENTATIVE while the baseline was taken on a machine that someone was using;
  * docs/04-validation.md no longer says that nothing was measured.

    python3 -m unittest tests.bench.test_budgets
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402


class CommittedBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.budgets = json.loads(bench.BUDGETS.read_text())
        cls.directory = ROOT / cls.budgets["baseline"]
        cls.summary = json.loads((cls.directory / "summary.json").read_text())

    def test_the_summary_is_computed_from_the_raw_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / self.directory.name
            shutil.copytree(self.directory, copy)
            (copy / "summary.json").unlink()
            (copy / "summary.md").unlink()
            with mock.patch.object(bench, "BASELINES", Path(scratch)):
                again = bench.summarize(self.directory.name)
            self.assertEqual(again, self.summary)
            self.assertEqual((copy / "summary.md").read_text(), (self.directory / "summary.md").read_text())

    def test_every_run_kept_its_raw_samples_and_the_conditions_it_ran_under(self):
        raw = sorted((self.directory / "raw").glob("*.json.gz"))
        self.assertGreater(len(raw), 0)
        expected = sum(runner["reps"] for entry in self.summary["scenarios"].values() for runner in entry["runners"].values())
        self.assertEqual(len(raw), expected)
        first = bench.read_raw(raw[0])
        for key in ("unit_wall_ns", "unit_cpu_ns", "load_before", "load_after", "units", "warmup", "peak_rss_bytes", "attempts"):
            self.assertIn(key, first)
        self.assertEqual(len(first["unit_wall_ns"]), first["units"])
        for key in ("load", "free_mib", "swap", "thermal", "busiest"):
            self.assertIn(key, first["load_before"])
        environment = json.loads((self.directory / "environment.json").read_text())
        for key in ("git", "host", "tools", "engine", "artifacts", "condition_when_started", "measurement_status"):
            self.assertIn(key, environment)
        self.assertEqual(environment["host"]["host_page_bytes"], 16384)
        self.assertEqual(environment["tools"]["native_flags"].get("CMAKE_CXX_FLAGS_RELEASE"), "-O2", "the native tree is built with the optimization of the Waterbox guest")

    def test_the_layers_compared_are_the_same_machine_and_native_and_waterbox_are_separate(self):
        self.assertEqual(self.summary["status"], "ORIENTATIVE")
        waterbox_scenarios = 0
        for scenario_id, entry in self.summary["scenarios"].items():
            self.assertTrue(entry["ticks_equal_across_runners"], f"{scenario_id}: the runners ended the window on different master ticks")
            layers = {runner["layer"] for runner in entry["runners"].values()}
            self.assertIn("native", layers, scenario_id)
            self.assertIn("libretro", layers, scenario_id)
            if scenario_id in bench.WATERBOX_SCENARIOS and any(l.startswith("waterbox") for l in layers):
                waterbox_scenarios += 1
                self.assertLessEqual({"native", "libretro", "waterbox-guest", "waterbox-core"}, layers, scenario_id)
        self.assertGreaterEqual(waterbox_scenarios, 3)
        self.assertIn("a500-ocs-busy-pal", self.summary["scenarios"])

    def test_every_row_has_its_margin_and_every_scenario_its_main_cost(self):
        for scenario_id, entry in self.summary["scenarios"].items():
            self.assertGreater(entry["unit_ms"], 10.0, scenario_id)
            for runner_id, runner in entry["runners"].items():
                self.assertGreaterEqual(runner["reps"], 3, f"{scenario_id}/{runner_id}")
                self.assertAlmostEqual(runner["p95_unit_fraction"], runner["p95_ms"] / entry["unit_ms"], places=9)
                self.assertAlmostEqual(runner["p95_headroom_x"] * runner["p95_unit_fraction"], 1.0, places=9)
                self.assertEqual(runner["meets_goal_p95_le_70_percent"], runner["p95_ms"] <= 0.70 * entry["unit_ms"])
            self.assertIn(self.summary["cost_breakdown"][scenario_id]["main_cost"], {"engine", "output production", "libretro adapter"})

    def test_the_budgets_repeat_the_observations_and_keep_the_goals_apart(self):
        self.assertEqual(self.budgets["status"], "ORIENTATIVE")
        self.assertEqual(self.budgets["goals"]["p95_unit_fraction_max"], bench.GOAL_P95_FRACTION)
        self.assertEqual(self.budgets["goals"]["time_regression_investigate_above"], bench.GOAL_TIME_REGRESSION)
        self.assertEqual(self.budgets["goals"]["state_size_or_cost_regression_investigate_above"], bench.GOAL_STATE_REGRESSION)
        self.assertGreater(len(self.budgets["entries"]), 10)
        for key, entry in self.budgets["entries"].items():
            runner = self.summary["scenarios"][entry["scenario"]]["runners"][entry["runner"]]
            self.assertEqual(key, f"{entry['runner']}/{entry['scenario']}")
            self.assertEqual(entry["observed"]["p95_ms"], round(runner["p95_ms"], 3), key)
            self.assertEqual(entry["observed"]["p50_ms"], round(runner["p50_ms"], 3), key)
            self.assertEqual(entry["observed"]["reps"], runner["reps"], key)
            self.assertEqual(entry["goal"], {"p95_unit_fraction_max": 0.70}, key)
            self.assertNotIn("p95_unit_fraction_max", entry["observed"], key)
            self.assertEqual(entry["goal_met_by_observation"], runner["meets_goal_p95_le_70_percent"], key)
            self.assertEqual(entry["enforcement"], "advisory", key)
            stable = entry["observed"]["between_run_cv"] <= 0.02 and entry["observed"]["noisy_runs"] == 0 and entry["observed"]["reps"] >= 5
            self.assertEqual(entry["stable"], stable, key)

    def test_the_documentation_points_at_the_baseline_and_no_longer_says_nothing_was_measured(self):
        text = (ROOT / "docs/04-validation.md").read_text()
        self.assertNotIn("No hay benchmarks observados aun", text)
        self.assertIn(self.budgets["baseline"], text)
        self.assertIn("ORIENTATIVA", text)


if __name__ == "__main__":
    unittest.main()
