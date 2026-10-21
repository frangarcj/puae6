"""Exact runtime patch inputs and the Darwin CPU-register regression."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox as wb


class RuntimePatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='runtime-patch-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'pristine'; self.source.mkdir()
        self.before, self.after = b'old\n', b'new\n'
        (self.source / 'test.rs').write_bytes(self.before)
        self.manifest = self.root / 'manifest.json'
        entries = wb.inventory(self.source)
        self.manifest.write_text(json.dumps({'files': [{**e, 'path': 'waterbox/waterboxhost/' + e['path']} for e in entries]}))
        self.patch = self.root / 'integration/waterbox-runtime/test.patch'
        self.patch.parent.mkdir(parents=True)
        self.patch.write_text('diff --git a/test.rs b/test.rs\n--- a/test.rs\n+++ b/test.rs\n@@ -1 +1 @@\n-old\n+new\n')
        sha = lambda b: hashlib.sha256(b).hexdigest()
        self.spec = {'file': 'integration/waterbox-runtime/test.patch', 'sha256': wb.sha256(self.patch),
                     'files': [{'path': 'test.rs', 'before_sha256': sha(self.before), 'after_sha256': sha(self.after)}]}
        self.destination = self.root / 'derived'
        patcher = mock.patch.object(wb, 'ROOT', self.root); patcher.start(); self.addCleanup(patcher.stop)

    def materialize(self):
        return wb.copy_runtime_source(self.source, self.manifest, self.destination, [self.spec])

    def test_pristine_cache_migrates_and_derived_cache_is_idempotent(self):
        wb.copy_runtime_source(self.source, self.manifest, self.destination)
        result = self.materialize()
        self.assertEqual(self.before, (self.source / 'test.rs').read_bytes())
        self.assertEqual(self.after, (self.destination / 'test.rs').read_bytes())
        self.assertEqual(result, self.materialize())
        self.assertEqual(result, wb.inventory(self.destination))

    def test_modified_source_patch_or_cache_is_rejected_without_overwrite(self):
        self.materialize()
        for path in (self.source / 'test.rs', self.patch, self.destination / 'test.rs'):
            original = path.read_bytes(); path.write_bytes(original + b'tampered\n')
            with self.subTest(path=path.name), self.assertRaises(wb.BuildError): self.materialize()
            self.assertEqual(original + b'tampered\n', path.read_bytes())
            path.write_bytes(original)

    def test_wrong_output_hash_and_undeclared_paths_never_publish(self):
        self.spec['files'][0]['after_sha256'] = '0' * 64
        with self.assertRaises(wb.BuildError): self.materialize()
        self.assertFalse(self.destination.exists())
        self.spec['files'][0]['path'] = '../test.rs'
        with self.assertRaises(wb.BuildError): self.materialize()
        self.assertFalse(self.destination.exists())

    def test_old_receipt_or_changed_patch_and_sources_cannot_select_a_runtime(self):
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps({'runtime': {'patches': [self.spec]}}))
        cached = self.root / 'runtime-src'; cached.mkdir()
        (cached / 'test.rs').write_bytes(self.after)
        path = self.root / 'receipt.json'; path.write_text('{}')
        with mock.patch.object(wb, 'LOCK', lock), mock.patch.object(wb, 'BUILD', self.root):
            with self.assertRaisesRegex(wb.BuildError, 'patchset'): wb.read_runtime_receipt(path)
            receipt = {'runtime_patchset_sha256': wb.canonical_hash([self.spec]),
                       'runtime_source_inventory_sha256': wb.canonical_hash(wb.inventory(cached))}
            path.write_text(json.dumps(receipt))
            self.assertEqual(receipt, wb.read_runtime_receipt(path))
            original = self.patch.read_bytes(); self.patch.write_bytes(original + b'changed')
            with self.assertRaisesRegex(wb.BuildError, 'parche'): wb.read_runtime_receipt(path)
            self.patch.write_bytes(original)
            (cached / 'test.rs').write_bytes(self.before)
            with self.assertRaisesRegex(wb.BuildError, 'fuentes'): wb.read_runtime_receipt(path)


@unittest.skipUnless(sys.platform == 'darwin', 'BLOCKED: Darwin ARM64 runtime required')
class RuntimeTpidrTests(unittest.TestCase):
    def test_real_runtime_regressions_and_interop_contain_no_thread_register_access(self):
        tc = wb.toolchain()
        blob = (tc['runtime_copy'] / 'src/context/interop_aarch64.bin').read_bytes()
        self.assertEqual(len(blob), 2048)
        forbidden = wb.THREAD_REGISTER_ACCESS
        self.assertEqual([], [hex(at) for at in range(0, len(blob), 4)
                             if struct.unpack_from('<I', blob, at)[0] & ~31 in forbidden])
        result = subprocess.run([str(tc['paths']['cargo']), 'test', '--offline', '--locked', '-j2',
                                 '--target', tc['lock']['runtime']['target'], '--manifest-path',
                                 str(tc['runtime_copy'] / 'Cargo.toml'), 'darwin_', '--', '--test-threads=1'],
                                cwd=ROOT, env=tc['env'], capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('2 passed; 0 failed; 0 ignored', result.stdout)


if __name__ == '__main__': unittest.main()
