"""Run the real P6 disk-asset validation path against immutable blob streams."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class DiskAssetTests(unittest.TestCase):
    def test_disk_validation_uses_exact_immutable_blob(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        body = function((tree / "disk.cpp").read_text(), "int DISK_validate_filename (")
        self.assertIn("P6_PORTABLE", body)
        # The executable probe compares against the file-scope legacy constant.
        exeheader = re.search(r"static uae_u8 exeheader\[\]=\{[^}]*\};\n", (tree / "disk.cpp").read_text())
        self.assertIsNotNone(exeheader)
        with tempfile.TemporaryDirectory(prefix="p6-disk-asset-") as directory:
            tmp = Path(directory)
            (tmp / "disk_asset_under_test.h").write_text(exeheader[0] + body)
            common = ["-UNDEBUG", "-DP6_PORTABLE", "-fsanitize=address,undefined",
                      "-fno-sanitize-recover=all", "-I" + str(tmp),
                      "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"),
                      "-I" + str(tree / "include"), "-I" + str(tree)]
            for optimization in ("-O0", "-O2"):
                blob, zfile, exe = tmp / "blob.o", tmp / "zfile.o", tmp / "probe"
                subprocess.run(["clang", "-std=c11", *common, optimization, "-c",
                                str(ROOT / "ports/portable/blob_io.c"), "-o", str(blob)],
                               check=True, timeout=30)
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                "-Dmalloc=p6_zfile_test_malloc",
                                "-Dcalloc=p6_zfile_test_calloc", "-c",
                                str(tree / "zfile.cpp"), "-o", str(zfile)],
                               check=True, timeout=60)
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                str(ROOT / "tests/host/disk_asset_probe.cpp"), str(zfile),
                                str(tree / "crc32.cpp"), str(blob), "-o", str(exe)],
                               check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=45,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
