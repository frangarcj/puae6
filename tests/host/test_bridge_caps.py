"""Identity and capability reporting of the native ABI, without the engine."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BridgeCapsTests(unittest.TestCase):
    def test_contract_under_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix="p6-bridge-caps-") as directory:
            for opt in ("-O0", "-O2"):
                exe = Path(directory) / ("probe" + opt)
                subprocess.run(["clang++", "-std=c++17", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", opt,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-I" + str(ROOT / "bridge/include"),
                                str(ROOT / "bridge/caps.cpp"),
                                str(ROOT / "tests/host/bridge_caps_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=120)
                run = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=30,
                                     env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(run.stdout, "bridge-caps-ok\n")

    def test_bridge_does_not_reach_into_the_engine(self):
        with tempfile.TemporaryDirectory(prefix="p6-bridge-object-") as directory:
            obj = Path(directory) / "caps.o"
            subprocess.run(["clang++", "-std=c++17", "-O0", "-I" + str(ROOT / "bridge/include"), "-c",
                            str(ROOT / "bridge/caps.cpp"), "-o", str(obj)], check=True, timeout=60)
            imports = subprocess.run(["nm", "-u", "-C", str(obj)], check=True, capture_output=True,
                                     text=True).stdout
            names = {line.strip().lstrip("_") for line in imports.splitlines()}
            self.assertLessEqual(names, {"memcpy", "strlen", "memcpy_chk", "dyld_stub_binder"})
            header = (ROOT / "bridge/include/p6.h").read_text()
            for forbidden in ("libretro.h", "uae_prefs", "addrbank", "windows.h"):
                self.assertNotIn(forbidden, header)


if __name__ == "__main__":
    unittest.main()
