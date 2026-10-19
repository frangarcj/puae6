"""Native IMG/SUB composition with exact stored subchannels; no engine build."""
import binascii
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from tests.media.test_ccd_metadata import ccd
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdCcdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-ccd-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only', '0357-ccd-metadata-only'):
            e = next(x for x in entries if x['id'] == identifier); record = e['files'][0]; target = cls.work / record['path']
            if not target.exists(): target.write_bytes((ROOT / 'upstream/winuae' / record['path']).read_bytes())
            patch = (ROOT / e['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == e['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['after_sha256']
        cls.exe = cls.work / 'probe'
        command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-DP6_CCD_METADATA', '-DUAE_SYSDEPS_H', '-include', 'cstring', '-I' + str(ROOT / 'ports/portable'), '-I' + str(ROOT / 'ports/portable/media'),
                   '-I' + str(ROOT / 'references/libretro-api/include'), '-I' + str(ROOT / 'upstream/winuae/include'), '-x', 'c++',
                   str(ROOT / 'tests/media/cd_ccd_probe.cpp'), str(ROOT / 'adapters/libretro/cd_ccd.cpp'), str(ROOT / 'ports/portable/media/sha256.c')]
        command += [str(ROOT / 'ports/portable/media' / (name + '.cpp')) for name in ('cd_source', 'cd_wave', 'cd_disc', 'cd_read', 'cd_subchannel')]
        out = subprocess.run(command + [str(cls.work / n) for n in ('cdrom.cpp', 'blkdev_cdimage.cpp')] + ['-o', str(cls.exe)], capture_output=True, text=True, timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def fixture(self, name='fixture', first_index0=-150):
        directory = self.work / name; directory.mkdir(exist_ok=True); path = directory / 'disc.ccd'
        text, entries = ccd(); path.write_text(text.replace('INDEX 0=-150', f'INDEX 0={first_index0}'))
        image, sub = bytearray(), bytearray()
        for lba in range(900):
            sector = bytearray((lba * 7 + n * 11) & 255 for n in range(2352)); sector[15] = 1 if lba < 150 else 2
            pw = bytearray((lba + n * 13 + 1) & 255 for n in range(96)); pw[22:24] = b'\0\0'
            self.assertNotEqual(0, binascii.crc_hqx(pw[12:22], 0) ^ 0xffff)  # Deliberately invalid stored Q CRC.
            image.extend(sector); sub.extend(pw)
        path.with_suffix('.img').write_bytes(image); path.with_suffix('.sub').write_bytes(sub)
        return path, bytes(image), bytes(sub), entries

    def run_ccd(self, path, *, code=0, vfs=False, rebind=None):
        env = {k: v for k, v in os.environ.items() if k != 'P6_CCD_PROBE_VFS'}
        if vfs: env['P6_CCD_PROBE_VFS'] = '1'
        out = subprocess.run([str(self.exe), str(path)] + ([] if rebind is None else [str(rebind)]), capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(code, out.returncode, out.stderr + out.stdout[:200])
        return out.stdout.splitlines()

    def test_raw2448_cross_sector_stored_gaps_and_identity_are_exact(self):
        path, image, sub, entries = self.fixture(); lines = self.run_ccd(path)
        stream = b''.join(image[n * 2352:(n + 1) * 2352] + sub[n * 96:(n + 1) * 96] for n in range(900))
        source = hashlib.sha256(stream).digest(); self.assertEqual(['source', str(len(stream)), source.hex()], lines[0].split())
        canonical = b'P6CCD1' + struct.pack('<QI', len(stream), 1) + source + struct.pack('<IIII', 3, 6, 2, 0)
        for e in entries: canonical += struct.pack('<14i', *e)
        for n, mode in enumerate((1, 0, 2)): canonical += struct.pack('<iiiI', mode, n * 300 - 150, n * 300, 1)
        self.assertEqual(['disc', '3', '900', hashlib.sha256(canonical).hexdigest()], lines[1].split())
        self.assertIn('rawindex0 -150', lines)
        self.assertEqual(['track 0 0 150 0 1', 'track 150 300 450 367200 3', 'track 450 600 900 1101600 2'], [x for x in lines if x.startswith('track ')])
        for row in map(str.split, lines[3:]):
            if row[0] == 'bytes':
                at, length = map(int, row[1:3]); self.assertEqual(row[3], hashlib.sha256(stream[at:at + length]).hexdigest())
            if row[0] == 'sub':
                lba = int(row[1]); self.assertEqual(row[2], hashlib.sha256(sub[lba * 96:(lba + 1) * 96]).hexdigest())
            if row[0] == 'sector':
                lba, view, status = map(int, row[1:4]); expected = stream[lba * 2448:(lba + 1) * 2448]
                if view == 2048 and lba >= 150: self.assertEqual(status, 5); continue
                expected = expected[16:2064] if view == 2048 else expected[:view]
                self.assertEqual((status, row[4]), (0, hashlib.sha256(expected).hexdigest()))
        self.assertEqual(lines, self.run_ccd(path, vfs=True))  # seek0, chunks3 and post-bind I/O/EOF faults.

    def test_rebind_new_owner_rejects_data_subchannel_or_raw_toc_changes(self):
        first, _, _, _ = self.fixture('first'); same, _, _, _ = self.fixture('renamed')
        baseline = self.run_ccd(first, rebind=same)[-1]; self.assertTrue(baseline.startswith('rebind 0 '))
        for suffix in ('.img', '.sub', '.ccd'):
            changed, _, _, _ = self.fixture('changed')
            target = changed.with_suffix(suffix); data = target.read_bytes()
            data = data.replace(b'INDEX 0=-150', b'INDEX 0=-450') if suffix == '.ccd' else bytes([data[0] ^ 1]) + data[1:]
            target.write_bytes(data); rejected = self.run_ccd(first, rebind=changed)[-1]
            self.assertEqual(rejected, baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_missing_sidecars_alignment_lengths_and_toc_extent_fail_without_publish(self):
        for bad in ('missing-img', 'missing-sub', 'unaligned', 'sub-short', 'image-short', 'empty'):
            path, _, _, _ = self.fixture(bad)
            if bad.startswith('missing'): path.with_suffix('.' + bad.split('-')[1]).unlink()
            elif bad == 'sub-short': target = path.with_suffix('.sub'); target.write_bytes(target.read_bytes()[:-1])
            else:
                target = path.with_suffix('.img'); data = target.read_bytes()
                target.write_bytes(b'' if bad == 'empty' else data[:-1] if bad == 'unaligned' else data[:-2352]);
                if bad == 'image-short': path.with_suffix('.sub').write_bytes(path.with_suffix('.sub').read_bytes()[:-96])
            self.run_ccd(path, code=2)


@unittest.skipUnless(os.environ.get('P6_CD_CCD_FILE'), 'optional authorized CCD absent: set P6_CD_CCD_FILE')
class OptionalRealCcdSourceTests(unittest.TestCase):
    setUpClass = classmethod(CdCcdTests.setUpClass.__func__)
    run_ccd = CdCcdTests.run_ccd

    def test_authorized_real_offsets_and_full_virtual_identity(self):
        path = Path(os.environ['P6_CD_CCD_FILE']); lines = self.run_ccd(path); digest = hashlib.sha256(); size = 0
        with path.with_suffix('.img').open('rb') as img, path.with_suffix('.sub').open('rb') as sub:
            while chunk := img.read(2352):
                pw = sub.read(96); self.assertEqual(len(pw), 96); digest.update(chunk + pw); size += 2448
        self.assertEqual(lines[0], f'source {size} {digest.hexdigest()}')
