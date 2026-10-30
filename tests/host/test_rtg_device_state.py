"""Versioned, pointer-free semantic state of the bounded RTG device."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]


class RtgDeviceStateTests(unittest.TestCase):
    def test_transactional_restore_and_identical_future(self):
        with tempfile.TemporaryDirectory(prefix="p6-rtg-state-") as directory:
            executable = Path(directory) / "probe"
            subprocess.run([
                "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math", "-O1",
                *SANITIZERS, "-I" + str(ROOT / "ports/portable"),
                str(ROOT / "ports/portable/rtg_device.cpp"),
                str(ROOT / "tests/host/rtg_device_state_probe.cpp"),
                "-o", str(executable),
            ], check=True, timeout=120, capture_output=True)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=120,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stderr[-1600:])


if __name__ == "__main__":
    unittest.main()
