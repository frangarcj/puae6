"""Performance goals are configuration; historical benchmark archives are not test inputs."""
import json
import unittest
from tools import bench


class BudgetTests(unittest.TestCase):
    def test_goals_keep_the_existing_time_and_state_limits(self):
        budgets = json.loads(bench.BUDGETS.read_text())
        self.assertEqual(budgets["goals"]["p95_unit_fraction_max"], bench.GOAL_P95_FRACTION)
        self.assertEqual(budgets["goals"]["time_regression_investigate_above"], bench.GOAL_TIME_REGRESSION)
        self.assertEqual(budgets["goals"]["state_size_or_cost_regression_investigate_above"], bench.GOAL_STATE_REGRESSION)
        self.assertEqual(budgets["status"], "ADVISORY")
        self.assertGreater(len(budgets["entries"]), 10)
        for key, entry in budgets["entries"].items():
            self.assertEqual(key, f"{entry['runner']}/{entry['scenario']}")
            self.assertEqual(entry["goal"], {"p95_unit_fraction_max": 0.70})
            self.assertEqual(entry["enforcement"], "advisory")
            self.assertGreater(entry["unit_ms"], 10)


if __name__ == "__main__":
    unittest.main()
