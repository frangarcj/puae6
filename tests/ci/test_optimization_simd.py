"""Hosted SIMD infrastructure fixtures; never compile or execute a probe."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.simd import evaluate, matrix
from tools.ci import optimization_simd as hosted


def temporary_directory():
    directory = hosted.ROOT / 'build'
    directory.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='simd-fixture-', dir=directory)


def fixture(row, mode):
    arm = matrix.ROWS[row]["isa"] == "arm64"
    return dict(row=row, mode=mode, environment={"isa": "aarch64" if arm else "x86_64"},
                verified_rgb_values=1 << 24, forced_fallback="scalar",
                backend="neon" if arm and mode != "fallback" else "scalar",
                product_backend="neon" if row.startswith("linux-arm64-") and mode != "fallback" else "portable",
                rgb_digest="0123456789abcdef", sources={"fixture": "a" * 64},
                compiler=matrix.PINS[row]["compiler_version"], compiler_sha256="b" * 64,
                flags=evaluate.flags(mode, row), sanitizer_options="detect_leaks=0")


class RecordTests(unittest.TestCase):
    def test_all_fifteen_records_and_every_original_oracle(self):
        for row in matrix.ROWS:
            for mode in ("opt", "fallback", "san"):
                good = fixture(row, mode)
                self.assertEqual(matrix.validate_record(good, row, mode, None, good["sources"]), good["rgb_digest"])
                faults = dict(row="foreign", mode="foreign", environment={"isa": "foreign"}, verified_rgb_values=1,
                              forced_fallback="neon", backend="foreign", product_backend="foreign", rgb_digest="f" * 16,
                              sources={}, compiler="foreign", compiler_sha256="invalid", flags=[], sanitizer_options="detect_leaks=1")
                for key, value in faults.items():
                    with self.subTest(row=row, mode=mode, field=key):
                        bad = {**copy.deepcopy(good), key: value}
                        with self.assertRaises(ValueError):
                            matrix.validate_record(bad, row, mode, good["rgb_digest"], good["sources"])

    def test_flag_variants_keep_strict_math_sanitizers_and_scalar_vectorization(self):
        for row in matrix.ROWS:
            for mode in ("opt", "fallback", "san"):
                flags = evaluate.flags(mode, row)
                self.assertIn("-fno-fast-math", flags); self.assertIn("-ffp-contract=off", flags)
                if mode == "san":
                    self.assertIn("-fsanitize=address,undefined", flags); self.assertIn("-fno-sanitize-recover=all", flags)
                if mode == "fallback":
                    self.assertIn("-DP6_SIMD_NO_NEON=1", flags)
                    self.assertIn("-fno-tree-vectorize" if row.endswith("gcc") else "-fno-vectorize", flags)


class HostedTests(unittest.TestCase):
    common = {'revision': 'fixture', 'run_id': '123', 'run_attempt': '1'}

    def records(self):
        records = []
        for row in matrix.ROWS:
            results = {mode: {**fixture(row, mode), 'sources': hosted.SOURCES, 'binary_sha256': 'e'*64,
                       'compiler_path': '/fixture/clang++', 'driver_mode': 'g++',
                       'compiler_implementation': {'path': '/fixture/actual', 'sha256': 'd'*64},
                       'headers': {'files': 9, 'sha256': 'f'*64}} for mode in hosted.MODES}
            compiler = {key: results['opt'][key] for key in ('compiler', 'compiler_sha256', 'compiler_path', 'driver_mode', 'compiler_implementation')}
            records.append(dict(status='PASS', profile=hosted.PROFILE, kind='simd-row', common=self.common,
                                source_environment_sha256='c'*64, row=row, compiler=compiler, results=results))
        return records

    def standalone_record(self):
        counts = {name: 0 for name in hosted.ci.COUNTS}; counts['tests'] = 3
        compiler = dict(compiler=hosted.LLVM['version'], compiler_sha256=hosted.LLVM['sha256'], driver_mode='g++',
                        compiler_path=str(hosted.ROOT/'build/hosted-tools/llvm/23.1.4/bin/clang-23'),
                        compiler_implementation={'path': str(hosted.ROOT/'build/hosted-tools/llvm/23.1.4/bin/clang-23'), 'sha256': hosted.LLVM['sha256']})
        builds = {mode: {**compiler, 'mode': mode, 'flags': evaluate.flags(mode), 'sources': hosted.SOURCES,
                  'binary_sha256': 'e'*64, 'headers': {'files': 8, 'sha256': 'f'*64}} for mode in hosted.MODES}
        return dict(status='PASS', profile=hosted.PROFILE, kind='simd-standalone', common=self.common,
                    source_environment_sha256='c'*64, selector=hosted.SELECTOR, compiler=compiler, counts=counts, builds=builds)

    def test_complete_rows_reject_missing_duplicate_mixed_context_and_corrupt_observations(self):
        good = self.records()
        self.assertEqual(hosted.validate_rows(good, self.common)['modes'], 15)
        for bad in (good[:-1], good+[good[0]]):
            with self.assertRaises(RuntimeError): hosted.validate_rows(bad, self.common)
        for key, value in (('status', 'FAIL'), ('profile', 'foreign'), ('common', {}), ('source_environment_sha256', 'bad')):
            bad = copy.deepcopy(good); bad[0][key] = value
            with self.assertRaises(RuntimeError): hosted.validate_rows(bad, self.common)
        for key, value in (('rgb_digest', 'f'*16), ('sources', {}), ('binary_sha256', 'bad'), ('compiler_sha256', 'a'*64), ('headers', {})):
            bad = copy.deepcopy(good); bad[0]['results']['san'][key] = value
            with self.assertRaises((RuntimeError, ValueError)): hosted.validate_rows(bad, self.common)

    def test_standalone_requires_three_actual_tests_and_exact_raw_llvm_pin(self):
        good = self.standalone_record()
        self.assertEqual(hosted.validate_standalone(good, self.common)['tests'], 3)
        redacted = json.loads(hosted.ci.redact(json.dumps(good)))
        self.assertTrue(redacted['compiler']['compiler_path'].startswith('@repo/'))
        self.assertEqual(hosted.validate_standalone(redacted, self.common)['tests'], 3)
        for key in hosted.ci.COUNTS:
            bad = copy.deepcopy(good); bad['counts'][key] = 2 if key == 'tests' else 1
            with self.assertRaises(RuntimeError): hosted.validate_standalone(bad, self.common)
        for key, value in (('compiler_sha256', 'a'*64), ('compiler', 'other LLVM'), ('driver_mode', 'gcc')):
            bad = copy.deepcopy(good); bad['compiler'][key] = value
            with self.assertRaises(RuntimeError): hosted.validate_standalone(bad, self.common)
        for value in ({}, {'san': good['builds']['san']}):
            bad = copy.deepcopy(good); bad['builds'] = value
            with self.assertRaises(RuntimeError): hosted.validate_standalone(bad, self.common)
        for key, value in (('headers', {'files': 0, 'sha256': 'f'*64}), ('flags', []), ('sources', {})):
            bad = copy.deepcopy(good); bad['builds']['san'][key] = value
            with self.assertRaises(RuntimeError): hosted.validate_standalone(bad, self.common)

    def test_worker_executes_all_modes_once_cleans_temporary_outputs_and_rejects_drift(self):
        row = next(iter(matrix.ROWS)); expected = self.records()[0]
        calls = []
        def execute(directory, mode, selected_row):
            self.assertEqual(selected_row, row); calls.append((Path(directory), mode))
            return copy.deepcopy(expected['results'][mode])
        with temporary_directory() as temporary, patch.object(hosted, 'ROOT', Path(temporary)), \
             patch.object(hosted.hosted, 'common', return_value=self.common), patch.object(hosted.hosted.native, 'preflight'), \
             patch.object(hosted, 'source_identity', return_value='c'*64), patch.object(evaluate, 'evaluate', side_effect=execute), \
             patch.object(evaluate, 'compiler_configuration', return_value=([], None, expected['compiler'])), \
             patch.object(evaluate, 'dependency_inputs', return_value=expected['results']['opt']['headers']), \
             patch.object(evaluate, 'digest', return_value='e'*64), patch.object(evaluate, 'sources', return_value=hosted.SOURCES):
            self.assertEqual(hosted.row(row)['results'], expected['results'])
            self.assertEqual([mode for _, mode in calls], list(hosted.MODES))
            self.assertTrue(all(not path.exists() for path, _ in calls))
            with patch.object(hosted, 'source_identity', side_effect=['c'*64, 'd'*64]):
                with self.assertRaises(RuntimeError): hosted.row(row)
            with patch.object(evaluate, 'evaluate', side_effect=RuntimeError('fixture failed')):
                with self.assertRaises(RuntimeError): hosted.row(row)
            self.assertEqual(list((Path(temporary)/'build').iterdir()), [])

    def test_standalone_launches_original_selector_and_propagates_subprocess_failure(self):
        record = self.standalone_record()
        text = ''.join(evaluate.BUILD_MARKER + json.dumps(b) + '\n' for b in record['builds'].values()) + json.dumps(record['counts'])
        ok = subprocess.CompletedProcess([], 0, text, '')
        with temporary_directory() as temporary, patch.object(hosted, 'ROOT', Path(temporary)), \
             patch.object(hosted.hosted, 'common', return_value=self.common), patch.object(hosted, 'source_identity', return_value='c'*64), \
             patch.object(evaluate, 'compiler_configuration', return_value=([], None, record['compiler'])), \
             patch.dict(os.environ, P6_WATERBOX_HOSTED_TOOLS='build/hosted-tools'), patch.object(hosted.ci, 'command', return_value=ok) as command:
            self.assertEqual(hosted.standalone()['counts']['tests'], 3)
            self.assertEqual(command.call_args.args[0][-1], hosted.SELECTOR)
            self.assertIn('-B', command.call_args.args[0]); self.assertFalse(Path(command.call_args.kwargs['env']['TMPDIR']).exists())
            command.return_value = subprocess.CompletedProcess([], 0, json.dumps(record['counts']), '')
            with self.assertRaises(RuntimeError): hosted.standalone()
            command.return_value = subprocess.CompletedProcess([], 1, json.dumps(record['counts']), 'fixture failed')
            with self.assertRaises(RuntimeError): hosted.standalone()


class CompilerTests(unittest.TestCase):
    def test_relocation_hashes_raw_clang_but_invokes_authenticated_cpp_launcher(self):
        from tools.ci import hosted_environment as environment
        with temporary_directory() as temporary:
            root = Path(temporary); (root/'plan').mkdir(); raw = root/'build/hosted-tools/llvm/bin/clang-23'
            raw.parent.mkdir(parents=True); raw.write_bytes(b'compiler fixture')
            launcher = root/'cpp-launcher'; launcher.write_bytes(b'launcher must never be hashed as compiler')
            pin = dict(path='/default/clang', version='LLVM fixture', sha256=evaluate.digest(raw))
            (root/'plan/toolchains.json').write_text(json.dumps({'tools': {'clang': pin}}))
            manifest = {'entrypoints': {'clang': 'llvm/bin/clang-23'}}
            selected = dict(launchers={'clang++': launcher}, tool_env={'fixture': 'compile'}, direct_env={'fixture': 'version'})
            with patch.object(evaluate, 'ROOT', root), patch.dict(os.environ, P6_WATERBOX_HOSTED_TOOLS='build/hosted-tools'), \
                 patch.object(environment.hosted, 'inputs', return_value=(manifest, None)), patch.object(environment, 'select', return_value=selected), \
                 patch.object(evaluate.subprocess, 'check_output', return_value='LLVM fixture\n') as version:
                driver, env, metadata = evaluate.compiler_configuration()
                self.assertEqual(driver, [str(launcher)]); self.assertEqual(env, selected['tool_env'])
                self.assertEqual(metadata['compiler_sha256'], evaluate.digest(raw))
                self.assertEqual(metadata['compiler_implementation']['sha256'], evaluate.digest(raw))
                self.assertEqual(version.call_args.args[0], [str(raw), '--driver-mode=g++', '--version'])
                self.assertEqual(version.call_args.kwargs['env'], selected['direct_env'])
                raw.write_bytes(b'changed compiler')
                with self.assertRaises(ValueError): evaluate.compiler_configuration()
                self.assertEqual(version.call_count, 1)

    def test_effective_header_inventory_handles_escaping_and_detects_same_mtime_drift(self):
        with temporary_directory() as temporary:
            root = Path(temporary); header = root/'system header.hpp'; header.write_text('before')
            source = root/'source.cpp'; source.write_text('fixture')
            exe = root/'rgb565'; exe.write_bytes(b'fixture executable')
            depfile = exe.with_suffix('.d')
            depfile.write_text('target: ' + str(source) + ' \\\n ' + str(header).replace(' ', '\\ ') + '\n')
            inputs = dict(headers=evaluate.dependency_inputs(depfile), binary_sha256=evaluate.digest(exe))
            self.assertEqual(inputs['headers']['files'], 2)
            with patch.object(evaluate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'RGB domain differs')):
                self.assertEqual(evaluate.run(exe, '--selftest-fail', build_inputs=inputs).returncode, 2)
            stamp = header.stat().st_mtime_ns
            def mutate(*args, **kwargs):
                header.write_text('after!'); os.utime(header, ns=(stamp, stamp))
                return subprocess.CompletedProcess([], 0, '', '')
            with patch.object(evaluate.subprocess, 'run', side_effect=mutate):
                with self.assertRaisesRegex(ValueError, 'during execution'): evaluate.run(exe, build_inputs=inputs)
            with patch.object(evaluate.subprocess, 'run') as execute:
                with self.assertRaisesRegex(ValueError, 'since compilation'): evaluate.run(exe, build_inputs=inputs)
                execute.assert_not_called()
            depfile.write_text('target: missing-header')
            with self.assertRaises(OSError): evaluate.dependency_inputs(depfile)

    def test_compilation_emits_system_dependencies_without_changing_codegen_flags(self):
        with temporary_directory() as temporary:
            root = Path(temporary); header = root/'sdk.hpp'; header.write_text('fixture SDK')
            compiler = root/'compiler'; compiler.write_bytes(b'fixture compiler')
            metadata = dict(compiler_path=str(compiler), compiler_sha256=evaluate.digest(compiler))
            def compile(command, **kwargs):
                self.assertIn('-MD', command)
                depfile = Path(command[command.index('-MF') + 1]); depfile.write_text('target: ' + str(header))
                Path(command[command.index('-o') + 1]).write_bytes(b'fixture executable')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(evaluate, 'compiler_configuration', return_value=([str(compiler)], None, metadata)), \
                 patch.object(evaluate.subprocess, 'run', side_effect=compile), patch.object(evaluate, 'sources', return_value={'fixture': 'f'*64}):
                _, built = evaluate.build(root, 'san')
                self.assertEqual(built['flags'], evaluate.flags('san'))
                self.assertEqual(built['headers']['files'], 1)
