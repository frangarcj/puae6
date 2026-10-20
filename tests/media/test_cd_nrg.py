"""DAO offset owner, raw audio and canonical SUB96; no engine/CD32 claims."""
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
from tests.media.test_nrg_metadata import nrg
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


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdNrgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-nrg-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only', '0357-ccd-metadata-only', '0361-nrg-metadata-only', '0388-mds-metadata-only', '0389-nrg-dao-mode3-2336','0391-nrg-dao-mode2-form1'):
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
        command += [str(ROOT / 'ports/portable/media' / (name + '.cpp')) for name in ('cd_source', 'cd_wave', 'cd_disc', 'cd_read', 'cd_subchannel', 'cd_utilities')]
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

    def run_nrg(self, path, *, code=0, vfs=False, rebind=None):
        env = {k: v for k, v in os.environ.items() if k != 'P6_NRG_PROBE_VFS'}
        if vfs: env['P6_NRG_PROBE_VFS'] = '1'
        out = subprocess.run([str(self.exe), str(path)] + ([] if rebind is None else [str(rebind)]), capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(code, out.returncode, out.stderr + out.stdout[-300:]); return out.stdout.splitlines()

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
