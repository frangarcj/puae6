"""Pinned parsecue metadata traversal, isolated from the engine and codecs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CueMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cue-metadata-', dir=ROOT / 'build')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        entry = next(p for p in json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches'] if p['id'] == '0356-cue-metadata-only')
        target = cls.work / 'blkdev_cdimage.cpp'
        target.write_bytes((ROOT / 'upstream/winuae/blkdev_cdimage.cpp').read_bytes())
        patch = (ROOT / entry['file']).read_bytes()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['files'][0]['before_sha256']
        assert hashlib.sha256(patch).hexdigest() == entry['sha256']
        mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['files'][0]['after_sha256']
        cls.exe = cls.work / 'probe'
        out = subprocess.run(['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
                              '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-DP6_CUE_METADATA',
                              '-I' + str(ROOT / 'ports/portable/media'), str(target), str(ROOT / 'tests/media/cue_metadata_probe.cpp'),
                              '-o', str(cls.exe)], capture_output=True, text=True, timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def run_cue(self, text, *, code=0, fault=0):
        path = self.work / 'input.cue'; path.write_text(text)
        out = subprocess.run([str(self.exe), str(path), str(fault)], capture_output=True, text=True, timeout=10)
        self.assertEqual(code, out.returncode, out.stderr + out.stdout + repr(text[:200]))
        return out.stdout.splitlines()

    def test_shared_file_indices_flags_combined_gaps_and_postgap(self):
        text = ('FILE "mixed.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n'
                ' TRACK 02 AUDIO\n FLAGS PRE DCP 4CH\n PREGAP 00:01:00\n INDEX 00 00:02:00\n INDEX 01 00:04:00\n POSTGAP 00:03:00\n'
                'FILE "audio.wav" WAVE\n TRACK 03 AUDIO\n INDEX 01 00:00:00')
        self.assertEqual(['tracks=3 calls=3', '1 0 2352 1 4 0 0 0 0 0 1', '2 0 2352 3 11 1 150 300 75 225 0',
                          '3 1 2352 3 0 0 0 0 0 0 1'], self.run_cue(text))
        self.assertEqual('1 0 2048 0 4 0 0 0 0 0 1', self.run_cue('file mixed.bin binary\ntrack 1 mode1/2048\nindex 1 00:00:00')[-1])
        for mode in ('MODE2/2336', 'CDI/2336'):
            self.assertEqual('1 0 2336 2 4 0 0 0 0 0 1', self.run_cue(f'FILE mixed.bin BINARY\nTRACK 1 {mode}\nINDEX 1 00:00:00')[-1])
        self.assertEqual('2 0 2352 3 0 0 0 75 0 0 0', self.run_cue('FILE mixed.bin BINARY\nTRACK 1 AUDIO\nFLAGS PRE\nINDEX 1 00:00:00\nTRACK 2 AUDIO\nINDEX 1 00:01:00')[-1])

    def test_bad_tokens_order_times_types_and_callback_failures_keep_output(self):
        base = 'FILE mixed.bin BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00\n'
        for bad in ('', 'TRACK 1 AUDIO', 'INDEX 1 00:00:00', 'PREGAP 00:01:00', 'POSTGAP 00:01:00', 'FLAGS PRE',
                    'FILE', 'FILE mixed.bin BINARY\x00ignored', 'FILE "unterminated BINARY', 'FILE "mixed.bin"BINARY', 'FILE mixed.bin MP3', 'FILE mixed.bin FLAC',
                    'FILE mixed.bin BINARY extra', 'FILE mixed.bin BINARY\nTRACK', base + 'FILE mixed.bin BINARY',
                    base.replace('TRACK 1', 'TRACK 0'), base.replace('TRACK 1', 'TRACK 100'), base.replace('TRACK 1', 'TRACK 2'),
                    base.replace('2048', '9999'), base.replace('BINARY', 'WAVE'), base.replace('FILE', 'FILEX'),
                    base.replace('INDEX 1', 'INDEX 1x'), base + 'INDEX 1 00:00:00', base + 'FLAGS PRE', base.replace('INDEX', 'FLAGS\nINDEX'), base + 'PREGAP 00:01:00',
                    base.replace('INDEX 1', 'FLAGS BAD\nINDEX 1'), base.replace('INDEX 1', 'INDEX 0 00:02:00\nINDEX 1'),
                    base + 'POSTGAP 00:01:00\nPOSTGAP 00:01:00', base.replace('INDEX', 'PREGAP 00:01:00\nPREGAP 00:01:00\nINDEX'),
                    'x' * 4096, 'REM blank\n' * 7000):
            self.run_cue(bad, code=2)
        for time in ('', '0', '00:00', '00:60:00', '00:00:75', '-1:00:00', 'xx:00:00', '00:00:00 junk'):
            self.run_cue(base.replace('00:00:00', time), code=2)
        for fault in range(1, 7): self.run_cue(base, code=2, fault=fault)
        self.run_cue(base + 'INDEX 2 00:01:00', code=2)
        self.run_cue(base + 'TRACK 2 AUDIO\nINDEX 1 00:00:00', code=2)
        self.assertEqual('2 0 2048 0 4 0 0 0 0 0 1', self.run_cue(base + base.replace('TRACK 1', 'TRACK 2'))[-1])
        self.assertTrue(self.run_cue('REM comment\nCATALOG 123\nTITLE "Album"\n' + base)[0].startswith('tracks=1'))

    def test_99_track_bound_and_no_synthetic_or_stored_metadata_lost(self):
        text = 'FILE mixed.bin BINARY\n' + ''.join(f'TRACK {n} AUDIO\nINDEX 00 {n // 60:02}:{n % 60:02}:00\nINDEX 01 {n // 60:02}:{n % 60:02}:01\n' for n in range(1, 100))
        self.assertEqual('tracks=99 calls=99', self.run_cue(text)[0])
        self.run_cue(text + 'TRACK 100 AUDIO\nINDEX 01 00:00:00', code=2)
