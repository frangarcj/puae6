"""Private CDTV firmware/profile only; authorized ROMs stay outside Git."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from tests.build_support import ROOT, build_targets


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
