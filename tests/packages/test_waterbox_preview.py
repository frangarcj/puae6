"""Own data fixtures exercise packaging; only the opt-in class executes Waterbox."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
from tools import package_waterbox as p


class PolicyTests(unittest.TestCase):
    def setUp(self):
        (p.ROOT / 'build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='wb-package-fixture-', dir=p.ROOT / 'build'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_preflight_rejects_nonrelease_and_linked_inputs_before_loading_tools(self):
        with mock.patch.object(p.platform, 'system', return_value='Darwin'), mock.patch.object(p.platform, 'machine', return_value='arm64'), mock.patch.object(p.wb, 'selected_toolchain') as tools:
            with self.assertRaisesRegex(ValueError, 'Release'): p.preflight(self.root / 'Debug', self.root / 'receipt')
            release = self.root / 'Release'; release.mkdir(); receipt = self.root / 'receipt'; receipt.symlink_to('/dev/null')
            with self.assertRaises(ValueError): p.preflight(release, receipt)
            tools.assert_not_called()

    def test_managed_closure_pin_versions_paths_and_framework(self):
        stage = self.root / 'bin'; stage.mkdir()
        versions = {'Ffi': '1.0.0', 'Puae6.Core': '1.0.0', 'CommunityToolkit.HighPerformance': '8.4.0', 'Newtonsoft.Json': '13.0.3'}
        graph = {'Ffi': {'Puae6.Core': '1.0.0'}, 'Puae6.Core': {n: versions[n] for n in list(versions)[2:]}}
        targets = {n + '/' + v: {'runtime': {n + '.dll': {}}, **({'dependencies': graph[n]} if n in graph else {})} for n, v in versions.items()}
        deps = {'targets': {'.NETCoreApp,Version=v8.0': targets}, 'runtimeTarget': {'name': '.NETCoreApp,Version=v8.0'},
                'compilationOptions': {}, 'libraries': {k: {'type': 'project' if k.split('/')[0] in graph else 'package'} for k in targets}}
        config = {'runtimeOptions': {'tfm': 'net8.0', 'rollForward': 'Major', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '8.0.0'}}}
        for n in versions: (stage / (n + '.dll')).write_bytes(b'own data')
        def write():
            (stage / 'Ffi.deps.json').write_text(json.dumps(deps)); (stage / 'Ffi.runtimeconfig.json').write_text(json.dumps(config))
        with mock.patch.object(p, 'ffi', return_value='pin ' + 'a' * 64):
            write(); p.managed(stage, 'a' * 64)
            with self.assertRaisesRegex(ValueError, 'pin'): p.managed(stage, 'b' * 64)
            for key, value in [('dependencies', {'Puae6.Core': 'foreign'}), ('runtime', {'../Ffi.dll': {}})]:
                old = targets['Ffi/1.0.0'][key]; targets['Ffi/1.0.0'][key] = value; write()
                with self.assertRaises(ValueError): p.managed(stage, 'a' * 64)
                targets['Ffi/1.0.0'][key] = old
            config['runtimeOptions']['additionalProbingPaths'] = ['/foreign']; write()
            with self.assertRaisesRegex(ValueError, 'probing'): p.managed(stage, 'a' * 64)

    def test_exact_payload_and_original_notices_even_with_rehashed_manifest(self):
        for n in ('bin', 'config', 'licenses'): (self.root / n).mkdir()
        sources = {'manifests/waterbox-memory.json': b'{}', 'docs/user/waterbox-preview.md': b'own guide', 'notice': b'own notice', 'tests/ffi/global.json': b'{"sdk":{"version":"own"}}', 'references/machawk.manifest.json': b'{"files":[]}',
                   **{'tests/fixtures/microrom/' + n: b'own fixture' for n in ('build_rom.py', 'boot.s')}}
        with tarfile.open(self.root / 'source.tar.gz', 'w:gz') as archive:
            for n, data in sources.items():
                info = tarfile.TarInfo('source/' + n); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        for n in p.BINS: (self.root / 'bin' / n).write_bytes(b'own ' + n.encode())
        for name, data in [('config/waterbox-memory.json', b'{}'), ('LEEME.md', b'own guide'), ('licenses/N.txt', b'own notice')]: (self.root / name).write_bytes(data)
        m = {'schema_version': 1, 'kind': 'waterbox-standalone-preview', 'paths': p.PATHS, 'platform': 'macos-arm64', 'revision': 'a' * 40,
             'guest_sha256': 'a' * 64, 'runtime_sha256': 'b' * 64, 'guest_profile': {'optimization': '-O2', 'target': 'aarch64', 'host_page_size': 16384}, 'managed_sdk_version': 'own',
             'managed_configuration': 'Release', 'binary_reproducibility_verified': False, 'managed_inputs': {'notice': p.policy.digest(b'own notice')},
             'guest_manual_inputs': {'notice': p.policy.digest(b'own notice')},
             'managed_outputs': {n: {'bytes': (self.root / 'bin' / n).stat().st_size, 'sha256': p.sha(self.root / 'bin' / n)} for n in p.BINS[:6]}}
        def manifest():
            m['files'] = {n.relative_to(self.root).as_posix(): p.sha(n) for n in self.root.rglob('*') if n.is_file() and n.name != 'manifest.json'}
            (self.root / 'manifest.json').write_text(json.dumps(m))
        composed = {'rust_std_external_complete': True, 'rust_std_external_pending': {}, 'source_paths': list(sources), 'licenses': {'N.txt': 'notice'}}
        with mock.patch.object(p.policy, 'compose', return_value=composed), mock.patch.object(p, 'binaries') as binary:
            manifest(); p.verify(self.root); binary.assert_called_once()
            m['guest_manual_inputs']['notice'] = '0' * 64; manifest()
            with self.assertRaisesRegex(ValueError, 'Build source'): p.verify(self.root)
            m['guest_manual_inputs']['notice'] = p.policy.digest(b'own notice'); manifest()
            for name, change in [('bin/extra.dll', b'extra'), ('bin/Ffi.dll', b'corrupt'), ('licenses/N.txt', b'changed')]:
                path = self.root / name; before = path.read_bytes() if path.exists() else None; path.write_bytes(change)
                if name.endswith('N.txt'): manifest()
                with self.assertRaises(ValueError): p.verify(self.root)
                if before is None: path.unlink()
                else: path.write_bytes(before)
                manifest()
            (self.root / 'bin/puae6.wbx').unlink()
            with self.assertRaises(ValueError): p.verify(self.root)

    def test_archive_rejects_escape_links_and_duplicates_before_extracting(self):
        for names, mode in [(['../outside'], 0o100644), (['bin/link'], 0o120777), (['same', 'same'], 0o100644)]:
            path = self.root / 'fixture.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in names:
                    info = zipfile.ZipInfo(name); info.external_attr = mode << 16; archive.writestr(info, b'own data')
            with self.assertRaises(ValueError): p.unpack(path, self.root / 'extract')
            self.assertFalse((self.root / 'outside').exists())

    def test_only_exact_captured_source_links_are_accepted_without_extraction(self):
        link = tarfile.TarInfo('source/references/machawk/ExternalProjects/Own/build_debug.sh'); link.type = tarfile.SYMTYPE; link.linkname = '../.build_debug.sh'
        reference = {'files': [{'path': link.name.removeprefix('source/references/machawk/'), 'mode': '120000', 'sha256': p.policy.digest(link.linkname.encode())}]}
        read = lambda _: json.dumps(reference).encode()
        p.source_links([link], read)
        for name, target in [(link.name, 'foreign'), ('source/extra', link.linkname), (link.name, '../../../../outside')]:
            changed = tarfile.TarInfo(name); changed.type = tarfile.SYMTYPE; changed.linkname = target
            with self.assertRaises(ValueError): p.source_links([changed], read)
        with self.assertRaises(ValueError): p.source_links([], read)


@unittest.skipUnless(os.environ.get('P6_WATERBOX_PACKAGE_ZIP'), 'P6_WATERBOX_PACKAGE_ZIP requires the actual standalone preview')
class InstalledPreviewTests(unittest.TestCase):
    def test_extracted_release_restores_in_same_and_new_process_and_rejects_foreign_config(self):
        with tempfile.TemporaryDirectory(prefix='p6-wb-package-test-') as directory:
            p.unpack(os.environ['P6_WATERBOX_PACKAGE_ZIP'], directory); result = p.smoke(directory)
        self.assertEqual(result, {'same_sandbox_units': 4, 'new_process_units': 4, 'foreign_config_unchanged': True})
