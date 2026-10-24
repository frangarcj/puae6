"""Native CUE normalization; commercial CD input is opt-in and never copied."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from tests.media.test_cd_file import wave
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdCueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-cue-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync', '0356-cue-metadata-only'):
            entry = next(x for x in entries if x['id'] == identifier); record = entry['files'][0]
            target = cls.work / record['path']; target.write_bytes((ROOT / 'upstream/winuae' / record['path']).read_bytes())
            patch = (ROOT / entry['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == entry['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == record['after_sha256']
        cls.exe = cls.work / 'probe'
        command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-DP6_CUE_METADATA', '-DUAE_SYSDEPS_H', '-include', 'cstring', '-I' + str(ROOT / 'references/libretro-api/include'),
                   '-I' + str(ROOT / 'ports/portable'), '-I' + str(ROOT / 'ports/portable/media'), '-I' + str(ROOT / 'upstream/winuae/include')]
        sources = ['tests/media/cd_cue_probe.cpp', 'adapters/libretro/cd_cue.cpp', 'ports/portable/media/cd_source.cpp', 'ports/portable/media/cd_disc.cpp',
                   'ports/portable/media/cd_wave.cpp', 'ports/portable/media/sha256.c']
        out = subprocess.run(command + ['-x', 'c++'] + [str(ROOT / p) for p in sources] + [str(cls.work / n) for n in ('cdrom.cpp', 'blkdev_cdimage.cpp')] + ['-o', str(cls.exe)], capture_output=True, text=True, timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def run_cue(self, text, *, code=0, path=None, rebind=None, vfs=False):
        if path is None:
            path = self.work / 'fixture.cue'; path.write_text(text)
        environment = {k: v for k, v in os.environ.items() if k != 'P6_CUE_PROBE_VFS'}
        if vfs: environment['P6_CUE_PROBE_VFS'] = '1'
        out = subprocess.run([str(self.exe), str(path)] + ([] if rebind is None else [str(rebind)]), capture_output=True, text=True, timeout=180, env=environment)
        self.assertEqual(code, out.returncode, out.stderr)
        return out.stdout.splitlines(), out.stderr

    def verify(self, lines, providers):
        head = lines[0].split(); tracks = [list(map(int, row.split()[1:])) for row in lines if row.startswith('track ')]
        canonical = b'P6CUE1' + struct.pack('<III', len(providers), len(tracks), int(head[2]))
        for row, (path, offset, length, encoding) in zip((r for r in lines if r.startswith('source ')), providers):
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                stream.seek(offset); left = length
                while left:
                    block = stream.read(min(left, 65536)); self.assertTrue(block); digest.update(block); left -= len(block)
            self.assertEqual(row.split()[1:], [str(length), '1', digest.hexdigest()])
            canonical += struct.pack('<IQI', encoding, length, 1) + digest.digest()
        for t in tracks: canonical += struct.pack('<IIIIIQIIIIII', *t)
        self.assertEqual(head, ['disc', str(len(tracks)), str(tracks[-1][2]), str(len(providers)), hashlib.sha256(canonical).hexdigest()])
        for row in (r.split() for r in lines if r.startswith('sector ')):
            lba, view, status = map(int, row[1:4]); t = next(t for t in tracks if lba < t[2])
            first = t[0] + t[10] if t[9] else t[1]; gap = lba < first or lba >= t[2] - t[11]
            if t[6] == 2 and gap:
                # T66g5: empty Mode2 gaps are Form1, with physical MSF and zero body.
                if view == 2048:
                    self.assertEqual(status,5); continue
                self.assertEqual(status,0); absolute=lba+150
                def bcd(n): return n//10*16+n%10
                header=b'\0'+b'\xff'*10+b'\0'+bytes(map(bcd,(absolute//4500,absolute//75%60,absolute%75)))+b'\2'
                self.assertEqual(row[4],hashlib.sha256(header+bytes(2336)).hexdigest()); continue
            self.assertEqual(status, 0)
            if gap: data = bytes(view)
            else:
                path, payload, _, _ = providers[t[3]]
                with path.open('rb') as stream:
                    stream.seek(payload + t[5] + (lba - first) * t[4]); data = stream.read(view)
            self.assertEqual(row[4], hashlib.sha256(data).hexdigest())
        return tracks

    def test_mixed_stride_shared_bin_combined_pregap_postgap_and_repeated_file(self):
        payload = bytes(range(256)) * 147
        binary, wav = self.work / 'mixed.bin', self.work / 'audio.wav'
        binary.write_bytes(payload[:4096] + payload[:2352 * 4]); wav.write_bytes(wave(payload[:2352 * 4]))
        cue = ('FILE mixed.bin BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00\nTRACK 2 AUDIO\nPREGAP 00:00:01\nINDEX 0 00:00:02\nINDEX 1 00:00:03\nPOSTGAP 00:00:02\n'
               'FILE audio.wav WAVE\nTRACK 3 AUDIO\nPREGAP 00:00:02\nINDEX 0 00:00:00\nINDEX 1 00:00:01\nPOSTGAP 00:00:01')
        tracks = self.verify(self.run_cue(cue)[0], [(binary, 0, binary.stat().st_size, 0), (wav, 56, 2352 * 4, 2)])
        self.assertEqual([2, 4, 9, 0, 2352, 4096, 3, 1, 0, 1, 1, 2], tracks[1])
        self.assertEqual(self.run_cue(cue)[0], self.run_cue(cue, vfs=True)[0])
        repeated = 'FILE audio.wav WAVE\nTRACK 1 AUDIO\nINDEX 1 00:00:00\nFILE audio.wav WAVE\nTRACK 2 AUDIO\nINDEX 1 00:00:00'
        tracks = self.verify(self.run_cue(repeated)[0], [(wav, 56, 2352 * 4, 2)])
        self.assertEqual([4, 4, 8, 0, 2352, 0, 3, 1, 0, 0, 0, 0], tracks[1])

    def test_identity_encoding_policy_rebind_and_explicit_unsupported(self):
        payload = bytes(range(256)) * 147; (self.work / 'raw.bin').write_bytes(payload); (self.work / 'a.wav').write_bytes(wave(payload))
        binary = 'FILE raw.bin BINARY\nTRACK 1 AUDIO\nINDEX 1 00:00:00'
        first = self.work / 'first.cue'; first.write_text(binary)
        same = self.work / 'same.cue'; same.write_text('REM changed text\n' + binary)
        different = self.work / 'other.cue'; different.write_text(binary.replace('raw.bin BINARY', 'a.wav WAVE'))
        self.assertEqual('0', self.run_cue(None, path=first, rebind=same)[0][-1].split()[1])
        self.assertEqual('2', self.run_cue(None, path=first, rebind=different)[0][-1].split()[1])
        for mode in ('MODE2/2336', 'CDI/2336'):
            self.assertIn('Unsupported', self.run_cue(binary.replace('AUDIO', mode), code=2)[1])
        for bad in (binary.replace('00:00:00', '99:59:74'), binary.replace('raw.bin', 'missing.bin'), binary + '\nTRACK 2 AUDIO\nINDEX 0 00:00:00\nINDEX 1 00:00:01'):
            self.run_cue(bad, code=2)
        (self.work / 'short.bin').write_bytes(b'x')
        self.run_cue(binary.replace('raw.bin', 'short.bin'), code=2)
        self.verify(self.run_cue(binary.replace('AUDIO', 'MODE2/2352').replace('INDEX', 'PREGAP 00:00:01\nPOSTGAP 00:00:01\nINDEX'))[0], [(self.work / 'raw.bin', 0, len(payload), 0)])

    def test_real_brian_29_tracks_both_pregaps_and_sector_payloads(self):
        if not os.environ.get('P6_CD_CUE_FILE'): self.skipTest('BLOCKED: set P6_CD_CUE_FILE to authorized Brian29 CUE')
        cue = Path(os.environ['P6_CD_CUE_FILE']); providers = []
        files = re.findall(r'FILE "([^"]+)" (BINARY|WAVE)', cue.read_text()); self.assertEqual(29, len(files))
        for name, kind in files:
            path = cue.parent / name; offset = 0; length = path.stat().st_size
            if kind == 'WAVE':
                with path.open('rb') as stream:
                    stream.seek(12)
                    while True:
                        tag, length = struct.unpack('<4sI', stream.read(8))
                        if tag == b'data': offset = stream.tell(); break
                        stream.seek(length + (length & 1), 1)
            providers.append((path, offset, length, 2 if kind == 'WAVE' else 0))
        tracks = self.verify(self.run_cue(None, path=cue)[0], providers); cursor = 0
        for n, t in enumerate(tracks):
            pregap = 150 if n == 1 else 0; stored = n >= 2; stride = 2048 if n == 0 else 2352
            end = cursor + pregap + providers[n][2] // stride
            self.assertEqual([cursor, cursor + (150 if n else 0), end, n, stride, 0, 3 if n else 0, 1, 0 if n else 4, int(stored), pregap, 0], t)
            cursor = end
