"""WB01 inventory fixtures; no compiler or Linux guest runs without the opt-in."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.ci import linux_waterbox_probe as probe


class LinuxWaterboxProbeTests(unittest.TestCase):
    def test_wrong_host_rejects_before_tool_or_source_access(self):
        for system, machine, pages in [('Darwin', 'arm64', 16384), ('Linux', 'aarch64', 4096), ('Linux', 'x86_64', 16384)]:
            with mock.patch.object(probe.platform, 'system', return_value=system), mock.patch.object(probe.platform, 'machine', return_value=machine), mock.patch.object(probe.os, 'sysconf', return_value=pages), mock.patch.object(probe, 'tool_identity') as tools, self.assertRaisesRegex(ValueError, 'Linux x86_64'):
                probe.inventory()
            tools.assert_not_called()

    def observe(self, missing=(), environment=None):
        env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu24', 'ImageVersion': 'own-fixture'}
        env.update(environment or {})
        def which(name):
            return '/own/' + name if name not in missing else None
        with mock.patch.object(probe.platform, 'system', return_value='Linux'), mock.patch.object(probe.platform, 'machine', return_value='x86_64'), mock.patch.object(probe.os, 'sysconf', return_value=4096), mock.patch.object(probe.shutil, 'which', side_effect=which), mock.patch.object(probe, 'tool_identity', side_effect=lambda p: {'path': str(p), 'sha256': 'a' * 64}), mock.patch.object(probe, 'rust_identity', return_value={'target': probe.TARGET}), mock.patch.object(probe, 'source_identity', return_value={'own': 'fixture'}), mock.patch.object(probe, 'cargo_cache', return_value={'offline_ready': False}), mock.patch.object(probe, 'sha', return_value='b' * 64), mock.patch.object(probe, 'command', return_value='c' * 40), mock.patch.object(probe.Path, 'exists', return_value=False), mock.patch.dict(os.environ, env, clear=True):
            return probe.inventory()

    def test_versioned_tools_are_observed_without_installing_or_claiming_wb01(self):
        record = self.observe(missing={'ld.lld', 'llvm-ar', 'llvm-objcopy', 'nasm'})
        self.assertEqual(record['status'], 'INVENTORIED')
        self.assertEqual(record['tools']['ld.lld']['path'], '/own/ld.lld-18')
        self.assertEqual(record['missing_tools'], ['nasm'])
        self.assertFalse(record['cargo_cache']['offline_ready'])
        self.assertIn('WB01 not executed', record['scope'])
        self.assertNotIn('PASS', json.dumps(record))
        self.assertNotIn('smoke', record)

    def test_missing_required_or_unverified_runner_never_credits_execution(self):
        for missing, env in [({'cargo'}, {}), (set(), {'RUNNER_ENVIRONMENT': 'self-hosted'}), (set(), {'GITHUB_ACTIONS': 'false'})]:
            record = self.observe(missing=missing, environment=env)
            self.assertEqual(record['status'], 'BLOCKED')
            self.assertTrue(record['errors'])
        with mock.patch.object(probe, 'inventory', return_value={'status': 'BLOCKED'}), mock.patch('sys.argv', ['probe', '--inventory']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(probe.main(), 2)
        self.assertTrue(output.getvalue().startswith(probe.MARKER))

    def test_regular_inputs_and_hashes_detect_changed_bytes(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); path = root / 'own-tool'; path.write_bytes(b'own data, never executed')
            first = probe.file_identity(path)
            path.write_bytes(b'changed data')
            self.assertNotEqual(first['sha256'], probe.file_identity(path)['sha256'])
            with self.assertRaisesRegex(ValueError, 'regular'):
                probe.file_identity(root)

    def test_cargo_cache_requires_exact_locked_archive_checksum(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); lock = root / 'references/machawk/waterbox/waterboxhost/Cargo.lock'
            lock.parent.mkdir(parents=True); payload = b'own archive bytes, never unpacked'
            checksum = hashlib.sha256(payload).hexdigest()
            lock.write_text('[[package]]\nname="own"\nversion="1.2.3"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="' + checksum + '"\n')
            home = root / 'cargo'; cache = home / 'registry/cache/own'; cache.mkdir(parents=True)
            with mock.patch.dict(os.environ, {'CARGO_HOME': str(home)}):
                self.assertFalse(probe.cargo_cache(root)['offline_ready'])
                archive = cache / 'own-1.2.3.crate'; archive.write_bytes(payload)
                self.assertTrue(probe.cargo_cache(root)['offline_ready'])
                archive.write_bytes(b'changed')
                self.assertFalse(probe.cargo_cache(root)['offline_ready'])

    def test_rust_sysroot_requires_real_installed_target_and_std_inventory(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); library = root / 'lib/rustlib' / probe.TARGET / 'lib'
            with mock.patch.object(probe, 'command', return_value=str(root)), mock.patch.object(probe, 'tool_identity', return_value={'sha256': 'a' * 64}):
                with self.assertRaisesRegex(ValueError, 'missing installed'):
                    probe.rust_identity({'path': '/own/rustc'})
                library.mkdir(parents=True); (library / 'libcore-own.rlib').write_bytes(b'own core')
                with self.assertRaisesRegex(ValueError, 'libstd'):
                    probe.rust_identity({'path': '/own/rustc'})
                (library / 'libstd-own.rlib').write_bytes(b'own std')
                first = probe.rust_identity({'path': '/own/rustc'})
                (library / 'libstd-own.rlib').write_bytes(b'changed')
                self.assertNotEqual(first['stdlib_inventory_sha256'], probe.rust_identity({'path': '/own/rustc'})['stdlib_inventory_sha256'])
