"""Independent MDS synthetic-pregap/data-offset fixtures, native/VFS, no engine."""
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
from tests.media import test_mds_metadata as meta, test_cd_nrg as nrg_ref
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


def msf(lba):
    absolute = lba + 150
    return bytes((absolute // 4500, absolute // 75 % 60, absolute % 75))


def subq(track, lba, number):
    start, index1, end, source, stride, offset, mode = track
    def bcd(n): return n // 10 * 16 + n % 10
    def clock(n): return bytes(map(bcd, (n // 4500, n // 75 % 60, n % 75)))
    q = bytes((1 if mode == 3 else 0x41, bcd(number), int(lba >= index1))) + clock(abs(lba - index1)) + b'\0' + clock(lba + 150)
    return (b'\xff' if lba < index1 else b'\0') * 12 + q + struct.pack('>H', binascii.crc_hqx(q, 0) ^ 0xffff) + bytes(72)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdMdsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-mds-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only', '0357-ccd-metadata-only', '0361-nrg-metadata-only', '0388-mds-metadata-only'):
            e = next(x for x in entries if x['id'] == identifier); record = e['files'][0]; target = cls.work / record['path']
            if not target.exists(): target.write_bytes((ROOT / 'upstream/winuae' / record['path']).read_bytes())
            patch = (ROOT / e['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == e['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['after_sha256']
        cls.exe = cls.work / 'probe'
        command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-DP6_MDS_METADATA', '-DUAE_SYSDEPS_H', '-include', 'cstring', '-I' + str(ROOT / 'ports/portable'), '-I' + str(ROOT / 'ports/portable/media'),
                   '-I' + str(ROOT / 'references/libretro-api/include'), '-I' + str(ROOT / 'upstream/winuae/include'), '-x', 'c++', str(ROOT / 'tests/media/cd_mds_probe.cpp'), str(ROOT / 'adapters/libretro/cd_mds.cpp'), str(ROOT / 'ports/portable/media/sha256.c')]
        command += [str(ROOT / 'ports/portable/media' / (name + '.cpp')) for name in ('cd_source', 'cd_wave', 'cd_disc', 'cd_read', 'cd_subchannel', 'cd_utilities')]
        out = subprocess.run(command + [str(cls.work / n) for n in ('cdrom.cpp', 'blkdev_cdimage.cpp')] + ['-o', str(cls.exe)], capture_output=True, text=True, timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def fixture(self, name='fixture', *, filename='shared.mdf', wide=False, pregap=152):
        # libMirage a9d6a2 parser.c:461..474,492,543..545,646..651. No copied parser code.
        modes = ((0xaa, 2048, 0), (0xaa, 2352, 0), (0xaa, 2448, 8), (0xa9, 2352, 0), (0xa9, 2448, 8), (0xab, 2352, 0), (0xab, 2448, 8))
        folder = self.work / name; folder.mkdir(exist_ok=True)
        data, _, at = meta.mds(modes=modes, name=filename, wide=wide); payload = bytearray(b'padding'); cursor = -150; tracks = []; streams = []
        for n, (mode, stride, sub) in enumerate(modes):
            gap = pregap if n == 0 else n + 1; index1 = cursor + gap; end = index1 + 3; offset = len(payload) + 17
            stream = bytearray(); stored = bytearray()
            for sector in range(3):
                main = bytearray((n * 31 + sector * 7 + k) & 255 for k in range(min(stride, 2352)))
                if stride != 2048: main[15] = 2 if mode == 0xab else 1
                pw = bytes((n * 11 + sector + k * 13) & 255 for k in range(96))
                stream += main + (pw if sub else b''); stored += main + (nrg_ref.interleave(pw) if sub else b'')
            payload += bytes(17) + stored
            pos = at['track'] + n * 80; data[pos + 9:pos + 12] = msf(index1)
            struct.pack_into('<iQ', data, pos + 36, index1, offset); struct.pack_into('<II', data, at['extra'] + n * 8, gap, 3)
            tracks.append([max(0, cursor), index1, end, n, stride, 0, 3 if mode == 0xa9 else 2 if mode == 0xab else 0 if stride == 2048 else 1]); streams.append(bytes(stream)); cursor = end
        struct.pack_into('<i', data, 92, cursor); data[112 + 160 + 9:112 + 160 + 12] = msf(cursor)
        path = folder / 'disc.mds'; path.write_bytes(data)
        mdf = path.with_suffix(filename[1:]) if filename.lower() == '*.mdf' else folder / filename.replace('\\', '/')
        mdf.parent.mkdir(exist_ok=True, parents=True); mdf.write_bytes(payload + b'unused tail')
        return path, mdf, tracks, streams

    def run_mds(self, path, *, code=0, vfs=False, rebind=None, fault=0):
        env = dict(os.environ); env.pop('P6_MDS_PROBE_VFS', None); env.pop('P6_MDS_PROBE_OPEN_FAULT', None)
        if vfs: env['P6_MDS_PROBE_VFS'] = '1'
        if fault: env['P6_MDS_PROBE_OPEN_FAULT'] = str(fault)
        out = subprocess.run([str(self.exe), str(path)] + ([] if rebind is None else [str(rebind)]), capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(code, out.returncode, out.stderr + out.stdout[-300:]); return out.stdout.splitlines()

    def verify(self, fixture, vfs=False):
        path, mdf, tracks, streams = fixture; lines = self.run_mds(path, vfs=vfs); canonical = b'P6MDS1'
        for label, source in (('descriptor', path), ('container', mdf)):
            data = source.read_bytes(); digest = hashlib.sha256(data).digest(); self.assertIn(f'{label} {len(data)} {digest.hex()}', lines); canonical += struct.pack('<Q', len(data)) + digest
        canonical += struct.pack('<II', len(tracks), tracks[-1][2])
        for n, (t, stream) in enumerate(zip(tracks, streams)):
            self.assertIn('track ' + ' '.join(map(str, t)), lines); digest = hashlib.sha256(stream).digest()
            self.assertIn(f'source {n} {len(stream)} {digest.hex()}', lines)
            canonical += struct.pack('<IIIIIQIIIIIIQ', *t[:6], t[6], 1, 0 if t[6] == 3 else 4, 0, t[1] - t[0], 0, len(stream)) + digest
        self.assertIn(f'disc {len(tracks)} {tracks[-1][2]} {hashlib.sha256(canonical).hexdigest()}', lines)
        for row in map(str.split, lines):
            if row[0] == 'bytes':
                n, at, length = map(int, row[1:4]); self.assertEqual(row[4], hashlib.sha256(streams[n][at:at + length]).hexdigest())
            if row[0] not in ('sector', 'sub'): continue
            lba = int(row[1]); t = next(t for t in tracks if lba < t[2]); gap = lba < t[1]; stride, mode = t[4], t[6]
            at = (lba - t[1]) * stride; main = bytes(2048 if mode == 0 else 2352) if gap else streams[t[3]][at:at + min(stride, 2352)]
            sub = subq(t, lba, t[3] + 1) if gap or stride != 2448 else streams[t[3]][at + 2352:at + 2448]
            if row[0] == 'sub': self.assertEqual(row[2], hashlib.sha256(sub).hexdigest()); continue
            view = int(row[2]); unsupported = (view == 2048 and mode in (2, 3)) or (gap and mode == 2)
            self.assertEqual(row[3], '5' if unsupported else '0')
            if unsupported: continue
            expected = main[16:2064] if mode == 1 and (view == 2048 or (gap and view == 2352)) else main
            if view == 2448:
                if mode == 0 or (gap and mode == 1): continue  # Mode1 ECC encoding has its own independent Disc gate.
                expected += sub
            self.assertEqual(row[4], hashlib.sha256(expected).hexdigest(), row)
        return lines

    def test_mixed_modes_offsets_synthetic_gaps_names_sub96_and_vfs(self):
        for n, options in enumerate(({}, {'pregap': 150}, {'filename': '*.MDF'}, {'filename': 'datos/Disco-ñ-😀.mdf', 'wide': True})):
            fixture = self.fixture(str(n), **options); self.assertEqual(self.verify(fixture), self.verify(fixture, vfs=True))

    def test_complete_id_and_new_owner_rebind_preserve_old_on_mismatch(self):
        first = self.fixture('first'); same = self.fixture('renamed'); baseline = self.run_mds(first[0], rebind=same[0])[-1]
        self.assertTrue(baseline.startswith('rebind 0 '))
        for name, which, at in (('mds-padding', 0, 60), ('mdf-padding', 1, 0), ('mdf-tail', 1, -1), ('payload', 1, 25), ('audio', 1, 22000), ('sub', 1, 15620)):
            other = self.fixture(name); source = other[which]; data = bytearray(source.read_bytes()); data[at] ^= 1; source.write_bytes(data)
            self.assertEqual(self.run_mds(first[0], vfs=True, rebind=other[0])[-1], baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_inconsistent_toc_spans_missing_truncated_and_vfs_errors_do_not_publish(self):
        self.run_mds(self.work / 'absent.mds', code=2)
        for n, (pos, value) in enumerate(((92, struct.pack('<i', 1)), (112 + 160 + 11, b'\x01'), (112 + 240 + 36, struct.pack('<i', 3)), (112 + 240 + 11, b'\x03'), (112 + 320 + 40, struct.pack('<Q', 30)), (112 + 240 + 40, struct.pack('<Q', 2**64 - 10000)))):
            fixture = self.fixture('bad' + str(n)); data = bytearray(fixture[0].read_bytes()); data[pos:pos + len(value)] = value; fixture[0].write_bytes(data); self.run_mds(fixture[0], code=2)
        for action in ('missing', 'short'):
            path, mdf, _, _ = self.fixture(action)
            if action == 'missing': mdf.unlink()
            else: mdf.write_bytes(mdf.read_bytes()[:100])
            self.run_mds(path, code=2)
        path = self.fixture('fault')[0]
        for fault in (1, 2, 3): self.run_mds(path, code=2, vfs=True, fault=fault)
