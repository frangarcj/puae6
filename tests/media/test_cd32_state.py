"""Full native CD32 state; authorized ROMs, synthetic CD and no game-boot claim."""
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from tests.build_support import ROOT, build_targets


class Cd32StateTests(unittest.TestCase):
    def test_public_machine_restore_and_refusal(self):
        main=os.environ.get('P6_CD32_ROM_MAIN'); ext=os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext:
            self.skipTest('BLOCKED: set P6_CD32_ROM_MAIN and P6_CD32_ROM_EXT to authorized ROMs')
        exe=build_targets(ROOT/'build/state-opt',['p6_cd32_state_probe'],optimized=True)['p6_cd32_state_probe']
        result=subprocess.run([str(exe),main,ext],capture_output=True,text=True,timeout=180)
        self.assertEqual(0,result.returncode,result.stdout[-2000:]+result.stderr[-4000:])
        for mode in range(5):
            self.assertIn(f'native CD32 mode{mode} exact restore/new owner/identity/corrupt/map PASS',result.stdout)
        self.assertIn('native CD catalogue A/B empty/new owner/exact future/identity PASS',result.stdout)


class Cd32NrgStateTests(unittest.TestCase):
    def test_private_multisession_restore_new_process(self):
        from tests.media.test_cd_nrg import nrg_multisession_fixture
        main=os.environ.get('P6_CD32_ROM_MAIN'); ext=os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext:
            self.skipTest('BLOCKED: set P6_CD32_ROM_MAIN and P6_CD32_ROM_EXT to authorized ROMs')
        exe=build_targets(ROOT/'build/state-opt',['p6_cd32_state_probe'],optimized=True)['p6_cd32_state_probe']
        with tempfile.TemporaryDirectory(prefix='cd32-nrg-',dir=ROOT/'build') as temporary:
            work=Path(temporary)
            original,raw,_,directory,blocks=nrg_multisession_fixture(work,'original')
            renamed=nrg_multisession_fixture(work,'renamed')[0]
            source=original.read_bytes(); changed=bytearray(source)
            changed[directory+blocks[1]['DAO']+12]=ord('Z')
            catalog=work/'catalog.nrg'; catalog.write_bytes(changed)
            changed=bytearray(source); changed[raw[2][1]+17]^=1
            payload=work/'payload.nrg'; payload.write_bytes(changed)
            pids=[]
            for mode,path in (('write',original),('read',renamed)):
                command=[str(exe),main,ext,mode,str(path),str(catalog),str(payload),str(work/'cut')]
                with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
                    pids.append(process.pid)
                    try: stdout,stderr=process.communicate(timeout=180)
                    except subprocess.TimeoutExpired:
                        process.kill(); stdout,stderr=process.communicate(); self.fail(mode+' timed out: '+stdout[-2000:]+stderr[-4000:])
                    self.assertEqual(0,process.returncode,stdout[-2000:]+stderr[-4000:])
                    for cut in range(2): self.assertIn(f'native NRG multi cut{cut} {mode} exact/future/fullstate/identity PASS',stdout)
            self.assertNotEqual(*pids)
