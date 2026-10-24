"""Infrastructure only; no compiler, emulator, network or commercial ROM runs."""
from pathlib import Path
import copy
import hashlib
import json
import ast
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ci import linux_native as ci, hosted_corpus as corpus_ci


class LinuxNativeTests(unittest.TestCase):
    def test_recipe_keeps_assertions_two_jobs_and_all_real_targets(self):
        configure, build = ci.build_commands()
        self.assertIn("-DCMAKE_BUILD_TYPE=RelWithDebInfo", configure)
        for language in ("C", "CXX"):
            self.assertIn(f"-DCMAKE_{language}_FLAGS_RELWITHDEBINFO=-O2 -g", configure)
        self.assertNotIn("NDEBUG", " ".join(configure))
        self.assertEqual(build[-2:], ["-j", "2"])
        self.assertEqual(tuple(build[build.index("--target") + 1:-2]), ci.TARGETS)

    def test_wrong_runner_or_missing_tool_blocks_before_build(self):
        with mock.patch.object(ci.platform, "system", return_value="Darwin"), self.assertRaisesRegex(ValueError, "Linux"):
            ci.preflight()
        with mock.patch.object(ci.platform, "system", return_value="Linux"), mock.patch.object(ci.platform, "machine", return_value="x86_64"), mock.patch.object(ci.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "preinstalled"):
            ci.preflight()

    def test_all_fifteen_rows_use_their_own_compiler_isa_and_instrumentation(self):
        self.assertEqual(set(ci.HOSTED_RUNNERS), set(ci.ROWS))
        self.assertEqual(len(ci.HOSTED_RUNNERS) * len(ci.VARIANTS), 15)
        for row in ci.HOSTED_RUNNERS:
            for opt in ci.VARIANTS:
                with self.subTest(row=row, optimization=opt):
                    spec, mode = ci.selection(row, opt)
                    configure, build = ci.build_commands(row, opt)
                    self.assertIn('-DCMAKE_TOOLCHAIN_FILE='+str(ci.ROOT/spec['toolchain']), configure)
                    self.assertIn('-DCMAKE_BUILD_TYPE='+mode['build_type'], configure)
                    self.assertIn('-DP6_SANITIZERS='+('ON' if opt=='san' else 'OFF'), configure)
                    self.assertNotIn('NDEBUG', ' '.join(configure))
                    self.assertEqual(build[-2:], ['-j','2'])
                    system='Darwin' if row.startswith('macos-') else 'Linux'
                    isa='aarch64' if system=='Linux' and spec['isa']=='arm64' else spec['isa']
                    with mock.patch.object(ci.platform,'system',return_value=system), mock.patch.object(ci.platform,'machine',return_value=isa), mock.patch.object(ci.shutil,'which',return_value='/tool'):
                        ci.preflight(row,opt)
                    self.assertEqual(ci.compilers(row)[1], '/usr/bin/g++' if row.endswith('-gcc') else '/usr/bin/clang++')
                    self.assertEqual(ci.core_name(row), 'puae6_libretro'+('.dylib' if system=='Darwin' else '.so'))
        for row,opt in (('windows-x86_64','O2'),(ci.DEFAULT_ROW,'O3')):
            with self.assertRaisesRegex(ValueError,'unknown'):
                ci.build_commands(row,opt)

    def test_wrong_architecture_is_not_a_cross_build(self):
        with mock.patch.object(ci.platform,'system',return_value='Linux'), mock.patch.object(ci.platform,'machine',return_value='x86_64'), self.assertRaisesRegex(ValueError,'arm64'):
            ci.preflight('linux-arm64-clang','O0')

    def test_failed_process_or_protocol_violation_cannot_pass(self):
        for code, output in ((1, ""), (-9, "timeout"), (0, "VIOLATION")):
            with mock.patch.object(ci, "command", return_value=subprocess.CompletedProcess([], code, output, "")), self.assertRaises(RuntimeError):
                ci.execute(["unused"])
        for output in ("", "run 1 ticks=1\nrun 1 ticks=2", "run 1 malformed"):
            with self.assertRaises(RuntimeError):
                ci.rows(output, "run", 2 if "run 1 ticks" in output else 1)

    def test_native_future_requires_complete_hardware_video_and_pcm(self):
        def text(unit):
            return "\n".join([f"digest {unit} {name} {'a'*64} 4" for name in (*ci.HARDWARE, "video")]
                             + [f"chip {unit} {name} {'b'*64}" for name in (*ci.CHIP_GROUPS,"registers")]
                             + [f"pcm {unit} {'c'*64} 882", f"picture {unit} none"])
        self.assertEqual(ci.native_future(text(5), 5, 1), ci.native_future(text(1), 1, 1))
        for bad in ("", text(1).replace("digest 1 cpu", "ignored 1 cpu"), text(1).replace("pcm 1", "ignored 1")):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                ci.native_future(bad, 1, 1)

    def test_restore_excludes_only_the_existing_noncausal_debug_shadow(self):
        source=ast.parse((ci.ROOT/'tests/state/test_det02.py').read_text())
        groups=next(node.value for node in source.body if isinstance(node,ast.Assign)
                    and any(isinstance(target,ast.Name) and target.id=='CHIP_GROUPS' for target in node.targets))
        self.assertEqual(ci.CHIP_GROUPS,ast.literal_eval(groups))
        original='\n'.join([f'digest 1 {name} {"a"*64} 4' for name in (*ci.HARDWARE,'video')]
                           + [f'chip 1 {name} {"b"*64}' for name in (*ci.CHIP_GROUPS,'registers')]
                           + [f'pcm 1 {"c"*64} 882','picture 1 none'])
        changed=original.replace(f'digest 1 chipset {"a"*64}',f'digest 1 chipset {"f"*64}').replace(f'chip 1 registers {"b"*64}',f'chip 1 registers {"f"*64}')
        self.assertEqual(ci.native_future(original,1,1),ci.native_future(changed,1,1))
        changed=original.replace(f'chip 1 irq_dma {"b"*64}',f'chip 1 irq_dma {"f"*64}')
        self.assertNotEqual(ci.native_future(original,1,1),ci.native_future(changed,1,1))
        for bad in (original.replace('chip 1 irq_dma','unknown 1 irq_dma'),original+f'\nchip 1 beam {"b"*64}'):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):
                ci.native_future(bad,1,1)

    def test_cold_native_must_load_at_zero_and_reemit_fullstates(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, data in (("cut.state", b"cut"), ("final.state", b"final"), ("again.state", b"cut"), ("restored.state", b"different")):
                (work / name).write_bytes(data)
            with mock.patch.object(ci, "execute", side_effect=["", "load 0 0\n"]) as execute, self.assertRaisesRegex(RuntimeError, "fullstate"):
                ci.check_native(work, work / "public.rom")
            args = execute.call_args_list[1].args[0]
            self.assertEqual(args[args.index("--public-load-at") + 1], "0")

    def test_manual_workflow_has_no_cache_upload_or_extra_action(self):
        workflow = (ci.ROOT / ".github/workflows/native-linux.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("runs-on: ${{ matrix.platform.runner }}", workflow)
        self.assertEqual(dict(re.findall(r"- row: (\S+)\n\s+runner: (\S+)",workflow)),ci.HOSTED_RUNNERS)
        variants=re.search(r"optimization: \[([^\]]+)\]",workflow)
        self.assertEqual(tuple(v.strip() for v in variants[1].split(',')),ci.VARIANTS)
        self.assertIn('fail-fast: false',workflow)
        self.assertIn('max-parallel: 4',workflow)
        self.assertIn('options: [all, san]',workflow)
        self.assertIn('options: [native, corpus]',workflow)
        self.assertIn("--gate '${{ inputs.gate }}'",workflow)
        self.assertIn('default: all',workflow)
        self.assertIn('inputs.variants == \'san\' && \'[{"optimization":"O0"},{"optimization":"O2"}]\'',workflow)
        self.assertIn("--row '${{ matrix.platform.row }}' --opt '${{ matrix.optimization }}'",workflow)
        self.assertIn("timeout-minutes: 45", workflow)
        self.assertEqual(workflow.count("uses:"), 1)
        self.assertIn("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683", workflow)
        self.assertNotRegex(workflow, r"upload-artifact|actions/cache|secrets\.|pull_request:|schedule:|continue-on-error")


class HostedCorpusTests(unittest.TestCase):
    def test_local_and_hosted_cells_share_exact_schema_cases_and_both_digests(self):
        from tests.portability import test_portability as local
        (ci.ROOT/'build').mkdir(exist_ok=True)
        for fault in ('none', 'schema', 'missing', 'extra-host', 'error', 'hardware', 'av'):
            expected, base, host = self.records()
            if fault == 'schema': host['probe_schema'] = True
            if fault == 'missing': base['cases'] = {}
            if fault == 'extra-host': host['cases']['extra'] = host['cases']['one']
            if fault == 'error': base['errors'] = {'one': 'own failure'}
            if fault == 'hardware': base['cases']['one']['hardware'] = 'd'*64
            if fault == 'av': host['cases']['one']['av'] = 'd'*64
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=ci.ROOT/'build') as directory:
                root = Path(directory); folder = root/'build/portability/own-row'; folder.mkdir(parents=True)
                for name, data in (('baseline', base), ('host', host), ('record', {'faketime': {'shifted': True}})):
                    (folder/f'O2-{name}.json').write_text(json.dumps(data))
                with mock.patch.object(local, 'ROOT', root), mock.patch.object(local, 'golden', return_value=expected):
                    if fault == 'none':
                        local.check_matrix_result(self, '', 'own-row', 'O2')
                        corpus_ci.compare(expected, base, host, headless=False)
                    else:
                        with self.assertRaises(AssertionError): local.check_matrix_result(self, '', 'own-row', 'O2')
                        with self.assertRaises(RuntimeError): corpus_ci.compare(expected, base, host, headless=False)

    @staticmethod
    def records(headless=False):
        case = dict(probe_schema=2, hardware='a'*64, av='b'*64, units=1)
        expected = dict(probe_schema=2, cases={'one': case})
        record = dict(probe_schema=2, errors={}, cases={'one': dict(case, lines=19, per_unit={'0': ['a'*16, 'b'*16]})},
                      headless={name: 'c'*64 for name, _, _ in corpus_ci.row_driver.HEADLESS_CASES} if headless else {})
        return expected, dict(record, variant='baseline'), dict(copy.deepcopy(record), variant='host')

    def test_complete_existing_oracle_rejects_missing_extra_bad_hash_units_and_host_desync(self):
        wanted, base, host = self.records(True)
        self.assertEqual(corpus_ci.compare(wanted, base, host, headless=True)['cases'], 1)
        for fault in ('missing', 'extra', 'error', 'hash', 'baseline', 'host', 'units', 'per_unit', 'headless', 'headless_hash', 'headless_host'):
            expected, baseline, shifted = copy.deepcopy((wanted, base, host))
            if fault == 'missing': del baseline['cases']['one']
            if fault == 'extra': baseline['cases']['extra'] = baseline['cases']['one']
            if fault == 'error': shifted['errors'] = {'one': 'timeout'}
            if fault == 'hash': baseline['cases']['one']['hardware'] = 'bad'
            if fault == 'baseline': baseline['cases']['one']['hardware'] = 'd'*64
            if fault == 'host': shifted['cases']['one']['av'] = 'd'*64
            if fault == 'units': shifted['cases']['one']['units'] = 0
            if fault == 'per_unit': shifted['cases']['one']['per_unit'] = {}
            if fault == 'headless': shifted['headless'] = {}
            if fault == 'headless_hash': shifted['headless'][next(iter(shifted['headless']))] = 'error'
            if fault == 'headless_host': shifted['headless'][next(iter(shifted['headless']))] = 'd'*64
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                corpus_ci.compare(expected, baseline, shifted, headless=True)
        with self.assertRaisesRegex(RuntimeError, 'empty'):
            corpus_ci.compare({'cases': {}}, {}, {}, headless=False)

    def test_clock_requires_all_three_real_shifts_and_an_unchanged_monotonic(self):
        base = 'time=100 gettimeofday=100 clock_gettime=100 monotonic=10'
        shifted = ' '.join(f'{k}={100+corpus_ci.run_matrix.FAKE_OFFSET}' for k in ('time', 'gettimeofday', 'clock_gettime'))+' monotonic=11'
        self.assertEqual(corpus_ci.clocks(base, shifted)['civil_clocks'], 3)
        for bad in (base, shifted.replace('monotonic=11', 'monotonic=99'), shifted.replace('gettimeofday=', 'unknown='), shifted+' time=1'):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError): corpus_ci.clocks(base, bad)

    def test_both_corpora_and_variants_keep_all_cases_clock_preload_two_jobs_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'tests/portability').mkdir(parents=True)
            files = {'one.rom': b'public', 'cases.json': b'[{"id":"one"}]'}
            checksum = hashlib.sha256()
            for name, data in sorted(files.items()): checksum.update(name.encode()+b'\0'+hashlib.sha256(data).digest())
            def writer(directory):
                directory.mkdir()
                for name, data in files.items(): (directory/name).write_bytes(data)
                return [{'id': 'one'}]
            for name in ('corpus', 'differential'):
                expected = self.records()[0]
                expected.update(schema_version=1, corpus=dict(cases=1, sha256=checksum.hexdigest()))
                (root/f'tests/portability/{name}.golden.json').write_text(json.dumps(expected))
            observed = []
            def execute(argv, timeout=180, env=None):
                argv = list(map(str, argv)); observed.append((argv, env))
                if '--out' in argv:
                    wanted, base, host = self.records('--headless' in argv)
                    Path(argv[argv.index('--out')+1]).write_text(json.dumps(host if '--preload' in argv else base))
                    self.assertEqual(argv[argv.index('--jobs')+1], '2'); self.assertNotIn('--group', argv)
                    self.assertEqual(timeout, 1800)
                if argv[0].endswith('clock-check'):
                    value = 100+(corpus_ci.run_matrix.FAKE_OFFSET if env else 0)
                    return f'time={value} gettimeofday={value} clock_gettime={value} monotonic=10'
                return ''
            with mock.patch.object(corpus_ci, 'ROOT', root), mock.patch.dict(corpus_ci.corpus.CORPORA, {'': writer, 'differential': writer}, clear=True), \
                 mock.patch.object(corpus_ci.row_driver, 'preload_environment', return_value={'LD_PRELOAD': 'real-runtime:clock'}) as preload:
                result = corpus_ci.run(root, '/usr/bin/gcc', dict(flags='', sanitizers=True), execute)
                self.assertEqual(set(result['corpora']), {'corpus', 'differential'})
                self.assertEqual(len([argv for argv, _ in observed if '--out' in argv]), 4)
                self.assertTrue(all(argv[0]=='/usr/bin/gcc' for argv, _ in observed if '-o' in argv))
                self.assertTrue(any('-fsanitize=address,undefined' in argv for argv, _ in observed))
                preload.assert_called_once()
                self.assertFalse(list(root.glob('public-corpus-*')))
                with self.assertRaisesRegex(RuntimeError, 'fixture failed'):
                    corpus_ci.run(root, '/usr/bin/gcc', dict(flags='', sanitizers=True), mock.Mock(side_effect=RuntimeError('fixture failed')))
                self.assertFalse(list(root.glob('public-corpus-*')))

    def test_corpus_gate_builds_once_without_repeating_native_and_rejects_changed_identity(self):
        for changed in ('none', 'recipe', 'source', 'binary'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(ci, 'ROOT', Path(directory)), mock.patch.object(ci, 'preflight'), \
                 mock.patch.object(ci, 'source_identity', side_effect=['a', 'b' if changed=='source' else 'a']), \
                 mock.patch.object(ci, 'digest', return_value='d'), mock.patch.object(ci, 'execute', return_value='revision') as execute, \
                 mock.patch.object(ci, 'binary_identity', side_effect=[{'binary': 'a'}, {'binary': 'b' if changed=='binary' else 'a'}]), \
                 mock.patch.object(ci, 'check_native') as native, mock.patch.object(ci, 'check_frontends') as frontend, \
                 mock.patch.object(corpus_ci, 'recipe_identity', side_effect=[{'recipe': 'a'}, {'recipe': 'b' if changed=='recipe' else 'a'}]), \
                 mock.patch.object(corpus_ci, 'run', return_value={'complete': True}) as run:
                if changed=='none':
                    result = ci.run(opt='san', gate='corpus')
                    self.assertEqual(result['corpus'], {'complete': True})
                    self.assertEqual(result['leak_detection'], 'off in existing corpus row_driver')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'changed during'): ci.run(gate='corpus')
                native.assert_not_called(); frontend.assert_not_called(); run.assert_called_once()
                build = execute.call_args_list[1].args[0]
                self.assertEqual(build[build.index('--target')+1:-2], ['p6_state_probe', 'p6_headless'])
        with self.assertRaisesRegex(ValueError, 'unknown hosted gate'): ci.run(gate='missing')

    def test_recipe_identity_tracks_python_fixtures_and_golden_and_requires_clean_revision(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(corpus_ci, 'ROOT', Path(directory)), \
             mock.patch.object(corpus_ci.package, 'clean_revision', return_value='revision') as clean:
            path = Path(directory)/'fixture.py'; path.write_bytes(b'original')
            execute = mock.Mock(return_value='fixture.py\0')
            before = corpus_ci.recipe_identity(execute)
            path.write_bytes(b'modified')
            self.assertNotEqual(before, corpus_ci.recipe_identity(execute))
            clean.side_effect = ValueError('dirty sources')
            with self.assertRaisesRegex(ValueError, 'dirty'): corpus_ci.recipe_identity(execute)


    def test_probe_schema_header_is_required_once_and_not_part_of_any_digest(self):
        driver = corpus_ci.row_driver
        text = 'digest 0 cpu '+'a'*64+' 4\n'+'pcm 0 '+'b'*64+' 882\n'
        valid = subprocess.CompletedProcess([], 0, 'probe-schema 2\n'+text, '')
        with mock.patch.object(driver.subprocess, 'run', return_value=valid):
            record = driver.run_case('probe', {'args': []}, 'rom', {}, '.', 1)
        self.assertEqual(record['probe_schema'], 2)
        self.assertEqual(record['hardware'], hashlib.sha256(text.splitlines()[0].encode()).hexdigest())
        for header in ('', 'probe-schema 1\n', 'probe-schema 3\n', 'probe-schema 2\nprobe-schema 2\n', 'probe-schema 2 extra\n'):
            with self.subTest(header=header), mock.patch.object(driver.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, header+text, '')):
                self.assertIn('observation schema', driver.run_case('probe', {'args': []}, 'rom', {}, '.', 1)['error'])

    def test_old_missing_and_mixed_schema_metadata_cannot_pass_complete_hashes(self):
        for target in ('golden', 'record', 'golden-case', 'case'):
            for schema in (None, 1, 3, "2", 2.0):
                expected, baseline, host = self.records()
                item = {'golden': expected, 'record': baseline, 'golden-case': expected['cases']['one'], 'case': host['cases']['one']}[target]
                if schema is None: del item['probe_schema']
                else: item['probe_schema'] = schema
                with self.subTest(target=target, schema=schema), self.assertRaisesRegex(RuntimeError, 'observation schema'):
                    corpus_ci.compare(expected, baseline, host, headless=False)
