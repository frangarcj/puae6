"""Self-test of the sanitizer setup: every deliberate defect must be reported, the control must stay silent.

This is separate from the engine on purpose. A sanitizer that does not fire on a known out-of-bounds write
proves nothing about the engine runs that come back clean. The same flags as the native-debug preset are used, and
the preset itself is checked to still ask for them.
"""
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", "-g", "-O0"]
# Either sanitizer may be the first to see a defect that both cover (UBSan checks constant array bounds before
# ASan sees the access), so each mode lists the reports that count.
EXPECTED = {
    "heap-overflow": ("AddressSanitizer: heap-buffer-overflow",),
    "stack-overflow": ("AddressSanitizer: stack-buffer-overflow", "out of bounds for type"),
    "global-overflow": ("AddressSanitizer: global-buffer-overflow", "out of bounds for type"),
    "use-after-free": ("AddressSanitizer: heap-use-after-free",),
    "signed-overflow": ("signed integer overflow",),
    "negative-shift": ("left shift of negative value",),
    "null-pointer-arithmetic": ("applying non-zero offset",),
    "misaligned-access": ("misaligned address",),
    "vector-out-of-bounds": ("AddressSanitizer: heap-buffer-overflow",),
}


@unittest.skipUnless(shutil.which("clang++"), "clang++ unavailable")
class CanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-canary-")
        cls.exe = Path(cls.tmp.name) / "canary"
        subprocess.run(["clang++", "-std=c++17", *FLAGS, str(ROOT / "tests/canaries/canary.cpp"), "-o", str(cls.exe)],
                       check=True, capture_output=True, text=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_mode(self, mode):
        return subprocess.run([str(self.exe), mode], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})

    def test_every_deliberate_defect_is_reported_and_stops_the_process(self):
        for mode, message in EXPECTED.items():
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(any(m in result.stderr for m in message), result.stderr[:300])

    def test_the_control_is_silent(self):
        result = self.run_mode("control")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_an_unknown_mode_is_not_a_pass(self):
        self.assertEqual(self.run_mode("no-such-mode").returncode, 64)

    def test_the_native_debug_preset_still_asks_for_the_same_instrumentation(self):
        presets = json.loads((ROOT / "CMakePresets.json").read_text())
        debug = next(p for p in presets["configurePresets"] if p["name"] == "native-debug")
        self.assertEqual(debug["cacheVariables"]["P6_SANITIZERS"], "ON")
        module = (ROOT / "cmake/Sanitizers.cmake").read_text()
        self.assertIn("-fsanitize=address,undefined -fno-sanitize-recover=all", module)
        code = "\n".join(line for f in (module, (ROOT / "CMakeLists.txt").read_text()) for line in f.splitlines()
                         if not line.lstrip().startswith("#"))
        for forbidden in (" -w ", "-Wno-", "-fsanitize-recover", "suppress", "-fno-sanitize="):
            self.assertFalse(forbidden in code.replace("-fno-sanitize-recover=all", ""), forbidden)


if __name__ == "__main__":
    unittest.main()
