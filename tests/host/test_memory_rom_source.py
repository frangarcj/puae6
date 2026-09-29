"""Compile memory fully and execute its explicit ROM-source delegation."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MemoryRomSourceTests(unittest.TestCase):
    def test_explicit_rom_source_no_path_fallback(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["-UNDEBUG", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        sanitize = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-memory-rom-source-") as directory:
            directory = Path(directory)
            for optimization in ("-O0", "-O2"):
                blob = directory / "blob.o"
                subprocess.run(["clang", "-std=c11", *common, *sanitize, optimization,
                                "-c", str(ROOT / "ports/portable/blob_io.c"),
                                "-o", str(blob)], check=True, timeout=30)
                exe = directory / "probe"
                subprocess.run(["clang++", "-std=c++17", *common, *sanitize, optimization,
                                "-ffunction-sections", "-fdata-sections", strip,
                                str(ROOT / "tests/host/memory_rom_source_probe.cpp"),
                                str(tree / "zfile.cpp"), str(tree / "crc32.cpp"),
                                str(blob), "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)
                obj = directory / "memory.o"
                deps = directory / "memory.d"
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                "-MMD", "-MF", str(deps), "-c", str(tree / "memory.cpp"),
                                "-o", str(obj)], check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                self.assertIn("read_rom_name", imports)
                for forbidden in ("start_path_data", "read_rom_name_guess", "rom_fopen", "_stprintf",
                                  "arosrom", "seriallog", "zfile_gunzip", "addkeydir",
                                  "read_executable_rom", "uae_restart"):
                    self.assertNotIn(forbidden, imports)
                content = deps.read_text()
                self.assertIn(str(ROOT / "ports/portable/tchar.h"), content)
                allowed = str(tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", content.replace(allowed, ""))
                self.assertNotIn("threaddep", content)


if __name__ == "__main__":
    unittest.main()
