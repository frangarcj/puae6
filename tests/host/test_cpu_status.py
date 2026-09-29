"""A500 CPU backend closure and real SR/reset/bus-error helpers."""
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CPUStatusTests(unittest.TestCase):
    def test_a500_status_without_mmu_fpu_backends(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-status-") as tmp:
            obj, exe = Path(tmp) / "cpu.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "newcpu.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                self.assertIsNone(re.search(
                    r"mmu|fpu|(?:tc|tt0|tt1|srp|crp)_030|cache_default|get_word_030_prefetch", imports), imports)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                # Only this helper probe uses section GC, never the engine link.
                subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/cpu_status_probe.cpp"),
                                str(ROOT / "ports/portable/atomic.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"),
                                str(ROOT / "ports/portable/gui.cpp"),
                                str(tree / "newcpu_common.cpp"),
                                str(tree / "readcpu.cpp"),
                                str(ROOT / "build/generated/cpudefs.cpp"),
                                str(tree / "od-win32/machdep/m68k.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
