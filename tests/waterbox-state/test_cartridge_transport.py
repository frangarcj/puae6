"""Original cartridge programs through the real additive Waterbox init, never native states."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools'), str(ROOT/'tests/waterbox-smoke')]
import build_waterbox as wb
from test_puae6 import runtime_available
from tests.cartridge.test_hrtmon import ar1_images, ar_state_firmware
from tests.fixtures.test_microrom import builder


def images(model):
    firmware, original = ar1_images() if model == 1 else (ar_state_firmware(), bytes(131072 if model == 2 else 262144))
    cart = bytearray(original)
    base, ram, last = (0xf00000, 0x9f0000, 0x9fffff) if model == 1 else ((0x800000, 0x880000, 0x8fffff) if model == 1200 else (0x400000, 0x440000, 0x44ffff))
    if model == 1200:
        cart[:12] = b'ATZ!HRT!'+(base+0x200).to_bytes(4, 'big')
    elif model in (2, 3):
        cart[0x7c:0x80] = (base+0x200).to_bytes(4, 'big')
    # AR1 starts inside its bootstrap; other models start in Kick and enter only on Freeze.
    if model == 1:
        boot = builder.assemble(f'''MOVE.L #0,${ram+0x100:X}.L
    MOVE.B #0,${last:X}.L
wait:
    .word $4A39,$0001,$1010,$67F8
    MOVE.L #${base+0x200:X},$60.L
    .word $1039,$00F0,$0000,$4ED5
''')
        cart[0x100:0x100+len(boot)] = boot
    exit_code = '.word $1039,$00F0,$0000' if model == 1 else ('.word $13FC,$0000,$0040,$0006' if model != 1200 else '')
    # Every entry reads and increments the final RAM byte before copying it to Chip.
    # A subsequent entry must see and increment this byte, proving the cut preserved cartRAM, not merely its Chip shadow.
    cause = '.word $33F9,$008C,$0000,$0001,$1026' if model == 1200 else ''
    irq = builder.assemble(f'''{cause}
    MOVE.B ${last:X}.L,D0
    .word $0C00,$0000,$6608
    MOVE.B #$A6,${last:X}.L
    .word $5239,${last>>16:04X},${last&65535:04X}
    .word $52B9,${ram>>16:04X},$0100
    .word $23F9,${ram>>16:04X},$0100,$0001,$1020
    .word $13F9,${last>>16:04X},${last&65535:04X},$0001,$1024
    MOVE.B #1,$11025.L
wait:
    .word $4A39,$0001,$100C,$67F8
    {exit_code}
    RTE
''')
    cart[0x200:0x200+len(irq)] = irq
    if model == 1:
        checksum = sum(int.from_bytes(cart[n:n+2], 'big', signed=True) for n in range(0, len(cart)-4, 2)) & 0xffffffff
        cart[-4:] = checksum.to_bytes(4, 'big')
    return firmware, bytes(cart)


@unittest.skipUnless(runtime_available(), 'current real Waterbox runtime/guest required')
class CartridgeTransportTests(unittest.TestCase):
    def test_all_models_init_freeze_and_cold_waterbox_state(self):
        receipt = wb.BUILD/'puae6/puae6-receipt.json'
        wb.check_guest_matches_trees(receipt)
        info = json.loads(receipt.read_text()); guest = Path(info['guest']['path'])
        self.assertEqual(wb.sha256(guest), info['guest']['sha256'])
        self.assertEqual(wb.validate_elf(guest)['machine'], 'EM_AARCH64')
        self.assertEqual(wb.check_no_compiler_tls(guest)['wb16'], 'PASS')
        self.assertEqual(set(wb.puae6_sources()), set(info['sources']))
        for name, digest in info['sources'].items():
            self.assertEqual(wb.sha256(ROOT/name), digest, name)
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
        self.assertEqual(wb.sha256(Path(runtime['path'])), runtime['sha256'])
        with tempfile.TemporaryDirectory(prefix='cartridge-wb-', dir=ROOT/'build') as tmp:
            directory = Path(tmp)
            legacy = directory/'legacy.rom'; legacy.write_bytes(ar_state_firmware())
            for model in (1, 2, 3, 1200):
                firmware, cart = images(model)
                fw, raw = directory/'kick.rom', directory/'cart.rom'; fw.write_bytes(firmware); raw.write_bytes(cart)
                twins = []
                for mode in ('write', 'read'):
                    result = wb.run_ffi('cartridges', '--runtime', str(runtime['path']), '--guest', str(guest), '--sha', info['guest']['sha256'],
                                        '--firmware', str(fw), '--cartridge', str(raw), '--model', str(model), '--states', str(directory), '--mode', mode, '--legacy', str(legacy))
                    self.assertEqual(result.returncode, 0, result.stdout[-6000:]+result.stderr[-6000:])
                    self.assertIn('cartridge transport PASS', result.stdout)
                    cuts = re.findall(r'^cartridge continuation .*?\n(.*?)\nend cartridge$', result.stdout, re.M | re.S)
                    self.assertEqual(len(cuts), 2)
                    self.assertTrue(all(len(cut.splitlines()) == 16 for cut in cuts))
                    twins.append(cuts)
                self.assertEqual(*twins, f'model {model}: cold new PID, all units/AV/PCM/ticks/RAM/full snapshots')
