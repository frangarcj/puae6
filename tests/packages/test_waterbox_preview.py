"""Own data fixtures exercise packaging; only the opt-in class executes Waterbox."""
import io
import copy
from contextlib import ExitStack
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

    def test_preflight_accepts_only_current_derived_proof_and_guest_wrappers(self):
        from tools.ci import managed_release
        root = self.root; build = root / 'build/waterbox'; release = root / 'Release'; release.mkdir()
        recipes = {'makefile': 'ports/waterbox/Makefile', 'common_mak': 'references/machawk/waterbox/common.mak',
                   'linkscript_aarch64': 'references/machawk/waterbox/linkscript_aarch64.T', 'build_recipe': 'tools/build_waterbox.py'}
        for name in (*recipes.values(), 'own.cpp', 'own.h', 'manifests/source-manifest.json',
                     'build/waterbox/guest-toolchain/bin/musl-clang', 'build/waterbox/guest-toolchain/bin/ld.musl-clang',
                     'build/waterbox/puae6/release/puae6.wbx', 'build/own-runtime.dylib'):
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(('own '+name).encode())
        (root / 'manifests/codegen.json').write_text('{"generators":[{"outputs":["own.cpp"]}]}')
        (root / 'references/machawk.manifest.json').write_text('{"files":[]}')
        lock = {'profile': 'own', 'toolchain_profile': 'local', 'tools': {'clang': {'sha256': 'c'*64, 'version': 'own'}},
                'runtime': {}, 'sysroot': {'inventory_sha256': 'base-sysroot'}}
        proof = {'inventory_sha256': 'derived-sysroot', 'inputs': {'source': 'own authenticated capture'}}
        manual = {'own.h': p.sha(root / 'own.h')}
        inputs = {'profile': 'own', 'toolchain_profile': 'local', 'recipe': {k:p.sha(root/n) for k,n in recipes.items()},
                  'sources': ['own.cpp'], 'tools': {'clang': 'c'*64}, 'versions': {'clang': 'own'},
                  'flags': {'optimization': '-O2', 'target': 'aarch64', 'host_page_size': 16384}, 'sysroot': proof['inventory_sha256'],
                  'guest_abi': proof, 'manual_inputs': manual,
                  'cc_wrapper': p.sha(build/'guest-toolchain/bin/musl-clang'), 'ld_wrapper': p.sha(build/'guest-toolchain/bin/ld.musl-clang')}
        receipt = {'guest': {'sha256': p.sha(build/'puae6/release/puae6.wbx')}, 'sources': {'own.cpp': p.sha(root/'own.cpp')},
                   'manual_inputs': manual, 'guest_abi': proof, 'build_inputs': inputs}
        runtime = {'runtime_profile': 'release', 'runtime_dylib': {'path': str(root/'build/own-runtime.dylib'), 'sha256': p.sha(root/'build/own-runtime.dylib')}}
        generated = {'identity': {'upstream_commit': 'a'*40, 'series': 'b'*64,
                                 'recipe': p.sha(root/'manifests/codegen.json'), 'source_manifest': p.sha(root/'manifests/source-manifest.json')}}
        def write(value): (build/'puae6/puae6-receipt.json').write_text(json.dumps(value))
        with ExitStack() as stack:
            for obj,name,value in ((p,'ROOT',root),(p.wb,'BUILD',build)):
                stack.enter_context(mock.patch.object(obj,name,value))
            for obj,name,value in ((p.platform,'system','Darwin'),(p.platform,'machine','arm64'),(p.wb,'selected_toolchain',lock),
                    (p.wb,'puae6_guest_abi',proof),(p.wb,'read_runtime_receipt',runtime),(p.wb,'inventory',[]),
                    (p.import_sources,'verify_source',{'source':{'commit':'a'*40}}),(p.import_sources,'inventory',[]),
                    (p.materialize,'_series',([], 'b'*64)),(p.materialize,'_expected_receipt',{'files':[], 'upstream_commit':'a'*40}),
                    (p.generate_cpu,'_cached_output',generated),(p.wb,'puae6_sources',['own.cpp']),(p.wb,'puae6_manual_inputs',manual),
                    (managed_release,'verify_receipt',{'inputs':{},'outputs':{},'sdk_version':'own'})):
                stack.enter_context(mock.patch.object(obj,name,return_value=value))
            write(receipt); self.assertEqual(p.preflight(release,root/'managed-receipt')[0],build/'puae6/release/puae6.wbx')
            for change in ('missing', 'top-proof', 'inner-proof', 'base', 'wrapper', 'source'):
                bad = copy.deepcopy(receipt)
                if change=='missing': del bad['guest_abi']
                if change=='top-proof': bad['guest_abi']['inputs']['source']='stale'
                if change=='inner-proof': bad['build_inputs']['guest_abi']={}
                if change=='base': bad['build_inputs']['sysroot']='base-sysroot'
                if change=='wrapper': bad['build_inputs']['cc_wrapper']='base-wrapper'
                if change=='source': bad['sources']['own.cpp']='altered'
                write(bad)
                with self.subTest(change=change), self.assertRaises(ValueError): p.preflight(release,root/'managed-receipt')
            write(receipt)
            p.wb.puae6_guest_abi.side_effect=ValueError('altered derived capture')
            with self.assertRaisesRegex(ValueError,'altered derived capture'): p.preflight(release,root/'managed-receipt')

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
        overlay = 'integration/waterbox-guest/0001-setjmp-x28.patch'; sources[overlay]=b'own source patch'
        sources['tools/build_waterbox_guest_sysroot.py']=b'own derivation recipe'
        sources['plan/toolchains.json']=json.dumps({'waterbox_guest_sysroot':{'patch':{'file':overlay,'sha256':p.policy.digest(sources[overlay])}}}).encode()
        captured=[]
        for name in ('setjmp','longjmp'):
            path='src/setjmp/waterbox_aarch64/'+name+'.s'; sources['references/musl-darwin/'+path]=('own '+name).encode()
            captured.append({'path':path,'mode':'100644','sha256':p.policy.digest(sources['references/musl-darwin/'+path])})
        sources['references/musl-darwin.manifest.json']=json.dumps({'files':captured}).encode()
        def archive_sources():
            with tarfile.open(self.root / 'source.tar.gz', 'w:gz') as archive:
                for n, data in sources.items():
                    info = tarfile.TarInfo('source/' + n); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        archive_sources()
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
            for name in (overlay,'references/musl-darwin/src/setjmp/waterbox_aarch64/setjmp.s'):
                before=sources[name]; sources[name]=b'changed'; archive_sources(); manifest()
                with self.assertRaises(ValueError): p.verify(self.root)
                sources[name]=before
            del sources[overlay]; archive_sources(); manifest()
            with self.assertRaisesRegex(ValueError,'Missing derived'): p.verify(self.root)
            sources[overlay]=b'own source patch'; archive_sources(); manifest()
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
