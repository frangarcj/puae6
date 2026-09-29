"""Compile and execute the portable engine accessors, not a mock emulator."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MemoryAccessTests(unittest.TestCase):
    def test_c_cpp_optimized_and_sanitized(self):
        with tempfile.TemporaryDirectory(prefix="p6-maccess-") as directory:
            for language, compiler, standard in (("c", "clang", "c11"), ("c++", "clang++", "c++17")):
                for instrumented in (False, True):
                    with self.subTest(language=language, sanitizers=instrumented):
                        output = Path(directory) / "probe"
                        flags = ["-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"] if instrumented else ["-O3", "-fstrict-aliasing"]
                        subprocess.run([compiler, "-x", language, "-std=" + standard, "-Wall", "-Wextra", "-Werror",
                                        *flags, "-I", str(ROOT / "ports/portable"),
                                        str(ROOT / "tests/host/maccess_probe.c"), "-o", str(output)], check=True, capture_output=True)
                        subprocess.run([str(output)], check=True, capture_output=True,
                                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0", "UBSAN_OPTIONS": "halt_on_error=1"})


if __name__ == "__main__":
    unittest.main()
