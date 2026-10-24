"""Copied blob storage only; zfile integration and media validation are later gates."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BlobTests(unittest.TestCase):
    def test_bounds_ownership_failures_c_and_cpp(self):
        with tempfile.TemporaryDirectory(prefix="p6-blob-") as directory:
            for language, standard, compiler in (("c", "c11", "clang"), ("c++", "c++17", "clang++")):
                for injected in (False, True):
                    with self.subTest(language=language, allocation_failure=injected):
                        obj, exe = Path(directory) / "blob.o", Path(directory) / "probe"
                        flags = ["-x", language, "-std="+standard, "-O2", "-Wall", "-Wextra", "-Werror",
                                 "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                 "-I", str(ROOT / "ports/portable")]
                        # Replace malloc in this test object only; the probe's
                        # allocator delegates to real libc except at the failpoint.
                        result = subprocess.run([compiler, *flags, *(["-Dmalloc=p6_test_malloc"] if injected else []),
                                                 "-c", str(ROOT / "ports/portable/blob_io.c"), "-o", str(obj)],
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        result = subprocess.run([compiler, *flags, *(["-DP6_FAULT_INJECTION"] if injected else []),
                                                 str(ROOT / "tests/host/blob_probe.c"), "-x", "none", str(obj), "-o", str(exe)],
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        subprocess.run([str(exe)], check=True)

    def test_no_host_io_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="p6-blob-symbols-") as directory:
            obj = Path(directory) / "blob.o"
            subprocess.run(["clang", "-std=c11", "-O2", "-c", str(ROOT / "ports/portable/blob_io.c"), "-o", str(obj)], check=True)
            result = subprocess.run(["nm", "-u", str(obj)], check=True, text=True, capture_output=True)
            symbols = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            # Darwin SDK may fortify the bounded-by-valid_id strcpy call.
            self.assertLessEqual(symbols, {"malloc", "free", "memset", "bzero", "memcpy", "memmove",
                                           "strcpy", "strcpy_chk", "strcmp", "strchr", "error", "errno_location",
                                           "stack_chk_fail", "stack_chk_guard"})


if __name__ == "__main__":
    unittest.main()
