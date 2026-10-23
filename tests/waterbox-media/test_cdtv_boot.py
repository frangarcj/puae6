"""Private numeric CDTV profile on the current Waterbox guest, never native state."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox as wb
from tests.media.test_cd_source_csharp import build_disc_source
from tests.media.test_cdtv_boot import boot_inputs
from tests.media import cdtv_boot_image


class CdtvWaterboxBootTests(unittest.TestCase):
    def test_original_iso_hunk_cold_snapshot_and_full_future(self):
        main, ext, tm = boot_inputs()
        guest = wb.BUILD / 'puae6/release/puae6.wbx'
        receipt = wb.BUILD / 'puae6/puae6-receipt.json'
        if not guest.is_file() or not wb.runtime_receipt_path().is_file():
            raise RuntimeError('BLOCKED: current Waterbox guest/runtime required')
        wb.check_guest_matches_trees(receipt)
        sha = json.loads(receipt.read_text())['guest']['sha256']
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']['path']
        with tempfile.TemporaryDirectory(prefix='cdtv-wbx-', dir=ROOT / 'build') as directory:
            work = Path(directory); assembly, env = build_disc_source(work)
            nonce = cdtv_boot_image.NONCE
            (work / 'original.iso').write_bytes(cdtv_boot_image.image(tm, nonce))
            (work / 'other.iso').write_bytes(cdtv_boot_image.image(tm, bytes(b ^ 0xa5 for b in nonce)))
            # Each mode is a new process. LOAD has no frame before snapshot restore.
            for mode in ('cdtvsave', 'cdtvload'):
                result = subprocess.run(['dotnet', str(assembly), directory, str(work / 'original.iso'),
                                         runtime, str(guest), sha, mode, main, ext, nonce.hex()],
                                        env=env, capture_output=True, text=True, timeout=300)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Private CDTV Waterbox nonce/Delay local/fresh sandbox/cold process/full future/rejects/teardown PASS', result.stdout)
                if mode == 'cdtvsave': self.assertIn('ISO/HUNK executed nonce and Delay by unit', result.stdout)


class CdtvPublicWaterboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        from tests.media import test_cd_source_csharp as source
        main, ext, tm = boot_inputs()
        receipt = Path(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT', str(wb.BUILD / 'puae6/puae6-receipt.json')))
        guest = wb.BUILD / 'puae6/release/puae6.wbx'
        if not guest.is_file() or not wb.runtime_receipt_path().is_file():
            raise RuntimeError('BLOCKED: current Waterbox guest/runtime required')
        wb.check_guest_matches_trees(receipt)
        sha = json.loads(receipt.read_text())['guest']['sha256']
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
        if wb.sha256(guest) != sha or wb.sha256(Path(runtime['path'])) != runtime['sha256']:
            raise AssertionError('Guest or runtime differs from its verified receipt')
        public = importlib.import_module('tests.waterbox-media.test_chd_public')
        base, env, output = public.build_public_cd(sha)
        (base / 'dll').mkdir(exist_ok=True)
        shutil.copy2(guest, base / 'dll/puae6.wbx')
        cls.work = base / 'cdtv-public'; cls.work.mkdir()
        for name, nonce in (('original', cdtv_boot_image.NONCE), ('other', bytes(b ^ 0xa5 for b in cdtv_boot_image.NONCE))):
            (cls.work / (name + '.iso')).write_bytes(cdtv_boot_image.image(tm, nonce))
        (cls.work / 'original.cue').write_text('FILE "original.iso" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
        (cls.work / 'original.xml').write_text('<BizHawk-XMLGame System="Amiga" Name="Original CDTV"><LoadAssets><Asset FileName="original.cue" /></LoadAssets></BizHawk-XMLGame>')
        (cls.work / 'catalog.xml').write_text('<BizHawk-XMLGame System="Amiga" Name="CDTV catalog"><LoadAssets><Asset FileName="original.iso" /><Asset FileName="other.iso" /></LoadAssets></BizHawk-XMLGame>')
        cls.command = [source.DOTNET, str(output / 'Cd32Loader.dll'), str(cls.work), '', runtime['path'], str(guest), sha, '', main, ext, cdtv_boot_image.NONCE.hex()]
        cls.env = env

    def scenario(self, mode, content):
        command = self.command.copy(); command[3] = str(self.work / content); command[7] = mode
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_public_iso_cue_xml_boot_and_cold_restore(self):
        self.assertIn('Public CDTV nonce/Delay boot by unit', self.scenario('cdtvpublicsave', 'original.xml'))
        self.assertIn('local/new sandbox/new process/full future/identity/lease PASS', self.scenario('cdtvpublicload', 'original.iso'))

    def test_fixed_media_firmware_profiles_and_no_content(self):
        self.assertIn('firmware/profile/fixed controls/no content/identity/close PASS', self.scenario('cdtvpublicnegative', 'original.xml'))
