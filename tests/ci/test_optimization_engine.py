"""Own receipts and mocked processes; no compiler, emulator, API or training runs."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ci import optimization_engine as engine


class OptimizationEngineTests(unittest.TestCase):
    def setUp(self):
        (engine.ROOT/'build').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='own-engine-', dir=engine.ROOT/'build')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.context = dict(revision='a'*40, run_id='123', run_attempt='1', source_tree_sha256='b'*64)
        self.tools = {name: dict(path='/own/'+name, sha256='a'*64, version=engine.VERSION,
                                implementation=dict(path='/own/compiler', sha256='c'*64))
                      for name in ('cc', 'cxx', 'git', 'cmake', 'ninja', 'patch', 'python', 'llvm-profdata')}
        self.generated = dict(receipt_sha256='d'*64, files={name: 'e'*64 for name in engine.matrix.generated_names()},
                              compilers={name: dict(sha256='a'*64, version=engine.VERSION) for name in ('c', 'c++')})
        self.variant = dict(build='b'*64, verification='c'*64, compile_commands='d'*64,
                            binaries={name: dict(bytes=7, sha256='e'*64) for name in ('p6_state_probe', 'p6_bench_probe')},
                            canonical=dict(cases=sorted(entry.id for entry in engine.experiment.corpus.ENTRIES), sha256='f'*64),
                            restored=engine.restored(), dependencies=dict(files=2, sha256='a'*64))
        self.training = dict(receipt='a'*64, instrument_verification='c'*64, profile=dict(bytes=3, sha256='b'*64),
                             raw_profiles=1, private_assets=False, tool_sha256='a'*64)

    def record(self):
        return copy.deepcopy(dict(status='PASS', profile=engine.PROFILE, kind='engine', common=self.context,
                                  source_environment_sha256='f'*64, tools=self.tools, generated=self.generated,
                                  preparation=engine.preparation(), variants={v: self.variant for v in engine.VARIANTS}, training=self.training,
                                  completed=[dict(selector=selector, counts=dict(tests=count, failures=0, errors=0, skipped=0,
                                                                                expected_failures=0, unexpected_successes=0)) for selector, count in engine.SELECTORS.items()]))

    def test_pure_validator_rejects_partial_foreign_failed_receipts_and_all_omissions(self):
        self.assertEqual(engine.validate(self.record(), self.context)['kind'], 'engine')
        redacted = self.record(); redacted['tools']['python']['path'] = str(engine.ROOT/'build/own-tool')
        redacted = json.loads(engine.matrix.ci.redact(json.dumps(redacted)))
        self.assertEqual(engine.validate(redacted, self.context)['status'], 'PASS')
        for fault in ('foreign', 'environment', 'preparation', 'variant', 'version', 'tool', 'generated', 'compiler',
                      'canonical', 'restore', 'binary', 'header', 'training', 'private', 'selector', 'count', 'skip', 'failure'):
            r = self.record()
            if fault == 'foreign': r['common'] = {}
            if fault == 'environment': r['source_environment_sha256'] = 'partial'
            if fault == 'preparation': r['preparation'] = r['preparation'][1:]
            if fault == 'variant': del r['variants']['instrument']
            if fault == 'version': r['tools']['cc']['version'] = 'different Apple Clang'
            if fault == 'tool': del r['tools']['llvm-profdata']
            if fault == 'generated': r['generated']['files'].pop(next(iter(r['generated']['files'])))
            if fault == 'compiler': r['generated']['compilers']['c']['sha256'] = 'f'*64
            if fault == 'canonical': r['variants']['pgo'] = copy.deepcopy(r['variants']['pgo']); r['variants']['pgo']['canonical']['sha256'] = 'e'*64
            if fault == 'restore': r['variants']['base']['restored'].pop()
            if fault == 'binary': r['variants']['base']['binaries']['p6_bench_probe']['bytes'] = 0
            if fault == 'header': r['variants']['base']['dependencies']['files'] = 0
            if fault == 'training': r['training']['instrument_verification'] = 'd'*64
            if fault == 'private': r['training']['private_assets'] = True
            if fault == 'selector': r['completed'].pop()
            if fault == 'count': r['completed'][0]['counts']['tests'] = 1
            if fault == 'skip': r['completed'][0]['counts']['skipped'] = 1
            if fault == 'failure': r['completed'][0]['counts']['failures'] = 1
            with self.subTest(fault=fault), self.assertRaises((RuntimeError, KeyError)): engine.validate(r, self.context)

    def test_original_preparation_and_four_methods_execute_once_with_fail_closed_drift(self):
        for fault in ('none', 'command', 'source', 'tools', 'codegen', 'output', 'training', 'skip'):
            events = []; calls = {}
            def execute(argv, timeout):
                events.append((argv[3], argv[5] if len(argv) > 4 else None)); self.assertEqual(timeout, 14400)
                if fault == 'command': raise RuntimeError('own build failure')
            def variant(name, source):
                self.assertEqual(source, 'f'*64); calls[name] = calls.get(name, 0)+1
                return {} if fault == 'output' and calls[name] > 1 else copy.deepcopy(self.variant)
            def suite(argv, env, timeout):
                self.assertEqual(env, {'P6_TEST_ENGINE_FLAGS': '1'}); events.append(('selector', argv[-1]))
                counts = dict(tests=engine.SELECTORS[argv[-1]], failures=0, errors=0, skipped=int(fault == 'skip'), expected_failures=0, unexpected_successes=0)
                return subprocess.CompletedProcess(argv, 0, json.dumps(counts), '')
            with self.subTest(fault=fault), mock.patch.object(engine.experiment, 'WORK', self.root/'fresh'), \
                 mock.patch.object(engine.matrix, 'common', return_value=self.context), \
                 mock.patch.object(engine, 'toolchain', side_effect=[self.tools, {} if fault == 'tools' else self.tools]), \
                 mock.patch.object(engine.experiment, 'source_identity', side_effect=['f'*64, ('e' if fault == 'source' else 'f')*64]), \
                 mock.patch.object(engine.matrix, 'generated', side_effect=[self.generated, {} if fault == 'codegen' else self.generated]), \
                 mock.patch.object(engine.matrix.native, 'execute', side_effect=execute), mock.patch.object(engine, 'variant_record', side_effect=variant), \
                 mock.patch.object(engine, 'training_record', side_effect=[self.training, {} if fault == 'training' else self.training]), \
                 mock.patch.object(engine.matrix.ci, 'command', side_effect=suite) as selected, mock.patch.object(engine.sys, 'stderr', io.StringIO()):
                if fault == 'none': self.assertEqual(engine.run()['completed'], self.record()['completed'])
                else:
                    with self.assertRaises(RuntimeError): engine.run()
                if fault == 'command': selected.assert_not_called(); continue
                self.assertEqual(events[:13], [(step['action'], step.get('variant')) for step in engine.preparation()])
                if fault != 'skip': self.assertEqual(events[13:], [('selector', name) for name in engine.SELECTORS])

    def test_wrong_host_version_or_existing_workspace_blocks_before_build(self):
        with mock.patch.object(engine.matrix.native, 'preflight', side_effect=ValueError('wrong host')), \
             mock.patch.object(engine.matrix, 'tools') as tools, self.assertRaises(ValueError): engine.toolchain()
        tools.assert_not_called()
        with mock.patch.object(engine.matrix.native, 'preflight'), mock.patch.object(engine.matrix, 'tools', return_value={**self.tools, 'cc': dict(version='wrong')}), \
             mock.patch.object(engine.matrix.native, 'execute') as execute, self.assertRaisesRegex(RuntimeError, 'Apple Clang'): engine.toolchain()
        execute.assert_not_called()
        with mock.patch.object(engine.experiment, 'WORK', self.root), mock.patch.object(engine.matrix, 'common') as common, self.assertRaisesRegex(RuntimeError, 'fresh'): engine.run()
        common.assert_not_called()

    def test_actual_own_bytes_receipts_and_headers_detect_tamper_with_preserved_mtime(self):
        directory = self.root/'base'; directory.mkdir()
        for name in ('p6_state_probe', 'p6_bench_probe', 'compile_commands.json', 'own.h'): (directory/name).write_text('own bytes '+name)
        binaries = {name: engine.fact(directory/name) for name in ('p6_state_probe', 'p6_bench_probe')}
        built = dict(source_identity='f'*64, binaries=binaries, compile_commands_sha256=engine.fact(directory/'compile_commands.json')['sha256'])
        cases = {entry.id: dict(hardware='a'*64, av='b'*64, per_unit={'0': ['c'*16, 'd'*16]}, units=1, lines=3) for entry in engine.experiment.corpus.ENTRIES}
        verified = dict(source_identity='f'*64, probe_sha256=binaries['p6_state_probe']['sha256'], canonical=dict(errors={}, cases=cases), restored=engine.restored())
        (directory/'build.json').write_text(json.dumps(built)); (directory/'verification.json').write_text(json.dumps(verified))
        with mock.patch.object(engine.experiment, 'WORK', self.root), mock.patch.object(engine.matrix.native, 'execute', return_value=f'own.o: #deps 1 (VALID)\n    {directory}/own.h\n'):
            before = engine.variant_record('base', 'f'*64)
            stat = (directory/'own.h').stat(); (directory/'own.h').write_text('own changed header')
            import os
            os.utime(directory/'own.h', ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(before['dependencies'], engine.variant_record('base', 'f'*64)['dependencies'])
            (directory/'p6_bench_probe').write_bytes(b'tampered')
            with self.assertRaisesRegex(RuntimeError, 'binary changed'): engine.variant_record('base', 'f'*64)
        for text in ('', 'own.o (STALE)\n    '+str(directory/'own.h')):
            with mock.patch.object(engine.matrix.native, 'execute', return_value=text), self.assertRaisesRegex(RuntimeError, 'inventory'): engine.dependencies(directory)

    def test_training_is_local_public_and_bound_to_exact_profile_verification_and_tool(self):
        directory = self.root/'instrument'; (directory/'profiles').mkdir(parents=True)
        raw = directory/'profiles/own.profraw'; raw.write_bytes(b'own training bytes')
        verified = directory/'verification.json'; verified.write_text(json.dumps(dict(raw_profiles=[str(raw)])))
        profile = self.root/'training.profdata'; profile.write_bytes(b'own merged bytes')
        training = dict(raw_profiles=1, private_assets=False, corpus=engine.fact(verified)['sha256'], profile_sha256=engine.fact(profile)['sha256'],
                        tool_sha256='a'*64, version=engine.VERSION)
        for fault in ('none', 'profile', 'verification', 'private', 'tool', 'count', 'foreign'):
            r = dict(training)
            if fault == 'profile': r['profile_sha256'] = 'f'*64
            if fault == 'verification': r['corpus'] = 'f'*64
            if fault == 'private': r['private_assets'] = True
            if fault == 'tool': r['tool_sha256'] = 'f'*64
            if fault == 'count': r['raw_profiles'] = 0
            if fault == 'foreign': verified.write_text(json.dumps(dict(raw_profiles=[str(profile)])))
            (self.root/'training.json').write_text(json.dumps(r))
            with self.subTest(fault=fault), mock.patch.object(engine.experiment, 'WORK', self.root), mock.patch.object(engine.experiment, 'PROFDATA', profile):
                if fault == 'none': self.assertEqual(engine.training_record(self.tools)['raw_profiles'], 1)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'training'): engine.training_record(self.tools)
