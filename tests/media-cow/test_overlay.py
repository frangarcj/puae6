"""MED02, the store: the write overlay of ports/portable/blob_io.c on its own (sanitizers on), see overlay_probe.c."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OverlayStoreTests(unittest.TestCase):
    def test_the_store_keeps_the_base_and_refuses_what_does_not_fit(self):
        with tempfile.TemporaryDirectory(prefix="p6-overlay-") as directory:
            exe = Path(directory) / "probe"
            flags = ["-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                     "-I", str(ROOT / "ports/portable"), "-I", str(ROOT / "ports/portable/media")]
            built = subprocess.run(["clang", *flags, str(ROOT / "tests/media-cow/overlay_probe.c"), str(ROOT / "ports/portable/blob_io.c"), "-o", str(exe)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("overlay ok", run.stdout)

    def test_the_store_links_against_nothing_but_the_allocator_and_string_primitives(self):
        with tempfile.TemporaryDirectory(prefix="p6-overlay-sym-") as directory:
            obj = Path(directory) / "blob.o"
            subprocess.run(["clang", "-std=c11", "-O2", "-I", str(ROOT / "ports/portable"), "-c", str(ROOT / "ports/portable/blob_io.c"), "-o", str(obj)], check=True)
            out = subprocess.run(["nm", "-u", str(obj)], check=True, text=True, capture_output=True).stdout
            symbols = {line.split()[-1].lstrip("_") for line in out.splitlines() if line.strip()}
            self.assertLessEqual(symbols, {"malloc", "calloc", "free", "memset", "bzero", "memcpy", "memmove", "strcpy", "strcpy_chk", "strcmp", "strchr", "error",
                                           "errno_location", "stack_chk_fail", "stack_chk_guard"})


if __name__ == "__main__":
    unittest.main()
