"""Full native CD32 state; authorized ROMs, synthetic CD and no game-boot claim."""
import os
import subprocess
import unittest
from tests.build_support import ROOT, build_targets


class Cd32StateTests(unittest.TestCase):
    def test_public_machine_restore_and_refusal(self):
        main=os.environ.get('P6_CD32_ROM_MAIN'); ext=os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext:
            self.skipTest('BLOCKED: set P6_CD32_ROM_MAIN and P6_CD32_ROM_EXT to authorized ROMs')
        exe=build_targets(ROOT/'build/link-audit',['p6_cd32_state_probe'])['p6_cd32_state_probe']
        result=subprocess.run([str(exe),main,ext],capture_output=True,text=True,timeout=180)
        self.assertEqual(0,result.returncode,result.stdout[-2000:]+result.stderr[-4000:])
        for mode in range(5):
            self.assertIn(f'native CD32 mode{mode} exact restore/new owner/identity/corrupt/map PASS',result.stdout)
        self.assertIn('native CD catalogue A/B empty/new owner/exact future/identity PASS',result.stdout)
