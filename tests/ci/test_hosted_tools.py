"""Tiny data archives and explicit network/subprocess mocks; no tool execution."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare_hosted_tools', ROOT / 'tools/ci/prepare_hosted_tools.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
sha = lambda data: hashlib.sha256(data).hexdigest()


class HostedToolsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='hosted-tools-test-', dir=ROOT / 'build')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ('tools/ci', 'references/ruby-macho/lib', 'plan'):
            (self.root / directory).mkdir(parents=True)
        (self.root / 'tools/ci/normalize_homebrew.rb').write_text('mock normalizer source')
        (self.root / 'references/ruby-macho/lib/macho.rb').write_text('mock Ruby source')
        source = {'id': 'ruby_macho', 'repository': 'test', 'commit': 'a' * 40, 'snapshot': {}}
        capture = {'schema_version': 1, 'source': source, 'destination': 'references/ruby-macho',
                   'files': [{'path': 'lib/macho.rb', 'mode': '100644', 'sha256': sha(b'mock Ruby source')}]}
        (self.root / 'references/ruby-macho.manifest.json').write_text(json.dumps(capture))
        (self.root / 'plan/upstream.lock.json').write_text(json.dumps({'sources': [{**source, 'version': '7.0.0'}]}))
        self.members = [(p, b'data', None) for p in ('tool/1/bin/clang', 'tool/1/bin/lld', 'tool/1/bin/cargo', 'tool/1/bin/rustc',
                        'tool/1/lib/driver', 'tool/1/lib/rustlib/std.rlib', 'tool/1/lib/clang/headers.h', 'tool/1/LICENSE',
                        'tool/1/share/examples/bounded model checking/example.smt2')]
        self.members.append(('tool/1/bin/ld.lld', b'', 'lld'))
        self.blob = self.archive(self.members)
        self.manifest = {'schema_version': 1, 'bottle_arch': 'arm64_tahoe',
            'excluded_symlinks': {},
            'materialized_symlinks': {},
            'normalizer': {'path': 'tools/ci/normalize_homebrew.rb', 'sha256': sha(b'mock normalizer source'),
                           'ruby_source_manifest_sha256': sha((self.root / 'references/ruby-macho.manifest.json').read_bytes())},
            'archives': [{'owner': 'tool', 'slug': 'tool', 'version': '1', 'prefix': 'tool/1', 'sha256': sha(self.blob), 'bytes': len(self.blob),
                          'url': 'https://ghcr.io/v2/homebrew/core/tool/blobs/sha256:' + sha(self.blob), 'notices': ['tool/1/LICENSE']}],
            'normalized_macho': [{'path': p, 'sha256': sha(b'data'), 'bytes': 4} for p, _, link in self.members[:5]],
            'entrypoints': {k: 'tool/1/bin/' + ('clang' if k == 'clang' else 'ld.lld' if k == 'lld' else k) for k in ('clang', 'lld', 'cargo', 'rustc')},
            'aliases': {'tool/1/bin/ld.lld': 'tool/1/bin/lld'}, 'library_directories': ['tool/1/lib']}
        self.write_manifest()
        self.addCleanup(patch.stopall)
        patch.object(h, 'ROOT', self.root).start()
        self.network = patch.object(h.urllib.request, 'build_opener').start()
        self.network.return_value.open.side_effect = lambda request, **kw: io.BytesIO(b'{"token":"secret"}' if isinstance(request, str) else self.blob)
        self.normalizer = patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"status":"PASS"}')).start()

    def archive(self, members, kind=None, mode=0o755):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as stream:
            for name, data, link in members:
                entry = tarfile.TarInfo(name); entry.mode = mode; entry.size = len(data)
                if link:
                    entry.type = tarfile.SYMTYPE; entry.linkname = link
                if kind: entry.type = kind
                stream.addfile(entry, io.BytesIO(data))
        return output.getvalue()

    def write_manifest(self):
        (self.root / 'tools/ci/hosted-tools.json').write_text(json.dumps(self.manifest))

    def test_data_roundtrip_idempotence_and_complete_resources(self):
        output = h.prepare(True, 'mock-ruby')
        self.assertTrue((output / 'tool/1/lib/rustlib/std.rlib').is_file())
        self.assertTrue((output / 'tool/1/lib/clang/headers.h').is_file())
        self.assertEqual((output / 'tool/1/share/examples/bounded model checking/example.smt2').read_bytes(), b'data')
        self.assertFalse((output / 'tool/1/bin/ld.lld').is_symlink())
        self.assertEqual((output / 'tool/1/bin/ld.lld').read_bytes(), b'data')
        self.assertEqual(self.normalizer.call_count, 5)
        self.assertTrue(all(c.args[0][0] == 'mock-ruby' for c in self.normalizer.call_args_list))
        before = (output / 'cache.json').stat()
        self.assertEqual(h.prepare(), output)
        self.assertEqual(before, (output / 'cache.json').stat())
        self.assertEqual(self.network.return_value.open.call_count, 2)
        self.assertEqual(json.loads((output / 'paths.json').read_text())['paths']['make'], '/usr/bin/make')

    def test_explicit_dylib_id_is_propagated_only_for_library_rows(self):
        row = self.manifest['normalized_macho'][-1]
        row['path'] = 'tool/1/lib/driver.dylib'
        row['dylib_id'] = '/opt/homebrew/opt/tool/lib/original.dylib'
        self.members.append((row['path'], b'data', None))
        self.blob = self.archive(self.members)
        archive = self.manifest['archives'][0]
        archive.update(sha256=sha(self.blob), bytes=len(self.blob))
        archive['url'] = 'https://ghcr.io/v2/homebrew/core/tool/blobs/sha256:' + archive['sha256']
        self.write_manifest(); h.prepare(True, 'mock-ruby')
        calls = self.normalizer.call_args_list
        self.assertEqual(5, len(calls))
        for call in calls[:-1]: self.assertEqual(3, len(call.args[0]))
        self.assertEqual(['--dylib-id', row['dylib_id']], calls[-1].args[0][2:4])
        self.assertTrue(calls[-1].args[0][-1].endswith(row['path']))

    def test_invalid_explicit_dylib_ids_and_executable_policy_precede_network(self):
        row = self.manifest['normalized_macho'][-1]
        row['path'] = 'tool/1/lib/driver.dylib'
        for identity in (None, 7, '', '/opt/homebrew/opt/other/lib/driver.dylib',
                         '/opt/homebrew/opt/tool/lib/sub/driver.dylib',
                         '/opt/homebrew/opt/tool/lib/../driver.dylib',
                         '/opt/homebrew/opt/tool/lib/driver.dylib\n'):
            row['dylib_id'] = identity; self.write_manifest()
            with self.assertRaisesRegex(ValueError, 'invalid explicit dylib ID'): h.prepare(True)
        row['path'] = 'tool/1/bin/clang'; row['dylib_id'] = '/opt/homebrew/opt/tool/bin/clang.dylib'
        self.manifest['normalized_macho'].pop(0)  # Avoid duplicate-path failure masking the policy check.
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'invalid explicit dylib ID'): h.prepare(True)
        self.network.assert_not_called(); self.normalizer.assert_not_called()

    def signature_policy_fixture(self):
        row = self.manifest['normalized_macho'][-1]
        row.update(path='zstd/1.5.7_1/lib/libzstd.1.5.7.dylib', dylib_id='/opt/homebrew/opt/zstd/lib/libzstd.1.dylib',
                   signature_policy='preserve-adhoc-layout')
        members = [(row['path'], b'data', None), ('zstd/1.5.7_1/LICENSE', b'own notice', None)]
        blob = self.archive(members)
        self.manifest['archives'].append({'owner': 'zstd', 'slug': 'zstd', 'version': '1.5.7_1', 'prefix': 'zstd/1.5.7_1',
            'sha256': sha(blob), 'bytes': len(blob), 'notices': ['zstd/1.5.7_1/LICENSE'],
            'url': 'https://ghcr.io/v2/homebrew/core/zstd/blobs/sha256:' + sha(blob)})
        self.manifest['library_directories'].append('zstd/1.5.7_1/lib')
        self.write_manifest()
        self.network.return_value.open.side_effect = lambda request, **kw: io.BytesIO(
            b'{"token":"secret"}' if isinstance(request, str) else blob if request.full_url.endswith(sha(blob)) else self.blob)
        return row

    def test_signature_policy_is_forwarded_only_to_explicit_zstd_row(self):
        self.signature_policy_fixture()
        h.prepare(True, 'mock-ruby')
        commands = [call.args[0] for call in self.normalizer.call_args_list]
        self.assertTrue(all('--signature-policy' not in command for command in commands[:-1]))
        self.assertEqual(['--signature-policy', 'preserve-adhoc-layout'], commands[-1][-3:-1])

    def test_signature_policy_metadata_rejects_unknown_and_foreign_rows_before_network(self):
        row = self.manifest['normalized_macho'][-1]
        row['signature_policy'] = 'preserve-adhoc-layout'; self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'invalid signature policy'): h.prepare(True)
        row = self.signature_policy_fixture()
        for policy in ('unknown', None, True):
            row['signature_policy'] = policy; self.write_manifest()
            with self.assertRaisesRegex(ValueError, 'invalid signature policy'): h.prepare(True)
        self.network.assert_not_called(); self.normalizer.assert_not_called()

    def test_asan_source_and_policy_are_checked_before_normalizing_only_its_fixed_path(self):
        row=dict(path=h.ASAN_PATH,sha256=sha(b'data'),bytes=4,dylib_id=h.ASAN_ID,signature_policy='asan-arm64-rpath',source_sha256=sha(b'data'),source_bytes=4)
        self.manifest['normalized_macho'].append(row)
        blob=self.archive([(h.ASAN_PATH,b'data',None),('llvm/23.1.4/LICENSE.TXT',b'own notice',None)])
        self.manifest['archives'].append(dict(owner='llvm',slug='llvm',version='23.1.4',prefix='llvm/23.1.4',sha256=sha(blob),bytes=len(blob),
            url='https://ghcr.io/v2/homebrew/core/llvm/blobs/sha256:'+sha(blob),notices=['llvm/23.1.4/LICENSE.TXT']))
        self.manifest['library_directories'].append('llvm/23.1.4/lib')
        self.network.return_value.open.side_effect=lambda request,**kw: io.BytesIO(b'{"token":"secret"}' if isinstance(request,str) else blob if request.full_url.endswith(sha(blob)) else self.blob)
        with patch.object(h,'ASAN_SOURCE_SHA',sha(b'data')),patch.object(h,'ASAN_SOURCE_BYTES',4):
            for key,bad in (('path',h.ASAN_PATH.replace('/23/','/24/')),('dylib_id','@rpath/foreign.dylib'),('source_sha256','a'*64),('source_bytes',5)):
                old=row[key]; row[key]=bad; self.write_manifest()
                with self.assertRaisesRegex(ValueError,'invalid ASan'): h.prepare(True)
                row[key]=old
            self.network.assert_not_called(); self.normalizer.assert_not_called()
            row['source_sha256']=sha(b'evil'); self.write_manifest()
            with patch.object(h,'ASAN_SOURCE_SHA',sha(b'evil')),self.assertRaisesRegex(ValueError,'ASan source SHA/size mismatch'): h.prepare(True)
            self.assertFalse((self.root/'build/hosted-tools').exists())
            row['source_sha256']=sha(b'data'); self.write_manifest(); self.normalizer.reset_mock()
            output=h.prepare(True,'mock-ruby'); commands=[c.args[0] for c in self.normalizer.call_args_list]
            self.assertEqual(1,sum('asan-arm64-rpath' in c for c in commands))
            self.assertIn('--dylib-id',commands[-1]); self.assertIn(h.ASAN_ID,commands[-1])
            self.assertEqual((output/h.ASAN_PATH).read_bytes(),b'data'); self.assertEqual(h.prepare(),output)

    def test_verify_collects_all_independent_target_mismatches(self):
        output = h.prepare(True)
        rows = self.manifest['normalized_macho']
        for row in rows: (output / row['path']).write_bytes(b'bad')
        (output / rows[-1]['path']).unlink()
        with self.assertRaises(ValueError) as error: h.verify(output, self.manifest)
        message = str(error.exception)
        self.assertEqual(len(rows), message.count('normalized target mismatch:'))
        for row in rows: self.assertIn(row['path'], message)

    def test_invalid_metadata_precedes_network(self):
        original = copy.deepcopy(self.manifest)
        for case in ('url', 'sha', 'bytes', 'prefix', 'normalizer', 'ruby', 'entrypoint', 'excluded_name', 'excluded_target', 'excluded_type',
                     'materialized_name', 'materialized_source', 'materialized_target', 'materialized_type', 'materialized_excluded'):
            with self.subTest(case=case):
                self.manifest = copy.deepcopy(original)
                if case in ('url', 'sha', 'bytes', 'prefix'):
                    self.manifest['archives'][0][{'sha': 'sha256'}.get(case, case)] = -1 if case == 'bytes' else '../bad'
                elif case == 'entrypoint': self.manifest['entrypoints']['cargo'] = '../bad'
                elif case.startswith('excluded_'):
                    self.manifest['excluded_symlinks'] = [] if case == 'excluded_type' else {'../outside' if case == 'excluded_name' else 'tool/1/etc/clang': 42 if case == 'excluded_target' else '../outside'}
                elif case.startswith('materialized_'):
                    name = '../outside' if case == 'materialized_name' else 'tool/1/etc/copy'
                    self.manifest['materialized_symlinks'] = [] if case == 'materialized_type' else {name: {
                        'target': 42 if case == 'materialized_target' else '../external',
                        'source': '../outside' if case == 'materialized_source' else 'tool/1/lib/driver'}}
                    if case == 'materialized_excluded': self.manifest['excluded_symlinks'] = {name: '../external'}
                else: self.manifest['normalizer']['sha256' if case == 'normalizer' else 'ruby_source_manifest_sha256'] = '0' * 64
                self.write_manifest()
                with self.assertRaises((ValueError, KeyError)): h.prepare(True)
        self.network.assert_not_called(); self.normalizer.assert_not_called()

    def test_failed_download_and_normalization_never_publish(self):
        for blob in (b'truncated', self.blob + b'excess', self.blob[:-1] + b'x'):
            self.network.return_value.open.side_effect = [io.BytesIO(b'{"token":"secret"}'), io.BytesIO(blob)]
            with self.assertRaises(ValueError): h.prepare(True)
            self.assertFalse((self.root / 'build/hosted-tools').exists())
        self.network.return_value.open.side_effect = lambda request, **kw: io.BytesIO(b'{"token":"secret"}' if isinstance(request, str) else self.blob)
        self.normalizer.return_value = subprocess.CompletedProcess([], 1, '{"status":"FAIL"}')
        with self.assertRaisesRegex(ValueError, 'data normalization failed: tool/1/bin/clang: .*FAIL'): h.prepare(True)
        self.assertEqual(list((self.root / 'build').iterdir()), [])

    def test_archive_boundaries(self):
        for members in ([('../escape', b'x', None)], [('other/1/file', b'x', None)], self.members * 2,
                        [('tool/1/link', b'', '../../../outside')], [('tool/1/link', b'', '/absolute')]):
            blob = self.archive(members); path = self.root / 'sample.tar.gz'; path.write_bytes(blob)
            row = {**self.manifest['archives'][0], 'sha256': sha(blob), 'bytes': len(blob)}
            with tempfile.TemporaryDirectory(dir=self.root) as target, self.assertRaises(ValueError): h.extract(path, row, Path(target))
        path.write_bytes(self.blob)
        row = self.manifest['archives'][0]
        with tempfile.TemporaryDirectory(dir=self.root) as target, patch.object(h, 'MAX_FILES', 1), self.assertRaises(ValueError): h.extract(path, row, Path(target))
        with tempfile.TemporaryDirectory(dir=self.root) as target, patch.object(h, 'MAX_FILE', 2), self.assertRaises(ValueError): h.extract(path, row, Path(target))

    def test_missing_cache_and_modified_cache_are_not_repaired(self):
        with self.assertRaises(FileNotFoundError): h.prepare()
        output = h.prepare(True)
        extra = output / 'unexpected'; extra.write_bytes(b'extra')
        with self.assertRaises(ValueError): h.prepare(True)
        self.assertEqual(extra.read_bytes(), b'extra'); extra.unlink()
        target = output / 'tool/1/bin/cargo'; target.unlink(); target.symlink_to('clang')
        with self.assertRaises(ValueError): h.prepare()
        self.assertTrue(target.is_symlink())

    def test_tar_types_truncation_and_global_bounds(self):
        blobs = [self.archive(self.members[:1], kind=k) for k in (tarfile.FIFOTYPE, tarfile.CHRTYPE)]
        blobs += [self.archive(self.members[:1], mode=0o4755), self.blob[:-8]]
        path = self.root / 'sample.tar.gz'
        for blob in blobs:
            path.write_bytes(blob)
            row = {**self.manifest['archives'][0], 'sha256': sha(blob), 'bytes': len(blob)}
            with tempfile.TemporaryDirectory(dir=self.root) as target, self.assertRaises((ValueError, EOFError, tarfile.TarError)):
                h.extract(path, row, Path(target))
        path.write_bytes(self.blob)
        for budget, message in (([h.MAX_FILES, 0], 'count limit'), ([0, h.MAX_EXPANDED - 1], 'expanded archive limit')):
            with tempfile.TemporaryDirectory(dir=self.root) as target, self.assertRaisesRegex(ValueError, message):
                h.extract(path, self.manifest['archives'][0], Path(target), budget)
        with tempfile.TemporaryDirectory(dir=self.root) as target, patch.object(h, 'MAX_EXPANDED', 8), self.assertRaisesRegex(ValueError, 'decoded tar size limit'):
            h.extract(path, self.manifest['archives'][0], Path(target))

    def test_cache_repin_cannot_override_independent_target_pin(self):
        output = h.prepare(True)
        (output / 'tool/1/bin/cargo').write_bytes(b'evil')
        receipt_path = output / 'cache.json'
        receipt = json.loads(receipt_path.read_text()); receipt['inventory'] = h.inventory(output, True)
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'normalized target mismatch: tool/1/bin/cargo'): h.prepare()
        self.assertEqual((output / 'tool/1/bin/cargo').read_bytes(), b'evil')

    def test_source_extras_and_input_links_precede_network(self):
        extra = self.root / 'references/ruby-macho/extra'; extra.mkdir()
        with self.assertRaisesRegex(ValueError, 'Ruby capture inventory mismatch'): h.prepare(True)
        extra.rmdir()
        source = self.root / 'tools/ci/normalize_homebrew.rb'; saved = source.with_suffix('.saved')
        source.rename(saved); source.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, 'symlink component'): h.prepare(True)
        self.network.assert_not_called(); self.normalizer.assert_not_called()
        source.unlink(); saved.rename(source)
        output = h.prepare(True); receipt = output / 'cache.json'; saved = output / 'receipt.saved'
        receipt.rename(saved); receipt.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, 'symlink component'): h.prepare()

    def test_excluded_symlink_is_exact_required_and_absent_from_cache(self):
        name, target = 'tool/1/etc/clang', '../../../../etc/clang'
        self.manifest['excluded_symlinks'] = {name: target}
        for case in ('target', 'type', 'missing', 'valid'):
            with self.subTest(case=case):
                members = self.members + [('tool/1/etc/internal', b'', '../lib/driver')]
                if case != 'missing':
                    members += [(name, b'data' if case == 'type' else b'', None if case == 'type' else target + ('x' if case == 'target' else ''))]
                self.blob = self.archive(members)
                row = self.manifest['archives'][0]
                row.update(sha256=sha(self.blob), bytes=len(self.blob))
                row['url'] = 'https://ghcr.io/v2/homebrew/core/tool/blobs/sha256:' + row['sha256']
                self.write_manifest()
                if case != 'valid':
                    with self.assertRaisesRegex(ValueError, 'excluded symlink'): h.prepare(True)
                    self.assertFalse((self.root / 'build/hosted-tools').exists())
                else:
                    output = h.prepare(True)
                    self.assertFalse((output / name).is_symlink())
                    self.assertFalse((output / name).exists())
                    self.assertTrue((output / 'tool/1/etc/internal').is_symlink())
                    self.assertEqual(h.prepare(), output)
        (output / name).symlink_to('../lib/driver')
        receipt_path = output / 'cache.json'
        receipt = json.loads(receipt_path.read_text()); receipt['inventory'] = h.inventory(output, True)
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'excluded symlink present: ' + name): h.prepare()

    def test_materialized_symlink_preserves_captured_resource(self):
        name, target, source = 'tool/1/lib/rustlib/bin/rust-objcopy', '../../../../opt/other/bin/objcopy', 'other/2/bin/objcopy'
        self.manifest['materialized_symlinks'] = {name: {'target': target, 'source': source}}
        other = {'owner': 'other', 'slug': 'other', 'version': '2', 'prefix': 'other/2', 'notices': ['other/2/LICENSE']}
        self.manifest['archives'].append(other)
        self.manifest['library_directories'].append('other/2/lib')
        for case in ('target', 'type', 'missing', 'source_missing', 'valid'):
            with self.subTest(case=case):
                members = list(self.members)
                if case != 'missing':
                    members += [(name, b'data' if case == 'type' else b'', None if case == 'type' else target + ('x' if case == 'target' else ''))]
                self.blob = self.archive(members)
                other_members = [('other/2/LICENSE', b'notice', None), ('other/2/lib/resource', b'resource', None)]
                if case != 'source_missing': other_members += [(source, b'captured resource', None)]
                other_blob = self.archive(other_members, mode=0o751)
                for row, blob in zip(self.manifest['archives'], (self.blob, other_blob)):
                    row.update(sha256=sha(blob), bytes=len(blob))
                    row['url'] = 'https://ghcr.io/v2/homebrew/core/' + row['slug'] + '/blobs/sha256:' + row['sha256']
                self.network.return_value.open.side_effect = lambda request, **kw: io.BytesIO(b'{"token":"secret"}' if isinstance(request, str) else other_blob if request.full_url == other['url'] else self.blob)
                self.write_manifest()
                if case != 'valid':
                    with self.assertRaises((ValueError, FileNotFoundError)): h.prepare(True)
                    self.assertFalse((self.root / 'build/hosted-tools').exists())
                else:
                    output = h.prepare(True)
                    self.assertFalse((output / name).is_symlink())
                    self.assertEqual((output / name).read_bytes(), (output / source).read_bytes())
                    self.assertEqual((output / name).stat().st_mode & 0o777, 0o751)
                    self.assertEqual(h.prepare(), output)
        (output / name).write_bytes(b'changed')
        receipt_path = output / 'cache.json'
        receipt = json.loads(receipt_path.read_text()); receipt['inventory'] = h.inventory(output, True)
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'materialized symlink mismatch: ' + name): h.prepare()
