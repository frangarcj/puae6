"""Read the exact candidate's real Windows run; never start another campaign."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('P6_WINDOWS_GITHUB_RUN'), 'explicit completed Windows candidate run required')
class WindowsCandidateTests(unittest.TestCase):
    def test_current_application_guest_abi_restore_and_chd(self):
        run_id = os.environ['P6_WINDOWS_GITHUB_RUN']
        self.assertTrue(run_id.isdecimal())
        def gh(*args):
            return subprocess.check_output(['gh', *args, '--repo', 'frangarcj/BizHawk'], text=True, timeout=60)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT / 'build/machawk-puae6', text=True).strip()
        run = json.loads(gh('run', 'view', run_id, '--json', 'headSha,status,conclusion,jobs'))
        self.assertEqual((run['headSha'], run['status'], run['conclusion']), (commit, 'completed', 'success'))
        jobs = [j for j in run['jobs'] if j['conclusion'] != 'skipped']
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual((job['name'], job['conclusion']), ('puae6-windows', 'success'))
        step = next(s for s in job['steps'] if s['name'] == 'Execute Windows ABI, RomLoader, restore and CHD checks')
        self.assertEqual(step['conclusion'], 'success')
        log = gh('run', 'view', run_id, '--job', str(job['databaseId']), '--log')
        records = [json.loads(line.split('P6_EMUHAWK_WINDOWS ', 1)[1]) for line in log.splitlines() if 'P6_EMUHAWK_WINDOWS ' in line]
        self.assertEqual(len(records), 1)
        record = records[0]
        guest = ROOT / 'build/waterbox-x64/release/puae6.wbx'
        self.assertEqual(record['native']['puae6.wbx'], hashlib.sha256(guest.read_bytes()).hexdigest())
        for key, value in dict(abi_slots=15, callbacks=1, reentries=1, sandboxes=2, advanced_frames=10, baseline_frame=3, future_frame=6, restored_frames=3).items():
            self.assertEqual(record[key], value)
        self.assertNotEqual(record['baseline_sha256'], record['future_sha256'])
        self.assertIn('Ran 4 tests', log)
        print('Windows candidate:', commit, run_id, 'actual ABI, full restore and four native CHD checks PASS')


class WindowsGuiTests(WindowsCandidateTests):
    def test_real_windows_frontend_loads_puae6_and_closes_normally(self):
        log = subprocess.check_output(['gh', 'run', 'view', os.environ['P6_WINDOWS_GITHUB_RUN'],
            '--log', '--repo', 'frangarcj/BizHawk'], text=True, timeout=60)
        records = [json.loads(line.split('P6_EMUHAWK_GUI ', 1)[1]) for line in log.splitlines() if 'P6_EMUHAWK_GUI ' in line]
        self.assertEqual(len(records), 1)
        gui = records[0]
        self.assertEqual(set(gui), {'pid', 'title', 'core', 'exit_code'})
        self.assertIs(type(gui['pid']), int)
        self.assertGreater(gui['pid'], 0)
        self.assertEqual((gui['core'], gui['exit_code']), ('(Experimental) PUAE6', 0))
        self.assertIn('Own', gui['title'])
        self.assertIn('Amiga', gui['title'])
        print('Windows product GUI: own content, PUAE6 status and normal exit PASS')
