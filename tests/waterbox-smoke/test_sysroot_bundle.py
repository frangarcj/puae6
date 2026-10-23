"""Real offline bundle extraction and hostile-archive boundaries; no compiler/runtime."""
import copy
import io
import json
import lzma
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import prepare_waterbox_sysroot as bundle


class SysrootBundleTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='bundle-test-', dir=ROOT / 'build')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        paths = ['plan/toolchains.json', 'plan/upstream.lock.json', 'tools/build_waterbox_sysroot.py',
                 'tools/prepare_waterbox_sysroot.py', 'references/machawk.manifest.json',
                 'references/musl-darwin.manifest.json', 'references/compiler-rt-emutls.manifest.json']
        for name in paths:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.directory = self.root / bundle.BUNDLE
        shutil.copytree(ROOT / bundle.BUNDLE, self.directory)
        self.archive = self.directory / bundle.ARCHIVE
        self.provenance = self.directory / 'PROVENANCE.json'
        self.metadata = json.loads(self.provenance.read_text())
        self.output, self.notices = (self.root / 'build' / name for name in ('waterbox-sysroot', 'waterbox-sysroot-notices'))

    def prepare(self, **kwargs):
        return bundle.prepare(self.root, **kwargs)

    def repin(self, data):
        self.archive.write_bytes(data)
        self.metadata['archive'].update(size_bytes=len(data), sha256=bundle.digest(data))
        self.provenance.write_text(json.dumps(self.metadata))

    def tar(self, members, *, format=tarfile.USTAR_FORMAT):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=format) as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data) if data else None)
        self.repin(lzma.compress(stream.getvalue()))

    @staticmethod
    def member(name='sysroot/file', data=b'x', mode=0o644, kind=tarfile.REGTYPE):
        info = tarfile.TarInfo(name)
        info.mode, info.type = mode, kind
        info.size = len(data) if kind == tarfile.REGTYPE else 0
        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            info.linkname = '../escape'
        return info, data if info.size else b''

    def refused(self, regex=None):
        with self.assertRaises((OSError, ValueError, lzma.LZMAError, tarfile.TarError)) as caught:
            self.prepare()
        if regex:
            self.assertRegex(str(caught.exception), regex)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.notices.exists())
        self.assertFalse(list((self.root / 'build').glob('sysroot-bundle-*')))

    def test_real_roundtrip_modes_notices_cli_and_idempotence(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'PASS')
        actual = bundle.entries(self.output)
        self.assertEqual((len(actual), bundle.canonical(actual)),
                         (1270, 'ee086b2a4403e8c17dfe93d04e944e660321d33249af50ce89abcbf2e28afc8b'))
        self.assertEqual(bundle.entries(self.notices), self.metadata['notices'])
        before = {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for root in (self.output, self.notices) for p in root.rglob('*')}
        self.assertEqual(result, self.prepare())
        process = subprocess.run([sys.executable, str(self.root / 'tools/prepare_waterbox_sysroot.py')],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(json.loads(process.stdout), result)
        self.assertEqual(before, {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for root in (self.output, self.notices) for p in root.rglob('*')})

    def test_sha_corruption_truncation_and_missing_input_never_publish(self):
        original = self.archive.read_bytes()
        for data, why in ((original[:-1], 'size'), (original[:-1] + bytes([original[-1] ^ 1]), 'SHA')):
            self.archive.write_bytes(data)
            self.refused(why)
        self.archive.unlink()
        self.refused()

    def test_paths_types_modes_duplicates_and_pax_are_rejected(self):
        for name in ('../outside', '/sysroot/file', 'sysroot/../file', 'sysroot/./file', 'sysroot//file',
                     'sysroot\\file', 'other/file', 'licenses', 'sysroot/file/'):
            with self.subTest(path=name):
                self.tar([self.member(name)])
                self.refused('path')
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            self.tar([self.member(kind=kind)])
            self.refused('type')
        for mode in (0o600, 0o664, 0o4755):
            self.tar([self.member(mode=mode)])
            self.refused('mode')
        self.tar([self.member(), self.member()])
        self.refused('duplicate')
        self.tar([self.member(), self.member('sysroot/file/child')])
        self.refused()
        info, data = self.member(); info.pax_headers = {'comment': 'extra header'}
        self.tar([(info, data)], format=tarfile.PAX_FORMAT)
        self.refused('header')

    def test_decoded_member_and_count_bounds_and_truncated_stream(self):
        self.repin(lzma.compress(bytes(bundle.MAX_BYTES + 1)))
        self.refused('decoded')
        info, _ = self.member(); info.size = bundle.MAX_BYTES + 1
        self.repin(lzma.compress(info.tobuf(format=tarfile.USTAR_FORMAT) + bytes(1024)))
        self.refused('payload')
        self.tar([self.member('sysroot/' + str(n), b'') for n in range(bundle.MAX_FILES + 1)])
        self.refused('count')
        data = lzma.compress(bytes(2048)); self.repin(data[:-1])
        self.refused()
        info, _ = self.member()
        self.repin(lzma.compress(info.tobuf(format=tarfile.USTAR_FORMAT) + b'x' + bytes(511)))
        self.refused('terminator')
        self.repin(data + data)
        self.refused('stream')

    def test_full_inventory_and_notices_are_required_even_with_a_repinned_archive(self):
        original = self.archive.read_bytes()
        for prefix in ('sysroot/', 'licenses/'):
            raw = bytearray(lzma.decompress(original))
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
                target = next(t for t in archive if t.name.startswith(prefix) and t.size)
            raw[target.offset_data] ^= 1
            self.repin(lzma.compress(raw))
            self.refused('inventory')

    def test_existing_mismatch_is_preserved_and_other_output_is_not_published(self):
        self.notices.mkdir(parents=True)
        marker = self.notices / 'foreign'; marker.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'existing output'):
            self.prepare()
        self.assertEqual(marker.read_bytes(), b'keep')
        self.assertFalse(self.output.exists())
        marker.unlink(); self.notices.rmdir()
        self.prepare()
        path = next(p for p in self.output.rglob('*') if p.is_file())
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'existing output'):
            self.prepare()
        self.assertEqual(path.read_bytes(), b'changed')

    def test_pins_symlinks_and_output_boundaries_refuse_before_publication(self):
        original = copy.deepcopy(self.metadata)
        for section, key in (('sysroot', 'inventory_sha256'), ('rebuild', 'recipe_sha256'), ('sources', 'machawk')):
            self.metadata = copy.deepcopy(original); self.metadata[section][key] = '0' * 64
            self.provenance.write_text(json.dumps(self.metadata))
            self.refused('pin|recipe')
        self.provenance.write_text(json.dumps(original))
        self.output.parent.mkdir(exist_ok=True)
        self.output.symlink_to(self.root / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.prepare()
        self.output.unlink()
        for path in (self.root / 'outside', self.root / 'build/../outside', self.notices):
            with self.assertRaisesRegex(ValueError, 'outside|below build/|overlapping'):
                self.prepare(output=path)


if __name__ == '__main__':
    unittest.main()
