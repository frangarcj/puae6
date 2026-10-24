"""Controller cut/continuation on real Akiko/EEPROM/audio/events; no machine codec."""
import subprocess
import unittest
from tests.media.test_akiko_service import built_probe


class AkikoControllerStateTests(unittest.TestCase):
    def test_partial_protocol_c2p_eeprom_and_clock_restore(self):
        with built_probe('akiko_controller_state_probe.cpp', reset=True) as exe:
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
