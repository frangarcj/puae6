"""Own Git/Python fixtures and metadata mocks; no real Actions, builds or emulation."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from tools.ci import recover_matrix as r


class MatrixRecoveryTests(unittest.TestCase):
    def setUp(self):
        (r.ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='matrix-recovery-', dir=r.ROOT / 'build'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.source = self.root / 'source'; self.source.mkdir()
        self.tooling = self.root / 'tooling'; (self.tooling / 'tools/ci').mkdir(parents=True)
        self.workflow = dict(id=7, path=r.WORKFLOW)
        self.data = dict(id=123, run_attempt=1, workflow_id=7, path=r.WORKFLOW, head_sha='a'*40,
                         repository={'full_name': 'own/repo'}, head_repository={'full_name': 'own/repo'},
                         event='workflow_dispatch', status='completed', conclusion='failure')

    def test_descriptor_comes_from_completed_original_attempt_and_rejects_foreign_context(self):
        for fault in ('none', 'repository', 'fork', 'workflow', 'path', 'run', 'attempt', 'event', 'status', 'sha', 'bool'):
            data = copy.deepcopy(self.data)
            if fault == 'repository': data['repository']['full_name'] = 'other/repo'
            if fault == 'fork': data['head_repository']['full_name'] = 'other/repo'
            for name, key, value in [('workflow','workflow_id',8), ('path','path','other.yml'), ('run','id',124),
                                     ('attempt','run_attempt',2), ('event','event','push'), ('status','status','in_progress'),
                                     ('sha','head_sha','../bad'), ('bool','run_attempt',True)]:
                if fault == name: data[key] = value
            with self.subTest(fault=fault), mock.patch.object(r, 'api', side_effect=[self.workflow, data]) as api:
                if fault == 'none': self.assertEqual(r.inspect_run('own/repo','123','1')['revision'], 'a'*40)
                else:
                    with self.assertRaises(ValueError): r.inspect_run('own/repo','123','1')
                self.assertIn('/attempts/1', api.call_args.args[0])
        for run in ('0', '01', '../123', '123\n', '1;touch bad'):
            with mock.patch.object(r,'api') as api, self.assertRaises(ValueError): r.inspect_run('own/repo',run,'1')
            api.assert_not_called()

    def test_bridge_intercepts_only_exact_whitelisted_log_endpoint_and_retains_protocol_guard(self):
        original = mock.Mock(return_value='own original'); reader = types.SimpleNamespace(read_job_log=mock.Mock(return_value='own log'))
        execute = r.log_bridge(original, reader, 'own/repo', {7})
        self.assertEqual(execute(['gh','api','repos/own/repo/actions/jobs/7/logs']), 'own log')
        for argv in (['git','status'], ['gh','api','repos/other/repo/actions/jobs/7/logs'],
                     ['gh','api','repos/own/repo/actions/runs/123/attempts/1/jobs?per_page=100'],
                     ['gh','api','repos/own/repo/actions/jobs/7/logs','--include']):
            self.assertEqual(execute(argv, timeout=2), 'own original'); original.assert_called_with(argv, timeout=2)
        with self.assertRaises(ValueError): execute(['gh','api','repos/own/repo/actions/jobs/8/logs'])
        reader.read_job_log.return_value = 'own VIOLATION'
        with self.assertRaisesRegex(ValueError, 'protocol'): execute(['gh','api','repos/own/repo/actions/jobs/7/logs'])

    def prepare_source(self):
        files = {'tests/__init__.py':'', 'tests/own.py':'VALUE="own source"\n',
                 'tools/ci/native.py': '''import json
def execute(argv, *args, **kwargs):
    if '/attempts/1/jobs?' in argv[-1]: return json.dumps(dict(total_count=1,jobs=[dict(id=7)]))
    if argv[-1].endswith('/logs'): raise RuntimeError('own original broken log transport')
    return 'own original command'
''', 'tools/ci/hosted_matrix.py': '''import os
from tools.ci import native
from tests import own
def aggregate():
    assert native.execute(['gh','api','repos/own/repo/actions/jobs/7/logs']) == 'own repaired log'
    assert native.execute(['git','status']) == 'own original command'
    return dict(status='PASS',fixture=own.VALUE,attempt=os.environ['GITHUB_RUN_ATTEMPT'])
'''}
        for name, data in files.items():
            path=self.source/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(data)
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','own sources'],check=True)
        return r.git(self.source,'rev-parse','HEAD')

    def test_child_isolated_imports_original_oracles_and_does_not_change_source_or_parent_context(self):
        sha=self.prepare_source(); helper=self.tooling/'tools/ci/recover_matrix.py'; shutil.copyfile(r.__file__,helper)
        (self.tooling/'tools/__init__.py').write_text('raise RuntimeError("tooling project imported")\n')
        (self.tooling/'tools/ci/action_logs.py').write_text('def read_job_log(repository,job): return "own repaired log"\n')
        descriptor=dict(repository='own/repo',run_id='123',run_attempt='1',revision=sha,workflow_conclusion='failure')
        env={**os.environ,'GITHUB_REPOSITORY':'own/repo','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':sha,'PYTHONDONTWRITEBYTECODE':'1'}
        before=dict(os.environ)
        process=subprocess.run([sys.executable,'-I','-B',str(helper),'_aggregate','--source',str(self.source)],
                               input=json.dumps(dict(source=descriptor,recovery={'run_id':'999'})),env=env,text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        result=json.loads(process.stdout.split(r.MARKER)[-1]); self.assertEqual(result['kind'],'directed-log-transport-recovery')
        self.assertEqual(result['source']['workflow_conclusion'],'failure'); self.assertEqual(result['recovery']['run_id'],'999')
        self.assertEqual(result['original_aggregate']['fixture'],'own source'); self.assertEqual(dict(os.environ),before)
        self.assertFalse(r.git(self.source,'status','--porcelain'))
        env['GITHUB_RUN_ATTEMPT']='2'
        process=subprocess.run([sys.executable,'-I','-B',str(helper),'_aggregate','--source',str(self.source)],
                               input=json.dumps(dict(source=descriptor,recovery={})),env=env,text=True,capture_output=True)
        self.assertEqual(process.returncode,1); self.assertIn('context differs',process.stdout)

    def test_source_revision_dirty_tree_context_and_mixed_modules_are_rejected(self):
        sha=self.prepare_source()
        with mock.patch.object(r,'ROOT',self.tooling):
            self.assertEqual(r.source_checkout(self.source,sha),self.source)
            with self.assertRaisesRegex(ValueError,'revision'): r.source_checkout(self.source,'a'*40)
            (self.source/'unexpected').write_text('own dirty file')
            with self.assertRaisesRegex(ValueError,'dirty'): r.source_checkout(self.source,sha)
            (self.source/'unexpected').unlink()
            with self.assertRaisesRegex(ValueError,'already imported'): r.aggregate_original(self.source,dict(revision=sha),{})
        with self.assertRaisesRegex(ValueError,'mixed'): r.project_isolation(self.source)

    def test_recover_passes_api_descriptor_to_isolated_child_only_and_preserves_parent_environment(self):
        descriptor=dict(repository='own/repo',run_id='123',run_attempt='1',revision='a'*40)
        before=dict(os.environ)
        with mock.patch.dict(os.environ,{'GITHUB_SHA':'b'*40}), mock.patch.object(r,'source_checkout',return_value=self.source), \
             mock.patch.object(r,'git',side_effect=['b'*40,'']), \
             mock.patch.object(r.subprocess,'run',return_value=types.SimpleNamespace(returncode=1)) as run:
            self.assertEqual(r.recover(self.source,descriptor),1)
        self.assertIn('-I',run.call_args.args[0]); self.assertEqual(run.call_args.kwargs['env']['GITHUB_SHA'],'a'*40)
        self.assertNotIn('GITHUB_TOKEN',run.call_args.kwargs['env']); self.assertEqual(dict(os.environ),before)

    def test_workflow_recovery_derives_original_sha_and_never_selects_build_jobs(self):
        workflow=(r.ROOT/'.github/workflows/native-linux.yml').read_text()
        native=workflow.split('  matrix-infrastructure:',1)[0]
        self.assertIn("if: inputs.gate == 'native' || inputs.gate == 'corpus'",native)
        recovery=workflow.split('  matrix-recovery:',1)[1]
        self.assertIn("if: inputs.gate == 'matrix-recover'",recovery); self.assertIn('actions: read',recovery)
        self.assertEqual(recovery.count('persist-credentials: false'),2)
        self.assertIn('ref: ${{ github.sha }}',recovery); self.assertIn('ref: ${{ steps.source.outputs.source_sha }}',recovery)
        self.assertIn('inspect --source-run "$SOURCE_RUN" --source-attempt "$SOURCE_ATTEMPT"',recovery)
        self.assertNotRegex(recovery,r'hosted_matrix.py cell|cmake|nightly.py|build_waterbox|--fetch|upload-artifact')
