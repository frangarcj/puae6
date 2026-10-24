"""Own summaries and mocked current-attempt API; no compilation or measurement."""
import copy
import json
import re
import subprocess
import sys
import unittest
from unittest import mock

from tools.ci import hosted_performance as hosted


class HostedPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.common = dict(repository='own/repo', run_id='123', run_attempt='2', revision='a'*40, source_tree_sha256='b'*64)
        self.checks = {kind:mock.Mock() for kind in ('bench','engine','frontend')}
        self.coverage = dict(verified_rows=5, measured_rows=3, measured_windows=150, original_methods=2, unittest_tests=0)
        self.checks['frontend'].validate_rows.return_value = self.coverage

    def records(self):
        return [dict(status='PASS', profile=hosted.PROFILE, common=copy.deepcopy(self.common), kind=kinds[0],
                     **({'row':job.removeprefix('performance / ')} if kinds[0] == 'frontend' else {})) for job,kinds in hosted.JOBS.items()]

    def test_exact_original_partition_and_distinct_preflight_marker(self):
        self.assertEqual(len(hosted.partition()), 12); self.assertEqual(sum(hosted.SELECTORS.values()), 43)
        with mock.patch.object(hosted.nightly, 'suites', return_value=list(hosted.SELECTORS)[:-1]), self.assertRaises(RuntimeError): hosted.partition()
        with mock.patch.dict(hosted.SELECTORS, {'tests.bench.test_budgets':2}), self.assertRaises(RuntimeError): hosted.partition()
        process = subprocess.run([sys.executable,'-B',hosted.__file__,'partition'],cwd=hosted.ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertTrue(process.stdout.startswith('P6_PERFORMANCE_PARTITION ')); self.assertNotIn(hosted.MARKER,process.stdout)

    def test_complete_recipe_invokes_all_original_oracles_and_distinguishes_distributed_checks(self):
        records = self.records()
        with mock.patch.object(hosted,'validators',return_value=self.checks): result = hosted.validate(records,self.common)
        self.assertEqual(result['coverage']['unittest_tests'],41)
        self.assertEqual(result['coverage']['original_methods'],43)
        self.assertEqual(result['coverage']['frontend'],self.coverage)
        self.checks['bench'].validate.assert_called_once_with(records[0],self.common)
        self.checks['engine'].validate.assert_called_once_with(records[1],self.common)
        self.checks['frontend'].validate_rows.assert_called_once_with(records[2:],self.common)
        for kind,method in (('bench','validate'),('engine','validate'),('frontend','validate_rows')):
            with self.subTest(kind=kind), mock.patch.object(self.checks[kind],method,side_effect=RuntimeError('own oracle failure')), \
                 mock.patch.object(hosted,'validators',return_value=self.checks), self.assertRaisesRegex(RuntimeError,'oracle failure'):
                hosted.validate(records,self.common)
        with mock.patch.object(self.checks['frontend'],'validate_rows',return_value={}), \
             mock.patch.object(hosted,'validators',return_value=self.checks), self.assertRaisesRegex(RuntimeError,'frontend'):
            hosted.validate(records,self.common)

    def test_every_worker_and_same_source_run_attempt_are_mandatory(self):
        for fault in ('missing','duplicate','kind','row','profile','failure','revision','source','attempt'):
            records = self.records()
            if fault == 'missing': records.pop()
            if fault == 'duplicate': records[-1] = copy.deepcopy(records[0])
            if fault == 'kind': records[0]['kind'] = 'historical'
            if fault == 'row': records[-1]['row'] = 'undeclared'
            if fault == 'profile': records[0]['profile'] = 'local-docker'
            if fault == 'failure': records[0]['status'] = 'FAIL'
            if fault == 'revision': records[0]['common']['revision'] = 'f'*40
            if fault == 'source': records[0]['common']['source_tree_sha256'] = 'f'*64
            if fault == 'attempt': records[0]['common']['run_attempt'] = '1'
            with self.subTest(fault=fault), mock.patch.object(hosted,'validators') as checks, self.assertRaises(RuntimeError):
                hosted.validate(records,self.common)
            checks.assert_not_called()

    def test_shared_transport_uses_performance_marker_job_binding_and_exact_current_attempt(self):
        records = self.records(); jobs = [dict(id=i+1,name=n,status='completed',conclusion='success') for i,n in enumerate(hosted.JOBS)]
        def execute(argv):
            self.assertEqual(argv,['gh','api','repos/own/repo/actions/runs/123/attempts/2/jobs?per_page=100'])
            return json.dumps(dict(total_count=len(jobs),jobs=jobs))
        for fault in ('none','wrong-marker','duplicate','wrong-job','violation','failed','drift'):
            jobs[0]['conclusion'] = 'failure' if fault == 'failed' else 'success'
            def log(repo,job_id):
                self.assertEqual(repo,'own/repo'); record = records[job_id-1]
                if fault == 'wrong-job' and job_id == 1: record = records[1]
                marker = hosted.shared.MARKER if fault == 'wrong-marker' else hosted.MARKER
                line = '2026-10-24T00:00:00Z '+marker+json.dumps(record)+'\n'
                return line*2 if fault == 'duplicate' else line+'VIOLATION own\n' if fault == 'violation' else line
            with self.subTest(fault=fault), mock.patch.object(hosted.shared.matrix,'common',side_effect=[self.common,{} if fault == 'drift' else self.common]), \
                 mock.patch.object(hosted.shared.matrix.native,'execute',side_effect=execute), mock.patch.object(hosted.shared,'read_job_log',side_effect=log), \
                 mock.patch.object(hosted,'validators',return_value=self.checks):
                if fault == 'none': self.assertEqual(hosted.aggregate()['status'],'PASS')
                else:
                    with self.assertRaises(RuntimeError): hosted.aggregate()

    def test_workflow_runs_all_protocols_with_pinned_public_inputs_and_four_job_bound(self):
        workflow = (hosted.ROOT/'.github/workflows/native-linux.yml').read_text()
        bench, rest = workflow.split('  performance-bench:',1)[1].split('  performance-engine:',1)
        engine, rest = rest.split('  performance-worker:',1)
        workers, result = rest.split('  performance-result:',1)
        for job in (bench,engine):
            self.assertIn('runs-on: macos-26',job); self.assertIn('timeout-minutes: 360',job)
            self.assertIn("test '${{ inputs.variants }}' = all",job)
            self.assertIn('hosted_performance.py partition',job)
        self.assertIn('uses: ./.github/actions/prepare-performance-waterbox',bench)
        self.assertIn('tools.ci.performance_bench',bench); self.assertIn('tools.ci.performance_engine',engine)
        self.assertIn('needs: [performance-bench, performance-engine]',workers)
        self.assertIn('max-parallel: 4',workers); self.assertIn('fail-fast: false',workers)
        expected = dict(hosted.shared.matrix.native.HOSTED_RUNNERS, **{'macos-arm64-appleclang':'macos-26'})
        self.assertEqual(dict(re.findall(r'- row: (\S+)\n\s+runner: (\S+)',workers)),expected)
        self.assertIn('tools.ci.performance_frontend --row',workers)
        self.assertIn("if: always() && inputs.gate == 'performance'",result)
        self.assertIn('needs: [performance-bench, performance-engine, performance-worker]',result)
        self.assertIn('actions: read',result); self.assertIn('hosted_performance.py aggregate',result)
        action = (hosted.ROOT/'.github/actions/prepare-performance-waterbox/action.yml').read_text()
        for fragment in ('prepare_hosted_tools.py --fetch','prepare_managed_inputs.py --fetch','uses: ./.github/actions/prepare-managed-sdk',
                         'P6_WATERBOX_SOURCE_AUDIT=snapshot','P6_WATERBOX_TOOLCHAIN_PROFILE=hosted-macos26','P6_WATERBOX_RUNTIME=release','P6_EVICT=0',
                         'prepare_waterbox_sysroot.py','--source-audit snapshot --fetch --jobs 2','--source-audit snapshot --puae6 --jobs 2'):
            self.assertIn(fragment,action)
        self.assertNotRegex(action+bench+engine+workers+result,r'continue-on-error|upload-artifact|actions/cache|docker|--smoke|authorized-downloads|P6_KICKSTART|P6_GAME_ADF')
