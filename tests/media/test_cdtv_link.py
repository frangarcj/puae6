"""Private CDTV MMIO proof: real controller/backend and deterministic fresh processes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.build_support import ROOT, build_targets
from tests.host.test_bridge_lifecycle import builder


class CdtvLinkTests(unittest.TestCase):
    def test_real_dmac_commands_dma_and_lifecycle(self):
        exe = build_targets(ROOT / 'build/link-audit', ['p6_cdtv_link_probe'])['p6_cdtv_link_probe']
        with tempfile.TemporaryDirectory(prefix='cdtv-link-', dir=ROOT / 'build') as tmp:
            rom = Path(tmp) / 'boot.rom'
            rom.write_bytes(builder.build((ROOT / 'tests/fixtures/microrom/boot.s').read_text()))
            outputs = []
            for _ in range(2):
                run = subprocess.run([str(exe), str(rom)], capture_output=True, text=True, timeout=120,
                                     env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
                self.assertEqual(0, run.returncode, run.stdout + run.stderr)
                outputs.append(next(line for line in run.stdout.splitlines() if line.startswith('real CDTV DMAC')))
            self.assertEqual(outputs[0], outputs[1])
