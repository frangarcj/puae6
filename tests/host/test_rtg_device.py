"""Bounded RTG software device: formats, rectangles, panning and host closure."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]


class RtgDeviceTests(unittest.TestCase):
    def test_all_modes_and_checked_operations(self):
        with tempfile.TemporaryDirectory(prefix="p6-rtg-device-") as directory:
            temporary = Path(directory)
            for optimization in ("-O0", "-O2"):
                executable = temporary / ("probe" + optimization)
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                    *SANITIZERS, optimization,
                    "-I" + str(ROOT / "ports/portable"),
                    str(ROOT / "ports/portable/rtg_device.cpp"),
                    str(ROOT / "tests/host/rtg_device_probe.cpp"),
                    "-o", str(executable),
                ], check=True, timeout=120, capture_output=True)
                result = subprocess.run(
                    [str(executable)], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(result.returncode, 0, result.stderr[-1200:])

    def test_object_has_no_allocator_thread_clock_or_host_graphics_imports(self):
        with tempfile.TemporaryDirectory(prefix="p6-rtg-object-") as directory:
            obj = Path(directory) / "rtg_device.o"
            subprocess.run([
                "clang++", "-std=c++17", "-O2",
                "-I" + str(ROOT / "ports/portable"), "-c",
                str(ROOT / "ports/portable/rtg_device.cpp"), "-o", str(obj),
            ], check=True, timeout=120, capture_output=True)
            imports = subprocess.run(
                ["nm", "-u", str(obj)], check=True, capture_output=True, text=True).stdout
            normalized = {line.split()[-1].lstrip("_") for line in imports.splitlines() if line.split()}
            for forbidden in (
                "malloc", "calloc", "realloc", "free", "operator new", "pthread_create",
                "clock_gettime", "gettimeofday", "time", "sleep", "usleep", "D3D11CreateDevice",
                "glTexImage2D", "VirtualAlloc", "GetWriteWatch",
            ):
                self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()
