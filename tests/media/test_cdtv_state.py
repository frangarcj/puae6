"""Private component and complete native cold restore/future scenarios."""
import subprocess
import tempfile
from pathlib import Path
import unittest
from tests.build_support import ROOT, build_targets
from tests.host.test_bridge_lifecycle import builder


class CdtvStateComponentTests(unittest.TestCase):
    def test_local_roundtrip_and_transactional_refusals(self):
        exe = build_targets(ROOT / 'build/link-audit', ['p6_cdtv_state_probe'])['p6_cdtv_state_probe']
        with tempfile.TemporaryDirectory(prefix='cdtv-state-', dir=ROOT / 'build') as tmp:
            rom = Path(tmp) / 'boot.rom'
            rom.write_bytes(builder.build((ROOT / 'tests/fixtures/microrom/boot.s').read_text()))
            run = subprocess.run([str(exe), str(rom), 'local'], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertIn('CDTV local component roundtrip and transactional refusals ok', run.stdout)


class CdtvStateTests(unittest.TestCase):
    def test_cold_restore_and_future(self):
        exe = build_targets(ROOT / 'build/link-audit', ['p6_cdtv_state_probe'])['p6_cdtv_state_probe']
        with tempfile.TemporaryDirectory(prefix='cdtv-native-', dir=ROOT / 'build') as tmp:
            rom = Path(tmp) / 'boot.rom'
            rom.write_bytes(builder.build((ROOT / 'tests/fixtures/microrom/boot.s').read_text()))
            for role in ('write', 'read'):
                run = subprocess.run([str(exe), str(rom), role, tmp], capture_output=True, text=True, timeout=240)
                self.assertEqual(0, run.returncode, run.stdout + run.stderr)
                self.assertEqual(7, run.stdout.count(f'{role} exact'))
