"""tools/check.py for DET04 and DET06 (T25a): the portability matrix is a registered check, and a matrix that did not run never passes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check  # noqa: E402

MODULE = "tests.portability.test_portability"
COUNTS = dict(tests=3, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)


def completed(counts, code=0):
    return subprocess.CompletedProcess([], code, "noise\n" + json.dumps(counts), "")


class MatrixCheckRegistrationTests(unittest.TestCase):
    def test_det04_and_det06_are_registered_for_t25_on_the_portability_module(self):
        for check_id in ("DET04", "DET06"):
            suites = check.TASK_SUITES[(check_id, "T25")]
            self.assertEqual(suites["matrix_coherence"], MODULE + ".MatrixCoherenceTests")
            self.assertIn(MODULE + ".RowExecutionTests", suites.values(), check_id)
            self.assertGreaterEqual(check.TASK_SUITE_TIMEOUT[(check_id, "T25")], 3600, "the rows take hours when Docker rows run")

    def test_the_suites_name_real_test_classes(self):
        import importlib
        module = importlib.import_module(MODULE)
        for check_id in ("DET04", "DET06"):
            for name in check.TASK_SUITES[(check_id, "T25")].values():
                self.assertTrue(isinstance(getattr(module, name.rsplit(".", 1)[1]), type), name)

    def test_other_tasks_stay_not_implemented(self):
        for check_id in ("DET04", "DET06"):
            for task in ("T24", "T26"):
                self.assertEqual(check.dispatch(check_id, task=task)["status"], "NOT_IMPLEMENTED", (check_id, task))
        gap = check.dispatch("DET04", task="T25", required=("kickstart_boot",))
        self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))


class MatrixCheckVerdictTests(unittest.TestCase):
    def test_all_suites_passing_is_a_pass_with_every_assertion_executed(self):
        for check_id in ("DET04", "DET06"):
            with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                 mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
                result = check.dispatch(check_id, task="T25")
            self.assertEqual(result["status"], "PASS", check_id)
            self.assertEqual(result["assertions_executed"], sorted(check.TASK_SUITES[(check_id, "T25")]))
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(runner.call_args.args[2], check.TASK_SUITE_TIMEOUT[(check_id, "T25")])

    def test_rows_that_were_skipped_are_blocked_not_passed(self):
        skipped = completed(dict(COUNTS, skipped=1))
        for check_id in ("DET04", "DET06"):
            with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                 mock.patch.object(check, "_run_suite", side_effect=[completed(COUNTS), skipped]):
                result = check.dispatch(check_id, task="T25")
            self.assertEqual(result["status"], "BLOCKED", check_id)
            self.assertNotEqual(result["exit_code"], 0)
            self.assertEqual(result["assertions_executed"], ["matrix_coherence"])

    def test_a_failing_row_is_a_failure(self):
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", side_effect=[completed(COUNTS), completed(dict(COUNTS, failures=1), 1)]):
            result = check.dispatch("DET04", task="T25")
        self.assertEqual(result["status"], "FAIL")

    def test_without_the_opt_in_the_real_run_is_blocked_after_the_coherence_tests_passed(self):
        # Nothing is mocked: the coherence tests of the matrix really run, the rows are skipped (no Docker, no builds).
        environment = {k: v for k, v in os.environ.items() if k != "P6_TEST_PORTABILITY"}
        with mock.patch.dict(os.environ, environment, clear=True):
            result = check.dispatch("DET06", task="T25")
        self.assertEqual((result["status"], result["assertions_executed"]), ("BLOCKED", ["matrix_coherence"]), result["diagnostic"])
        self.assertIn("rows_under_another_host", result["diagnostic"])


if __name__ == "__main__":
    unittest.main()
