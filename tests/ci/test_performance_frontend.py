"""Performance frontend fixtures only: no compiler, emulator or timing campaign."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.ci import performance_frontend as f


def samples():
    result = dict(inputs={'own':'inputs'}, runs=[], summary={})
    for scenario in f.frontend.WORKLOADS:
        units = 10000 if scenario.primary else 3000
        result['summary'][scenario.id] = {}
        for variant, value in (('current',1000),('candidate',900)):
            raw = dict(run_cpu_ns=[value]*(300+units), run_wall_ns=[value+100]*(300+units),
                       state_save_ns=[2000]*20, state_load_ns=[3000]*20, state_bytes=12345,pictures=300+units,audio_frames=456)
            for repeat in range(5):
                result['runs'].append(dict(scenario=scenario.id,variant=variant,repeat=repeat,warmup=300,units=units,
                                          raw=raw,last_run=['own','boundary'],final_state=['own','state']))
            result['summary'][scenario.id][variant] = dict(median_cpu_ns=value)
    return result


class PerformanceFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.measured = samples()
        cls.comparisons = f.matrix.validate_measurements(cls.measured, cls.measured['inputs'], cls.measured['inputs'])

    def record(self,row):
        pin=f.PINS[row]; sha='a'*64
        tools={n:dict(path='@repo/own/'+n,sha256=sha,version='own') for n in ('cc','cxx','git','cmake','ninja','patch','python')}
        for n in ('cc','cxx'): tools[n].update(version=pin['compiler_version'],implementation={'sha256':sha})
        built=dict(row=row,compiler=pin['compiler_version'],compiler_sha256=sha,isa=pin['isa'],binaries=dict.fromkeys(('current','candidate','host'),sha),
                   product_variant='candidate' if row in f.frontend.NEON_PRODUCT_ROWS else 'current')
        cases={n:dict(runs=[['a' if i in (4,5,9) else '1' for i in range(12)] for _ in range(14)],state_sha256='b'*64,
                      same_process_restore=True,new_process_restore=True) for n in f.matrix.case_names()}
        proof={k:sha for k in ('build_receipt_sha256','cmake_cache_sha256','compile_commands_sha256','verification_sha256','inputs_sha256','product_sha256')}
        proof.update(binaries=built['binaries'],headers=dict(count=2,sha256=sha),configuration=f.CONFIGURATION)
        measured=dict(runs=50,repetitions=5,warmup=300,state_rounds=20,raw_bytes=123,raw_sha256=sha,inputs_sha256=sha,
                      verification_sha256=sha,comparisons=self.comparisons) if row in f.matrix.MEASURED else None
        return dict(status='PASS',profile=f.PROFILE,kind='frontend',common={'own':'context'},row=row,tools=tools,
                    generated=dict(receipt_sha256=sha,files=dict.fromkeys(f.hosted.generated_names(),sha),
                                   compilers={language:dict(sha256=sha,version=tools[tool]['version']) for language,tool in (('c','cc'),('c++','cxx'))}),
                    source_environment_sha256=sha,verification=dict(build=built,cases=cases),provenance=proof,measurement=measured,
                    decision=dict(adr_sha256=f.ADR_SHA) if measured else None,
                    runner=dict(os='Darwin' if row.startswith('macos-') else 'Linux',isa=pin['isa'],macos='26.5'))

    def test_original_protocol_arrays_facts_statistics_and_inputs_are_all_enforced(self):
        for fault in ('inputs','verified','current','count','repeat','warmup','units','cpu-count','cpu-positive','wall-count','loads','facts','last','final','summary'):
            result=copy.deepcopy(self.measured); sample=result['runs'][0]; verified=current=result['inputs']
            if fault=='inputs': result['inputs']={}
            if fault=='verified': verified={}
            if fault=='current': current={}
            if fault=='count': result['runs'].pop()
            if fault=='repeat': sample['repeat']=6
            if fault=='warmup': sample['warmup']=1
            if fault=='units': sample['units']=1
            if fault=='cpu-count': sample['raw']['run_cpu_ns']=[]
            if fault=='cpu-positive': sample['raw']['run_cpu_ns']=[0]*len(sample['raw']['run_cpu_ns'])
            if fault=='wall-count': sample['raw']['run_wall_ns']=[]
            if fault=='loads': sample['raw']['state_load_ns']=[]
            if fault=='facts': sample['raw']['state_bytes']=0
            if fault=='last': sample['last_run']=['foreign']
            if fault=='final': sample['final_state']=['foreign']
            if fault=='summary': result['summary'][sample['scenario']]['current']['median_cpu_ns']=1
            with self.subTest(fault=fault),self.assertRaises((AssertionError,ValueError)):
                f.matrix.validate_measurements(result,verified,current)

    def test_decision_preserves_heading_all_measured_rows_and_current_inputs(self):
        for row in f.matrix.MEASURED: f.matrix.validate_decision(row,{}, {}, f.ADR)
        for row,inputs,current,adr in ((f.matrix.MEASURED[0],{}, {}, ''),(f.matrix.MEASURED[0],{}, {'changed':1}, f.ADR),('foreign',{}, {},f.ADR)):
            with self.assertRaises(AssertionError): f.matrix.validate_decision(row,inputs,current,adr)

    def test_transport_requires_five_verified_three_measured_rows_and_complete_provenance(self):
        records=[self.record(row) for row in f.matrix.ROWS]
        self.assertEqual(f.validate_rows(records)['measured_windows'],150)
        with self.assertRaises(RuntimeError): f.validate_rows(records[:-1])
        with self.assertRaises(RuntimeError): f.validate_rows(records+[records[0]])
        for fault in ('common','measurement','protocol','headers','configuration','compiler','codegen','decision','samples','rawhash','pairs','state-cost'):
            record=copy.deepcopy(records[0])
            if fault=='common': record['common']={}
            if fault=='measurement': record['measurement']=None
            if fault=='protocol': record['measurement']['warmup']=1
            if fault=='headers': record['provenance']['headers']['count']=0
            if fault=='configuration': record['provenance']['configuration']={}
            if fault=='compiler': record['verification']['build']['compiler']='foreign'
            if fault=='codegen': record['generated']['files']={}
            if fault=='decision': record['decision']={}
            if fault=='samples': next(iter(record['measurement']['comparisons'].values()))['variants']['current']['cpu']['n']=1
            if fault=='rawhash': record['measurement']['raw_sha256']='bad'
            if fault=='pairs': next(iter(record['measurement']['comparisons'].values()))['paired_reductions_percent']=[0]*5
            if fault=='state-cost': next(iter(record['measurement']['comparisons'].values()))['state_p95_cost_percent']={}
            with self.subTest(fault=fault),self.assertRaises((RuntimeError,AssertionError)): f.validate(record,{'own':'context'})
        redacted=json.loads(f.hosted.ci.redact(json.dumps(records)))
        self.assertEqual(f.validate_rows(redacted)['unittest_tests'],0)

    def test_worker_builds_verifies_then_measures_only_declared_rows_without_docker(self):
        (f.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=f.ROOT/'build') as temporary:
            root=Path(temporary); calls=[]
            for row in ('linux-arm64-clang','linux-x86_64-clang'):
                record=self.record(row); data=copy.deepcopy(self.measured)
                class OwnFrontend:
                    def __init__(self,row,work):
                        self.work=Path(work); self.tree=self.work/'opt'; self.cores={n:self.tree/n for n in ('current','candidate')}
                    def build(self):
                        calls.append((row,'build')); self.tree.mkdir(parents=True,exist_ok=True)
                        (self.tree/'CMakeCache.txt').write_text('\n'.join(k+':STRING='+v for k,v in f.CONFIGURATION.items()))
                    def verify(self):
                        calls.append((row,'verify')); (self.tree/'verification.json').write_text(json.dumps(dict(record['verification'],inputs=data['inputs'])))
                    def measure(self):
                        calls.append((row,'measure')); (self.tree/'measurements.json').write_text(json.dumps(data))
                artifact={k:v for k,v in record['provenance'].items() if k not in ('configuration','verification_sha256','inputs_sha256','product_sha256')}
                artifact['generated']=record['generated']
                with patch.object(f,'ROOT',root),patch.object(f.hosted.native,'preflight'),patch.object(f.hosted,'common',return_value=record['common']), \
                     patch.object(f.hosted,'tools',return_value=record['tools']),patch.object(f.frontend,'source_identity',return_value='a'*64), \
                     patch.object(f.frontend,'Frontend',OwnFrontend),patch.object(f.shared,'artifacts',return_value=artifact) as observe, \
                     patch.object(f.frontend,'digest',return_value='a'*64),patch.object(f.matrix,'current_inputs',return_value=data['inputs']), \
                     patch.object(f.platform,'system',return_value='Linux'),patch.object(f.platform,'machine',return_value=record['runner']['isa']):
                    result=f.run(row); self.assertEqual(result['measurement'] is not None,row in f.matrix.MEASURED)
                    self.assertEqual(observe.call_count,3)
                    observe.side_effect=[artifact,artifact,dict(artifact,headers=dict(count=1,sha256='f'*64))]
                    with self.assertRaisesRegex(RuntimeError,'inputs changed'): f.run(row)
                self.assertEqual(list((root/'build').iterdir()),[])
            self.assertEqual(calls[:3],[('linux-arm64-clang','build'),('linux-arm64-clang','verify'),('linux-arm64-clang','measure')])

    def test_current_inputs_uses_fresh_assets_and_actual_work_binaries(self):
        (f.ROOT/'build').mkdir(exist_ok=True)
        with patch.object(f.frontend.assets,'write_differential') as write,patch.object(f.frontend,'timing_inputs',return_value={'own':'fresh'}) as inputs:
            self.assertEqual(f.matrix.current_inputs('linux-arm64-clang',Path('/own/work')),{'own':'fresh'})
            self.assertEqual(inputs.call_args.args[0]['host'],Path('/own/work/opt/p6_libretro_host'))
            self.assertEqual(inputs.call_args.args[1],write.call_args.args[0]); self.assertFalse(Path(write.call_args.args[0]).exists())
