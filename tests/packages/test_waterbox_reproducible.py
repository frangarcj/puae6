"""T40i: own products and mocked builders, plus an explicit real opt-in gate."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import reproduce_waterbox as r


class ReproducibilityTests(unittest.TestCase):
    def setUp(self):
        (r.ROOT / 'build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='wb-repro-test-', dir=r.ROOT / 'build')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'build').mkdir()
        for target, attribute, value in [(r, 'ROOT', self.root), (r.wb, 'BUILD', self.root / 'build/official')]:
            patch = mock.patch.object(target, attribute, value); patch.start(); self.addCleanup(patch.stop)
        self.state = {'revision': 'a' * 40, 'cargo': {}, 'dotnet': 'own-sdk', 'sdk': 'own-sdk', 'tools': {}}
        self.actual_hrtmon = r.verify_hrtmon
        self.actual_preflight = r.preflight
        self.preflight = mock.patch.object(r, 'preflight', return_value=self.state).start()
        self.hrtmon = mock.patch.object(r, 'verify_hrtmon').start()
        self.addCleanup(mock.patch.stopall)

    def fake_build(self, work, suffix, home, before, jobs):
        self.assertFalse((work / 'waterbox').exists())
        self.assertEqual(home, self.root / 'build/official/cargo-home')
        self.assertTrue(all(not list((self.root / ('build/' + n + suffix)).iterdir()) for n in ('ffi', 'bizhawk-core')))
        (work / 'waterbox').mkdir()
        return {n: {'bytes': len(n), 'sha256': r.hashlib.sha256(n.encode()).hexdigest()} for n in r.pkg.BINS}

    def test_two_clean_passes_use_same_paths_complete_inventory_and_remove_outputs(self):
        with mock.patch.object(r, 'build_once', side_effect=self.fake_build) as build:
            result = r.reproduce()
        self.assertEqual(result['status'], 'PASS'); self.assertEqual(result['builds'], 2)
        self.assertEqual(set(result['products']), set(r.pkg.BINS))
        self.assertIsNone(result['archive_payload_matches'])
        self.assertEqual(build.call_args_list[0], build.call_args_list[1])
        self.assertEqual(list((self.root / 'build').iterdir()), [])

    def test_failures_drift_missing_or_changed_product_never_pass_and_clean_up(self):
        for failure in ('build', 'drift', 'missing', 'changed'):
            self.preflight.side_effect = None
            calls = []
            def build(*args):
                products = self.fake_build(*args); calls.append(1)
                if failure == 'build': raise RuntimeError('own build failure')
                if failure == 'missing': products.pop('Ffi.dll')
                if failure == 'changed' and len(calls) == 2: products['Ffi.dll']['sha256'] = 'b' * 64
                return products
            if failure == 'drift': self.preflight.side_effect = [self.state, {'changed': True}]
            with self.subTest(failure=failure), mock.patch.object(r, 'build_once', side_effect=build):
                with self.assertRaises((RuntimeError, ValueError)): r.reproduce()
                self.assertEqual(list((self.root / 'build').iterdir()), [])

    def test_missing_preflight_blocks_before_outputs_and_check_only_does_not_build(self):
        self.preflight.side_effect = FileNotFoundError('own missing input')
        with self.assertRaises(FileNotFoundError): r.reproduce()
        self.hrtmon.assert_not_called()
        self.preflight.side_effect = None
        with mock.patch.object(r, 'build_once') as build:
            self.assertEqual(r.reproduce(check_only=True)['status'], 'READY'); build.assert_not_called()
        self.assertEqual(list((self.root / 'build').iterdir()), [])

    def test_actual_preflight_requires_derived_sysroot_before_outputs_and_tracks_its_drift(self):
        for name in ('build/upstream/own.cpp', 'build/generated/own.cpp', 'build/assets/hrtmon_builtin.cpp',
                     'build/.upstream-materialize.json', 'ports/own.cpp', 'compiler', 'dotnet'):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'own nonexecutable input')
        proof = {'sysroot': str(self.root / 'build/derived/sysroot'), 'inventory_sha256': 'a'*64,
                 'receipt_sha256': 'b'*64, 'inputs': {'base': 'c'*64, 'recipe': 'd'*64}}
        prerequisites = [(r.wb.platform, 'system', 'Darwin'), (r.wb.platform, 'machine', 'arm64'),
            (r.wb, 'runtime_profile', 'release'), (r.pkg.package, 'clean_revision', 'a'*40),
            (r.prepare_managed_inputs, 'prepare', None), (r, 'tree_proof', {'own': 'trees'}),
            (r.wb, 'puae6_manual_inputs', {'own': 'manual'}), (r.managed_release, 'capture_inputs', {'own': 'managed'}),
            (r.wb, 'puae6_sources', ['ports/own.cpp']), (r, 'cargo_inputs', {}),
            (r.wb, 'selected_toolchain', {'sysroot': {'associated_source': 'own-musl'}}),
            (r.wb, 'verify_sysroot', ('own-base', [])), (r.wb, 'source_spec', ({}, {'own': 'capture'})),
            (r.wb, 'source_audit_mode', 'snapshot'),
            (r.wb, 'locked_tools', ({'clang': self.root / 'compiler'}, {'clang': 'own-version'})),
            (r.shutil, 'which', str(self.root / 'dotnet')), (r.subprocess, 'check_output', 'own-sdk\n'),
            (r.managed_release, 'expected_sdk', 'own-sdk')]
        for target, name, value in prerequisites: self.enterContext(mock.patch.object(target, name, return_value=value))
        abi = self.enterContext(mock.patch.object(r.wb, 'puae6_guest_abi', return_value=proof))
        child = self.enterContext(mock.patch.object(r.subprocess, 'run'))
        self.preflight.side_effect = self.actual_preflight
        actual = r.preflight(self.root / 'cache')
        self.assertEqual(actual['inputs']['guest_abi'], proof); abi.assert_called_once_with(self.root)
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(r, 'build_once') as build:
            self.assertEqual(r.reproduce(check_only=True)['status'], 'READY'); build.assert_not_called()
        for error in (FileNotFoundError('missing derived sysroot'), ValueError('stale derived authority')):
            self.hrtmon.reset_mock(); abi.side_effect = error
            with self.subTest(error=error), mock.patch.object(r, 'build_once') as build:
                with self.assertRaisesRegex(type(error), 'derived'): r.reproduce()
                build.assert_not_called(); self.hrtmon.assert_not_called()
            self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        abi.side_effect = [proof, {**proof, 'receipt_sha256': 'e'*64}]
        with mock.patch.object(r, 'build_once', side_effect=self.fake_build) as build:
            with self.assertRaisesRegex(ValueError, 'inputs/environment changed'): r.reproduce()
            self.assertEqual(build.call_count, 1)
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        child.assert_not_called()

    def test_builder_failure_restores_global_and_uses_new_target_and_cargo_archives_only(self):
        original, environment = r.wb.BUILD, dict(os.environ)
        home = self.root / 'cache'; archive = home / 'registry/cache/own/own-1.crate'
        archive.parent.mkdir(parents=True); archive.write_bytes(b'own archive')
        self.state['cargo'] = {archive.relative_to(home).as_posix(): r.pkg.sha(archive)}
        work = self.root / 'work'; work.mkdir()
        def cargo(tc, profile):
            self.assertEqual(profile, 'release'); self.assertFalse((r.wb.BUILD / 'cargo').exists())
            self.assertEqual((r.wb.BUILD / 'cargo-home' / archive.relative_to(home)).read_bytes(), b'own archive')
            raise ValueError('own compiler failure')
        with mock.patch.object(r.wb, 'toolchain', return_value={}), mock.patch.object(r.wb, 'cargo_build_runtime', side_effect=cargo):
            with self.assertRaisesRegex(ValueError, 'own compiler'): r.build_once(work, '-own', home, self.state, 2)
        self.assertEqual(r.wb.BUILD, original); self.assertEqual(dict(os.environ), environment)

    def test_cargo_archives_authenticate_lock_checksums_and_reject_missing_changed_links(self):
        lock = self.root / 'references/machawk/waterbox/waterboxhost/Cargo.lock'; lock.parent.mkdir(parents=True)
        digest = r.hashlib.sha256(b'own crate').hexdigest()
        lock.write_text(f'[[package]]\nname="own"\nversion="1.0.0"\nchecksum="{digest}"\n')
        home = self.root / 'cache'; archive = home / 'registry/cache/own/own-1.0.0.crate'
        archive.parent.mkdir(parents=True); archive.write_bytes(b'own crate')
        index = home / 'registry/index/own/config.json'; index.parent.mkdir(parents=True); index.write_text('{}')
        self.assertEqual(r.cargo_inputs(home)[archive.relative_to(home).as_posix()], digest)
        for mode in ('changed', 'missing', 'link'):
            archive.unlink(missing_ok=True)
            if mode == 'changed': archive.write_bytes(b'other')
            if mode == 'link': archive.symlink_to(index)
            with self.subTest(mode=mode), self.assertRaises((ValueError, FileNotFoundError)): r.cargo_inputs(home)

    def test_fresh_guest_pin_reaches_managed_build_and_mismatched_pin_fails(self):
        work = self.root / 'work'; work.mkdir(); original = r.wb.BUILD
        def artifact(name, data):
            path = r.wb.BUILD / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path
        def guest(*args):
            path = artifact('puae6/release/puae6.wbx', b'own guest')
            return {'guest': {'path': str(path), 'sha256': r.pkg.sha(path)}}
        digest = r.hashlib.sha256(b'own guest').hexdigest()
        def managed(command, env, cwd):
            self.assertIn('-p:Puae6GuestSha256=' + digest, command); self.assertIn('-p:P6ManagedTestSuffix=-own', command)
            self.assertIn('-p:RestoreSources=' + str(self.root / 'build/hosted-managed-feed'), command)
            path = self.root / 'build/ffi-own/bin/Release/net8.0'; path.mkdir(parents=True)
            for name in r.managed_release.OUTPUTS: (path / name).write_bytes(b'own managed')
        with mock.patch.object(r.wb, 'toolchain', return_value={}), mock.patch.object(r.wb, 'cargo_build_runtime', side_effect=lambda *a: artifact('runtime.dylib', b'own runtime')), \
             mock.patch.object(r.wb, 'build_puae6', side_effect=guest), mock.patch.object(r.wb, 'run_logged', side_effect=managed), \
             mock.patch.object(r.subprocess, 'check_output', return_value='pin ' + digest) as pin:
            self.assertEqual(set(r.build_once(work, '-own', self.root, self.state, 2)), set(r.pkg.BINS))
            shutil.rmtree(self.root / 'build/ffi-own'); pin.return_value = 'pin foreign'
            with self.assertRaisesRegex(ValueError, 'managed guest pin'): r.build_once(work, '-own', self.root, self.state, 2)
        self.assertEqual(r.wb.BUILD, original)

    def test_archive_claim_requires_all_eight_product_hashes(self):
        archive = self.root / 'own.zip'
        products = {n: {'bytes': len(n), 'sha256': r.hashlib.sha256(n.encode()).hexdigest()} for n in r.pkg.BINS}
        with zipfile.ZipFile(archive, 'w') as output:
            for name in products: output.writestr('bin/' + name, name)
        self.assertTrue(r.archive_matches(archive, products))
        products['Ffi.dll']['sha256'] = '0' * 64
        self.assertFalse(r.archive_matches(archive, products))
        products['missing.dll'] = products['Ffi.dll']
        with self.assertRaises(KeyError): r.archive_matches(archive, products)

    def test_actual_hrtmon_helper_cache_hit_preserves_bytes_and_mtime(self):
        source = self.root / 'upstream/winuae/hrtmon.rom.cpp'; source.parent.mkdir(parents=True)
        shutil.copyfile(Path(r.__file__).resolve().parents[1] / 'upstream/winuae/hrtmon.rom.cpp', source)
        asset = r.prepare_hrtmon.prepare(self.root); before = (asset.read_bytes(), asset.stat().st_mtime_ns)
        self.assertEqual(r.prepare_hrtmon.prepare(self.root), asset)
        self.assertEqual((asset.read_bytes(), asset.stat().st_mtime_ns), before)
        self.actual_hrtmon(self.root / 'expected')
        self.assertEqual((asset.read_bytes(), asset.stat().st_mtime_ns), before)
        asset.write_bytes(b'own damaged asset'); damaged = asset.stat().st_mtime_ns
        with self.assertRaisesRegex(ValueError, 'HRTMon asset is stale'): self.actual_hrtmon(self.root / 'expected-again')
        self.assertEqual((asset.read_bytes(), asset.stat().st_mtime_ns), (b'own damaged asset', damaged))

    def test_actual_current_tree_cache_hit_preserves_files_receipts_and_mtimes(self):
        from tests.codegen.test_build_trees import CurrentTreesTests, materializer
        fixture = CurrentTreesTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        materializer.materialize(fixture.root); fixture.write_generated()
        files = [p for p in (fixture.root / 'build').rglob('*') if p.is_file()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        with mock.patch.object(r, 'ROOT', fixture.root), mock.patch.object(r.pkg.import_sources, 'verify_source', return_value=fixture.manifest):
            r.tree_proof()
        r.wb.ensure_current_trees(fixture.root)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}, before)
        (fixture.root / 'build/.upstream-materialize.json').write_text('{}')
        with mock.patch.object(r, 'ROOT', fixture.root), mock.patch.object(r.pkg.import_sources, 'verify_source', return_value=fixture.manifest):
            with self.assertRaisesRegex(ValueError, 'proof is stale'): r.tree_proof()


@unittest.skipUnless(os.environ.get('P6_TEST_WATERBOX_REPRODUCIBLE') == '1', 'BLOCKED: opt in to two real clean Waterbox builds')
class RealReproducibilityTests(unittest.TestCase):
    def test_eight_products_from_two_clean_builds_match(self):
        result = r.reproduce(archive=os.environ.get('P6_WATERBOX_PACKAGE_ZIP'))
        print(json.dumps(result, sort_keys=True))
        self.assertEqual(result['status'], 'PASS'); self.assertEqual(set(result['products']), set(r.pkg.BINS))
