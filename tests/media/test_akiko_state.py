"""Real aggregate cut/rebind/continuation; outer RAM/Paula/scheduler are explicit fixtures."""
import subprocess
import unittest
from tests.media.test_akiko_service import built_probe


class AkikoStateTests(unittest.TestCase):
    def test_joint_controller_transport_and_cdda_restore(self):
        with built_probe('akiko_state_probe.cpp', reset=True) as exe:
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
