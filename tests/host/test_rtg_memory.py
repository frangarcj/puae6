"""The static RTG VRAM is a real WinUAE addrbank, not a host allocation."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated


ROOT = Path(__file__).resolve().parents[2]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]


class RtgMemoryTests(unittest.TestCase):
    def test_big_endian_bank_bounds_mapping_and_rebind(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        includes = ["-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-rtg-memory-") as directory:
            temporary = Path(directory)
            for optimization in ("-O0", "-O2"):
                executable = temporary / ("probe" + optimization)
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                    *SANITIZERS, optimization, *includes,
                    str(ROOT / "ports/portable/rtg_device.cpp"),
                    str(ROOT / "ports/portable/rtg_memory.cpp"),
                    str(ROOT / "tests/host/rtg_memory_probe.cpp"),
                    "-o", str(executable),
                ], check=True, timeout=120, capture_output=True)
                result = subprocess.run(
                    [str(executable)], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(result.returncode, 0, result.stderr[-1200:])

    def test_bank_object_does_not_import_allocation_or_host_services(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-rtg-bank-object-") as directory:
            obj = Path(directory) / "rtg_memory.o"
            subprocess.run([
                "clang++", "-std=c++17", "-O2",
                "-I" + str(ROOT / "ports/portable"),
                "-I" + str(ROOT / "build/generated"),
                "-I" + str(tree / "include"), "-I" + str(tree),
                "-c", str(ROOT / "ports/portable/rtg_memory.cpp"), "-o", str(obj),
            ], check=True, timeout=120, capture_output=True)
            imports = subprocess.run(
                ["nm", "-u", str(obj)], check=True, capture_output=True, text=True).stdout
            normalized = {line.split()[-1].lstrip("_") for line in imports.splitlines() if line.split()}
            for forbidden in ("malloc", "calloc", "realloc", "free", "pthread_create",
                              "clock_gettime", "gettimeofday", "time", "sleep", "usleep",
                              "VirtualAlloc", "GetWriteWatch"):
                self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()
