"""DAO/ETNF offset owner, raw audio and canonical SUB96; no engine/CD32 claims."""
import binascii
import functools
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
from tests.media.test_nrg_metadata import nrg, etnf, nrg_sessions
from tests.fixtures.nrg_etnf_cdi import converted_fixture
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


def mode2_header(lba):
    # DiscSystem9b627578 SynthUtils.SectorHeader: physical MSF, never INDEX-relative.
    absolute = lba + 150
    return b'\0' + b'\xff' * 10 + b'\0' + bytes((n // 10 * 16 + n % 10) for n in (absolute // 4500, absolute // 75 % 60, absolute % 75)) + b'\2'


@functools.lru_cache(maxsize=16)
def form1_body(payload, subheader=bytes(8)):
    # Independent polynomial/field oracle: no WinUAE CRC/RS tables or encoder.
    sector = bytearray(2352); sector[16:24] = subheader; sector[24:2072] = payload
    edc = 0
    for value in sector[16:2072]:
        edc ^= value
        for _ in range(8): edc = (edc >> 1) ^ (0xd8018001 if edc & 1 else 0)
    struct.pack_into('<I', sector, 2072, edc)
    def mul(a, b):
        result = 0
        while b:
            if b & 1: result ^= a
            a <<= 1
            if a & 256: a ^= 0x11d
            b >>= 1
        return result
    inverse3 = next(n for n in range(256) if mul(n, 3) == 1)
    def parity(row):
        total = power = 0
        for value in row: total ^= value; power = mul(power ^ value, 2)
        first = mul(mul(power, 2) ^ total, inverse3)
        return first, first ^ total
    # Header12..15 remains zero for both codes; Q consumes the computed P bytes.
    for col in range(86):
        sector[2076 + col], sector[2162 + col] = parity(sector[12 + col + i * 86] for i in range(24))
    for col in range(52):
        start = col // 2 * 86 + col % 2
        sector[2248 + col], sector[2300 + col] = parity(sector[12 + (start + i * 88) % 2236] for i in range(43))
    return bytes(sector[16:])


def interleave(pw):
    return bytes(sum(((pw[ch * 12 + bit // 8] >> (7 - bit % 8)) & 1) << (7 - ch) for ch in range(8)) for bit in range(96))


def nrg_multisession_fixture(work, name, version=2):
    tail, directory, expected, blocks = nrg_sessions(version)
    raw = [list(map(int, row.split()[1:])) for row in expected if row.startswith('track ')]
    payload = bytearray(directory); streams = []
    for n, r in enumerate(raw):
        stream = bytearray()
        for ordinal, at in enumerate(range(r[0], r[2], r[3])):
            sector = bytearray((bytes((n * 53 + ordinal + k) & 255 for k in range(256)) * 10)[:r[3]])
            if r[4] == 6: sector[15] = 2
            payload[at:at + r[3]] = sector; stream.extend(sector)
        streams.append(bytes(stream))
    path = work / (name + '.nrg'); path.write_bytes(payload + tail)
    return path, raw, streams, directory, blocks


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdNrgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-nrg-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only', '0357-ccd-metadata-only', '0361-nrg-metadata-only', '0388-mds-metadata-only', '0389-nrg-dao-mode3-2336','0391-nrg-dao-mode2-form1','0392-nrg-etnf-canonical-pregap','0403-nrg-etnf-mixed-stride','0404-nrg-etn2-canonical-pregap','0405-nrg-dao-sessions-metadata','0413-nrg-etnf-global-sessions'):
            e = next(x for x in entries if x['id'] == identifier); patch = (ROOT / e['file']).read_bytes()
            for record in e['files']:
                target = cls.work / record['path']; target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists(): target.write_bytes((ROOT / 'upstream/winuae' / record['path']).read_bytes())
                assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == e['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            for record in e['files']: assert hashlib.sha256((cls.work / record['path']).read_bytes()).hexdigest() == record['after_sha256']
        cls.exe = cls.work / 'probe'
        command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I' + str(cls.work / 'include'), '-DP6_NRG_METADATA', '-DUAE_SYSDEPS_H', '-include', 'cstring', '-I' + str(ROOT / 'ports/portable'), '-I' + str(ROOT / 'ports/portable/media'),
                   '-I' + str(ROOT / 'references/libretro-api/include'), '-I' + str(ROOT / 'upstream/winuae/include'), '-x', 'c++', str(ROOT / 'tests/media/cd_nrg_probe.cpp'), str(ROOT / 'adapters/libretro/cd_nrg.cpp'), str(ROOT / 'ports/portable/media/sha256.c')]
        command += [str(ROOT / 'ports/portable/media' / (name + '.cpp')) for name in ('cd_source', 'cd_wave', 'cd_disc', 'cd_read', 'cd_subchannel', 'cd_utilities', 'cd_akiko')]
        out = subprocess.run(command + [str(cls.work / n) for n in ('cdrom.cpp', 'blkdev_cdimage.cpp')] + ['-o', str(cls.exe)], capture_output=True, text=True, timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def fixture(self, name='fixture', version=2, shift=0, missing=False, modes=(0, 5, 6, 7, 15, 16, 17), sectors=50):
        tail, directory, expected = nrg(version, modes, sectors=sectors)
        raw = [list(map(int, row.split()[1:])) for row in expected[1:]]
        payload = bytearray((bytes(range(256)) * ((directory + 255) // 256))[:directory]); streams = []
        for row in raw:
            off0, off1, end, stride, mode, idx0, idx1, has, adr, control = row; stream = bytearray()
            for at in range(off0, end, stride):
                sector = bytearray(payload[at:at + stride])
                if stride not in (2048, 2336): sector[15] = 2 if mode in (6, 17) else 1
                if stride == 2448:
                    pw = bytearray((at // stride + n * 13 + 1) & 255 for n in range(96)); pw[22:24] = b'\0\0'
                    self.assertNotEqual(0, binascii.crc_hqx(pw[12:22], 0) ^ 0xffff)
                    sector[2352:] = interleave(pw); stream.extend(sector[:2352] + pw)
                elif mode == 2: stream.extend(mode2_header(idx0 + (at - off0) // stride + shift) + form1_body(bytes(sector)))
                elif mode == 3: stream.extend(mode2_header(idx0 + (at - off0) // stride + shift) + sector)
                else: stream.extend(sector)
                payload[at:at + stride] = sector
            row[5] += shift; row[6] += shift; streams.append(bytes(stream))
        tail = bytearray(tail); cue_length = struct.unpack_from('>I', tail, 4)[0]
        for at in range(8, 8 + cue_length, 8):
            if tail[at + 1] and shift:
                if version == 2: struct.pack_into('>i', tail, at + 4, struct.unpack_from('>i', tail, at + 4)[0] + shift)
                else:
                    frames = tail[at + 5] * 4500 + tail[at + 6] * 75 + tail[at + 7] + shift
                    tail[at + 5:at + 8] = bytes((frames // 4500, frames // 75 % 60, frames % 75))
        if missing:
            number = int(missing) - 1; at = 16 + number * 16
            del tail[at:at + 8]
            struct.pack_into('>I', tail, 4, cue_length - 8)
            raw[number][5] = raw[number][7] = 0
        path = self.work / (name + '.nrg'); path.write_bytes(payload + tail)
        return path, raw, streams, directory, int(expected[0].split()[5]) + shift

    def etnf_fixture(self, name='etnf', modes=(3, 3, 3), *, etn2=False):
        tail, directory, rows = etnf(modes, etn2=etn2); raw = [list(map(int, row.split()[1:])) for row in rows[1:]]
        payload = bytearray(); streams = []
        for n, r in enumerate(raw):
            stream = bytearray()
            for ordinal in range((r[2] - r[1]) // r[3]):
                stored = (bytes(((n * 43 + ordinal + k) & 255 for k in range(256))) * 10)[:r[3]]
                payload.extend(stored)
                stream.extend(mode2_header(r[6] + ordinal) + (form1_body(stored) if r[4] == 2 else stored) if r[4] in (2, 3) else stored)
            streams.append(bytes(stream))
        path = self.work / (name + '.nrg'); path.write_bytes(payload + tail)
        return path, raw, streams

    def run_nrg(self, path, *, code=0, vfs=False, rebind=None, metadata_only=False, private_sessions=False, global_tao=False):
        env = {k: v for k, v in os.environ.items() if k not in ('P6_NRG_PROBE_VFS', 'P6_NRG_PROBE_METADATA_ONLY', 'P6_NRG_PRIVATE_SESSIONS', 'P6_NRG_GLOBAL_TAO')}
        if vfs: env['P6_NRG_PROBE_VFS'] = '1'
        if metadata_only: env['P6_NRG_PROBE_METADATA_ONLY'] = '1'
        if private_sessions: env['P6_NRG_PRIVATE_SESSIONS'] = '1'
        if global_tao: env['P6_NRG_GLOBAL_TAO'] = '1'
        out = subprocess.run([str(self.exe), str(path)] + ([] if rebind is None else [str(rebind)]), capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(code, out.returncode, out.stderr + out.stdout[-300:]); return out.stdout.splitlines()

    def test_dao_multisession_rejects_before_payload_or_publication(self):
        for version in (1, 2):
            data, directory, _, _ = nrg_sessions(version)
            path = self.work / 'multi-session.nrg'; path.write_bytes(bytes(directory) + data)
            self.assertEqual(['error 5'], self.run_nrg(path, code=2))
            self.assertEqual(['error 5'], self.run_nrg(path, code=2, vfs=True, metadata_only=True))

    def multisession_fixture(self, name, version=2):
        return nrg_multisession_fixture(self.work, name, version)

    def test_private_multisession_bytes_tocs_gaps_identity_and_vfs(self):
        for version in (1, 2):
            path, raw, streams, directory, _ = self.multisession_fixture('private-read', version)
            lines = self.run_nrg(path, private_sessions=True)
            self.assertEqual(lines, self.run_nrg(path, private_sessions=True, vfs=True))
            self.assertEqual(['session 1 1 2 0 -150 11 2 5 0 11', 'session 2 3 3 32 20 39 1 4 30 39'],
                             [row for row in lines if row.startswith('session ')])
            self.assertEqual(['toc 1 160 1 0 0', 'toc 1 1 1 0 0', 'toc 1 2 2 6 4', 'toc 1 161 2 11 0', 'toc 1 162 0 11 0',
                              'toc 2 160 3 0 4', 'toc 2 3 3 32 4', 'toc 2 161 3 39 4', 'toc 2 162 0 39 4'],
                             [row for row in lines if row.startswith('toc ')])
            self.assertEqual(['gap 11', 'gap 20', 'gap 29'], [row for row in lines if row.startswith('gap ')])
            whole = path.read_bytes(); source = hashlib.sha256(whole).digest()
            canonical = b'P6NRGM' + struct.pack('<Q', len(whole)) + source + struct.pack('<QIIiiII', directory, version, 3, -150, 39, 0, 1) + b'1234567890123'
            for r, stream in zip(raw, streams):
                canonical += struct.pack('<QQQiiIIIII', *(r[i] for i in (0, 1, 2, 5, 6, 3, 4, 8, 9, 7))) + b'US1234567890' + hashlib.sha256(stream).digest()
                main = stream[r[1] - r[0]:r[1] - r[0] + r[3]]
                self.assertIn(f'sector {r[6]} 2352 0 {hashlib.sha256(main).hexdigest()}', lines)
                if r[4] == 0: self.assertIn(f'sector {r[6]} 2048 0 {hashlib.sha256(main).hexdigest()}', lines)
            canonical += struct.pack('<II', 1, 2)
            for first, last, fmt, cue0, a2, hint, catalog in ((1, 2, 0, -150, 11, 1, b'1234567890123'), (3, 3, 32, 20, 39, 7, b'ABCDEFGHIJKLM')):
                canonical += struct.pack('<IIIIII', first, last, fmt, cue0 & 0xffffffff, a2, hint) + catalog
            self.assertEqual(lines[1], f'disc 3 39 {hashlib.sha256(canonical).hexdigest()}')
            self.assertEqual(['track 0 0 4 0 2352 352800 3', 'track 4 6 11 1 2048 0 0', 'track 30 32 39 2 2352 0 2'],
                             [row for row in lines if row.startswith('track ')])

    def test_private_multisession_new_owner_and_rebind_reject_every_identity_component(self):
        first, raw, _, directory, blocks = self.multisession_fixture('private-original')
        same = self.multisession_fixture('private-renamed')[0]
        baseline = self.run_nrg(first, rebind=same, vfs=True, private_sessions=True)[-1]
        self.assertTrue(baseline.startswith('rebind 0 ')); original = same.read_bytes()
        mutations = [(r[0] + 17, original[r[0] + 17] ^ 1, 'B') for r in raw]
        second = blocks[1]
        mutations += [(directory + second['CUE'] + 12, 21, 'I'),
                      (directory + second['DAO'] + 26, 0x10, 'B'),
                      (directory + second['DAO'] + 27, 8, 'B'),
                      (directory + second['DAO'] + 12, ord('Z'), 'B')]
        for at, value, fmt in mutations:
            changed = bytearray(original); struct.pack_into('>' + fmt, changed, at, value); same.write_bytes(changed)
            self.assertEqual(self.run_nrg(first, rebind=same, vfs=True, private_sessions=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_private_policy_preserves_all_legacy_identity_domains(self):
        paths = [self.fixture('policy-dao')[0], self.etnf_fixture('policy-etnf')[0], self.etnf_fixture('policy-etn2', etn2=True)[0]]
        for path in paths: self.assertEqual(self.run_nrg(path), self.run_nrg(path, private_sessions=True))

    def verify(self, fixture, vfs=False):
        path, raw, streams, directory, leadout = fixture; lines = self.run_nrg(path, vfs=vfs); whole = path.read_bytes(); source = hashlib.sha256(whole).digest()
        self.assertEqual(lines[0], f'container {len(whole)} {source.hex()}')
        canonical = b'P6NRG1' + struct.pack('<Q', len(whole)) + source + struct.pack('<QIIiiII', directory, 1 if whole[-8:-4] == b'NERO' else 2, len(raw), -150, leadout, 32, 1) + b'1234567890123'
        for r, stream in zip(raw, streams):
            canonical += struct.pack('<QQQiiIIIII', *(r[i] for i in (0, 1, 2, 5, 6, 3, 4, 8, 9, 7))) + b'US1234567890' + hashlib.sha256(stream).digest()
        self.assertEqual(lines[1], f'disc {len(raw)} {leadout} {hashlib.sha256(canonical).hexdigest()}')
        tracks = [list(map(int, line.split()[1:])) for line in lines if line.startswith('track ')]
        for n, (r, t, stream) in enumerate(zip(raw, tracks, streams)):
            start = r[5] if r[7] else r[6] - (r[1] - r[0]) // r[3]; clipped = max(0, start)
            width = 2352 if r[4] in (2, 3) else r[3]
            self.assertEqual(t, [clipped, r[6], r[6] + (r[2] - r[1]) // r[3], n, width, (clipped - start) * width, 0 if r[4] == 0 else 3 if r[4] in (7, 16) else 2 if r[4] in (2, 3, 6, 17) else 1])
            self.assertIn(f'source {n} {len(stream)} {hashlib.sha256(stream).hexdigest()}', lines)
        for row in map(str.split, lines):
            if row[0] == 'encoder':
                expected = mode2_header(1234) + form1_body(bytes((n * 13 + 7) & 255 for n in range(2048)), bytes(range(4)) * 2)
                self.assertEqual(row[1], hashlib.sha256(expected).hexdigest())
            if row[0] == 'parity':
                n, lba = map(int, row[1:3]); t = tracks[n]; at = t[5] + (lba - t[0]) * 2352
                self.assertEqual(row[3], hashlib.sha256(streams[n][at + 2072:at + 2352]).hexdigest())
            if row[0] == 'bytes':
                n, at, length = map(int, row[1:4]); self.assertEqual(row[4], hashlib.sha256(streams[n][at:at + length]).hexdigest())
            if row[0] in ('sector', 'sub'):
                lba = int(row[1]); t = next(t for t in tracks if lba < t[2]); at = t[5] + (lba - t[0]) * t[4]; stream = streams[t[3]]; view = int(row[2]) if row[0] == 'sector' else 96
                if row[0] == 'sub':
                    if t[4] == 2448: self.assertEqual(row[2], hashlib.sha256(stream[at + 2352:at + 2448]).hexdigest())
                    continue
                if view == 2048 and t[6] in (2, 3): self.assertEqual(row[3], '5'); continue
                self.assertEqual(row[3], '0')
                data = stream[at + 16:at + 2064] if view == 2048 and t[6] == 1 else stream[at:at + (2048 if t[6] == 0 else min(view, 2352 if t[4] != 2448 else 2448))]
                if view != 2448 or t[4] == 2448: self.assertEqual(row[4], hashlib.sha256(data).hexdigest())
        return lines

    def test_etnf_canonical_three_tracks_four_modes_and_mixed2048(self):
        cases = [(modes, False) for modes in ((0,) * 3, (2,) * 3, (3,) * 3, (7,) * 3, (0, 2, 0),
                                             (0, 7, 3), (3, 7, 0), (0, 3, 0), (7, 2, 7))]
        cases += [(modes, True) for modes in ((0, 7, 3), (3, 7, 0), (2, 2, 2))]
        for number, (modes, wide) in enumerate(cases):
            path, raw, streams = self.etnf_fixture('etnf' + str(number), modes, etn2=wide)
            lines = self.run_nrg(path); self.assertEqual(lines, self.run_nrg(path, vfs=True))
            whole = path.read_bytes(); source = hashlib.sha256(whole).digest(); directory = sum(r[2] - r[0] for r in raw)
            canonical = (b'P6ETN2' if wide else b'P6ETN1') + struct.pack('<Q', len(whole)) + source + struct.pack('<QIIiiII', directory, 2 if wide else 1, 3, -150, 1200, 0, 0) + bytes(13)
            self.assertEqual(lines[0], f'container {len(whole)} {source.hex()}')
            tracks = [list(map(int, line.split()[1:])) for line in lines if line.startswith('track ')]
            for n, (r, stream, start, index1, end) in enumerate(zip(raw, streams, (0, 300, 675), (0, 450, 825), (300, 675, 1200))):
                mode = 0 if modes[n] == 0 else 3 if modes[n] == 7 else 2; width = 2048 if mode == 0 else 2352
                self.assertEqual(tracks[n], [start, index1, end, n, width, 0, mode])
                self.assertIn(f'etnf {n} {sum((300, 225, 375)[:n])}', lines)
                self.assertIn(f'source {n} {len(stream)} {hashlib.sha256(stream).hexdigest()}', lines)
                canonical += struct.pack('<QQQiiIIIII', *(r[i] for i in (0, 1, 2, 5, 6, 3, 4, 8, 9, 7))) + bytes(12) + hashlib.sha256(stream).digest()
            self.assertEqual(lines[1], f'disc 3 1200 {hashlib.sha256(canonical).hexdigest()}')
            for row in map(str.split, lines):
                if row[0] == 'bytes':
                    n, at, length = map(int, row[1:4]); self.assertEqual(row[4], hashlib.sha256(streams[n][at:at + length]).hexdigest())
                if row[0] != 'sector': continue
                lba, view, result = map(int, row[1:4]); t = next(t for t in tracks if lba < t[2]); gap = lba < t[1]; mode = t[6]
                if view == 2048 and mode in (2, 3): self.assertEqual(result, 5); continue
                self.assertEqual(result, 0); at = (lba - t[1]) * t[4]
                main = bytes(2048 if mode == 0 else 2352) if gap else streams[t[3]][at:at + t[4]]
                if gap and mode == 2: main = mode2_header(lba) + bytes(2336)
                if mode == 0 and view == 2448: continue  # Full Mode1 ECC has its own Disc gate.
                if view == 2448:
                    def bcd(v): return v // 10 * 16 + v % 10
                    def clock(v): return bytes(map(bcd, (v // 4500, v // 75 % 60, v % 75)))
                    q = bytes((1 if mode == 3 else 0x41, bcd(t[3] + 1), int(not gap))) + clock(abs(lba - t[1])) + b'\0' + clock(lba + 150)
                    main += (b'\xff' if gap else b'\0') * 12 + q + struct.pack('>H', binascii.crc_hqx(q, 0) ^ 0xffff) + bytes(72)
                self.assertEqual(row[4], hashlib.sha256(main).hexdigest(), row)

    def test_etnf_full_identity_rebind_and_invalid_or_unsupported_never_publish(self):
        first = self.etnf_fixture('etnf-first')[0]; same = self.etnf_fixture('etnf-renamed')[0]
        baseline = self.run_nrg(first, rebind=same, vfs=True)[-1]; self.assertTrue(baseline.startswith('rebind 0 '))
        for name, at in (('data', 17), ('later-data', 1230000)):
            changed = self.etnf_fixture(name)[0]; data = bytearray(changed.read_bytes()); data[at] ^= 1; changed.write_bytes(data)
            self.assertEqual(self.run_nrg(first, rebind=changed, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
        # Extra raw padding outside all stored tracks remains part of container identity.
        padded = self.work / 'padding.nrg'; data = bytearray(first.read_bytes()); directory = struct.unpack_from('>I', data, len(data) - 4)[0]
        data[directory:directory] = bytes(8); struct.pack_into('>I', data, len(data) - 4, directory + 8); padded.write_bytes(data)
        self.assertEqual(self.run_nrg(first, rebind=padded, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_global_etnf_producer_private_sectors_tocs_gaps_identity_and_rebind(self):
        data, payloads = converted_fixture(); first = self.work / 'global.nrg'; first.write_bytes(data)
        same = self.work / 'renamed.nrg'; same.write_bytes(data)
        for private in (False,True):
            self.assertEqual(['error 5'],self.run_nrg(first,code=2,vfs=True,metadata_only=True,private_sessions=private))
        streams = (payloads[0],b''.join(mode2_header(11702+i)+payloads[1][i*2336:(i+1)*2336] for i in range(300)))
        lines = self.run_nrg(first,vfs=True,rebind=same,global_tao=True)
        identity = b'P6ETGM'+struct.pack('<Q',len(data))+hashlib.sha256(data).digest()+struct.pack('<QIIiiII',1411104,1,2,-150,12002,0,0)+bytes(13)
        for fields,stream in zip(((0,0,710304,-150,0,2352,7,1,0,0),(710304,710304,1411104,11552,11702,2336,3,1,4,0)),streams):
            identity += struct.pack('<QQQiiIIIII',*fields)+bytes(12)+hashlib.sha256(stream).digest()
        identity += struct.pack('<II',1,2)
        for first_track,fmt,cue0,a2 in ((1,0,-150,302),(2,32,11552,12002)):
            identity += struct.pack('<IIIIII',first_track,first_track,fmt,cue0&0xffffffff,a2,0)+bytes(13)
        self.assertEqual(f'disc 2 12002 {hashlib.sha256(identity).hexdigest()}',lines[1])
        self.assertEqual(['track 0 0 302 0 2352 0 3','track 11552 11702 12002 1 2352 0 2'],[s for s in lines if s.startswith('track ')])
        self.assertEqual(['session 1 1 1 0 -150 302 1 4 0 302','session 2 2 2 32 11552 12002 1 4 11552 12002'],[s for s in lines if s.startswith('session ')])
        self.assertEqual(['gap 302','gap 11551'],[s for s in lines if s.startswith('gap ')])
        for row in map(str.split,lines):
            if row[0]=='bytes':
                n,at,length=map(int,row[1:4]); self.assertEqual(hashlib.sha256(streams[n][at:at+length]).hexdigest(),row[4])
            if row[0]!='sector': continue
            lba,view,status=map(int,row[1:4]); number=1 if lba<302 else 2; start=0 if number==1 else 11702; gap=lba<start
            if view==2048: self.assertEqual(5,status); continue
            self.assertEqual(0,status)
            at=(lba-start)*2352; main=mode2_header(lba)+bytes(2336) if gap else streams[number-1][at:at+2352]
            if view==2448:
                def bcd(n): return n//10*16+n%10
                def clock(n): return bytes(map(bcd,(n//4500,n//75%60,n%75)))
                q=bytes((1 if number==1 else 0x41,number,int(not gap)))+clock(abs(lba-start))+b'\0'+clock(lba+150)
                main+=(b'\xff' if gap else b'\0')*12+q+struct.pack('>H',binascii.crc_hqx(q,0)^0xffff)+bytes(72)
            self.assertEqual(hashlib.sha256(main).hexdigest(),row[4],row)
        baseline=lines[-1]; self.assertTrue(baseline.startswith('rebind 0 '))
        for at in (17,710304+17):
            changed=bytearray(data); changed[at]^=1; same.write_bytes(changed)
            self.assertEqual(baseline.replace('rebind 0 ','rebind 2 '),self.run_nrg(first,vfs=True,rebind=same,global_tao=True)[-1])

    def test_etnf_mixed_stride_rebind_identity_and_rejections_are_transactional(self):
        for modes in ((0, 7, 3), (3, 7, 0)):
            first, raw, _ = self.etnf_fixture('mixed-original', modes)
            same = self.etnf_fixture('mixed-renamed', modes)[0]
            baseline = self.run_nrg(first, rebind=same, vfs=True)[-1]
            self.assertTrue(baseline.startswith('rebind 0 '))
            # Bytes from every physical track participate in full identity.
            original = same.read_bytes()
            for record in raw:
                changed = bytearray(original); changed[record[0] + 17] ^= 1
                same.write_bytes(changed)
                self.assertEqual(self.run_nrg(first, rebind=same, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
            same.write_bytes(original)
            directory = sum(row[2] - row[0] for row in raw)
            for at, value in ((28, raw[1][0] + 1), (40, 301), (32, 225 * 2352 - 1)):
                changed = bytearray(original); struct.pack_into('>I', changed, directory + at, value)
                same.write_bytes(changed)
                self.assertEqual(self.run_nrg(same, code=2, vfs=True), ['error 1'])

    def test_etn2_identity_rebind_and_invalid_extents_are_transactional(self):
        for modes in ((0, 7, 3), (3, 7, 0)):
            first, raw, _ = self.etnf_fixture('etn2-original', modes, etn2=True)
            same = self.etnf_fixture('etn2-renamed', modes, etn2=True)[0]
            baseline = self.run_nrg(first, rebind=same, vfs=True)[-1]
            self.assertTrue(baseline.startswith('rebind 0 '))
            original = same.read_bytes()
            for record in raw:
                changed = bytearray(original); changed[record[0] + 17] ^= 1; same.write_bytes(changed)
                self.assertEqual(self.run_nrg(first, rebind=same, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
            narrow = self.etnf_fixture('etnf-same-sectors', modes)[0]
            self.assertEqual(self.run_nrg(first, rebind=narrow, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
            directory = sum(row[2] - row[0] for row in raw)
            for at, fmt, value, error in ((40, 'Q', raw[1][0] + 1, 1), (60, 'I', 301, 1),
                                           (48, 'Q', 529201, 1), (32, 'Q', 1 << 32, 5)):
                changed = bytearray(original); struct.pack_into('>' + fmt, changed, directory + at, value)
                same.write_bytes(changed)
                self.assertEqual(self.run_nrg(same, code=2, vfs=True), [f'error {error}'])

    def test_mixed_dao_v1_v2_clipped_and_derived_pregaps_raw_subchannels_and_vfs_faults(self):
        for version, shift, missing in ((1, 0, False), (2, 75, False), (2, 0, True), (1, 0, 2), (2, 0, 2)):
            fixture = self.fixture(version=version, shift=shift, missing=missing); self.assertEqual(self.verify(fixture), self.verify(fixture, vfs=True))
        fixture = self.fixture(shift=75, modes=(16, 15, 17)); self.assertEqual(self.verify(fixture), self.verify(fixture, vfs=True))

    def test_mode3_headers_payload_cross_sector_hash_and_new_owner(self):
        for version in (1, 2):
            for shift, missing in ((0, False), (75, 2)):
                fixture = self.fixture(f'mode3-{version}-{shift}', version, shift, missing, modes=(3, 3, 0, 7))
                self.assertEqual(self.verify(fixture), self.verify(fixture, vfs=True))
        # Full-stream hashes include every BCD rollover, also the clipped negative INDEX00 prefix.
        self.verify(self.fixture('minute', modes=(3,), sectors=4501))
        first = self.fixture('first-mode3', modes=(3,))[0]; same = self.fixture('same-mode3', modes=(3,))[0]
        baseline = self.run_nrg(first, rebind=same, vfs=True)[-1]; self.assertTrue(baseline.startswith('rebind 0 '))
        changed = self.fixture('changed-mode3', modes=(3,))[0]; data = bytearray(changed.read_bytes()); data[17] ^= 1; changed.write_bytes(data)
        self.assertEqual(self.run_nrg(first, rebind=changed, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_mode2_form1_edc_ecc_are_exact_and_independent_of_lba(self):
        for version in (1, 2):
            for shift, missing in ((0, False), (75, 2)):
                fixture = self.fixture(f'form1-{version}-{shift}', version, shift, missing, modes=(2, 2, 3, 7))
                lines = self.verify(fixture); self.assertEqual(lines, self.verify(fixture, vfs=True))
                tails = [r.split()[3] for r in lines if r.startswith('parity 0 ')]
                self.assertEqual(3, len(tails)); self.assertEqual(1, len(set(tails)), 'identical payload has identical EDC/P/Q at different LBAs')
        first = self.fixture('first-form1', modes=(2,))[0]; same = self.fixture('same-form1', modes=(2,))[0]
        baseline = self.run_nrg(first, rebind=same, vfs=True)[-1]; self.assertTrue(baseline.startswith('rebind 0 '))
        changed = self.fixture('changed-form1', modes=(2,))[0]; data = bytearray(changed.read_bytes()); data[150 * 2048 + 17] ^= 1; changed.write_bytes(data)
        self.assertEqual(self.run_nrg(first, rebind=changed, vfs=True)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
        tail, directory, _ = nrg(modes=(2,), sectors=449851)
        path = self.work / 'form1-msf-overflow.nrg'
        with path.open('wb') as stream: stream.seek(directory); stream.write(tail)
        self.assertEqual(self.run_nrg(path, code=2), ['error 5'])

    def test_mode3_impossible_msf_is_rejected_before_reading_sparse_payload(self):
        # The parser validates this large extent; the owner must refuse its unrepresentable header before hashing it.
        tail, directory, _ = nrg(modes=(3,), sectors=449851)
        path = self.work / 'msf-overflow.nrg'
        with path.open('wb') as stream: stream.seek(directory); stream.write(tail)
        self.assertEqual(self.run_nrg(path, code=2), ['error 5'])
        path = self.fixture('msf-negative', modes=(3,))[0]; data = bytearray(path.read_bytes()); cue = data.index(b'CUEX')
        # Extend the stored first pregap by one sector while preserving all physical/TOC lengths.
        struct.pack_into('>i', data, cue + 8 + 8 + 4, -151)
        dao = data.index(b'DAOX') + 8 + 22; struct.pack_into('>Q', data, dao + 26, 151 * 2336)
        struct.pack_into('>i', data, cue + 8 + 24 + 4, 49)
        path.write_bytes(data); self.assertEqual(self.run_nrg(path, code=2), ['error 5'])

    def test_full_container_identity_rebind_new_owner_and_reject_discarded_or_raw_metadata_changes(self):
        first = self.fixture('first')[0]; same = self.fixture('same')[0]; baseline = self.run_nrg(first, rebind=same)[-1]
        self.assertTrue(baseline.startswith('rebind 0 '))
        absent = self.fixture('missing', missing=2)[0]; renamed = self.fixture('missing-renamed', missing=2)[0]
        self.assertEqual(self.run_nrg(first, rebind=absent)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))
        self.assertEqual(self.run_nrg(absent, rebind=renamed)[-1], baseline)
        for name, at in (('clipped', 0), ('catalog', first.read_bytes().index(b'1234567890123')), ('isrc', first.read_bytes().index(b'US1234567890')), ('unused', first.read_bytes().index(b'CUEX') - 1)):
            target = self.work / (name + '.nrg'); data = bytearray(first.read_bytes()); data[at] ^= 1; target.write_bytes(data)
            self.assertEqual(self.run_nrg(first, rebind=target)[-1], baseline.replace('rebind 0 ', 'rebind 2 '))

    def test_padded_stride_missing_file_and_truncated_source_fail_before_publish(self):
        self.run_nrg(self.work / 'absent.nrg', code=2)
        for original, mode in ((5, 0), (15, 5)):
            tail, directory, _ = nrg(modes=(original,)); data = bytearray(bytes(directory) + tail); data[directory + data[directory:].index(b'DAOX') + 8 + 22 + 14] = mode
            path = self.work / 'padded.nrg'; path.write_bytes(data); self.assertEqual(self.run_nrg(path, code=2), ['error 5'])
        path = self.fixture()[0]; path.write_bytes(path.read_bytes()[:-1]); self.run_nrg(path, code=2)


@unittest.skipUnless(os.environ.get('P6_CD_NRG_FILE'), 'optional authorized NRG absent: set P6_CD_NRG_FILE')
class OptionalRealNrgSourceTests(unittest.TestCase):
    setUpClass = classmethod(CdNrgTests.setUpClass.__func__)
    run_nrg = CdNrgTests.run_nrg
    def test_authorized_real_dao_container_identity(self):
        path = Path(os.environ['P6_CD_NRG_FILE']); lines = self.run_nrg(path)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(65536): digest.update(block)
        self.assertEqual(lines[0], f'container {path.stat().st_size} {digest.hexdigest()}')
