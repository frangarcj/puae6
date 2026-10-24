"""Sanitizer-check the canonical Kickstart one's-complement checksum."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class RomChecksumTests(unittest.TestCase):
    def test_checksum_is_unsigned_and_rejects_corruption(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "rommgr.cpp").read_text()
        signature = "static int kickstart_checksum_do (uae_u8 *mem, int size)"
        body = function(source, signature)
        self.assertEqual(source.count(signature), 1)
        with tempfile.TemporaryDirectory(prefix="p6-rom-checksum-") as directory:
            tmp = Path(directory)
            (tmp / "rom_checksum_under_test.h").write_text(body)
            common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                      "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                      "-I" + str(tree)]
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run([*common, optimization,
                                str(ROOT / "tests/host/rom_checksum_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
