"""Directed check of the deployed fork, not another engine campaign."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def run_application():
    from tools import emuhawk_selfcheck as check, emuhawk_coexistence as scenario
    tree = Path(os.environ.get('P6_MACHAWK_TREE', ROOT / 'build/machawk-puae6')).resolve()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tree, text=True).strip()
    for folder in ('Puae6', 'EmuHawk'):
        for source in (ROOT / 'integration/bizhawk/core' / folder).glob('*.cs'):
            if source.read_bytes() != (tree / check.builder.INSTALL_AT / source.name).read_bytes():
                raise AssertionError('core source differs: ' + source.name)
    output = tree / 'output'
    files = check.builder.output_closure(output)
    receipt = output / 'bootstrap-receipt.json'
    if receipt.exists():
        raise AssertionError('do not overwrite an existing build receipt')
    native = scenario.inputs()
    receipt.write_text(json.dumps({'tree_key': head, 'commit': head, 'files': files}, sort_keys=True) + '\n')
    try:
        with patch.object(check.builder, 'TREE_EMUHAWK', tree), \
             patch.object(check.builder, 'pinned_commit', return_value=head), \
             patch.object(check.builder, 'tree_key', return_value=head), \
             patch.object(check.builder, 'check_source', return_value=head):
            result = check.run(coexistence=True)
        return result, native, head
    finally:
        receipt.unlink()


@unittest.skipUnless(os.environ.get('P6_TEST_MACHAWK_INTEGRATION') == '1',
                     'explicit built MacHawk integration required')
class MacHawkIntegrationTests(unittest.TestCase):
    def test_real_application_replay(self):
        result, native, head = run_application()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['native'], {n: v['sha256'] for n, v in native.items()})
        self.assertEqual(len(result['cases']), 4)
        print('MacHawk integration:', head, result['status'],
              'source-derived UAE', native['uae.wbx']['sha256'], 'BK2/TAS UAE+PUAE6, 12 frames each')


@unittest.skipUnless(os.environ.get('P6_TEST_MACHAWK_STATE_DIAGNOSTIC') == '1',
                     'explicit built MacHawk mismatch diagnostic required')
class MacHawkStateDiagnosticTests(unittest.TestCase):
    def test_failed_state_is_localized_by_the_actual_application(self):
        try:
            result, _, head = run_application()
        except ValueError as failure:
            self.assertTrue(str(failure).startswith('EmuHawk coexistence state mismatch: '), str(failure))
            observation = json.loads(str(failure).split(': ', 1)[1])
        else:
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(len(result['cases']), 4)
            print('MacHawk state localization:', head, 'no mismatch in the complete four-case path')
            return
        self.assertEqual((observation['format'], observation['core'], observation['frames']), ('tasproj', 'UAE', 12))
        self.assertFalse(observation['equal'])
        self.assertTrue(observation['ranges'])
        print('MacHawk state localization:', json.dumps(observation, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
