"""Loader-diagnostic fixtures; subprocesses, compiler and dyld are mocked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.ci import rgb565_loader_probe as probe


class LoaderProbeTests(unittest.TestCase):
    def test_install_name_parser_keeps_only_ids_and_literal_foreign_paths(self):
        text = 'cmd LC_LOAD_DYLIB\n name /unrelated (offset 24)\ncmd LC_ID_DYLIB\n name @@HOMEBREW_PREFIX@@/opt/llvm/runtime (offset 24)\n'
        self.assertEqual(probe.install_names(text), ['@@HOMEBREW_PREFIX@@/opt/llvm/runtime'])

    def test_tiny_compile_is_once_and_loader_failure_keeps_every_observation_without_rgb_claim(self):
        (probe.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='loader-fixture-', dir=probe.ROOT/'build') as temporary:
            root = Path(temporary); resource = root/'build/hosted-tools/llvm/23.1.4/lib/clang/23'
            runtime = resource/'lib/darwin/libclang_rt.asan_osx_dynamic.dylib'
            runtime.parent.mkdir(parents=True); runtime.write_bytes(b'own runtime')
            compiler = resource.parents[2]/'bin/clang-23'; compiler.parent.mkdir(); compiler.write_bytes(b'own compiler')
            metadata = dict(compiler_path=str(compiler), compiler_sha256=probe.evaluate.digest(compiler))
            commands, runs = [], []
            def execute(argv, **kwargs):
                argv = list(map(str, argv)); commands.append((argv, kwargs.get('env')))
                stdout, stderr, code = '', '', 0
                if '-print-resource-dir' in argv: stdout = str(resource)
                elif argv[0] == '/usr/bin/otool':
                    stdout = 'cmd LC_ID_DYLIB\n name /nonexistent/runtime.dylib (offset 24)\n' if '-l' in argv else 'own dependencies'
                elif argv[0] == '/usr/bin/nm': stdout = '00000000 T ___asan_handle_no_return\n'
                elif '-###' in argv: stderr = 'own driver trace with original sanitizer flags'
                elif '-o' in argv: Path(argv[argv.index('-o')+1]).write_bytes(b'own executable')
                else:
                    runs.append(argv); code = -6; stderr = 'dyld Symbol not found: ___asan_handle_no_return'
                return subprocess.CompletedProcess(argv, code, stdout, stderr)
            with patch.object(probe, 'ROOT', root), patch.object(probe.platform, 'system', return_value='Darwin'), \
                 patch.object(probe.platform, 'machine', return_value='arm64'), patch.dict(os.environ, P6_WATERBOX_HOSTED_TOOLS='build/hosted-tools'), \
                 patch.object(probe.evaluate, 'compiler_configuration', return_value=(['own-launcher'], {'DYLD_LIBRARY_PATH': 'own libraries'}, metadata)), \
                 patch.object(probe.evaluate, 'digest', return_value='a'*64), patch.object(probe.subprocess, 'run', side_effect=execute):
                result = probe.probe()
            self.assertEqual(result['status'], 'LOAD_FAIL'); self.assertTrue(result['observation_complete'])
            self.assertIn('no RGB565', result['scope']); self.assertEqual(result['bundled_runtime']['sha256'], 'a'*64)
            self.assertTrue(result['bundled_runtime']['exports']['symbols']['___asan_handle_no_return'])
            self.assertFalse(result['installed_runtime_candidates'][0]['present'])
            self.assertEqual(result['asan_options'], os.environ.get('ASAN_OPTIONS', 'detect_leaks=1'))
            self.assertEqual(len([c for c,e in commands if '-o' in c and '-###' not in c]), 1)
            self.assertEqual(len(runs), 1); self.assertFalse(Path(runs[0][0]).exists())
            self.assertEqual(result['compile_dyld'], {'DYLD_LIBRARY_PATH': 'own libraries'})
            self.assertEqual(result['flags'], probe.evaluate.flags('san'))
            self.assertIn('dyld Symbol', json.loads(probe.ci.redact(json.dumps(result)))['execution']['stderr'])

    def test_command_timeout_is_explicit_and_does_not_imply_success(self):
        with patch.object(probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('own', 90)):
            result = probe.command(['own'])
        self.assertIsNone(result['returncode']); self.assertEqual(result['stderr'], 'diagnostic timeout')
