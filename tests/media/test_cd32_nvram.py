"""Real Akiko MMIO, device registry, EEPROM and per-session NVRAM ownership."""
import subprocess
import unittest
from tests.media.test_akiko_service import built_probe


class Cd32NvramTests(unittest.TestCase):
    def test_mmio_reset_lifecycle_and_private_restore(self):
        with built_probe('cd32_nvram_probe.cpp', reset=True) as exe:
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
