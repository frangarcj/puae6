"""Private native CD32 boot gate; required user ROMs are never distributed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.build_support import ROOT, build_targets
from tests.host.test_bridge_lifecycle import builder


class Cd32BootTests(unittest.TestCase):
    def test_stock_profile_combined_rom_refusals_cd_reads_and_a1200_regression(self):
        main=os.environ.get('P6_CD32_ROM_MAIN'); ext=os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext: self.skipTest('BLOCKED: set P6_CD32_ROM_MAIN and P6_CD32_ROM_EXT to authorized stock ROMs')
        exe=build_targets(ROOT/'build/link-audit',['p6_cd32_boot_probe'])['p6_cd32_boot_probe']
        with tempfile.TemporaryDirectory(prefix='cd32-boot-',dir=ROOT/'build') as tmp:
            directory=Path(tmp); rom=directory/'micro.rom'; rom.write_bytes(builder.build((ROOT/'tests/fixtures/microrom/boot.s').read_text()))
            synthetic=directory/'public.iso'; synthetic.write_bytes(bytes(32*2048))
            profile=json.loads((ROOT/'config/profiles/cd32-aga.json').read_text())
            self.assertEqual((profile['machine_id'],profile['fields']['nr_floppies'],profile['fields']['cs_ciaoverlay']),(9,0,0))
            content=os.environ.get('P6_CD_CUE_FILE',str(synthetic))
            result=subprocess.run([str(exe),main,ext,content,str(rom)],capture_output=True,text=True,timeout=180,
                                  env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
            self.assertEqual(0,result.returncode,result.stdout[-1000:]+result.stderr[-4000:])
            observed={parts[1]:int(parts[2]) for line in result.stdout.splitlines() if (parts:=line.split()) and parts[0]=='pref'}
            self.assertEqual(profile['fields'],observed)
            self.assertIn('CD32 firmware executed',result.stdout); self.assertIn('payload_reads=',result.stdout)
            self.assertIn('game boot not asserted',result.stdout); self.assertIn('A1200 reload PASS',result.stdout)
