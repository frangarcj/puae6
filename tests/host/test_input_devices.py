"""Logical input devices: shape and mappings, then the real inputdevice.cpp/keybuf.cpp on top."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]
SAN = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]


def flags(tree):
    return ["-std=c++17", "-UNDEBUG", "-fno-fast-math", "-I" + str(ROOT / "ports/portable"),
            "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"), "-I" + str(tree)]


class InputDeviceTests(unittest.TestCase):
    def test_devices_and_default_mappings(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-input-devices-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                provider, probe, exe = tmp / f"prov{opt}.o", tmp / f"probe{opt}.o", tmp / f"run{opt}"
                subprocess.run(["clang++", *flags(tree), *SAN, opt, "-Wall", "-Wextra", "-Werror", "-c",
                                str(ROOT / "ports/portable/input_devices.cpp"), "-o", str(provider)],
                               check=True, timeout=180)
                subprocess.run(["clang++", *flags(tree), *SAN, opt, "-Wno-missing-field-initializers", "-c",
                                str(ROOT / "tests/host/input_devices_probe.cpp"), "-o", str(probe)],
                               check=True, timeout=180)
                subprocess.run(["clang++", *SAN, str(probe), str(provider), "-o", str(exe)],
                               check=True, timeout=180)
                run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120,
                                     env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(run.returncode, 0, run.stderr[-600:])

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O undefined-symbol policy of the harness")
    def test_real_engine_units_run_on_the_providers(self):
        # The harness links the unmodified, patched inputdevice.cpp and keybuf.cpp with the
        # providers and stand-ins for code paths it never enters (dynamic_lookup makes a stray
        # call fail loudly at run time instead of at link time).
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-input-harness-") as directory:
            tmp = Path(directory)
            objects = []
            for name, source in (("provider", ROOT / "ports/portable/input_devices.cpp"),
                                 ("inputdevice", tree / "inputdevice.cpp"), ("keybuf", tree / "keybuf.cpp"),
                                 ("harness", ROOT / "tests/host/input_engine_harness.cpp")):
                obj = tmp / (name + ".o")
                subprocess.run(["clang++", *flags(tree), *SAN, "-O0", "-g", "-Wno-missing-field-initializers",
                                "-c", str(source), "-o", str(obj)], check=True, timeout=300,
                               capture_output=True)
                objects.append(str(obj))
            exe = tmp / "harness"
            subprocess.run(["clang++", *SAN, "-Wl,-undefined,dynamic_lookup", *objects, "-o", str(exe)],
                           check=True, timeout=180, capture_output=True)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120,
                                 env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(run.returncode, 0, run.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
