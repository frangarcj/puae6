"""Managed provenance infrastructure, with own outputs and mocked SDK/build calls."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import bench
from tools.ci import managed_release as release


class ManagedReleaseTests(unittest.TestCase):
    def setUp(self):
        (release.ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='managed-release-', dir=release.ROOT / 'build')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        files = {'tests/ffi/global.json': json.dumps({'sdk': {'version': '10.0.402'}}),
                 'tests/ffi/Ffi.csproj': '<Project/>', 'tests/ffi/Own.cs': 'own C# fixture',
                 'Directory.Build.targets': '<Project/>', 'plan/toolchains.json': '{}',
                 'tools/ci/managed-inputs.json': json.dumps({'sdk_version': '10.0.402'})}
        for name, text in files.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        for name in ('tools/bench.py', 'tools/ci/managed_release.py'):
            shutil.copy2(release.ROOT / name, self.root / name)
        self.git('init', '-q'); self.git('add', '.')
        self.outputs = self.root / 'build/ffi/bin/Release/net8.0'; self.outputs.mkdir(parents=True)
        for name in release.OUTPUTS: (self.outputs / name).write_bytes(('own fixture ' + name).encode())
        self.guest = 'a' * 64
        self.sources = [f'-p:RestoreSources={self.root / "build/hosted-managed-feed"}']
        self.path = release.receipt_path(self.root)
        self.guest_path = self.root / 'guest.json'; self.guest_path.write_text(json.dumps({'guest': {'sha256': self.guest}}))

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE)

    def record(self):
        return release.record(release.capture_inputs(self.root), '10.0.402', self.guest, self.sources, root=self.root)

    def verify(self):
        return release.verify_receipt(self.path, self.outputs, self.guest, root=self.root)

    def build(self, action=None, versions=None):
        original = subprocess.check_output
        versions = iter(versions or ('10.0.402', '10.0.402'))
        def command(argv, **kwargs):
            if argv == ['dotnet', '--version']:  # Explicit SDK observation mock, no compiler.
                self.assertEqual(kwargs['cwd'], self.root)  # Observe the SDK from the actual build directory.
                return next(versions)
            return original(argv, **kwargs)
        with mock.patch.object(bench, 'ROOT', self.root), mock.patch.object(bench, 'GUEST_RECEIPT', self.guest_path), \
             mock.patch.object(bench.build_waterbox, 'managed_env', return_value={}), \
             mock.patch.object(bench.build_waterbox, 'managed_restore_sources', return_value=self.sources), \
             mock.patch.object(bench.subprocess, 'check_output', side_effect=command), \
             mock.patch.object(bench, 'sh', side_effect=action) as build:
            result = bench.build_ffi_release()
        build.assert_called_once()
        return result

    def test_current_receipt_is_pure_and_unrelated_metadata_does_not_invalidate_it(self):
        data = self.build()
        (self.root / 'plan/tasks.json').write_text('unrelated task status')
        (self.root / 'tools/package.py').write_text('unrelated packaging Python')
        self.git('add', '.')
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()}
        original_command = subprocess.check_output
        def read_git_only(argv, **kwargs):
            self.assertEqual(argv[0], 'git', 'pure verifier cannot execute the SDK')
            return original_command(argv, **kwargs)
        with mock.patch.object(bench, 'sh', side_effect=AssertionError('verifier must not build')), \
             mock.patch.object(release.subprocess, 'check_output', side_effect=read_git_only), \
             mock.patch('urllib.request.urlopen', side_effect=AssertionError('verifier must not access network')):
            self.assertEqual(self.verify(), data)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(set(data['outputs']), set(release.OUTPUTS))

    def test_changed_csharp_same_guest_new_tracked_source_and_missing_or_linked_inputs_are_rejected(self):
        self.record(); source = self.root / 'tests/ffi/Own.cs'; original = source.read_bytes()
        source.write_bytes(original + b' changed')
        with self.assertRaisesRegex(ValueError, 'source inputs changed'): self.verify()
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'): self.verify()
        target = self.root / 'original'; target.write_bytes(original); source.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Linked'): self.verify()
        source.unlink(); source.write_bytes(original)
        extra = self.root / 'tests/ffi/New.cs'; extra.write_text('new tracked C#'); self.git('add', str(extra))
        with self.assertRaisesRegex(ValueError, 'source inputs changed'): self.verify()

    def test_missing_tampered_outputs_and_receipt_identity_are_rejected(self):
        original = self.record()
        for name in release.OUTPUTS:
            path = self.outputs / name; body = path.read_bytes()
            path.write_bytes(body + b'tampered')
            with self.assertRaisesRegex(ValueError, 'output inventory/hash'): self.verify()
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing'): self.verify()
            path.write_bytes(body)
        for key, value in (('sdk_version', '8.0.0'), ('guest_sha256', 'b' * 64), ('outputs', {}), ('recipe', {})):
            self.path.write_text(json.dumps({**original, key: value}))
            with self.assertRaises(ValueError): self.verify()
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'): self.verify()

    def test_source_drift_during_build_invalidates_old_receipt(self):
        self.record()
        def mutate(*args, **kwargs): (self.root / 'tests/ffi/Own.cs').write_text('changed during mocked build')
        with self.assertRaisesRegex(ValueError, 'changed during build'): self.build(mutate)
        self.assertFalse(self.path.exists())

    def test_failed_build_and_sdk_drift_leave_no_receipt(self):
        self.record()
        def fail(*args, **kwargs): raise subprocess.CalledProcessError(1, 'mocked compiler failure')
        with self.assertRaises(subprocess.CalledProcessError): self.build(fail)
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, 'SDK'): self.build(versions=('10.0.402', '10.0.403'))
        self.assertFalse(self.path.exists())
        self.assertEqual([], list(self.path.parent.glob('.managed-release-*')))
