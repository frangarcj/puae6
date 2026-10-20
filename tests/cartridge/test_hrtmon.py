"""Run only with an authorized local A1200 Kickstart; the GPL builtin stays in build/."""
import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from tests.build_support import ROOT, build_targets


class HrtMonTests(unittest.TestCase):
    def test_real_monitor_command_return_twins_refusals_and_no_cart_state(self):
        firmware = os.environ.get('P6_KICKSTART_A1200_ROM')
        if not firmware:
            self.skipTest('BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized local Kickstart 2+/3.x')
        source = (ROOT/'upstream/winuae/hrtmon.rom.cpp').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), 'f3cdc921cb33d9f7b16197d66a182dda63a107e331032fb30ffad96dc98b9367')
        packed = bytes(int(x,16) for x in re.findall(rb'0x([0-9a-fA-F]{2})', source))
        self.assertEqual(len(packed), 41970)
        raw = gzip.decompress(packed)
        self.assertEqual((len(raw),raw[4:8]), (198732,b'HRT!'))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '15e3e106248822665cc64f5c9a3ed5f52a67c07f81b06eb7118da39099d171f5')
        exe = build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='hrtmon-',dir=ROOT/'build') as tmp:
            cart = Path(tmp)/'hrtmon.rom'; cart.write_bytes(raw)
            twins = []
            for _ in range(2):
                result = subprocess.run([str(exe),firmware,str(cart)],capture_output=True,text=True,timeout=300,
                                        env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-1200:]+result.stderr[-4000:])
                twins.append(next(line for line in result.stdout.splitlines() if line.startswith('HRTMon Freeze/command/return twins ')))
                self.assertIn('HRTMon refusal/teardown and A1200 without cartridge PASS',result.stdout)
            self.assertEqual(*twins)
