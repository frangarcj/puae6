"""Native graph proof, real MMIO and A1200 load/frame regression."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.build_support import ROOT, build_targets
from tests.host.test_bridge_lifecycle import builder


class AkikoLinkTests(unittest.TestCase):
    def test_native_hardware_links_and_a1200_still_runs(self):
        exe = build_targets(ROOT / 'build/link-audit', ['p6_akiko_link_probe'])['p6_akiko_link_probe']
        with tempfile.TemporaryDirectory(prefix='akiko-link-', dir=ROOT / 'build') as tmp:
            rom = Path(tmp) / 'boot.rom'
            rom.write_bytes(builder.build((ROOT / 'tests/fixtures/microrom/boot.s').read_text()))
            result = subprocess.run([str(exe), str(rom)], capture_output=True, text=True, timeout=120,
                                    env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('real Akiko link, MMIO/C2P and A1200 reload/frame ok', result.stdout)
            self.assertIn('native backend CDDA PCM and completion ok', result.stdout)
