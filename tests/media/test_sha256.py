"""T12a: the dependency-free SHA-256 (ports/portable/media/sha256.c) against FIPS 180-4 and hashlib.

The C file is compiled together with a tiny driver (clang, -std=c11 -Wall -Wextra -Werror), once plain and once with
ASan+UBSan. The driver reads a file and prints one digest per chunk size given: 0 is the one-shot p6_sha256_hash, N feeds
p6_sha256_update in pieces of N bytes (the last one shorter). With --sweep it prints them for every prefix length
0..size of the file (one process instead of hundreds). The expected values are the NIST vectors written out below and,
for everything else, hashlib on the same bytes.
"""
import hashlib
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "ports/portable/media"
FLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SRC)]
CLANG = shutil.which("clang")

DRIVER = r"""
#include "sha256.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void digest(const unsigned char *data, uint64_t size, uint64_t chunk)
{
 uint8_t out[32];
 if (!chunk) p6_sha256_hash(data, size, out);
 else {
  p6_sha256 c;
  p6_sha256_init(&c);
  for (uint64_t at = 0; at < size; at += chunk) {
   uint64_t n = size - at < chunk ? size - at : chunk;
   p6_sha256_update(&c, data + at, n);
  }
  p6_sha256_update(&c, NULL, 0);
  p6_sha256_final(&c, out);
 }
 for (int i = 0; i < 32; ++i) printf("%02x", out[i]);
 printf("\n");
}

int main(int argc, char **argv)
{
 int sweep = argc > 2 && !strcmp(argv[2], "--sweep");
 if (argc < 3 + sweep) return 2;
 FILE *f = fopen(argv[1], "rb");
 if (!f) return 3;
 fseek(f, 0, SEEK_END);
 long size = ftell(f);
 fseek(f, 0, SEEK_SET);
 unsigned char *data = size ? malloc((size_t)size) : NULL; /* NULL exercises data == NULL with bytes == 0 */
 if (size && (!data || fread(data, 1, (size_t)size, f) != (size_t)size)) return 4;
 fclose(f);
 for (long n = sweep ? 0 : size; n <= size; ++n) {
  /* A private copy of exactly n bytes, so ASan sees any read past the end. */
  unsigned char *copy = n ? malloc((size_t)n) : NULL;
  if (n) memcpy(copy, data, (size_t)n);
  for (int a = 2 + sweep; a < argc; ++a) digest(copy, (uint64_t)n, strtoull(argv[a], NULL, 10));
  free(copy);
 }
 free(data);
 return 0;
}
"""

# FIPS 180-4 / NIST example values (the hashlib comparison below would catch a wrong transcription).
M448 = b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"
M896 = (b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmn"
        b"hijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu")
VECTORS = [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (M448, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"),
    (M896, "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"),
    (b"a" * 1000000, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"),
]
CHUNKS = [0, 1, 63, 64, 65]


@unittest.skipUnless(CLANG, "clang not available")
class Sha256(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-sha256-")
        cls.dir = Path(cls.temp.name)
        (cls.dir / "driver.c").write_text(DRIVER)
        cls.exe = {}
        for name, extra in (("plain", ["-O2"]), ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined",
                                                                "-fno-sanitize-recover=undefined"])):
            exe = cls.dir / ("sha256_" + name)
            subprocess.run([CLANG, *FLAGS, *extra, "-o", str(exe), str(SRC / "sha256.c"), str(cls.dir / "driver.c")],
                           check=True, capture_output=True, text=True)
            cls.exe[name] = exe

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def digests(self, build, data, chunks=(0,), sweep=False):
        path = self.dir / "input.bin"
        path.write_bytes(data)
        run = subprocess.run([str(self.exe[build]), str(path), *(["--sweep"] if sweep else []), *map(str, chunks)],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-600:])
        lines = run.stdout.split()
        self.assertEqual(len(lines), len(chunks) * (len(data) + 1 if sweep else 1))
        return lines

    def check(self, build, data, chunks=CHUNKS):
        want = hashlib.sha256(data).hexdigest()
        for chunk, got in zip(chunks, self.digests(build, data, chunks)):
            self.assertEqual(got, want, "%s, %d bytes, chunk %d" % (build, len(data), chunk))

    def test_nist_vectors(self):
        for build in self.exe:
            for data, want in VECTORS:
                self.assertEqual(hashlib.sha256(data).hexdigest(), want)
                for chunk, got in zip(CHUNKS, self.digests(build, data, CHUNKS)):
                    self.assertEqual(got, want, "%s, %d bytes, chunk %d" % (build, len(data), chunk))

    def test_size_sweep(self):
        # Every length around the padding boundaries (55/56/63/64/119/120...) with all the split patterns.
        rng = random.Random(0x5A256)
        pool = bytes(rng.randrange(256) for _ in range(300))
        for build in self.exe:
            got = self.digests(build, pool, CHUNKS, sweep=True)
            for size in range(301):
                want = hashlib.sha256(pool[:size]).hexdigest()
                self.assertEqual(got[size * len(CHUNKS):(size + 1) * len(CHUNKS)], [want] * len(CHUNKS),
                                 "%s, %d bytes" % (build, size))

    def test_chunk_patterns_across_blocks(self):
        rng = random.Random(0xC0FFEE)
        data = bytes(rng.randrange(256) for _ in range(1000))
        for build in self.exe:
            self.check(build, data, [0, 1, 2, 7, 55, 56, 57, 63, 64, 65, 127, 128, 129, 999, 1000, 4096])

    def test_large_buffer(self):
        # 901120 bytes = 880 KiB, the size of a standard DD floppy image.
        data = random.Random(880 * 1024).randbytes(901120)
        for build in self.exe:
            self.check(build, data, [0, 1, 63, 64, 65, 512])

    def test_header_from_cplusplus(self):
        clangxx = shutil.which("clang++")
        if not clangxx:
            self.skipTest("clang++ not available")
        source = self.dir / "cxx.cpp"
        source.write_text('#include "sha256.h"\n'
                          "static_assert(sizeof(p6_sha256) >= 32 + 8 + 64 + 4, \"layout\");\n"
                          "uint8_t f(const void *d) { uint8_t o[32]; p6_sha256_hash(d, 0, o); return o[0]; }\n")
        subprocess.run([clangxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(SRC), "-fsyntax-only",
                        str(source)], check=True, capture_output=True, text=True)

    def test_no_libc_symbols(self):
        # Compiled with optimisation, the object must not import anything (no memcpy/memset the compiler
        # might have recognised in the byte loops). The stack protector's own symbols are a toolchain default, not a call.
        nm = shutil.which("nm")
        if not nm:
            self.skipTest("nm not available")
        for opt in ("-O0", "-O2", "-Os"):
            obj = self.dir / ("sha256%s.o" % opt)
            subprocess.run([CLANG, *FLAGS, opt, "-fno-stack-protector", "-c", "-o", str(obj), str(SRC / "sha256.c")],
                           check=True, capture_output=True, text=True)
            undefined = subprocess.run([nm, "-u", str(obj)], capture_output=True, text=True, check=True).stdout
            self.assertEqual(undefined.strip(), "", "%s imports: %s" % (opt, undefined))


if __name__ == "__main__":
    unittest.main()
