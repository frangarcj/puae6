"""Standalone Waterbox source/notice policy; no emulator or installed package."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import package, waterbox_package_policy as policy


class WaterboxPolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = json.loads((policy.ROOT / policy.POLICY).read_bytes())
        self.read = lambda p: (policy.ROOT / p).read_bytes()

    def test_composition_preserves_every_native_source_and_notice_and_adds_only_selected_captures(self):
        base = json.loads(self.read(policy.BASE)); combined = policy.compose(self.p)
        self.assertEqual(combined['source_paths'], base['source_paths'] + policy.EXTRA)
        self.assertNotIn('references', combined['source_paths']); self.assertNotIn('build', combined['source_paths'])
        native = package.notice_files(base, self.read); notices = package.notice_files(combined, self.read)
        self.assertEqual({k: notices[k] for k in native}, native)
        self.assertEqual(set(notices) - set(native), {'WATERBOX-NOTICES.txt'})
        self.assertEqual(notices['WATERBOX-NOTICES.txt'], self.read(self.p['notices']['path']))
        self.assertEqual([c['phase'] for c in self.p['components'] if c['kind'] == 'crate'].count('runtime'), 19)
        self.assertEqual([c['phase'] for c in self.p['components'] if c['kind'] == 'crate'].count('build'), 6)
        self.assertEqual(len(self.p['components']), 34)

    def test_unexpected_missing_sources_or_changed_recipe_pins_are_rejected(self):
        for mutate in (lambda p: p['source_additions'].append('references'),
                       lambda p: p['inputs'].pop('plan/toolchains.json'),
                       lambda p: p['inputs'].update({'build/private': '0' * 64}),
                       lambda p: p['excluded_cargo'].update({'anyhow': '1.0.86'})):
            p = copy.deepcopy(self.p); mutate(p)
            with self.assertRaises(ValueError): policy.compose(p)
        changed = lambda path: self.read(path) + (b'\n' if path == self.p['cargo_lock'] else b'')
        with self.assertRaisesRegex(ValueError, 'Changed policy input'): policy.compose(self.p, changed)

    def test_component_coverage_original_license_sets_versions_phases_and_sources_are_exact(self):
        for field, value in (('phase', 'build'), ('version', '0'), ('checksum', '0' * 64), ('source_url', 'https://invalid')):
            p = copy.deepcopy(self.p); p['components'][0][field] = value
            with self.assertRaises(ValueError): policy.compose(p)
        for mode in ('component', 'duplicate', 'notice', 'rust-source'):
            p = copy.deepcopy(self.p)
            if mode == 'component': p['components'].pop()
            if mode == 'duplicate': p['components'].append(copy.deepcopy(p['components'][0]))
            if mode == 'notice': p['components'][0]['notices'].pop()
            if mode == 'rust-source': p['rust_std']['commit'] = '0' * 40
            with self.assertRaises(ValueError): policy.compose(p)

    def test_altered_original_text_fails_even_when_collection_hash_is_updated(self):
        path = self.p['notices']['path']; blob = bytearray(self.read(path))
        n = self.p['components'][0]['notices'][0]; blob[n['offset']] ^= 1
        self.p['notices']['sha256'] = policy.digest(blob)
        with self.assertRaisesRegex(ValueError, 'Changed original notice'):
            policy.compose(self.p, lambda name: bytes(blob) if name == path else self.read(name))
        # Sysroot imports also have independent committed-archive authentication.
        p = json.loads(self.read(policy.POLICY)); blob = bytearray(self.read(path))
        n = next(c for c in p['components'] if c['kind'] == 'sysroot')['notices'][0]
        blob[n['offset']] ^= 1; n['sha256'] = policy.digest(blob[n['offset']:n['offset'] + n['bytes']])
        p['notices']['sha256'] = policy.digest(blob)
        with self.assertRaisesRegex(ValueError, 'Sysroot notice mismatch'):
            policy.compose(p, lambda name: bytes(blob) if name == path else self.read(name))
        for offset, size in ((-1, 1), (0, 0), (len(blob), 1), (0.0, 1)):
            with self.assertRaisesRegex(ValueError, 'bounds'):
                policy.notice_bytes(blob, {'offset': offset, 'bytes': size, 'member': 'own'})

    def test_std_metadata_preserves_all_library_attributions_and_required_license_texts(self):
        c = next(c for c in self.p['components'] if c['kind'] == 'rust-std')
        data = {n['member']: policy.notice_bytes(self.read(self.p['notices']['path']), n) for n in c['notices']}
        metadata = json.loads(data['license-metadata.json'])
        root = metadata['files']['children'][0]
        names = {n['name'] for n in root['children'] if n.get('name', '').startswith('library/')}
        self.assertEqual(names, {'library/core/src/unicode', 'library/backtrace', 'library/std/src/sync/mpmc',
                                 'library/std/src/sys/sync/mutex/fuchsia.rs'})
        for attribution in (b'Alex Crichton', b'Crossbeam', b'Fuchsia', b'Unicode, Inc', b'Rust Project Developers'):
            self.assertIn(attribution, data['license-metadata.json'])
        self.assertEqual(set(self.p['rust_std']['licenses']), {'MIT', 'Apache-2.0', 'BSD-2-Clause', 'Unicode-3.0'})
        p = copy.deepcopy(self.p); p['rust_std']['licenses'].remove('Unicode-3.0')
        with self.assertRaisesRegex(ValueError, 'Std license coverage'): policy.compose(p)

    def test_complete_policy_cli_is_blocked_while_std_external_notices_remain_uncovered(self):
        self.p['rust_std_external_pending'] = {'reason': 'Own fixture lacks an authenticated external std notice'}
        (policy.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='wb-policy-', dir=policy.ROOT / 'build') as temporary:
            fixture = Path(temporary) / 'policy.json'; fixture.write_text(json.dumps(self.p))
            with mock.patch.object(policy, 'POLICY', fixture), mock.patch('sys.argv', ['waterbox_package_policy']), \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(policy.main(), 2)
                self.assertIn('BLOCKED: Rust std external-crate notices remain uncovered', output.getvalue())
        self.p.pop('rust_std_external_pending')
        with self.assertRaisesRegex(ValueError, 'Missing std external coverage declaration'): policy.compose(self.p)

    def test_own_archive_fixtures_authenticate_original_bytes_before_reading_notices(self):
        (policy.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='wb-policy-', dir=policy.ROOT / 'build') as temporary:
            root = Path(temporary); text = b'Own fixture license\r\n'; (root / 'notices').write_bytes(text)
            n = {'member': 'LICENSE', 'offset': 0, 'bytes': len(text), 'sha256': policy.digest(text)}
            crate = root / 'own-1.crate'
            with tarfile.open(crate, 'w') as archive:
                item = tarfile.TarInfo('own-1/LICENSE'); item.size = len(text); archive.addfile(item, io.BytesIO(text))
            nuget = root / 'own.1.nupkg'
            with zipfile.ZipFile(nuget, 'w') as archive: archive.writestr('LICENSE', text)
            import base64
            components = [{'kind': 'crate', 'name': 'own', 'version': '1', 'checksum': policy.digest(crate.read_bytes()), 'notices': [n]},
                          {'kind': 'nuget', 'name': 'own', 'version': '1', 'bytes': nuget.stat().st_size,
                           'archive_sha512_base64': base64.b64encode(hashlib.sha512(nuget.read_bytes()).digest()).decode(), 'notices': [n]}]
            p = {'notices': {'path': 'notices'}, 'components': components}
            with mock.patch.object(policy, 'ROOT', root):
                policy.verify_archives(p, root, root)
                original = crate.read_bytes(); crate.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'Crate archive pin'): policy.verify_archives(p, root, root)
                crate.write_bytes(original); original = nuget.read_bytes(); nuget.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'NuGet archive pin'): policy.verify_archives(p, root, root)
                nuget.write_bytes(original); (root / 'notices').write_bytes(b'X' + text[1:]); n['sha256'] = policy.digest((root / 'notices').read_bytes())
                with self.assertRaisesRegex(ValueError, 'Crate notice mismatch'): policy.verify_archives(p, root, root)

    def test_own_metadata_fixture_separates_normal_dependencies_from_build_and_proc_macro_closures(self):
        packages = [{'id': n, 'name': n, 'version': '1', 'source': None if n == 'root' else 'registry',
                     'targets': [{'kind': ['proc-macro' if n == 'derive' else 'lib']}]} for n in ('root', 'normal', 'derive', 'syntax', 'builder')]
        dep = lambda name, kind=None: {'pkg': name, 'dep_kinds': [{'kind': kind}]}
        nodes = [{'id': 'root', 'deps': [dep('normal'), dep('derive'), dep('builder', 'build')]},
                 {'id': 'derive', 'deps': [dep('syntax')]}] + [{'id': n, 'deps': []} for n in ('normal', 'syntax', 'builder')]
        self.assertEqual(policy.metadata_selection({'packages': packages, 'resolve': {'root': 'root', 'nodes': nodes}}),
                         {('normal', '1'): 'runtime', ('derive', '1'): 'build', ('syntax', '1'): 'build', ('builder', '1'): 'build'})
