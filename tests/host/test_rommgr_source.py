"""Compile the complete ROM manager and inspect its portable host boundary."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class RomManagerSourceTests(unittest.TestCase):
    def test_complete_object_without_arcadia_paths_or_tchar_formatter(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-rommgr-source-") as directory:
            tmp = Path(directory)
            for optimization in ("-O0", "-O2"):
                obj, deps = tmp / "rommgr.o", tmp / "rommgr.d"
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", optimization,
                    "-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree),
                    "-MMD", "-MF", str(deps), "-c", str(tree / "rommgr.cpp"),
                    "-o", str(obj)], check=True, timeout=90)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                # Entire object: no linker section collection can hide dependencies.
                self.assertIn("zfile_fopen", imports)
                for forbidden in ("_stprintf", "scan_arcadia_rom", "fetch_nvrampath",
                                  "fetch_videopath", "addkeydir", "target_load_keyfile"):
                    self.assertNotIn(forbidden, imports)
                content = deps.read_text()
                self.assertIn(str(ROOT / "ports/portable/tchar.h"), content)
                allowed = str(tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", content.replace(allowed, ""))
                self.assertNotIn("threaddep", content)


if __name__ == "__main__":
    unittest.main()
