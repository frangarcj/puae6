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
    def test_release_has_all_required_gates_and_real_overlay_movie_suites(self):
        task = next(t for t in json.loads((ROOT / 'plan/tasks.json').read_text())['tasks'] if t['id'] == 'T41')
        for gate in task['checks']:
            self.assertTrue(check.TASK_SUITES.get((gate, 'T41')), gate)
            for selector in check.TASK_SUITES[(gate, 'T41')].values():
                parts = selector.split('.')
                while parts and not (ROOT.joinpath(*parts).with_suffix('.py')).is_file():
                    parts.pop()
                self.assertTrue(parts, ('missing release suite module', selector))
        self.assertEqual(set(check.TASK_SUITES[('REF01', 'T32')].values()),
                         set(check.TASK_SUITES[('REF01', 'T41')].values()))
        self.assertEqual(set(check.TASK_SUITES[('LR02', 'T60')].values()),
                         set(check.TASK_SUITES[('LR02', 'T41')].values()))
        self.assertIn('tests.waterbox-media.test_media_overlay', check.TASK_SUITES[('WB10', 'T41')].values())
        self.assertIn('tests.waterbox-media.test_media_hdf', check.TASK_SUITES[('WB10', 'T41')].values())
        self.assertIn('tests.bizhawk-movies.test_media_start', check.TASK_SUITES[('WB09', 'T41')].values())

    def test_cd_parents_keep_all_child_selectors_without_duplicate_execution(self):
        gates = {'T66': {'MED01': ('MED01', 'MED02'),
                         'DET02': ('DET02', 'STATE01', 'LR01', 'WB03'),
                         'LR01': ('LR01',), 'WB03': ('WB03',)},
                 'T67': {'MED01': ('MED01',),
                         'DET02': ('DET02', 'STATE01', 'WB03'), 'LR01': ('LR01',)}}
        for parent, mapped in gates.items():
            for gate, child_gates in mapped.items():
                selected = tuple(check.TASK_SUITES[(gate, parent)].values())
                self.assertTrue(selected, (parent, gate))
                self.assertEqual(len(selected), len(set(selected)))
                for (child_gate, child), suites in check.TASK_SUITES.items():
                    if child == parent or not child.startswith(parent) or child.startswith('T66x'):
                        continue
                    if child_gate in child_gates:
                        for selector in suites.values():
                            self.assertTrue(any(selector == s or selector.startswith(s + '.')
                                                for s in selected), (parent, gate, child, selector))
                for selector in selected:
                    self.assertFalse(any(selector.startswith(s + '.') for s in selected if s != selector))

    def test_cd_parent_keeps_real_game_audio_cold_restore_and_deferred_rejections(self):
        expected = {
            ('LR01', 'T66'): ('tests.libretro-host.test_cd32_load.Cd32GameTests.test_brian_gameplay_and_restore',),
            ('MED01', 'T66'): ('tests.media.test_cd_nrg.CdNrgTests', 'tests.media.test_nrg_metadata.NrgMetadataTests'),
            ('AV02', 'T66'): ('tests.audio.test_cd_audio', 'tests.media.test_cd_device_audio',
                             'tests.media.test_akiko_state', 'tests.waterbox-media.test_cd_source'),
            ('DET02', 'T67'): ('tests.waterbox-media.test_cdtv_boot.CdtvPublicWaterboxTests',),
            ('LR01', 'T67'): ('tests.libretro-host.test_cdtv_load.CdtvLoadTests',),
        }
        for key, selectors in expected.items():
            selected = tuple(check.TASK_SUITES[key].values())
            for selector in selectors:
                self.assertTrue(any(selector == s or selector.startswith(s + '.') for s in selected), (key, selector))

    def test_cd_extras_do_not_silently_enter_the_first_release_gate(self):
        with mock.patch.dict(check.TASK_SUITES, {('MED01', 'T66x1'): {'future': 'tests.future_tinf'},
                                                ('MED01', 'T66x2'): {'future': 'tests.future_etn2'}}):
            selected = check._cd_parent_suites('T66', ('MED01',))
        self.assertNotIn('tests.future_tinf', selected.values())
        self.assertNotIn('tests.future_etn2', selected.values())

    def test_cd32_parent_keeps_pad_and_movie_swap_from_other_gate_names(self):
        for gate, child_gate, child in (('MED01', 'IN01', 'T70a'),
                                        ('DET02', 'DET02', 'T70a'),
                                        ('LR01', 'LR01', 'T70a'),
                                        ('WB03', 'WB03', 'T70a'),
                                        ('WB03', 'WB09', 'T66u6'),
                                        ('DET02', 'WB09', 'T66u6')):
            selected = check.TASK_SUITES[(gate, 'T66')].values()
            for selector in check.TASK_SUITES[(child_gate, child)].values():
                self.assertTrue(any(selector == s or selector.startswith(s + '.') for s in selected), (gate, selector))

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
