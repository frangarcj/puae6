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
                        'tool/1/lib/driver', 'tool/1/lib/rustlib/std.rlib', 'tool/1/lib/clang/headers.h', 'tool/1/LICENSE')]
        self.members.append(('tool/1/bin/ld.lld', b'', 'lld'))
        self.blob = self.archive(self.members)
        self.manifest = {'schema_version': 1, 'bottle_arch': 'arm64_tahoe',
            'excluded_symlinks': {},
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
        self.assertFalse((output / 'tool/1/bin/ld.lld').is_symlink())
        self.assertEqual((output / 'tool/1/bin/ld.lld').read_bytes(), b'data')
        self.assertEqual(self.normalizer.call_count, 5)
        self.assertTrue(all(c.args[0][0] == 'mock-ruby' for c in self.normalizer.call_args_list))
        before = (output / 'cache.json').stat()
        self.assertEqual(h.prepare(), output)
        self.assertEqual(before, (output / 'cache.json').stat())
        self.assertEqual(self.network.return_value.open.call_count, 2)
        self.assertEqual(json.loads((output / 'paths.json').read_text())['paths']['make'], '/usr/bin/make')

    def test_invalid_metadata_precedes_network(self):
        original = copy.deepcopy(self.manifest)
        for case in ('url', 'sha', 'bytes', 'prefix', 'normalizer', 'ruby', 'entrypoint', 'excluded_name', 'excluded_target', 'excluded_type'):
            with self.subTest(case=case):
                self.manifest = copy.deepcopy(original)
                if case in ('url', 'sha', 'bytes', 'prefix'):
                    self.manifest['archives'][0][{'sha': 'sha256'}.get(case, case)] = -1 if case == 'bytes' else '../bad'
                elif case == 'entrypoint': self.manifest['entrypoints']['cargo'] = '../bad'
                elif case.startswith('excluded_'):
                    self.manifest['excluded_symlinks'] = [] if case == 'excluded_type' else {'../outside' if case == 'excluded_name' else 'tool/1/etc/clang': 42 if case == 'excluded_target' else '../outside'}
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
