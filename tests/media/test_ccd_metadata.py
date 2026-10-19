"""Pinned CCD parser, raw TOC/index preservation and bounded callback failures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import materialize_upstream as mat
FIELDS = ('Session', 'Point', 'ADR', 'Control', 'TrackNo', 'AMin', 'ASec', 'AFrame', 'ALBA', 'Zero', 'PMin', 'PSec', 'PFrame', 'PLBA')


def entry(number, point, lba, control=4):
    minute, rest = divmod(lba + 150, 4500); second, frame = divmod(rest, 75)
    values = (1, point, 1, control, 0, 0, 0, 0, -150, 0, minute, second, frame, lba)
    return f'[Entry {number}]\n' + ''.join(f'{key}={hex(value) if key == "Point" else value}\n' for key, value in zip(FIELDS, values)), values


def ccd(modes=(1, 0, 2)):
    count = len(modes); text = f'[CloneCD]\nVersion=3\n[Disc]\nTocEntries={count + 3}\nSessions=1\nDataTracksScrambled=0\nCDTextLength=0\n[Session 1]\nPreGapMode=2\nPreGapSubC=0\n'; values = []
    items = [(0xa0, 4350, 4), (0xa1, count * 4500 - 150, 4), (0xa2, count * 300, 4)] + [(n + 1, n * 300, 4 if mode else 0) for n, mode in enumerate(modes)]
    for n, (point, lba, control) in enumerate(items):
        block, raw = entry(n, point, lba, control); text += block; values.append(raw)
    for n, mode in enumerate(modes): text += f'[Track {n + 1}]\nMODE={mode}\nINDEX 0={n * 300 - 150}\nINDEX 1={n * 300}\n'
    return text, values


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CcdMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='ccd-metadata-', dir=ROOT / 'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name); target = cls.work / 'blkdev_cdimage.cpp'
        target.write_bytes((ROOT / 'upstream/winuae/blkdev_cdimage.cpp').read_bytes())
        entries = json.loads((ROOT / 'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0356-cue-metadata-only', '0357-ccd-metadata-only'):
            e = next(x for x in entries if x['id'] == identifier); patch = (ROOT / e['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == e['files'][0]['before_sha256']
            assert hashlib.sha256(patch).hexdigest() == e['sha256']
            mat._apply(patch, cls.work, True); mat._apply(patch, cls.work, False)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == e['files'][0]['after_sha256']
        for kind in ('ccd', 'cue'):
            command = ['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       '-DP6_' + kind.upper() + '_METADATA', '-I' + str(ROOT / 'ports/portable/media'), str(target), str(ROOT / f'tests/media/{kind}_metadata_probe.cpp'), '-o', str(cls.work / kind)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode: raise AssertionError(result.stderr)

    def run_ccd(self, text, *, code=0, fault=0, kind='ccd'):
        path = self.work / 'fixture.txt'; path.write_text(text)
        result = subprocess.run([str(self.work / kind), str(path), str(fault)], capture_output=True, text=True, timeout=10)
        self.assertEqual(code, result.returncode, result.stderr + result.stdout + repr(text[:160]))
        return result.stdout.splitlines()

    def test_complete_raw_toc_modes_and_stored_indices_including_leadin(self):
        text, entries = ccd(); lines = self.run_ccd(text)
        self.assertEqual(['ccd 3 6 2 0'] + ['entry ' + ' '.join(map(str, row)) for row in entries] +
                         ['track 1 1 -150 0', 'track 0 1 150 300', 'track 2 1 450 600'], lines)
        text, _ = ccd((0,) * 99); self.assertEqual('ccd 99 102 2 0', self.run_ccd(text)[0])
        self.assertTrue(self.run_ccd(ccd()[0].replace('\n', '\r\n')))
        self.assertIn('track 1 1 -450 0', self.run_ccd(ccd()[0].replace('INDEX 0=-150', 'INDEX 0=-450')))

    def test_malformed_numbers_points_sections_indices_and_callbacks_are_transactional(self):
        text, _ = ccd()
        for bad in ('', 'X=1\n' + text, text.replace('Version=3', 'Version=4'), text.replace('Sessions=1', 'Sessions=2'),
                    text.replace('DataTracksScrambled=0', 'DataTracksScrambled=1'), text.replace('TocEntries=6', 'TocEntries=103'),
                    text.replace('[Entry 0]', '[Entry 1]'), text.replace('[Track 2]', '[Track 3]'), text.replace('[Disc]', '[Disc]junk'),
                    text.replace('Point=0x2', 'Point=0x1'), text.replace('ADR=1', 'ADR=2'), text.replace('Control=4', 'Control=16'),
                    text.replace('AMin=0', 'AMin=100'), text.replace('ASec=0', 'ASec=60'), text.replace('AFrame=0', 'AFrame=75'),
                    text.replace('ALBA=-150', 'ALBA=-149'), text.replace('Zero=0', 'Zero=1'), text.replace('TrackNo=0', 'TrackNo=1'),
                    text.replace('MODE=2', 'MODE=3'), text.replace('INDEX 1=600', 'INDEX 1=599'), text.replace('INDEX 0=450', 'INDEX 0=650'),
                    text.replace('PLBA=0\n', ''), text.replace('PLBA=0\n', 'PLBA=0\nPLBA=0\n'), text.replace('PreGapMode=2', 'PreGapMode=2\nPreGapMode=2'),
                    text.replace('MODE=1', 'MODE=1\nMODE=1'), text.replace('INDEX 1=600\n', ''), text.replace('PLBA=0', 'PLBA=0\x00ignored'),
                    text.replace('[Track 1]', '[Unknown 1]'), 'x' * 4096, ';comment\n' * 9000):
            self.run_ccd(bad, code=2)
        for point in ('0', '-1', '100', '159', '163', '0xa3'):
            self.run_ccd(text.replace('Point=0x1\n', f'Point={point}\n'), code=2)
        for value in ('', 'junk', '1junk', '0x', '2147483648', '-2147483648', '999999999999999999999999999'):
            self.run_ccd(text.replace('PLBA=0\n', f'PLBA={value}\n'), code=2)
        for fault in range(1, 5): self.run_ccd(text, code=2, fault=fault)

    def test_existing_cue_metadata_export_still_uses_pinned_traversal(self):
        lines = self.run_ccd('FILE mixed.bin BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00', kind='cue')
        self.assertEqual(['tracks=1 calls=1', '1 0 2048 0 4 0 0 0 0 0 1'], lines)


@unittest.skipUnless(os.environ.get('P6_CD_CCD_FILE'), 'optional real CCD absent: set P6_CD_CCD_FILE')
class OptionalRealCcdTests(unittest.TestCase):
    setUpClass = classmethod(CcdMetadataTests.setUpClass.__func__)
    run_ccd = CcdMetadataTests.run_ccd

    def test_authorized_real_ccd_metadata_only(self):
        self.assertTrue(self.run_ccd(Path(os.environ['P6_CD_CCD_FILE']).read_text())[0].startswith('ccd '))
