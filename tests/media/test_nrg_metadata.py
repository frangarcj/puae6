"""Real pinned parsenrg, independent DAO layouts, bounded virtual >4GiB input."""
import hashlib
import json
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


def chunk(tag, payload=b''):
    return tag.encode() + struct.pack('>I', len(payload)) + payload


def nrg(version=2, modes=(0, 7), base=0, *, prefix=b'', sinf=True, sectors=50):
    # DiscSystem NRG_format.cs: DAO header22, record30/42; binary first/last.
    cues = bytearray(); records = bytearray(); rows = []; offset = base; lba = -150
    def cue(track, index, address, control=4):
        bcd = track if track == 0xaa else track // 10 * 16 + track % 10
        pos = struct.pack('>i', address) if version == 2 else bytes((0, (address + 150) // 4500, (address + 150) // 75 % 60, (address + 150) % 75))
        return bytes((control * 16 + 1, bcd, index, 0)) + pos
    cues.extend(cue(0, 0, -150))
    for number, mode in enumerate(modes, 1):
        stride = 2048 if mode in (0, 2) else 2336 if mode == 3 else 2352 if mode in (5, 6, 7) else 2448
        control = 0 if mode in (7, 16) else 4; pregap = 150 if number == 1 else 10
        index1 = lba + pregap; offset1 = offset + pregap * stride; end = offset1 + sectors * stride
        cues.extend(cue(number, 0, lba, control) + cue(number, 1, index1, control))
        records.extend(b'US1234567890' + struct.pack('>H', stride) + bytes((mode, 0, 0, 0)) + struct.pack('>QQQ' if version == 2 else '>III', offset, offset1, end))
        rows.append(f'track {offset} {offset1} {end} {stride} {mode} {lba} {index1} 1 1 {control}')
        offset = end; lba = index1 + sectors
    cues.extend(cue(0xaa, 1, lba))
    dao = struct.pack('>I', 22 + len(records)) + b'1234567890123' + bytes((0, 0x20, 1, 1, len(modes))) + records
    directory = offset + 123; data = prefix + chunk('CUEX' if version == 2 else 'CUES', cues) + chunk('DAOX' if version == 2 else 'DAOI', dao)
    if sinf: data += chunk('SINF', struct.pack('>I', len(modes)))
    data += chunk('END!'); data += (b'NER5' + struct.pack('>Q', directory)) if version == 2 else (b'NERO' + struct.pack('>I', directory))
    return data, directory, [f'nrg {version} {len(modes)} {directory} -150 {lba} 32 1'] + rows


def etnf(modes=(3, 3, 3), lengths=(300, 225, 375), version=1):
    # VCDImager23f07386 image_nrg.c:_write_tail: packed ETNF, no pregap bytes.
    # Expectations originate in fixed cues0/450/825, not in the parser output.
    records = bytearray(); rows = []; packed = physical = 0
    for n, (mode, count) in enumerate(zip(modes, lengths)):
        stride = 2048 if mode in (0, 2) else 2336 if mode == 3 else 2352
        index1 = packed + n * 150; length = count * stride
        records.extend(struct.pack('>IIIII', physical, length, mode, packed, 0))
        rows.append(f'track {physical} {physical} {physical + length} {stride} {mode} {index1 - 150} {index1} 0 1 {0 if mode == 7 else 4}')
        packed += count; physical += length
    data = chunk('ETNF', records) + chunk('SINF', struct.pack('>I', len(modes))) + chunk('END!')
    data += b'NERO' + struct.pack('>I', physical) if version == 1 else b'NER5' + struct.pack('>Q', physical)
    return data, physical, [f'nrg {version} {len(modes)} {physical} -150 {packed + (len(modes) - 1) * 150} 0 0'] + rows


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class NrgMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='nrg-metadata-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); target = cls.work / 'blkdev_cdimage.cpp'; target.write_bytes((ROOT / 'upstream/winuae/blkdev_cdimage.cpp').read_bytes())
        entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only', '0357-ccd-metadata-only', '0361-nrg-metadata-only', '0388-mds-metadata-only', '0389-nrg-dao-mode3-2336', '0391-nrg-dao-mode2-form1','0392-nrg-etnf-canonical-pregap'):
            e = next(x for x in entries if x['id'] == identifier); patch = (ROOT / e['file']).read_bytes()
            for record in e['files']:
                target = cls.work / record['path']; target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists(): target.write_bytes((ROOT / 'upstream/winuae' / record['path']).read_bytes())
                assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == e['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            for record in e['files']: assert hashlib.sha256((cls.work / record['path']).read_bytes()).hexdigest() == record['after_sha256']
        target = cls.work / 'blkdev_cdimage.cpp'
        for kind in ('nrg', 'cue', 'ccd'):
            out = subprocess.run(['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                  '-DP6_' + kind.upper() + '_METADATA', '-I' + str(ROOT / 'ports/portable/media'), str(target), str(ROOT / f'tests/media/{kind}_metadata_probe.cpp'), '-o', str(cls.work / kind)], capture_output=True, text=True, timeout=60)
            if out.returncode: raise AssertionError(out.stderr)

    def run_nrg(self, data, base, *, error=None, fault=0):
        path = self.work / 'fixture.nrg'; path.write_bytes(data)
        out = subprocess.run([str(self.work / 'nrg'), str(path), str(base), str(fault)], capture_output=True, text=True, timeout=10)
        self.assertEqual(0 if error is None else 2, out.returncode, out.stderr + out.stdout)
        if error is not None: self.assertEqual(f'error={error}', out.stdout.strip())
        return out.stdout.splitlines()

    def test_dao_v1_v2_indices_binary_track_numbers_modes_and_large_offsets(self):
        for version, modes, base in ((1, (0, 2, 3, 5, 6, 7, 15, 16, 17), 0), (2, (0, 2, 3, 5, 6, 7, 15, 16, 17), 2**32 + 7), (2, (7,) * 99, 0)):
            data, directory, expected = nrg(version, modes, base); self.assertEqual(expected, self.run_nrg(data, directory))
        data, directory, expected = nrg(sinf=False); self.assertEqual(expected, self.run_nrg(data, directory))
        for version in (1, 2):
            data, directory, expected = nrg(version); data = bytearray(data)
            struct.pack_into('>I', data, 4, struct.unpack_from('>I', data, 4)[0] - 8); del data[32:40] # Only track2 INDEX00.
            row = expected[2].split(); row[6] = row[8] = '0'; expected[2] = ' '.join(row)
            self.assertEqual(expected, self.run_nrg(data, directory))

    def test_etnf_packed_three_tracks_four_modes_mixed2048_and_track_limit(self):
        for modes in ((0,) * 3, (2,) * 3, (3,) * 3, (7,) * 3, (0, 2, 0)):
            data, base, expected = etnf(modes)
            self.assertEqual(expected, self.run_nrg(data, base))
            tracks = [row.split() for row in expected[1:]]
            self.assertEqual([0, 450, 825], [int(row[7]) for row in tracks])
        data, base, expected = etnf((7,) * 99, (1,) * 99, version=2)
        self.assertEqual(expected, self.run_nrg(data, base))

    def test_etnf_bad_geometry_and_unsupported_contracts_leave_metadata_intact(self):
        data, base, _ = etnf(); invalid = [data[:-1], data[:4] + struct.pack('>I', 59) + data[8:]]
        for at, value in ((8, 1), (12, 0), (12, 300 * 2336 + 1), (20, 1), (28, 0), (40, 301), (76, 2), (8, 2**32 - 1), (20, 2**32 - 1), (12, (2**32 - 1) // 2336 * 2336)):
            bad = bytearray(data); struct.pack_into('>I', bad, at, value); invalid.append(bad)
        for bad in invalid: self.run_nrg(bad, base, error=1)
        self.run_nrg(*etnf((7,) * 100, (1,) * 100)[:2], error=1)
        self.run_nrg(*etnf((), ())[:2], error=1)
        for fault in (1, 2, 3): self.run_nrg(data, base, fault=fault, error=4)
        for modes in ((0, 3, 0), (7, 0, 7)):
            d, b, _ = etnf(modes); self.run_nrg(d, b, error=5)
        for at, value in ((16, 4), (24, 1)):
            bad = bytearray(data); struct.pack_into('>I', bad, at, value); self.run_nrg(bad, base, error=5)
        for tag in (b'ETN2', b'TINF'):
            self.run_nrg(tag + data[4:], base, error=5)
        end = data.index(b'END!'); sinf = chunk('SINF', struct.pack('>I', 3))
        self.run_nrg(data[:end] + sinf + data[end:], base, error=5)
        self.run_nrg(data[:end] + data[:68] + data[end:], base, error=5)
        self.run_nrg(data.replace(sinf, b''), base, error=1)
        for tag in ('DAOI', 'CUES'):
            dao, _, _ = nrg(version=1); at = dao.index(tag.encode()); size = struct.unpack_from('>I', dao, at + 4)[0]
            # Valid DAO/CUE chunks cannot reinterpret an ETNF session.
            self.run_nrg(data[:end] + dao[at:at + 8 + size] + data[end:], base, error=5)

    def test_invalid_footer_chunks_endian_bcd_extent_and_callback_are_transactional(self):
        data, base, _ = nrg(); dao = data.index(b'DAOX'); start = dao + 8 + 22
        invalid = [b'', b'NERO', data[:-1], data[:-12] + b'NER5' + struct.pack('>Q', 2**64 - 1), data + b'x', data.replace(b'END!\0\0\0\0', chunk('END!', b'x'))]
        for position, replacement in ((4, b'\xff' * 4), (9, b'\x9a'), (10, b'\x1a'), (dao + 4, b'\0\0\0\x16'), (dao + 8 + 20, b'\x00'), (start + 18, struct.pack('>Q', 2**63)), (start + 26, struct.pack('>Q', 1)), (start + 34, struct.pack('>Q', base + 1)), (start + 26, struct.pack('>Q', 150 * 2048 + 1)), (9, b'\x21'), (16 + 7, b'\x01')):
            invalid.append(data[:position] + replacement + data[position + len(replacement):])
        for bad in invalid: self.run_nrg(bad, base if len(bad) >= 12 else 0, error=1)
        for fault in (1, 2, 3): self.run_nrg(data, base, fault=fault, error=4)
        self.run_nrg(data.replace(chunk('SINF', struct.pack('>I', 2)), chunk('SINF', struct.pack('>I', 3))), base, error=1)
        d, b, _ = nrg(1); self.run_nrg(d[:22] + b'\x3c' + d[23:], b, error=1)
        for version in (1, 2):
            d, b, _ = nrg(version); self.run_nrg(d.replace(b'CUES' if version == 1 else b'CUEX', b'CUEX' if version == 1 else b'CUES'), b, error=1)

    def test_unimplemented_variants_are_explicit_and_previous_endpoints_compile(self):
        for mode in (4,):
            data, base, _ = nrg(modes=(mode,)); self.run_nrg(data, base, error=5)
        data, base, _ = nrg(modes=(3,)); at = data.index(b'DAOX') + 8 + 22 + 14
        self.run_nrg(data[:at] + b'\2' + data[at + 1:], base, error=5) # Mode02 cannot have padded2336 geometry.
        for tag in ('ETN2', 'TINF', 'CDTX', 'MTYP'):
            data, base, _ = nrg(prefix=chunk(tag)); self.run_nrg(data, base, error=5)
        data, base, _ = nrg(); self.run_nrg(data.replace(b'END!', b'SINF'), base, error=1)
        for tag in ('CUEX', 'DAOX'):
            index = data.index(tag.encode()); length = struct.unpack_from('>I', data, index + 4)[0]
            duplicate, directory, _ = nrg(prefix=data[index:index + 8 + length]); self.run_nrg(duplicate, directory, error=5)
        sinf = chunk('SINF', struct.pack('>I', 2)); data, base, _ = nrg(prefix=sinf); self.run_nrg(data, base, error=5)
        for kind, text in (('cue', 'FILE mixed.bin BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00'), ('ccd', ccd()[0])):
            path = self.work / 'legacy.txt'; path.write_text(text)
            out = subprocess.run([str(self.work / kind), str(path), '0'], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, out.returncode, out.stderr + out.stdout)
