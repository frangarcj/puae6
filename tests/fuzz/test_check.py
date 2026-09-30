"""tools/check.py for SAFE01 and FUZZ01 (T26): suites are real, anything not covered is NOT_IMPLEMENTED."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check  # noqa: E402

COUNTS = dict(tests=3, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)


def completed(counts, code=0):
    return subprocess.CompletedProcess([], code, "noise\n" + json.dumps(counts), "")


class TaskSuiteDispatchTests(unittest.TestCase):
    def test_the_state_fuzz_target_is_not_implemented_until_t15(self):
        result = check.dispatch("FUZZ01", task="T26", required=("state",))
        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertIn("state", result["diagnostic"])
        self.assertEqual(result["assertions_executed"], [])
        self.assertNotEqual(result["exit_code"], 0)

    def test_other_tasks_and_profiles_are_not_covered(self):
        for kwargs in ({"task": "T27"}, {"task": "T26", "profile": "other"}):
            self.assertEqual(check.dispatch("SAFE01", **kwargs)["status"], "NOT_IMPLEMENTED")

    def test_all_suites_passing_is_reported_with_the_full_assertion_set(self):
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("SAFE01", task="T26")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions_executed"], sorted(check.TASK_SUITES[("SAFE01", "T26")]))
        self.assertEqual(result["assertion_set"], result["assertions_executed"])
        self.assertFalse(result["scenario_executed"])
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(runner.call_args.args[1], check.TASK_SUITE_ENV)   # the opt-in sanitizer runs are switched on

    def test_a_failing_skipping_empty_or_truncated_suite_cannot_pass(self):
        cases = [(completed(COUNTS, 1), "FAIL"), (completed(dict(COUNTS, failures=1)), "FAIL"),
                 (completed(dict(COUNTS, tests=0)), "FAIL"), (completed(dict(COUNTS, skipped=1)), "BLOCKED"),
                 (subprocess.CompletedProcess([], 0, "", ""), "FAIL")]
        for process, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                    mock.patch.object(check, "_run_suite", return_value=process):
                result = check.dispatch("FUZZ01", task="T26")
            self.assertEqual(result["status"], expected)
            self.assertNotEqual(result["exit_code"], 0)

    def test_missing_toolchain_is_blocked(self):
        with mock.patch.object(check.shutil, "which", return_value=None):
            self.assertEqual(check.dispatch("SAFE01", task="T26")["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
