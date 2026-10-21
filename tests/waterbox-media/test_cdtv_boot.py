"""Private numeric CDTV profile on the current Waterbox guest, never native state."""
import json
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
