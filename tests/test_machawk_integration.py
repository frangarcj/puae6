"""Directed check of the deployed fork, not another engine campaign."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('P6_TEST_MACHAWK_INTEGRATION') == '1',
                     'explicit built MacHawk integration required')
class MacHawkIntegrationTests(unittest.TestCase):
    def test_real_application_replay(self):
        from tools import emuhawk_selfcheck as check, emuhawk_coexistence as scenario
        tree = Path(os.environ.get('P6_MACHAWK_TREE', ROOT / 'build/machawk-puae6')).resolve()
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tree, text=True).strip()
        for folder in ('Puae6', 'EmuHawk'):
            for source in (ROOT / 'integration/bizhawk/core' / folder).glob('*.cs'):
                self.assertEqual(source.read_bytes(), (tree / check.builder.INSTALL_AT / source.name).read_bytes())
        output = tree / 'output'
        files = check.builder.output_closure(output)
        receipt = output / 'bootstrap-receipt.json'
        self.assertFalse(receipt.exists(), 'do not overwrite an existing build receipt')
        native = scenario.inputs()
        receipt.write_text(json.dumps({'tree_key': head, 'commit': head, 'files': files}, sort_keys=True) + '\n')
        try:
            with patch.object(check.builder, 'TREE_EMUHAWK', tree), \
                 patch.object(check.builder, 'pinned_commit', return_value=head), \
                 patch.object(check.builder, 'tree_key', return_value=head), \
                 patch.object(check.builder, 'check_source', return_value=head):
                result = check.run(coexistence=True)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['native'], {n: v['sha256'] for n, v in native.items()})
            self.assertEqual(len(result['cases']), 4)
            print('MacHawk integration:', head, result['status'],
                  'source-derived UAE', native['uae.wbx']['sha256'], 'BK2/TAS UAE+PUAE6, 12 frames each')
        finally:
            receipt.unlink()


if __name__ == '__main__':
    unittest.main()
