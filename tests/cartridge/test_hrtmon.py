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
from tools import prepare_hrtmon


class HrtMonBuiltinTests(unittest.TestCase):
    def test_generation_is_reproducible_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory(prefix='hrtmon-asset-', dir=ROOT/'build') as tmp:
            root = Path(tmp)
            source = root/'upstream/winuae/hrtmon.rom.cpp'
            source.parent.mkdir(parents=True)
            source.write_bytes((ROOT/'upstream/winuae/hrtmon.rom.cpp').read_bytes())
            output = prepare_hrtmon.prepare(root)
            first, stamp = output.read_bytes(), output.stat().st_mtime_ns
            self.assertEqual(prepare_hrtmon.prepare(root).read_bytes(), first)
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            raw = bytes(int(x,16) for x in re.findall(rb'0x([0-9a-fA-F]{2})', first))
            self.assertEqual((len(raw),raw[4:8]), (198732,b'HRT!'))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), prepare_hrtmon.RAW_SHA)
            output.write_bytes(b'changed generated output')
            self.assertEqual(prepare_hrtmon.prepare(root).read_bytes(), first)
            source.write_bytes(source.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError, 'pinned snapshot'):
                prepare_hrtmon.prepare(root)
            self.assertEqual(output.read_bytes(), first)

    def test_public_config_builtin_load_restore_identity_and_teardown(self):
        firmware = os.environ.get('P6_KICKSTART_A1200_ROM')
        if not firmware:
            self.skipTest('BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized local Kickstart')
        exe = build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='hrtmon-builtin-',dir=ROOT/'build') as tmp:
            twins = []
            for mode in ('builtin-write','builtin-read'):
                result = subprocess.run([str(exe),firmware,'-',mode,tmp],capture_output=True,text=True,timeout=120,
                                        env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-1200:]+result.stderr[-4000:])
                twins.append([line for line in result.stdout.splitlines() if line.startswith('HRTMon builtin continuation ')])
                self.assertEqual(len(twins[-1]),1)
                self.assertIn('HRTMon builtin config/identity/teardown PASS',result.stdout)
            self.assertEqual(*twins)


class HrtMonCommandTests(unittest.TestCase):
    def test_public_freeze_level_survives_commands_return_reset_and_cold_restore(self):
        firmware = os.environ.get('P6_KICKSTART_A1200_ROM')
        if not firmware:
            self.skipTest('BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized local Kickstart')
        exe = build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='hrtmon-commands-',dir=ROOT/'build') as tmp:
            twins = []
            for mode in ('commands-write','commands-read'):
                result = subprocess.run([str(exe),firmware,'-',mode,tmp],capture_output=True,text=True,timeout=180,
                                        env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-1200:]+result.stderr[-4000:])
                twins.append([line for line in result.stdout.splitlines() if line.startswith('HRTMon command continuation ')])
                self.assertEqual(len(twins[-1]),1)
                self.assertIn('HRTMon command edges/held/reset/ABI/refusals PASS',result.stdout)
            self.assertEqual(*twins)


class HrtMonTests(unittest.TestCase):
    def test_complete_native_state_cuts_command_return_cold_owners_and_refusals(self):
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
            for mode in ("write","read"):
                result = subprocess.run([str(exe),firmware,str(cart),mode,tmp],capture_output=True,text=True,timeout=300,
                                        env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-1200:]+result.stderr[-4000:])
                twins.append([line for line in result.stdout.splitlines() if line.startswith('HRTMon continuation ')])
                self.assertEqual(len(twins[-1]),7)
                self.assertIn('HRTMon refusal/teardown and A1200 without cartridge PASS',result.stdout)
            self.assertEqual(*twins)


class ArOwnerTests(unittest.TestCase):
    def test_private_ar23_owner_loads_copied_rom_and_refuses_states(self):
        from tests.fixtures.test_microrom import builder, SOURCE
        exe = build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='ar-owner-',dir=ROOT/'build') as tmp:
            firmware=Path(tmp)/'original.rom'; firmware.write_bytes(builder.build(SOURCE))
            result=subprocess.run([str(exe),str(firmware),'-','ar-owner',tmp],capture_output=True,text=True,timeout=90,
                                  env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
            self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
            self.assertIn('AR2/3 copied owner/banks/refusals/cleanup PASS',result.stdout)


def ar_state_firmware():
    from tests.fixtures.test_microrom import builder
    # Original byte-only boot, stack within stock Chip RAM. TST.B waits for
    # the host signal; the first long write is deferred until after cut0.
    source='''entry:
    MOVE.W #$2700,SR
    MOVEA.L #$7F000,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #0,$1100C.L
wait:
    .word $4A39,$0001,$100C,$67F8
count:
    .word $52B9,$0001,$1000
    BRA.S count
'''
    return builder.build(source)


def ar1_images():
    from tests.fixtures.test_microrom import builder
    firmware = builder.build("""entry:
    MOVE.W #$2700,SR
    MOVEA.L #$7F000,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #0,$11000.L
    MOVE.L #0,$11010.L
    MOVE.B #0,$11014.L
    MOVEA.L #main,A5
    .word $4EF9,$00F0,$0002
main:
    .word $52B9,$0001,$1000
    BRA.S main
""")
    cart = bytearray(65536); cart[:8] = bytes.fromhex('11114ef900f00100')
    # Bootstrap installs vector0x60 from cartridge code, so it is not a chipwrite trigger.
    bootstrap = builder.assemble("""wait:
    .word $4A39,$0001,$1010,$67F8
    MOVE.L #$F00200,$60.L
    .word $1039,$00F0,$0000,$4ED5
""")
    irq = builder.assemble("""irq:
    .word $52B9,$009F,$0100
wait:
    .word $4A39,$0001,$1011,$67F8
    .word $4A39,$0001,$1012,$6708
    .word $3039,$00F0,$0000
    BRA.S marked
byte:
    .word $1039,$00F0,$0000
marked:
    MOVE.B #1,$11013.L
hold:
    .word $4A39,$0001,$1014,$67F8
    MOVE.B #0,$11011.L
    MOVE.B #0,$11014.L
    RTE
""")
    cart[0x100:0x100+len(bootstrap)] = bootstrap; cart[0x200:0x200+len(irq)] = irq
    # AR1 sums signed big-endian words up to its last nonzero long (ar.cpp).
    checksum = sum(int.from_bytes(cart[n:n+2],'big',signed=True) for n in range(0,len(cart)-4,2)) & 0xffffffff
    assert checksum
    cart[-4:] = checksum.to_bytes(4,'big')
    return firmware,bytes(cart)


class Ar1StateTests(unittest.TestCase):
    def test_complete_ar1_private_owner_wait_pc_cold_state_and_refusals(self):
        exe=build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='ar1-state-',dir=ROOT/'build') as tmp:
            firmware,cart=ar1_images(); firmware_path=Path(tmp)/'original.rom'; cart_path=Path(tmp)/'ar1.rom'
            firmware_path.write_bytes(firmware); cart_path.write_bytes(cart); twins=[]
            for mode in ('ar1-write','ar1-read'):
                result=subprocess.run([str(exe),str(firmware_path),str(cart_path),mode,tmp],capture_output=True,text=True,timeout=180,
                                      env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-2500:]+result.stderr[-4000:])
                twins.append([line for line in result.stdout.splitlines() if line.startswith('AR1 continuation ')])
                self.assertEqual(len(twins[-1]),16)
                self.assertIn('AR1 private owner/cuts/refusals/teardown PASS',result.stdout)
            self.assertEqual(*twins)


class ArStateTests(unittest.TestCase):
    def test_complete_ar23_semantic_cold_state_and_refusals(self):
        exe=build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='ar-state-',dir=ROOT/'build') as tmp:
            firmware=Path(tmp)/'original.rom'; firmware.write_bytes(ar_state_firmware())
            twins=[]
            for mode in ('ar-write','ar-read'):
                result=subprocess.run([str(exe),str(firmware),'-',mode,tmp],capture_output=True,text=True,timeout=180,
                                      env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
                twins.append([line for line in result.stdout.splitlines() if line.startswith('AR continuation ')])
                self.assertEqual(len(twins[-1]),36)
            self.assertEqual(*twins)


class NativeArTests(unittest.TestCase):
    def test_public_ar23_freeze_return_real_firmware_and_cold_state(self):
        names=('P6_KICKSTART_A500_ROM','P6_ACTION_REPLAY2_ROM','P6_ACTION_REPLAY3_ROM')
        assets=[os.environ.get(name) for name in names]
        if not all(assets):
            self.skipTest('BLOCKED: set authorized local '+', '.join(names))
        for path in assets:
            self.assertTrue(Path(path).is_file(),path)
        # The editor call-frame addresses in the real-ROM fixture are version-specific.
        for path,expected in zip(assets[1:],('255d6df63a4eab0a838eb1a16a267b0959dff634',
                                             '5d4987c2e3ffea8b1b02e31430ef190f2db76542')):
            self.assertEqual(hashlib.sha1(Path(path).read_bytes()).hexdigest(),expected,path)
        exe=build_targets(ROOT/'build/state-opt',['p6_hrtmon_probe'],optimized=True)['p6_hrtmon_probe']
        with tempfile.TemporaryDirectory(prefix='ar-public-',dir=ROOT/'build') as tmp:
            own=Path(tmp)/'original.rom'; own.write_bytes(ar_state_firmware())
            for kind,firmware,cart,count in (('own',str(own),'-',2),('real2',assets[0],assets[1],2),('real3',assets[0],assets[2],2)):
                twins=[]
                for mode in ('write','read'):
                    result=subprocess.run([str(exe),firmware,cart,'ar-public-'+kind+'-'+mode,tmp],capture_output=True,text=True,timeout=360,
                                          env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                    self.assertEqual(result.returncode,0,kind+'/'+mode+'\n'+result.stdout[-2500:]+result.stderr[-4000:])
                    twins.append([line for line in result.stdout.splitlines() if line.startswith('AR public continuation ')])
                    self.assertEqual(len(twins[-1]),count)
                    if kind=='own':
                        self.assertIn('AR public bind/copy/commands/held/reset/ABI/refusals PASS',result.stdout)
                self.assertEqual(*twins,kind)
