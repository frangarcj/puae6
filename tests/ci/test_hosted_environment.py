"""Loader plumbing with mocked cache verification and own executable probes only."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.ci import hosted_environment as h


class HostedEnvironmentTests(unittest.TestCase):
    def setUp(self):
        (h.ROOT / 'build').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=h.ROOT / 'build', prefix="loader ' $-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / 'build/hosted-tools'
        self.cache.mkdir(parents=True)
        self.manifest = {'entrypoints': {n: 'own/bin/' + n for n in ('clang', 'lld', 'cargo', 'rustc')},
                         'library_directories': [f'lib{i}/lib' for i in range(10)]}
        self.paths = {**{n: self.cache / p for n, p in self.manifest['entrypoints'].items()}, 'make': Path('/usr/bin/make')}
        # Own Python probes report argv and loader env; no compiler or bottle runs.
        probe = '#!' + sys.executable + '\nimport json,os,sys\nprint(json.dumps([sys.argv[1:],os.environ.get("DYLD_LIBRARY_PATH")]))\n'
        for n in self.manifest['entrypoints']:
            path = self.paths[n]; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(probe); path.chmod(0o755)
        for rel in self.manifest['library_directories']: (self.cache / rel).mkdir(parents=True)
        self.mapping = self.cache / 'paths.json'
        self.mapping.write_text(json.dumps({'schema_version': 1, 'paths': {k: str(v) for k, v in self.paths.items()}}))
        self.out = self.root / 'build/hosted-tool-launchers'
        self.env = {h.SELECTOR: 'build/hosted-tools', 'PATH': os.environ['PATH'], 'DYLD_LIBRARY_PATH': '/foreign', 'RUSTC': '/foreign/rustc'}
        self.enterContext(mock.patch.object(h, 'ROOT', self.root))
        self.prepare = self.enterContext(mock.patch.object(h.hosted, 'prepare', return_value=self.cache))
        self.inputs = self.enterContext(mock.patch.object(h.hosted, 'inputs', return_value=(self.manifest, {})))

    def test_default_has_no_cache_io_and_preserves_all_environment_values(self):
        env = {k: v for k, v in self.env.items() if k != h.SELECTOR}
        with mock.patch.object(h.hosted, 'regular', side_effect=AssertionError('unexpected filesystem access')):
            result = h.select({}, env)
        self.assertEqual(env, result['direct_env']); self.assertEqual(env, result['tool_env'])
        self.assertEqual('', result['shell_exports']); self.assertEqual({}, result['launchers'])
        self.prepare.assert_not_called(); self.inputs.assert_not_called(); self.assertFalse(self.out.exists())
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(env, h.select({})['direct_env'])
        result['direct_env']['PATH'] = 'changed'; self.assertNotEqual(env, result['direct_env'])
        self.assertEqual(env, result['tool_env'])

    def test_invalid_selectors_cache_failures_and_foreign_paths_never_write(self):
        for value in ('', './build/hosted-tools', 'build/../build/hosted-tools', str(self.cache) + '/', '/foreign'):
            with self.subTest(value=value), self.assertRaises(ValueError): h.select(self.paths, {**self.env, h.SELECTOR: value})
        self.prepare.assert_not_called()
        for error in (FileNotFoundError('missing cache'), ValueError('corrupt cache')):
            self.prepare.side_effect = error
            with self.assertRaises(type(error)): h.select(self.paths, self.env)
        self.prepare.side_effect = None
        self.prepare.return_value = self.root / 'foreign'
        with self.assertRaises(ValueError): h.select(self.paths, self.env)
        self.prepare.return_value = self.cache
        for paths in ({}, {**self.paths, 'make': Path('/foreign/make')}, {**self.paths, 'clang': self.paths['rustc']}):
            with self.assertRaises(ValueError): h.select(paths, self.env)
        data = self.mapping.read_text(); self.mapping.write_text('{}')
        with self.assertRaises(ValueError): h.select(self.paths, self.env)
        self.mapping.write_text(data)
        original = list(self.manifest['library_directories'])
        self.manifest['library_directories'] = original[:1] * 10
        with self.assertRaises(ValueError): h.select(self.paths, self.env)
        self.manifest['library_directories'] = original
        self.assertFalse(self.out.exists())

    def test_environment_exports_script_content_and_reuse_are_exact(self):
        before = dict(self.env)
        for value in ('build/hosted-tools', str(self.cache)):
            result = h.select(self.paths, {**self.env, h.SELECTOR: value})
            self.prepare.assert_called_with(fetch=False)
            loader = os.pathsep.join(str(self.cache / p) for p in self.manifest['library_directories'])
            self.assertEqual(loader, result['direct_env']['DYLD_LIBRARY_PATH'])
            self.assertEqual(self.env['PATH'], result['direct_env']['PATH'])
            self.assertEqual(str(self.out) + os.pathsep + self.env['PATH'], result['tool_env']['PATH'])
            self.assertEqual(str(self.out / 'rustc'), result['tool_env']['RUSTC'])
            self.assertEqual(self.out / 'ld.lld', result['launchers']['lld'])
            self.assertEqual('export DYLD_LIBRARY_PATH=' + shlex.quote(loader) + '\n', result['shell_exports'])
            self.assertEqual(set(h.NAMES), set(p.name for p in self.out.iterdir()))
            times = {p.name: p.stat().st_mtime_ns for p in self.out.iterdir()}
            h.select(self.paths, self.env)
            self.assertEqual(times, {p.name: p.stat().st_mtime_ns for p in self.out.iterdir()})
        self.assertEqual(before, self.env)

    def test_foreign_files_links_modes_and_extra_entries_are_never_overwritten(self):
        self.out.symlink_to(self.cache, target_is_directory=True)
        with self.assertRaises(ValueError): h.select(self.paths, self.env)
        self.out.unlink()
        h.select(self.paths, self.env)
        target = self.out / 'clang'; original = target.read_bytes()
        for change in ('bytes', 'mode', 'link', 'hardlink', 'extra'):
            if change == 'bytes': target.write_bytes(b'foreign')
            elif change == 'mode': target.chmod(0o644)
            elif change == 'link': target.unlink(); target.symlink_to(self.paths['clang'])
            elif change == 'hardlink': target.unlink(); os.link(self.paths['clang'], target)
            else: (self.out / 'foreign').write_bytes(b'keep')
            with self.subTest(change=change), self.assertRaises(ValueError): h.select(self.paths, self.env)
            if change == 'bytes': self.assertEqual(b'foreign', target.read_bytes())
            if target.is_symlink() or change == 'hardlink': target.unlink()
            target.write_bytes(original); target.chmod(0o755)
        self.assertEqual(b'keep', (self.out / 'foreign').read_bytes())

    def test_shell_and_make_reexport_removed_loader_and_preserve_arguments(self):
        result = h.select(self.paths, self.env)
        stripped = dict(result['tool_env']); stripped.pop('DYLD_LIBRARY_PATH')
        args = ['one two', "apostrophe'", '$HOME', ';not-a-command', '', '*.o']
        for name in h.NAMES:
            process = subprocess.run(['/bin/sh', '-c', 'exec "$@"', 'probe', str(self.out / name), *args],
                                     env=stripped, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, process.returncode, process.stderr)
            expected = ['--driver-mode=g++', *args] if name == 'clang++' else args
            self.assertEqual([expected, result['direct_env']['DYLD_LIBRARY_PATH']], json.loads(process.stdout))
        # Real make/shell, without compilation, models SIP discarding inherited DYLD.
        make = shutil.which('make'); self.assertIsNotNone(make)
        script = self.root / 'run-probe.sh'
        script.write_text('#!/bin/sh\nunset DYLD_LIBRARY_PATH\nexec ' + shlex.quote(str(self.out / 'cargo')) + " 'make argument'\n")
        makefile = self.root / 'Makefile'; makefile.write_text('all:\n\t@/bin/sh ' + shlex.quote(str(script)).replace('$', '$$') + '\n')
        process = subprocess.run([make, '-s', '-f', str(makefile)], env=stripped, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual([['make argument'], result['direct_env']['DYLD_LIBRARY_PATH']], json.loads(process.stdout))
        # Contract for insertion immediately after either musl wrapper's shebang.
        script.write_text('#!/bin/sh\n' + result['shell_exports'] + 'exec ' + shlex.quote(str(self.paths['lld'])) + ' "$@"\n')
        process = subprocess.run(['/bin/sh', str(script), *args], env=stripped, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual([args, result['direct_env']['DYLD_LIBRARY_PATH']], json.loads(process.stdout))
