"""Own identities and mocked constructors only; no compiler or sandbox execution."""
import contextlib
import copy
import io
from pathlib import Path
import unittest
from unittest import mock

from tools.ci import performance_inputs as diag


class PerformanceInputsTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(common=dict(repository='own/repo',revision='a'*40,source_tree_sha256='b'*64,
                                         run_id='123',run_attempt='1'), tools={'own':'tool'},
                             source_environment='c'*64)
        self.snapshot = dict(**self.identity, generated={'own':'codegen'},inputs={'native':{'state-opt':{'cache_sha256':'d'*64}}})

    @contextlib.contextmanager
    def plumbing(self):
        # Every operation that could build/measure is mocked explicitly.
        with mock.patch.object(diag,'identity',return_value=self.identity), \
             mock.patch.object(diag.bench.hosted,'generated',return_value=self.snapshot['generated']), \
             mock.patch.object(diag,'snapshot',side_effect=lambda:copy.deepcopy(self.snapshot)) as capture, \
             mock.patch.object(diag.bench,'prepare') as prepare, mock.patch.object(diag.bench,'execute') as execute, \
             mock.patch.object(diag.bench,'campaign') as campaign, mock.patch.object(diag.bench,'partition') as partition, \
             mock.patch.object(diag.bench.hosted.ci,'command') as command, \
             mock.patch.dict(diag.bench.os.environ,{},clear=True),contextlib.redirect_stderr(io.StringIO()):
            yield capture,prepare,execute,campaign,partition,command

    def test_exact_four_child_constructors_original_environment_and_no_campaign_or_selectors(self):
        with self.plumbing() as (capture,prepare,execute,campaign,partition,command):
            record=diag.run()
            prepare.assert_called_once_with(dict(diag.bench.nightly.ENV,P6_RUN_CPU_PERF='0'))
            self.assertEqual(capture.call_count,9)
            self.assertEqual([c.args[0] for c in execute.call_args_list],
                             [[diag.sys.executable,'-B','-c',body] for _,body in diag.CONSTRUCTORS])
            self.assertTrue(all(c.args[1] == prepare.call_args.args[0] for c in execute.call_args_list))
            campaign.assert_not_called(); partition.assert_not_called(); command.assert_not_called()
            self.assertEqual(record['status'],'DIAGNOSTIC_STABLE'); self.assertNotEqual(record['status'],'PASS')
            self.assertEqual(record['coverage'],dict(constructors=4,campaign_windows=0,original_methods=0))
            self.assertIs(diag.validate(record,self.identity['common']),record)

    def test_drift_in_each_constructor_names_the_step_and_exact_field_and_stops(self):
        for index,(name,_) in enumerate(diag.CONSTRUCTORS):
            with self.subTest(constructor=name),self.plumbing() as (capture,prepare,execute,*_):
                count=0
                def snapshot():
                    nonlocal count
                    result=copy.deepcopy(self.snapshot)
                    if count == 2+2*index: result['inputs']['native']['state-opt']['cache_sha256']='e'*64
                    count+=1
                    return result
                capture.side_effect=snapshot
                with self.assertRaisesRegex(diag.DiagnosticDrift,name+': .*?/inputs/native/state-opt/cache_sha256') as raised:
                    diag.run()
                self.assertEqual(raised.exception.observation['before'],self.snapshot)
                self.assertEqual(raised.exception.observation['after']['inputs']['native']['state-opt']['cache_sha256'],'e'*64)
                self.assertEqual(execute.call_count,index+1)

    def test_preparation_and_source_run_environment_drift_cannot_be_stable(self):
        for field,value in (('common',dict(self.identity['common'],run_attempt='2')),('source_environment','f'*64)):
            with self.subTest(field=field),self.plumbing() as (capture,prepare,execute,*_):
                capture.return_value=None
                capture.side_effect=lambda:dict(copy.deepcopy(self.snapshot),**{field:value})
                with self.assertRaisesRegex(RuntimeError,'preparation: .*?/'+field): diag.run()
                execute.assert_not_called()
        with self.plumbing() as (_,_,execute,*_):
            execute.side_effect=RuntimeError('own failed compiler mock')
            with self.assertRaisesRegex(RuntimeError,'ffi-release: own failed compiler mock'): diag.run()

    def test_codegen_transition_during_preparation_is_observed_then_all_four_constructors_run(self):
        with self.plumbing() as (_,prepare,execute,*_):
            prepare.side_effect=lambda env:self.snapshot.update(generated={'own':'prepared-codegen'})
            record=diag.run()
            self.assertEqual(execute.call_count,4)
            self.assertEqual(record['preparation']['generated_before'],{'own':'codegen'})
            self.assertEqual(record['preparation']['generated_after'],{'own':'prepared-codegen'})
            self.assertEqual(record['preparation']['after'],{k:v for k,v in record['baseline'].items() if k != 'inputs'})
            self.assertTrue(all(step['before']['generated'] == step['after']['generated'] == {'own':'prepared-codegen'}
                                for step in record['completed']))
            broken=copy.deepcopy(record); broken['preparation']['generated_after']={'own':'foreign-codegen'}
            with self.assertRaisesRegex(diag.DiagnosticDrift,'preparation codegen observation'):
                diag.validate(broken,self.identity['common'])

    def test_codegen_drift_during_a_constructor_is_still_an_exact_failure(self):
        with self.plumbing() as (capture,_,execute,*_):
            count=0
            def snapshot():
                nonlocal count
                result=copy.deepcopy(self.snapshot)
                if count == 2: result['generated']['own']='changed-by-constructor'
                count+=1
                return result
            capture.side_effect=snapshot
            with self.assertRaisesRegex(diag.DiagnosticDrift,'ffi-release: .*?/generated/own'):
                diag.run()
            self.assertEqual(execute.call_count,1)

    def test_missing_duplicate_commands_coverage_and_foreign_identity_are_rejected(self):
        with self.plumbing(): record=diag.run()
        for fault in ('missing','duplicate','command','coverage','source','environment'):
            broken=copy.deepcopy(record)
            if fault=='missing': broken['completed'].pop()
            if fault=='duplicate': broken['completed'][-1]=copy.deepcopy(broken['completed'][0])
            if fault=='command': broken['completed'][0]['argv'][-1]='print("not a constructor")'
            if fault=='coverage': broken['coverage']['original_methods']=39
            if fault=='source': broken['baseline']['common']['revision']='f'*40
            if fault=='environment': broken['completed'][0]['after']['source_environment']='f'*64
            with self.subTest(fault=fault),self.assertRaises(RuntimeError): diag.validate(broken,self.identity['common'])
        with mock.patch.object(diag,'CONSTRUCTORS',diag.CONSTRUCTORS[:-1]),self.plumbing() as (_,prepare,*_):
            with self.assertRaisesRegex(RuntimeError,'coverage'): diag.run()
            prepare.assert_not_called()

    def test_workflow_is_manual_independent_and_only_prepares_then_diagnoses(self):
        workflow=(Path(diag.__file__).resolve().parents[2]/'.github/workflows/native-linux.yml').read_text()
        self.assertIn('performance-inputs]',workflow)
        job=workflow.split('  performance-inputs:',1)[1].split('  performance-bench:',1)[0]
        for fragment in ("if: inputs.gate == 'performance-inputs'",'runs-on: macos-26',
                         "test '${{ inputs.variants }}' = all",'uses: ./.github/actions/prepare-performance-waterbox',
                         'python3 -B -m tools.ci.performance_inputs','persist-credentials: false'):
            self.assertIn(fragment,job)
        self.assertNotRegex(job,r'needs:|continue-on-error|performance_engine|performance_frontend|hosted_performance|aggregate|artifact')
