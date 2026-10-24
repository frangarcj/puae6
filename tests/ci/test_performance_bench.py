"""Own fixtures and mocked builders/campaigns; no compiler, emulator or network."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.ci import performance_bench as p


class PerformanceBenchTests(unittest.TestCase):
    def record(self):
        version = p.engine.VERSION
        native = {n:dict(path='@repo/own/'+n,sha256='a'*64,version=version if n in ('cc','cxx') else 'own')
                  for n in ('cc','cxx','git','cmake','ninja','patch','python')}
        for name in ('cc','cxx'): native[name]['implementation'] = dict(sha256='a'*64)
        lock = json.loads((p.ROOT/'plan/toolchains.json').read_text())
        pins = {**lock['tools'],**lock['toolchain_profiles']['hosted-macos26']['tools']}
        tree = dict(binaries=dict.fromkeys(p.TARGETS,'a'*64),cache_sha256='a'*64,commands_sha256='a'*64,headers=dict(files=1,sha256='a'*64))
        artifact = dict(sha256='a'*64,receipt_sha256='a'*64)
        managed = dict(inputs_sha256='a'*64,receipt_sha256='a'*64,release=dict.fromkeys(p.managed_release.OUTPUTS,'a'*64),
                       debug=dict.fromkeys((*p.managed_release.OUTPUTS,'libwaterboxhost.dylib'),'a'*64),
                       smoke=dict.fromkeys(('Smoke.dll','Smoke.deps.json','Smoke.runtimeconfig.json'),'a'*64))
        return dict(status='PASS',profile=p.PROFILE,kind='bench',common={'own':'context'},source_environment_sha256='a'*64,
                    tools=dict(native=native,waterbox={n:dict(path='@repo/own/'+n,sha256=pins[n]['sha256'],version=pins[n]['version']) for n in p.hosted.ci.wb.TOOL_NAMES},
                               sdk=dict(path='@repo/own/dotnet',sha256='a'*64,version='10.0.402'),runner=dict(os='Darwin',isa='arm64',macos='26.5')),
                    generated=dict(receipt_sha256='a'*64,files=dict.fromkeys(p.hosted.generated_names(),'a'*64),
                                   compilers={language:dict(sha256='a'*64,version=version) for language in ('c','c++')}),
                    inputs=dict(native={name:copy.deepcopy(tree) for name in ('release','state-opt')},
                                runtime={name:copy.deepcopy(artifact) for name in ('release','debug')},guest=artifact,manual_sha256='a'*64,managed=managed),
                    campaign=dict(identity_sha256='a'*64,rows=27,windows=135,raw_files=135,raw_sha256='a'*64,summaries_sha256='a'*64,protocol=p.PROTOCOL),
                    completed=[dict(selector=s,counts=dict(tests=n,failures=0,errors=0,skipped=0,expected_failures=0,unexpected_successes=0)) for s,n in p.SELECTORS.items()],policy=p.policy())

    def test_exact_original_selector_partition_counts_and_advisory_record(self):
        p.partition()  # Loading original TestCases does not run setUp/build/measure.
        self.assertEqual(list(p.SELECTORS.values()),[1,5,4,5,5,12,6,1])
        self.assertEqual(sum(p.SELECTORS.values()),39)
        record=self.record(); self.assertIs(p.validate(record,{'own':'context'}),record)
        self.assertLess(len(json.dumps(record)),100*1024)
        with mock.patch.object(p.unittest.defaultTestLoader,'loadTestsFromName') as load:
            load.return_value.countTestCases.return_value=0
            with self.assertRaisesRegex(RuntimeError,'count changed'): p.partition()

    def test_missing_duplicate_mixed_skipped_and_foreign_proofs_never_pass(self):
        for fault in ('context','selector','duplicate','count','skip','xfail','windows','raw','protocol','hash','tool','compiler','sdk','runner','headers','binary','generated','managed','runtime','policy'):
            record=self.record()
            if fault=='context': record['common']={}
            if fault=='selector': record['completed'].pop()
            if fault=='duplicate': record['completed'][-1]=record['completed'][0]
            if fault in ('count','skip','xfail'): record['completed'][0]['counts'][{'count':'tests','skip':'skipped','xfail':'expected_failures'}[fault]]=2
            if fault in ('windows','raw'): record['campaign'][{'windows':'windows','raw':'raw_files'}[fault]]=134
            if fault=='protocol': record['campaign']['protocol']='quick'
            if fault=='hash': record['campaign']['identity_sha256']='bad'
            if fault=='tool': record['tools']['waterbox']['make']['sha256']='a'*64
            if fault=='compiler': record['tools']['native']['cc']['version']='Apple clang17'
            if fault=='sdk': record['tools']['sdk']['version']='foreign'
            if fault=='runner': record['tools']['runner']['macos']='15.7'
            if fault=='headers': record['inputs']['native']['release']['headers']['files']=0
            if fault=='binary': record['inputs']['native']['state-opt']['binaries'].pop('p6_bench_probe')
            if fault=='generated': record['generated']['files'].pop(next(iter(record['generated']['files'])))
            if fault=='managed': record['inputs']['managed']['debug'].pop('Puae6.Core.dll')
            if fault=='runtime': record['inputs']['managed']['debug']['libwaterboxhost.dylib']='b'*64
            if fault=='policy': record['policy']['dedicated_runner']=True
            with self.subTest(fault=fault),self.assertRaises((RuntimeError,KeyError)): p.validate(record,{'own':'context'})

    def test_preparation_uses_original_public_recipes_and_state_targets(self):
        with mock.patch.object(p,'execute') as execute,mock.patch.object(p.build_support,'build_targets') as build:
            p.prepare({'own':'env'})
        self.assertEqual(execute.call_args.args[0],[p.sys.executable,'-B','tools/bench.py','build','--jobs','2'])
        build.assert_called_once_with(p.ROOT/'build/state-opt',p.TARGETS,optimized=True)

    def test_raw_proof_calls_original_validator_counts_actual_files_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); (directory/'raw').mkdir()
            for n in range(135): (directory/'raw'/f'own{n}.json.gz').write_bytes(b'own data, not a benchmark')
            rows={(str(n),'own'):{'own':n} for n in range(27)}
            with mock.patch.object(p.bench,'validate_cpu_campaign',return_value=rows) as validate, \
                 mock.patch.object(p.bench,'cpu_campaign_identity',return_value={'protocol':p.PROTOCOL}):
                proof=p.campaign(directory); validate.assert_called_once_with(directory)
                self.assertEqual(proof['windows'],135); self.assertNotIn('samples',proof)
                (directory/'raw'/'own0.json.gz').unlink()
                with self.assertRaises(RuntimeError): p.campaign(directory)
                validate.side_effect=ValueError('own raw validation failed')
                with self.assertRaisesRegex(ValueError,'raw validation'): p.campaign(directory)

    def test_worker_prepares_then_runs_one_forced_full_campaign_and_all_selectors_without_replay(self):
        record=self.record(); commands=[]
        def command(argv,env,timeout):
            commands.append((argv,env.copy())); selector=argv[-1]
            counts=next((r['counts'] for r in record['completed'] if r['selector']==selector),{})
            return p.hosted.ci.subprocess.CompletedProcess(argv,0,json.dumps(counts),'')
        with mock.patch.object(p,'partition'),mock.patch.object(p,'tools',return_value=record['tools']), \
             mock.patch.object(p.hosted,'common',return_value=record['common']),mock.patch.object(p.build_support,'source_identity',return_value='a'*64), \
             mock.patch.object(p,'prepare') as prepare,mock.patch.object(p,'inputs',return_value=record['inputs']) as inputs, \
             mock.patch.object(p.hosted,'generated',return_value=record['generated']),mock.patch.object(p,'campaign',return_value=record['campaign']) as campaign, \
             mock.patch.object(p.hosted.ci,'command',side_effect=command),mock.patch.dict(p.os.environ,{},clear=True):
            result=p.run(); self.assertEqual(result['kind'],'bench'); prepare.assert_called_once()
            self.assertEqual([argv[-1] for argv,_ in commands[1:]],list(p.SELECTORS))
            self.assertEqual(commands[0][0][-2:],['--compare-cpu-levels','--force'])
            self.assertEqual(len(commands),9); self.assertEqual(campaign.call_count,2); self.assertEqual(inputs.call_count,2)
            directory=Path(commands[1][1]['P6_CPU_PERF_DIR']); self.assertFalse(directory.parent.exists())
            self.assertTrue(all(env['P6_RUN_CPU_PERF']=='0' for _,env in commands))
            commands.clear(); inputs.side_effect=[record['inputs'],dict(record['inputs'],manual_sha256='b'*64)]
            with self.assertRaisesRegex(RuntimeError,'inputs/outputs/headers changed'): p.run()
