"""Dispatcher contract tests; mocked suites only prove HARNESS transport."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import check


class DispatcherTests(unittest.TestCase):
    def test_cli_unknown_and_unimplemented_checks_never_pass(self):
        for check_id, expected in [('NO_SUCH_CHECK', 'FAIL'), ('STEP01', 'NOT_IMPLEMENTED')]:
            result = subprocess.run([sys.executable, ROOT / 'tools/check.py', '--check', check_id],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['status'], expected)
            self.assertEqual(payload['exit_code'], result.returncode)

    def test_wrong_scope_and_missing_required_suite_are_not_implemented(self):
        for kwargs in ({'task': 'T05'}, {'profile': 'other-profile'},
                       {'required': ('future_assertion',)}):
            result = check.dispatch('HARNESS01', **kwargs)
            self.assertEqual(result['status'], 'NOT_IMPLEMENTED')
            self.assertNotEqual(result['exit_code'], 0)
        reduced = dict(check.SUITES)
        reduced.pop(check.REQUIRED_ASSERTIONS[0])
        with mock.patch.object(check, 'SUITES', reduced):
            result = check.dispatch('HARNESS01')
        self.assertEqual(result['status'], 'NOT_IMPLEMENTED')

    def test_scenario_validation_never_claims_execution(self):
        source = ROOT / 'tests/scenarios/counter68k.json'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scenario.json'
            valid = json.loads(source.read_text())
            path.write_text(json.dumps(valid))
            result = check.dispatch('HARNESS01', scenario=path)
            self.assertEqual(result['status'], 'NOT_IMPLEMENTED')
            self.assertFalse(result['scenario_executed'])
            for content in ('{"schema_version":', json.dumps(dict(valid, units=100001))):
                path.write_text(content)
                failed = check.dispatch('HARNESS01', scenario=path)
                self.assertEqual(failed['status'], 'FAIL')
                self.assertNotEqual(failed['exit_code'], 0)

    def test_missing_compiler_is_blocked(self):
        with mock.patch.object(check.shutil, 'which', return_value=None):
            result = check.dispatch('HARNESS01')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['evidence'], 'HARNESS')

    def test_mocked_suites_pass_as_harness_only(self):
        counts = dict(tests=1, failures=0, errors=0, skipped=0,
                      expected_failures=0, unexpected_successes=0)
        completed = subprocess.CompletedProcess([], 0, json.dumps(counts), '')
        with mock.patch.object(check.shutil, 'which', return_value='/usr/bin/clang'), \
             mock.patch.object(check, '_run_suite', return_value=completed) as runner:
            result = check.dispatch('HARNESS01')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['evidence'], 'HARNESS')
        self.assertFalse(result['scenario_executed'])
        self.assertIsNone(result['core_build_id'])
        self.assertEqual(result['assertions_executed'], list(check.REQUIRED_ASSERTIONS))
        self.assertEqual(runner.call_count, len(check.REQUIRED_ASSERTIONS))
        self.assertIn('no emulacion', result['diagnostic'])

    def test_suite_failure_truncation_empty_and_skips_cannot_pass(self):
        base = dict(tests=1, failures=0, errors=0, skipped=0,
                    expected_failures=0, unexpected_successes=0)
        cases = [(1, json.dumps(base), 'FAIL'), (0, '{', 'FAIL')]
        empty = dict(base, tests=0)
        skipped = dict(base, skipped=1)
        expected = dict(base, expected_failures=1)
        cases.extend([(0, json.dumps(empty), 'FAIL'),
                      (0, json.dumps(skipped), 'BLOCKED'),
                      (0, json.dumps(expected), 'BLOCKED')])
        for code, output, status in cases:
            completed = subprocess.CompletedProcess([], code, output, 'synthetic suite output')
            with self.subTest(status=status, output=output), \
                 mock.patch.object(check.shutil, 'which', return_value='/usr/bin/clang'), \
                 mock.patch.object(check, '_run_suite', return_value=completed):
                result = check.dispatch('HARNESS01')
            self.assertEqual(result['status'], status)
            self.assertNotEqual(result['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()
