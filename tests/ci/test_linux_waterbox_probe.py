"""WB01 inventory fixtures; no compiler or Linux guest runs without the opt-in."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import struct
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


class LinuxWaterboxPreparationTests(unittest.TestCase):
    def fixture(self, root):
        inputs = {'authority': {'tools': {n: {'path': '/own/' + n} for n in ('cargo', 'rustc', 'clang', 'ar', 'make', 'patch', 'lld')}}}
        runtime = root / 'references/machawk/waterbox/waterboxhost'; runtime.mkdir(parents=True)
        (runtime / 'Cargo.toml').write_text('own source fixture')
        header = root / 'references/musl-darwin/arch/waterbox/atomic_arch.h'; header.parent.mkdir(parents=True)
        header.write_bytes(b'\t*p++;\n\t*p--;\n')
        patch = root / 'references/machawk/waterbox/musl-waterbox-x86_64-atomics.patch'; patch.write_bytes(b'own patch fixture')
        return inputs

    def elf(self, kind=3, machine=62):
        header = bytearray(64); header[:7] = b'\x7fELF\x02\x01\x01'; struct.pack_into('<HH', header, 16, kind, machine)
        return header

    @unittest.skipUnless(probe.fcntl is not None, 'POSIX preparation lock required')
    def test_preparation_is_isolated_offline_after_fetch_and_cache_is_fail_closed(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); before = self.fixture(root); stages = []; commands = []
            def execute(argv, cwd, env):
                commands.append(list(map(str, argv))); stage = Path(env['HOME']).parent; stages.append(stage)
                self.assertEqual(env['RUSTC_BOOTSTRAP'], '1'); self.assertEqual(env['RUSTC'], '/own/rustc')
                self.assertTrue(Path(env['CARGO_HOME']).is_relative_to(stage))
                self.assertNotIn('RUSTUP_TOOLCHAIN', env); self.assertNotIn('RUSTC_WRAPPER', env)
                if argv[1] == 'build':
                    output = Path(env['CARGO_TARGET_DIR']) / probe.TARGET / 'release/libwaterboxhost.so'
                    output.parent.mkdir(parents=True); output.write_bytes(self.elf())
                if argv[0] == '/own/patch':
                    header = cwd / 'arch/waterbox/atomic_arch.h'; header.write_bytes(b'\t(*p)++;\n\t(*p)--;\n')
                if argv[0] == '/own/make' and 'install' in argv:
                    output = stage / 'install' / str(root / probe.OUTPUT / 'sysroot').lstrip('/')
                    for name, data in [('lib/libc.a', b'!<arch>\n'), ('lib/crt1.o', self.elf(1)), ('include/pthread.h', b'own header')]:
                        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            with mock.patch.object(probe, 'pinned_inputs', return_value=before), mock.patch.object(probe, 'cargo_cache', return_value={'offline_ready': True}), mock.patch.object(probe, 'execute', side_effect=execute) as child:
                result = probe.prepare(root)
                self.assertEqual((result['status'], result['scope']), ('PREPARED', 'runtime_and_musl_only'))
                self.assertEqual(set(p.name for p in (root / probe.OUTPUT).iterdir()), {'runtime.so', 'sysroot', 'receipt.json'})
                self.assertTrue(all(not p.exists() for p in stages))
                self.assertIn(['/own/cargo', 'fetch', '--locked'], commands)
                self.assertIn(['/own/cargo', 'build', '--offline', '--locked', '--release', '--target', probe.TARGET, '-j2'], commands)
                receipt = root / probe.OUTPUT / 'receipt.json'; stamp = receipt.stat().st_mtime_ns
                child.reset_mock(); self.assertEqual(result, probe.prepare(root)); child.assert_not_called()
                self.assertEqual(stamp, receipt.stat().st_mtime_ns)
                (root / probe.OUTPUT / 'runtime.so').write_bytes(self.elf(machine=183))
                with self.assertRaisesRegex(ValueError, 'ELF'):
                    probe.prepare(root)
                child.assert_not_called()

    @unittest.skipUnless(probe.fcntl is not None, 'POSIX preparation lock required')
    def test_failure_or_changed_sources_never_publish_partial_outputs(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); before = self.fixture(root)
            with mock.patch.object(probe, 'pinned_inputs', return_value=before), mock.patch.object(probe, 'execute', side_effect=ValueError('network failure')):
                with self.assertRaisesRegex(ValueError, 'network failure'):
                    probe.prepare(root)
            self.assertFalse((root / probe.OUTPUT).exists())
            self.assertFalse(any((root / 'build').glob('linux-wb01-*')))
            with mock.patch.object(probe, 'pinned_inputs', side_effect=ValueError('unpinned stdlib')), mock.patch.object(probe, 'execute') as child:
                with self.assertRaisesRegex(ValueError, 'unpinned stdlib'):
                    probe.prepare(root)
                child.assert_not_called()

    def test_null_stdlib_and_wrong_runner_fail_before_tool_execution(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); path = root / 'plan/extensions/waterbox-platforms.json'; path.parent.mkdir(parents=True)
            pin = {'runner': {'os': 'Linux', 'isa': 'x86_64', 'page_bytes': 4096, 'image_os': 'own', 'image_version': '1'}, 'rust_stdlib_inventory_sha256': None}
            path.write_text(json.dumps({'linux_wb01_inputs': pin}))
            with mock.patch.object(probe.platform, 'system', return_value='Linux'), mock.patch.object(probe.platform, 'machine', return_value='x86_64'), mock.patch.object(probe.os, 'sysconf', return_value=4096), mock.patch.object(probe.Path, 'exists', return_value=False), mock.patch.dict(os.environ, {'ImageOS': 'own', 'ImageVersion': '1', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64'}), mock.patch.object(probe, 'tool_identity') as tool:
                with self.assertRaisesRegex(ValueError, 'not pinned'):
                    probe.pinned_inputs(root)
                with mock.patch.dict(os.environ, {'ImageVersion': '2'}), self.assertRaisesRegex(ValueError, 'runner identity'):
                    probe.pinned_inputs(root)
                tool.assert_not_called()

    def test_preflight_rejects_bad_tool_bytes_before_executing_it_and_stdlib_drift(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); path = root / 'plan/extensions/waterbox-platforms.json'; path.parent.mkdir(parents=True)
            tools = {}
            for name in ('clang', 'lld', 'ar', 'make', 'patch', 'rustc', 'cargo'):
                tool = root / name; tool.write_bytes(('own ' + name).encode())
                tools[name] = {'path': str(tool), 'sha256': probe.sha(tool), 'version': 'own version'}
            pin = {'runner': {'os': 'Linux', 'isa': 'x86_64', 'page_bytes': 4096, 'image_os': 'own', 'image_version': '1'},
                   'tools': tools, 'rust_stdlib_inventory_sha256': 'a' * 64}
            path.write_text(json.dumps({'linux_wb01_inputs': pin}))
            with mock.patch.object(probe.platform, 'system', return_value='Linux'), mock.patch.object(probe.platform, 'machine', return_value='x86_64'), mock.patch.object(probe.os, 'sysconf', return_value=4096), mock.patch.object(probe.Path, 'exists', return_value=False), mock.patch.dict(os.environ, {'ImageOS': 'own', 'ImageVersion': '1', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64'}), mock.patch.object(probe, 'tool_identity', side_effect=lambda p: {**probe.file_identity(p), 'version': 'own version'}) as child, mock.patch.object(probe, 'rust_identity', return_value={'stdlib_inventory_sha256': 'a' * 64}) as rust, mock.patch.object(probe, 'source_identity', return_value={'own': 'source'}):
                first = probe.pinned_inputs(root); self.assertEqual(first['sources'], {'own': 'source'})
                rust.return_value = {'stdlib_inventory_sha256': 'b' * 64}
                with self.assertRaisesRegex(ValueError, 'standard library inventory differs'):
                    probe.pinned_inputs(root)
                child.reset_mock(); (root / 'clang').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'tool bytes differ'):
                    probe.pinned_inputs(root)
                child.assert_not_called()

    def test_foreign_symlink_output_and_truncated_or_other_isa_elf_are_rejected(self):
        (probe.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=probe.ROOT / 'build') as tmp:
            root = Path(tmp); (root / 'build').mkdir(); (root / 'elsewhere').mkdir()
            output = root / probe.OUTPUT; output.symlink_to(root / 'elsewhere', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                probe.verify(output, root)
            binary = root / 'own-elf'
            for data in (b'short', self.elf(machine=183), self.elf(kind=2)):
                binary.write_bytes(data)
                with self.assertRaisesRegex(ValueError, 'ELF'):
                    probe.elf(binary, 3)


@unittest.skipUnless(os.environ.get('P6_LINUX_WATERBOX_PREPARE') == '1', 'requires pinned native Linux x86_64 build inputs')
class LinuxWaterboxBuildTests(unittest.TestCase):
    def test_real_runtime_and_musl_are_source_derived_and_authenticated(self):
        result = probe.prepare()
        self.assertEqual((result['status'], result['scope']), ('PREPARED', 'runtime_and_musl_only'))
        self.assertEqual(result, probe.verify())
        self.assertEqual(64, len(result['runtime_sha256']))
        self.assertEqual(64, len(result['sysroot_inventory_sha256']))
        print('P6_LINUX_WATERBOX_PREPARATION ' + json.dumps(result, sort_keys=True))
