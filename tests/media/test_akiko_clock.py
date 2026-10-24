"""Actual Akiko reset and cycle-driven clock; no complete-machine/state claim."""
import subprocess
import unittest
from tests.media.test_akiko_service import built_probe


class AkikoClockTests(unittest.TestCase):
    def test_reset_after_a_partial_phase_matches_clean_reset(self):
        with built_probe('akiko_clock_probe.cpp', reset=True) as exe:
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
