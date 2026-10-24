"""Own records and mocked processes only; never build, run an engine or use GitHub."""
import copy
from email.message import Message
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ci import hosted_matrix as matrix, action_logs as logs


class HostedMatrixTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(repository='own/repository', run_id='123', run_attempt='2', revision='a'*40,
                            source_tree_sha256='b'*64, recipe={'own': 'recipe'}, goldens={'own': 'goldens'})
        self.toolset = {name: dict(path='/own/'+name, sha256='a'*64, version='own tool 1')
                        for name in ('cc', 'cxx', 'git', 'cmake', 'ninja', 'patch', 'python')}
        for name in ('cc', 'cxx'): self.toolset[name]['implementation'] = dict(path='/own/'+name, sha256='a'*64)
        self.build = dict(cmake_cache_sha256='b'*64, codegen_sha256='c'*64,
                          binaries={name: 'd'*64 for name in ('p6_state_probe', 'p6_headless')})
        self.generated = dict(receipt_sha256='c'*64, files={name: 'e'*64 for name in matrix.generated_names()},
                              compilers={name: dict(sha256='a'*64, version='own tool 1') for name in ('c', 'c++')})

    def observations(self):
        corpora = {}
        for name in ('corpus', 'differential'):
            golden = json.loads((matrix.ROOT/f'tests/portability/{name}.golden.json').read_text())
            corpora[name] = dict(probe_schema=2, cases=len(golden['cases']), variants=['baseline', 'host'],
                                hardware_equal=len(golden['cases']), av_equal=len(golden['cases']),
                                headless=len(matrix.corpus.row_driver.HEADLESS_CASES) if name == 'corpus' else 0,
                                corpus_sha256=golden['corpus']['sha256'])
        return dict(corpora=corpora, clock=dict(offset=matrix.corpus.run_matrix.FAKE_OFFSET, civil_clocks=3, monotonic_unchanged=True))

    def records(self):
        result = [dict(status='PASS', profile=matrix.PROFILE, kind='infrastructure', common=self.context,
                       completed=[dict(selector=name, counts=dict(tests=count, failures=0, errors=0, skipped=0,
                                                                  expected_failures=0, unexpected_successes=0)) for name, count in matrix.INFRA.items()])]
        for row in matrix.native.HOSTED_RUNNERS:
            for opt in matrix.native.VARIANTS:
                result.append(dict(status='PASS', profile=matrix.PROFILE, kind='cell', common=self.context, row=row, optimization=opt,
                                   flags=matrix.native.selection(row, opt)[1], tools=self.toolset, generated=self.generated, build=self.build,
                                   runner=dict(os='Darwin' if row.startswith('macos-') else 'Linux', isa=matrix.native.ROWS[row]['isa']),
                                   observations=self.observations(), source_environment_sha256=f'{len(result):064x}'))
        return copy.deepcopy(result)

    def test_complete_partition_and_current_records_reject_omissions_duplicates_mixing_and_false_counts(self):
        matrix.partition()
        result = matrix.validate(self.records(), self.context)
        self.assertEqual(result['coverage'], dict(original_methods=21, unittest_tests=18, corpus_cells=30, sanitized_clock_rows=5, clock_checks=15))
        faults = ('missing', 'duplicate', 'no-infra', 'extra-infra', 'attempt', 'revision', 'source', 'profile', 'fail', 'skip',
                  'zero', 'wrong-count', 'flags', 'isa', 'tools', 'codegen', 'compiler', 'binary', 'cases', 'clock', 'host', 'corpus',
                  'missing-output', 'extra-output', 'missing-environment', 'bad-environment')
        for fault in faults:
            records = self.records(); infra, cell = records[0], records[1]
            if fault == 'missing': records.pop()
            if fault == 'duplicate': records[-1] = copy.deepcopy(cell)
            if fault == 'no-infra': records.pop(0)
            if fault == 'extra-infra': records.append(copy.deepcopy(infra))
            if fault == 'attempt': cell['common']['run_attempt'] = '1'
            if fault == 'revision': cell['common']['revision'] = 'f'*40
            if fault == 'source': cell['common']['source_tree_sha256'] = 'f'*64
            if fault == 'profile': cell['profile'] = 'local-docker'
            if fault == 'fail': cell['status'] = 'FAIL'
            if fault == 'skip': infra['completed'][0]['counts']['skipped'] = 1
            if fault == 'zero': infra['completed'][0]['counts']['tests'] = 0
            if fault == 'wrong-count': infra['completed'][0]['counts']['tests'] = 6
            if fault == 'flags': cell['flags'] = {}
            if fault == 'isa': cell['runner']['isa'] = 'x86_64'
            if fault == 'tools': cell['tools'].pop('cmake')
            if fault == 'codegen': cell['generated']['files'] = {}
            if fault == 'compiler': cell['generated']['compilers']['c']['sha256'] = 'f'*64
            if fault == 'binary': cell['build']['binaries'].pop('p6_state_probe')
            if fault == 'cases': cell['observations']['corpora']['corpus']['hardware_equal'] -= 1
            if fault == 'clock': cell['observations']['clock']['civil_clocks'] = 2
            if fault == 'host': cell['observations']['corpora']['corpus']['variants'] = ['baseline']
            if fault == 'corpus': cell['observations']['corpora'].pop('differential')
            if fault == 'missing-output': cell['generated']['files'].pop(next(iter(cell['generated']['files'])))
            if fault == 'extra-output': cell['generated']['files']['own-extra.cpp'] = 'f'*64
            if fault == 'missing-environment': del cell['source_environment_sha256']
            if fault == 'bad-environment': cell['source_environment_sha256'] = 'partial'
            with self.subTest(fault=fault), self.assertRaises(RuntimeError): matrix.validate(records, self.context)

    def test_worker_builds_only_probe_headless_and_compares_before_after_every_input(self):
        for drift in ('none', 'common', 'tools', 'source', 'binary', 'generated'):
            with self.subTest(drift=drift), mock.patch.object(matrix.native, 'preflight'), \
                 mock.patch.object(matrix, 'common', side_effect=[self.context, {} if drift == 'common' else self.context]), \
                 mock.patch.object(matrix, 'tools', side_effect=[self.toolset, {} if drift == 'tools' else self.toolset]), \
                 mock.patch.object(matrix.native, 'source_identity', side_effect=['f'*64, ('e' if drift == 'source' else 'f')*64]), \
                 mock.patch.object(matrix.native, 'binary_identity', side_effect=[self.build, {} if drift == 'binary' else self.build]), \
                 mock.patch.object(matrix, 'generated', side_effect=[self.generated, {} if drift == 'generated' else self.generated]), \
                 mock.patch.object(matrix.corpus, 'run', return_value=self.observations()) as run, \
                 mock.patch.object(matrix.native, 'execute') as execute:
                if drift == 'none': self.assertEqual(matrix.cell('linux-arm64-clang', 'san')['source_environment_sha256'], 'f'*64)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'changed'): matrix.cell('linux-arm64-clang', 'san')
                self.assertEqual(execute.call_count, 2)
                argv = execute.call_args.args[0]
                self.assertEqual(argv[argv.index('--target')+1:-2], ['p6_state_probe', 'p6_headless'])
                self.assertIs(run.call_args.kwargs['clock_execute'], matrix.strict_clock)

    def test_clock_stderr_or_failed_startup_cannot_pass_and_asan_preload_is_preserved(self):
        for code, stderr in ((0, ''), (0, 'own sanitizer error'), (1, '')):
            with mock.patch.object(matrix.ci, 'command', return_value=subprocess.CompletedProcess([], code, 'own clocks', stderr)) as execute:
                if code or stderr:
                    with self.assertRaisesRegex(RuntimeError, 'startup'): matrix.strict_clock(['own'], env={'LD_PRELOAD': 'asan:clock'})
                else: self.assertEqual(matrix.strict_clock(['own'], env={'LD_PRELOAD': 'asan:clock'}), 'own clocks')
                self.assertEqual(execute.call_args.kwargs['env']['LD_PRELOAD'], 'asan:clock')
                self.assertEqual(execute.call_args.kwargs['env']['ASAN_OPTIONS'], 'detect_leaks=0:halt_on_error=1')

    def test_infrastructure_runs_all_five_original_selectors_with_strict_counts(self):
        def execute(argv, env):
            self.assertEqual(env, {'P6_TEST_PORTABILITY': '0'})
            count = matrix.INFRA[argv[-1]]
            return subprocess.CompletedProcess(argv, 0, json.dumps(dict(tests=count, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)), '')
        with mock.patch.object(matrix, 'common', return_value=self.context), mock.patch.object(matrix.ci, 'command', side_effect=execute) as call:
            self.assertEqual(len(matrix.infrastructure()['completed']), 5)
            self.assertEqual([c.args[0][-1] for c in call.call_args_list], list(matrix.INFRA))
        with mock.patch.dict(matrix.INFRA, {'own.unclassified': 1}), self.assertRaises(RuntimeError): matrix.partition()

    def test_aggregation_reads_only_current_attempt_logs_and_exactly_one_terminal_per_worker(self):
        records = self.records(); labels = ['matrix-infrastructure'] + [f'{r["row"]} / {r["optimization"]}' for r in records[1:]]
        for fault in ('none', 'failed-job', 'missing-job', 'duplicate-json', 'missing-json', 'wrong-job'):
            jobs = [dict(id=i+1, name=name, status='completed', conclusion='success') for i, name in enumerate(labels)]
            if fault == 'failed-job': jobs[0]['conclusion'] = 'failure'
            if fault == 'missing-job': jobs.pop()
            def execute(argv):
                self.assertEqual(argv[:2], ['gh', 'api'])
                if '/attempts/2/jobs?' in argv[2]: return json.dumps(dict(total_count=len(jobs), jobs=jobs))
                self.fail('unexpected GitHub CLI request')
            def log(repository, job_id):
                self.assertEqual(repository, 'own/repository')
                index = job_id-1; record = records[index]
                if fault == 'wrong-job' and index == 0: record = records[1]
                line = '2026-10-24T00:00:00Z ' + matrix.MARKER + json.dumps(record) + '\n'
                return '' if fault == 'missing-json' else line*2 if fault == 'duplicate-json' else line
            with self.subTest(fault=fault), mock.patch.object(matrix, 'common', return_value=self.context), \
                 mock.patch.object(matrix.native, 'execute', side_effect=execute), mock.patch.object(matrix, 'read_job_log', side_effect=log):
                if fault == 'none': self.assertEqual(matrix.aggregate()['status'], 'PASS')
                else:
                    with self.assertRaises(RuntimeError): matrix.aggregate()

    def test_log_transport_authenticates_only_api_and_sanitizes_bounded_data(self):
        text = b'2026-10-24T00:00:00Z \x1b[32mP6_MATRIX_RESULT {"own":true}\x1b[0m\n\x1b]0;own title\x07\x00\x7f\xc2\x9b'
        headers = Message(); headers['Content-Type'] = 'text/plain; charset=utf-8'; headers['Content-Length'] = str(len(text))
        response = mock.MagicMock(status=200, headers=headers); response.__enter__.return_value = response
        response.read.return_value = text
        opener = mock.Mock(); redirect = logs.urllib.error.HTTPError('https://api.github.com/', 302, 'Found', {'Location': 'https://storage.example/own?signature=data'}, io.BytesIO())
        opener.open.side_effect = [redirect, response]
        with mock.patch.dict(logs.os.environ, {'GH_TOKEN': 'own-token'}), mock.patch.object(logs.urllib.request, 'build_opener', return_value=opener) as factory:
            observed = matrix.read_job_log('own/repo', 1)
            self.assertEqual(observed, '2026-10-24T00:00:00Z P6_MATRIX_RESULT {"own":true}\n')
            api, storage = [call.args[0] for call in opener.open.call_args_list]
            self.assertEqual(api.host, 'api.github.com'); self.assertEqual(api.get_header('Authorization'), 'Bearer own-token')
            self.assertEqual(storage.host, 'storage.example'); self.assertIsNone(storage.get_header('Authorization'))
            self.assertEqual(storage.header_items(), [('User-agent', logs.USER_AGENT), ('Accept-encoding', 'identity')])
            self.assertIsNone(factory.call_args.args[0]().redirect_request(None, None, 302, '', {}, 'https://other.example'))
            response.read.assert_called_once_with(8*1024*1024+1)

    def test_log_reader_loads_by_filepath_without_project_imports_or_path_changes(self):
        script = '''import importlib.util, sys
before = list(sys.path)
spec = importlib.util.spec_from_file_location("own_action_logs", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert before == sys.path
assert not any(n in sys.modules for n in ("tools", "tests", "tools.ci.hosted_matrix"))
assert callable(module.read_job_log)
'''
        process = subprocess.run([matrix.sys.executable, '-I', '-c', script, logs.__file__], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_log_transport_rejects_unsafe_redirect_types_limits_and_invalid_descriptors(self):
        for fault in ('http', 'credentials', 'control', 'redirect-again', 'type', 'length', 'body', 'status', 'token', 'descriptor'):
            headers = Message(); headers['Content-Type'] = 'application/json' if fault == 'type' else 'text/plain'
            if fault == 'length': headers['Content-Length'] = str(8*1024*1024+1)
            response = mock.MagicMock(status=500 if fault == 'status' else 200, headers=headers); response.__enter__.return_value = response
            response.read.return_value = b'x'*(8*1024*1024+1) if fault == 'body' else b'own text'
            location = {'http': 'http://storage.example/', 'credentials': 'https://user:pass@storage.example/', 'control': 'https://storage.example/\n'}.get(fault, 'https://storage.example/')
            redirect = lambda: logs.urllib.error.HTTPError('https://api.github.com/', 302, 'Found', {'Location': location}, io.BytesIO())
            first, second = redirect(), redirect() if fault == 'redirect-again' else response
            self.addCleanup(first.close)
            if fault == 'redirect-again': self.addCleanup(second.close)
            opener = mock.Mock(); opener.open.side_effect = [first, second]
            with self.subTest(fault=fault), mock.patch.dict(logs.os.environ, {'GH_TOKEN': '' if fault == 'token' else 'own-token'}), \
                 mock.patch.object(logs.urllib.request, 'build_opener', return_value=opener), self.assertRaises(RuntimeError):
                matrix.read_job_log('foreign.example/../repo' if fault == 'descriptor' else 'own/repo', 1)

    def test_common_identity_requires_clean_current_actions_revision_and_attempt(self):
        identity = dict(revision='a'*40, dirty=False, source_tree_sha256='b'*64)
        environment = dict(GITHUB_REPOSITORY='own/repo', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', GITHUB_SHA='a'*40)
        with mock.patch.dict(matrix.os.environ, environment, clear=True), mock.patch.object(matrix.ci, 'identity', return_value=identity), \
             mock.patch.object(matrix.corpus, 'recipe_identity', return_value={'own': 'recipe'}):
            self.assertEqual(matrix.common()['run_attempt'], '2')
            for field, value in (('GITHUB_RUN_ATTEMPT', ''), ('GITHUB_SHA', 'c'*40)):
                with mock.patch.dict(matrix.os.environ, {field: value}), self.assertRaises(RuntimeError): matrix.common()
            identity['dirty'] = True
            with self.assertRaisesRegex(RuntimeError, 'clean'): matrix.common()

    def test_tool_implementation_and_generated_inventory_hash_actual_own_bytes(self):
        (matrix.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=matrix.ROOT/'build') as temporary:
            root = Path(temporary); driver = root/'driver'; driver.write_bytes(b'own driver')
            compiler = root/'compiler'; compiler.write_bytes(b'own compiler implementation')
            def execute(argv): return str(compiler) if '--find' in argv else 'own version 1'
            with mock.patch.object(matrix.native, 'compilers', return_value=(str(driver), str(driver))), \
                 mock.patch.object(matrix.shutil, 'which', return_value=str(driver)), \
                 mock.patch.object(matrix.sys, 'executable', str(driver)), \
                 mock.patch.object(matrix.native.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(matrix.native, 'execute', side_effect=execute):
                observed = matrix.tools('macos-arm64-appleclang')
                self.assertEqual(observed['cc']['sha256'], matrix.native.digest(driver))
                self.assertEqual(observed['cc']['implementation']['sha256'], matrix.native.digest(compiler))
            directory = root/'build/generated'; directory.mkdir(parents=True)
            source = directory/'own.cpp'; source.write_bytes(b'own generated bytes')
            other = directory/'other.cpp'; other.write_bytes(b'own second generated output')
            (root/'manifests').mkdir()
            (root/'manifests/codegen.json').write_text(json.dumps(dict(schema_version=1, generators=[dict(outputs=['own.cpp', 'other.cpp'])])))
            files = [dict(path=p.name, sha256=matrix.native.digest(p)) for p in (source, other)]
            receipt = dict(files=files, identity=dict(compilers={}))
            (directory/'codegen-receipt.json').write_text(json.dumps(receipt))
            with mock.patch.object(matrix, 'ROOT', root):
                self.assertEqual(matrix.generated()['files'], {p.name: matrix.native.digest(p) for p in (source, other)})
                for invalid in (files[:-1], files+[dict(path='extra.cpp', sha256='f'*64)], [files[0], files[0]]):
                    (directory/'codegen-receipt.json').write_text(json.dumps(dict(receipt, files=invalid)))
                    with self.subTest(invalid=invalid), mock.patch.object(matrix.native, 'digest') as digest, \
                         self.assertRaisesRegex(RuntimeError, 'codegen inventory'):
                        matrix.generated()
                    digest.assert_not_called()
                (directory/'codegen-receipt.json').write_text(json.dumps(receipt))
                source.write_bytes(b'own altered bytes')
                with self.assertRaisesRegex(RuntimeError, 'generated source drift'): matrix.generated()
