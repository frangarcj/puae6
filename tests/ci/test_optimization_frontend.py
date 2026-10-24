"""Own observations and mocked frontend builders; no engine, compiler or API."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.ci import optimization_frontend as f


class OptimizationFrontendTests(unittest.TestCase):
    def record(self,row='linux-arm64-clang'):
        pin=json.loads((f.ROOT/'plan/toolchains.json').read_text())['portability_matrix']['rows'][row]
        tools={n:dict(path='@repo/own/'+n,sha256='a'*64,version='own') for n in ('cc','cxx','git','cmake','ninja','patch','python')}
        for n in ('cc','cxx'): tools[n].update(version=pin['compiler_version'],implementation={'sha256':'a'*64})
        cases={n:dict(runs=[['a' if i in (4,5,9) else '1' for i in range(12)] for _ in range(14)],state_sha256='b'*64,
                      same_process_restore=True,new_process_restore=True) for n in f.matrix.case_names()}
        result=dict(build=dict(row=row,compiler=pin['compiler_version'],compiler_sha256='a'*64,isa=pin['isa'],
                               binaries=dict.fromkeys(('current','candidate','host'),'a'*64),
                               product_variant='candidate' if row in f.frontend.NEON_PRODUCT_ROWS else 'current'),cases=cases,
                    product_sha256='a'*64,inputs_sha256='a'*64,build_receipt_sha256='a'*64,cmake_cache_sha256='a'*64,
                    compile_commands_sha256='a'*64,headers=dict(count=2,sha256='a'*64),codegen_sha256='c'*64)
        return dict(status='PASS',profile=f.PROFILE,kind='frontend',common={'own':'context'},row=row,tools=tools,
                    source_environment_sha256='a'*64,generated=dict(receipt_sha256='c'*64,files=dict.fromkeys(f.hosted.generated_names(),'d'*64),
                    compilers={language:dict(sha256=tools[tool]['sha256'],version=tools[tool]['version']) for language,tool in (('c','cc'),('c++','cxx'))}),
                    runner=dict(os='Darwin' if row.startswith('macos-') else 'Linux',isa=pin['isa'],macos='26.5'),
                    variants={m:copy.deepcopy(result) for m in ('opt','san')},
                    sanitizer_commands=dict(count=3,sha256='a'*64,targets=dict.fromkeys(f.matrix.SAN_TARGETS,1),flags=list(f.matrix.SAN_FLAGS)))

    def test_complete_five_rows_share_original_validators_and_fit_bounded_observations(self):
        records=[self.record(row) for row in f.matrix.ROWS]
        for record in records:
            self.assertIs(f.validate(record,{'own':'context'}),record)
            self.assertLess(len(json.dumps(record,separators=(',',':')).encode()),100*1024)
        f.validate_rows(records)
        with self.assertRaises(RuntimeError): f.validate_rows(records[:-1])
        records[-1]=copy.deepcopy(records[0])
        with self.assertRaises(RuntimeError): f.validate_rows(records)

    def test_omissions_bad_pins_instrumentation_and_provenance_are_rejected(self):
        for fault in ('context','variant','case','runs','restore','binary','product','compiler','mac15','headers','codegen','environment','flags','count'):
            record=self.record('macos-arm64-appleclang'); result=record['variants']['san']; case=next(iter(result['cases'].values()))
            if fault=='context': record['common']={}
            if fault=='variant': record['variants'].pop('opt')
            if fault=='case': result['cases'].pop(next(iter(result['cases'])))
            if fault=='runs': case['runs'].pop()
            if fault=='restore': case['new_process_restore']=False
            if fault=='binary': result['build']['binaries'].pop('host')
            if fault=='product': result['product_sha256']='f'*64
            if fault=='compiler': result['build']['compiler']='Apple clang version17'
            if fault=='mac15': record['runner']['macos']='15.7'
            if fault=='headers': result['headers']['count']=0
            if fault=='codegen': record['generated']['files'].pop(next(iter(record['generated']['files'])))
            if fault=='environment': record['source_environment_sha256']='missing'
            if fault=='flags': record['sanitizer_commands']['flags'].pop()
            if fault=='count': record['sanitizer_commands']['count']=4
            with self.subTest(fault=fault),self.assertRaises((RuntimeError,AssertionError)): f.validate(record,{'own':'context'})

    def test_opt_san_and_crossrow_compare_complete_runs_exactly(self):
        records=[self.record(row) for row in f.matrix.ROWS]; result=records[-1]['variants']['san']
        next(iter(result['cases'].values()))['runs'][0][0]='2'
        with self.assertRaises(AssertionError): f.matrix.compare_variants(records[-1]['row'],records[-1]['variants']['opt'],result)
        with self.assertRaises(AssertionError): f.validate_rows(records)

    def commands(self):
        return [dict(file='own.cpp',command='c++ '+ ' '.join(f.matrix.SAN_FLAGS)+' -o CMakeFiles/'+t+'.dir/own.cpp.o -c own.cpp') for t in f.matrix.SAN_TARGETS]

    def test_actual_command_flags_all_targets_duplicates_and_hash_are_strict(self):
        commands=self.commands(); proof=f.matrix.sanitizer_commands(commands,'a'*64)
        self.assertEqual(proof['count'],3); self.assertEqual(set(proof['targets']),set(f.matrix.SAN_TARGETS))
        for fault in ('missing-target','missing-flag','duplicate','hash'):
            value=copy.deepcopy(commands); digest='a'*64
            if fault=='missing-target': value.pop()
            if fault=='missing-flag': value[0]['command']=value[0]['command'].replace(f.matrix.SAN_FLAGS[1],'')
            if fault=='duplicate': value.append(value[0])
            if fault=='hash': digest='bad'
            with self.subTest(fault=fault),self.assertRaises(AssertionError): f.matrix.sanitizer_commands(value,digest)

    def test_ninja_headers_hash_effective_bytes_and_worker_runs_both_build_verify_pairs(self):
        (f.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=f.ROOT/'build') as temporary:
            root=Path(temporary); header=root/'own.h'; header.write_text('own header')
            with mock.patch.object(f.hosted.native,'execute',return_value='    '+str(header)+'\n'):
                before=f.headers(root); header.write_text('own changed header'); self.assertNotEqual(f.headers(root),before)
            with mock.patch.object(f.hosted.native,'execute',return_value='own.o: #deps1 (STALE)\n    '+str(header)+'\n'),self.assertRaises(RuntimeError): f.headers(root)
            record=self.record(); calls=[]
            class OwnFrontend:
                def __init__(self,row,work,sanitizers):
                    self.tree=Path(work)/('san' if sanitizers else 'opt'); self.cores={n:self.tree/n for n in ('current','candidate')}
                def build(self):
                    calls.append(('build',self.tree.name)); self.tree.mkdir(parents=True,exist_ok=True)
                    for p in self.cores.values(): p.write_bytes(b'own binary')
                    (self.tree/'compile_commands.json').write_text(json.dumps(commands))
                def verify(self):
                    calls.append(('verify',self.tree.name)); (self.tree/'verification.json').write_text(json.dumps(dict(record['variants']['opt'],inputs={'own':'fixture'})))
            commands=self.commands()
            artifact=dict(binaries=record['variants']['opt']['build']['binaries'],generated=record['generated'],
                          **{k:record['variants']['opt'][k] for k in ('build_receipt_sha256','cmake_cache_sha256','compile_commands_sha256','headers')})
            with mock.patch.object(f,'ROOT',root),mock.patch.object(f.hosted.native,'preflight'),mock.patch.object(f.hosted,'common',return_value=record['common']), \
                 mock.patch.object(f.hosted,'tools',return_value=record['tools']),mock.patch.object(f.frontend,'source_identity',return_value='a'*64), \
                 mock.patch.object(f.frontend,'Frontend',OwnFrontend),mock.patch.object(f,'artifacts',return_value=artifact) as observe,mock.patch.object(f,'validate') as validate:
                self.assertEqual(f.run(record['row'])['kind'],'frontend'); validate.assert_called_once()
                self.assertEqual([call.args[0].tree.name for call in observe.call_args_list],['opt','opt','san','san','opt','san'])
                observe.side_effect=[artifact]*4+[dict(artifact,headers=dict(count=1,sha256='e'*64))]
                with self.assertRaisesRegex(RuntimeError,'after SAN'): f.run(record['row'])
            self.assertEqual(calls,[('build','opt'),('verify','opt'),('build','san'),('verify','san')]*2)
