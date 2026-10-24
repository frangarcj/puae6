"""Exercise real ROM normalization and explicit keyring functions on P6 blobs."""
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


class RomInputTests(unittest.TestCase):
    def test_exact_rom_input_transforms_and_cloanto_key_lifecycle(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "rommgr.cpp").read_text()
        support = source[source.index("#define ROM_KEY_NUM 4"):source.index("void addkeyfile", source.index("#define ROM_KEY_NUM 4"))]
        pieces = [support]
        for signature in (
            "void addkeyfile (const TCHAR *path)", "int get_keyring (void)",
            "int load_keyring (struct uae_prefs *p, const TCHAR *path)",
            "void free_keyring (void)", "static int cmpsha1 (",
            "static struct romdata *checkromdata (", "static int kickstart_checksum_do (",
            "static int kickstart_checksum_more_do (", "int decode_cloanto_rom_do (",
            "static void byteswap(", "static void wordbyteswap (", "static void mergecd32 (",
            "struct zfile *rom_fopen(", "static struct zfile *rom_fopen2(",
            "struct zfile *read_rom_name("):
            pieces.append(function(source, signature))
        body = "\n".join(pieces)
        self.assertIn("#ifdef P6_PORTABLE", body)
        with tempfile.TemporaryDirectory(prefix="p6-rom-input-") as directory:
            tmp = Path(directory)
            (tmp / "rom_input_under_test.h").write_text(body)
            common = ["-UNDEBUG", "-DP6_PORTABLE", "-fsanitize=address,undefined",
                      "-fno-sanitize-recover=all", "-I" + str(tmp),
                      "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
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
                                str(ROOT / "tests/host/rom_input_probe.cpp"),
                                str(zfile), str(tree / "crc32.cpp"), str(blob),
                                "-o", str(exe)], check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=45,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
