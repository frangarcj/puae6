"""Private CDTV firmware/profile only; authorized ROMs stay outside Git."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.build_support import ROOT, build_targets
from tests.media import cdtv_boot_image


class TrademarkSourceTests(unittest.TestCase):
    def test_cooked_and_raw_tracks_extract_only_the_declared_cross_sector_tm(self):
        tm=bytes((n*17+3)&255 for n in range(22152)); iso=cdtv_boot_image.image(tm)
        with tempfile.TemporaryDirectory(prefix='cdtv-tm-',dir=ROOT/'build') as directory:
            work=Path(directory)
            for mode in ('MODE1/2048','MODE1/2352'):
                source=work/'track.bin'; cue=work/'disc.cue'
                data=iso if mode=='MODE1/2048' else b''.join(bytes(16)+iso[n:n+2048]+bytes(288) for n in range(0,len(iso),2048))
                source.write_bytes(data);cue.write_text(f'FILE "track.bin" BINARY\n TRACK 01 {mode}\n INDEX 01 00:00:00\n')
                self.assertEqual(cdtv_boot_image.trademark(cue),tm)
                source.write_bytes(data[:24*(2048 if mode=='MODE1/2048' else 2352)])
                with self.assertRaises(ValueError):cdtv_boot_image.trademark(cue)
            cue.write_text('FILE "track.bin" BINARY\n')
            with self.assertRaises(ValueError):cdtv_boot_image.trademark(cue)


class CdtvFirmwareTests(unittest.TestCase):
    def test_private_firmware_maps_execution_and_rejections(self):
        main=os.environ.get('P6_CDTV_ROM_MAIN'); ext=os.environ.get('P6_CDTV_ROM_EXT')
        if not main or not ext: raise RuntimeError('BLOCKED: authorized P6_CDTV_ROM_MAIN and P6_CDTV_ROM_EXT required')
        for path,wanted in ((main,'891e9a547772fe0c6c19b610baf8bc4ea7fcb785'),(ext,'5bc114bba29f60a614a311745b3e2464bfa06846')):
            data=Path(path).read_bytes(); self.assertEqual(len(data),262144); self.assertEqual(hashlib.sha1(data).hexdigest(),wanted)
        exe=build_targets(ROOT/'build/state-opt',['p6_cdtv_boot_probe'],optimized=True)['p6_cdtv_boot_probe']
        result=subprocess.run([str(exe),main,ext],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        observed={row[1]:int(row[2]) for line in result.stdout.splitlines() if (row:=line.split()) and row[0]=='pref'}
        profile=json.loads((ROOT/'config/profiles/cdtv-ecs.json').read_text()); self.assertEqual(profile['machine_id'],10)
        self.assertEqual(observed,profile['fields']); self.assertIn('CDTV firmware executed',result.stdout)
        self.assertIn('CD boot/autoconfig/state not asserted',result.stdout)


class CdtvOriginalBootTests(unittest.TestCase):
    def test_original_iso_hunk_executes_nonce(self):
        main,ext=(os.environ.get(key) for key in ('P6_CDTV_ROM_MAIN','P6_CDTV_ROM_EXT'))
        cue=os.environ.get('P6_CDTV_BOOT_CUE') or os.environ.get('P6_CD32_GAME_CUE') or os.environ.get('P6_CD_CUE_FILE')
        if not main or not ext or not cue:
            raise RuntimeError('BLOCKED: authorized CDTV main/ext ROMs and Brian CUE required')
        tm=cdtv_boot_image.trademark(cue)
        self.assertIn(hashlib.sha256(tm).hexdigest(), {
            'df6da5cea7c67fe02bdee1c9b7e3debc0406496ef99971b76b78b121d7d923c3',
            'bf7b79d89e981cca68eeced34f9c01c12d4065df02ad439678dafb9904c1c5cb'})
        exe=build_targets(ROOT/'build/state-opt',['p6_cdtv_boot_probe'],optimized=True)['p6_cdtv_boot_probe']
        with tempfile.TemporaryDirectory(prefix='cdtv-original-',dir=ROOT/'build') as directory:
            iso=Path(directory)/'original.iso'; iso.write_bytes(cdtv_boot_image.image(tm))
            limit=os.environ.get('P6_CDTV_BOOT_LIMIT')
            result=subprocess.run([str(exe),main,ext,str(iso),cdtv_boot_image.NONCE.hex(),*([limit] if limit else [])],
                                  capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('nonce read and guest Delay counter advanced',result.stdout)
