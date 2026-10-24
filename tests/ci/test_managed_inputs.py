"""Feed infrastructure: own archive bytes and mocked HTTP; no NuGet restore."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.ci import prepare_managed_inputs as managed

class Response(io.BytesIO):
    status = 200

    def __init__(self, data, url):
        super().__init__(data)
        self.url, self.headers = url, {}

    def geturl(self):
        return self.url

class ManagedInputTests(unittest.TestCase):
    def setUp(self):
        (managed.ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='managed-feed-', dir=managed.ROOT / 'build')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.body = {'fixture.one': b'own fixture archive one', 'fixture.two': b'own fixture archive two'}
        self.data = json.loads((managed.ROOT / 'tools/ci/managed-inputs.json').read_text())
        self.data['packages'] = []
        for name, body in self.body.items():
            self.data['packages'].append(dict(id=name, version='1.0.0', bytes=len(body),
                archive_sha512_base64=base64.b64encode(hashlib.sha512(body).digest()).decode(),
                url=f'https://api.nuget.org/v3-flatcontainer/{name}/1.0.0/{name}.1.0.0.nupkg',
                catalog=f'https://api.nuget.org/v3/catalog0/data/2026.01.01.00.00.00/{name}.1.0.0.json'))
        for name in managed.SOURCE_FILES:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(managed.ROOT / name, path)
        self.metadata = self.root / 'tools/ci/managed-inputs.json'
        self.metadata.parent.mkdir(parents=True)
        self.feed = self.root / 'build/hosted-managed-feed'
        self.save()

    def save(self):
        self.metadata.write_text(json.dumps(self.data))

    def response(self, url, timeout):
        self.assertEqual(timeout, 30)
        return Response(self.body[url.split('/')[-3]], url)

    def test_fetch_verify_and_exact_reuse(self):
        with mock.patch('urllib.request.build_opener', side_effect=AssertionError('implicit network')):
            with self.assertRaises(managed.MissingFeed): managed.prepare(self.root)
        result = managed.prepare(self.root, fetch=True, opener=self.response)
        self.assertEqual((result['packages'], result['bytes']), (2, sum(map(len, self.body.values()))))
        times = {p.name: p.stat().st_mtime_ns for p in self.feed.iterdir()}
        with mock.patch('urllib.request.build_opener', side_effect=AssertionError('unnecessary network')):
            self.assertEqual(result, managed.prepare(self.root, fetch=True))
        self.assertEqual(times, {p.name: p.stat().st_mtime_ns for p in self.feed.iterdir()})
        first = next(self.feed.iterdir()); first.write_bytes(b'changed')
        with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=self.response)
        self.assertEqual(first.read_bytes(), b'changed')

    def test_multiple_versions_of_one_id_are_distinct_but_exact_pairs_are_rejected(self):
        first = self.data['packages'][0]
        body = b'own archive of the second version'
        second = {**first, 'version': '1.1.0', 'bytes': len(body),
                  'archive_sha512_base64': base64.b64encode(hashlib.sha512(body).digest()).decode(),
                  'url': first['url'].replace('1.0.0', '1.1.0'), 'catalog': first['catalog'].replace('1.0.0', '1.1.0')}
        self.data['packages'].append(second); self.save()
        def response(url, timeout):
            return Response(body, url) if '/1.1.0/' in url else self.response(url, timeout)
        result = managed.prepare(self.root, fetch=True, opener=response)
        self.assertEqual(3, result['packages'])
        self.assertEqual(self.body[first['id']], (self.feed / f"{first['id']}.1.0.0.nupkg").read_bytes())
        self.assertEqual(body, (self.feed / f"{first['id']}.1.1.0.nupkg").read_bytes())
        self.assertEqual(result, managed.prepare(self.root, fetch=False))
        for duplicate in (first, second):
            self.data['packages'].append(duplicate.copy()); self.save()
            opener = mock.Mock(side_effect=AssertionError('network before duplicate rejection'))
            with self.assertRaisesRegex(managed.InputError, 'duplicate'): managed.prepare(self.root, fetch=True, opener=opener)
            opener.assert_not_called(); self.data['packages'].pop()

    def test_targeting_policy_and_poison_project_are_pinned_before_network(self):
        self.assertEqual(8, len(managed.SOURCE_FILES))
        for name in ('Directory.Build.targets', 'tests/waterbox-poison/Poison.csproj'):
            source = self.root / name; original = source.read_bytes()
            source.write_bytes(original + b'changed')
            opener = mock.Mock(side_effect=AssertionError('network before input rejection'))
            with self.assertRaisesRegex(managed.InputError, 'differs from pinned'): managed.prepare(self.root, fetch=True, opener=opener)
            opener.assert_not_called(); source.write_bytes(original)
            self.assertFalse(self.feed.exists())

    def test_bad_metadata_rejected_before_network(self):
        original = json.dumps(self.data)
        for case in ('schema', 'sdk', 'framework', 'source_path', 'source_hash', 'extra_field', 'duplicate', 'identity', 'version', 'url', 'catalog', 'hash', 'size', 'total', 'top'):
            with self.subTest(case=case):
                self.data = json.loads(original); item = self.data['packages'][0]
                if case in ('schema', 'sdk', 'framework'):
                    self.data[{'schema':'schema_version', 'sdk':'sdk_version', 'framework':'target_framework'}[case]] = 'invalid'
                if case == 'source_path': self.data['source_files']['../escape'] = '0' * 64
                if case == 'source_hash': self.data['source_files'][next(iter(managed.SOURCE_FILES))] = '0' * 64
                if case == 'extra_field': item['filename'] = '../escape'
                if case == 'duplicate': self.data['packages'].append(item.copy())
                if case == 'identity': item['id'] = '../escape'
                if case == 'version': item['version'] = '../escape'
                if case == 'url': item['url'] += '?redirect=evil'
                if case == 'catalog': item['catalog'] = 'https://evil.invalid/'
                if case == 'hash': item['archive_sha512_base64'] = base64.b64encode(b'wrong length').decode()
                if case == 'size': item['bytes'] = managed.MAX_PACKAGE + 1
                if case == 'top': self.data = []
                if case == 'total':
                    with mock.patch.object(managed, 'MAX_TOTAL', 1):
                        self.save()
                        with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=mock.Mock(side_effect=AssertionError('network')))
                    continue
                self.save()
                with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=mock.Mock(side_effect=AssertionError('network')))
                self.assertFalse(self.feed.exists())

    def test_stream_and_network_failures_leave_no_partial_feed(self):
        for case in ('network', 'short', 'oversize', 'wrong_hash', 'redirect', 'status', 'header'):
            with self.subTest(case=case):
                def response(url, timeout):
                    if 'fixture.two' in url: raise OSError('mocked failed second request')
                    body = self.body['fixture.one']
                    if case == 'short': body = body[:-1]
                    if case == 'oversize': body += b'extra'
                    if case == 'wrong_hash': body = b'x' * len(body)
                    r = Response(body, url)
                    if case == 'redirect': r.url = 'https://evil.invalid/'
                    if case == 'status': r.status = 503
                    if case == 'header': r.headers['Content-Length'] = '999'
                    return r
                with self.assertRaises((OSError, managed.InputError)): managed.prepare(self.root, fetch=True, opener=response)
                self.assertFalse(self.feed.exists())
                self.assertEqual(list((self.root / 'build').iterdir()), [])

    def test_links_and_existing_different_inventory_are_preserved(self):
        (self.root / 'build').mkdir()
        (self.root / 'build').rmdir(); (self.root / 'build').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=self.response)
        (self.root / 'build').unlink(); (self.root / 'build').mkdir()
        self.feed.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=self.response)
        self.feed.unlink(); self.feed.mkdir(); (self.feed / 'foreign').write_bytes(b'keep')
        with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=self.response)
        self.assertEqual((self.feed / 'foreign').read_bytes(), b'keep')

    def test_existing_archive_links_are_rejected(self):
        managed.prepare(self.root, fetch=True, opener=self.response)
        target = next(self.feed.iterdir()); saved = self.root / 'archive'; target.rename(saved)
        for hardlink in (False, True):
            os.link(saved, target) if hardlink else target.symlink_to(saved)
            with self.assertRaises(managed.InputError): managed.prepare(self.root, fetch=True, opener=self.response)
            self.assertTrue(saved.exists()); target.unlink()

    def test_missing_feed_cli_is_blocked_without_fetch(self):
        script = self.root / 'tools/ci/prepare_managed_inputs.py'
        shutil.copy2(Path(managed.__file__), script)
        run = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'BLOCKED')
        self.assertFalse((self.root / 'build').exists())

    def test_release_benchmark_uses_verified_feed_and_rejects_tamper_before_build(self):
        from tools import bench
        wb = bench.build_waterbox
        managed.prepare(self.root, fetch=True, opener=self.response)
        receipt = self.root / 'guest.json'
        receipt.write_text(json.dumps({'guest': {'sha256': 'a' * 64}}))
        with mock.patch.object(bench, 'ROOT', self.root), mock.patch.object(bench, 'GUEST_RECEIPT', receipt), \
             mock.patch.object(wb, 'ROOT', self.root), mock.patch.object(wb, 'managed_env', return_value={}), \
             mock.patch.object(bench, 'sh') as build, \
             mock.patch('urllib.request.build_opener', side_effect=AssertionError('implicit network')):
            for selector in ('build/hosted-managed-feed', str(self.feed)):
                with self.subTest(selector=selector), mock.patch.dict(os.environ, {wb.MANAGED_FEED_ENV: selector}):
                    bench.build_ffi_release()
                    argv = list(map(str, build.call_args.args[0]))
                    self.assertIn(f'-p:RestoreSources={self.feed}', argv)
                    self.assertIn('-p:Puae6GuestSha256=' + 'a' * 64, argv)
                    self.assertIn('Release', argv)
                    self.assertNotIn(f'-p:RestoreSources={Path.home() / ".nuget/packages"}', argv)
                    self.assertEqual(str(self.root / 'build/bizhawk-core/nuget'), build.call_args.kwargs['env']['NUGET_PACKAGES'])
            build.reset_mock()
            for selector in ('build/unverified-feed', 'build/hosted-managed-feed'):
                with self.subTest(rejected=selector), mock.patch.dict(os.environ, {wb.MANAGED_FEED_ENV: selector}):
                    if selector == 'build/hosted-managed-feed':
                        next(self.feed.iterdir()).write_bytes(b'tampered own archive')
                    with self.assertRaises(wb.BuildError):
                        bench.build_ffi_release()
                    build.assert_not_called()

    def test_release_benchmark_keeps_local_cache_default_without_a_feed_selection(self):
        from tools import bench
        wb = bench.build_waterbox
        receipt = self.root / 'guest.json'
        receipt.write_text(json.dumps({'guest': {'sha256': 'b' * 64}}))
        with mock.patch.object(bench, 'GUEST_RECEIPT', receipt), \
             mock.patch.object(wb, 'managed_env', return_value={}), mock.patch.object(bench, 'sh') as build, \
             mock.patch.dict(os.environ, clear=True), \
             mock.patch('urllib.request.build_opener', side_effect=AssertionError('implicit network')):
            bench.build_ffi_release()
        argv = list(map(str, build.call_args.args[0]))
        self.assertEqual([value for value in argv if value.startswith('-p:RestoreSources=')],
                         [f'-p:RestoreSources={Path.home() / ".nuget/packages"}'])
