"""Exercise both production ROM-failure gates in the complete m68k_go body."""
from pathlib import Path
import platform
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


class CPURomGuardTests(unittest.TestCase):
    def test_prior_and_reset_time_failures_never_reach_cpu(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "newcpu.cpp").read_text()
        body = function(source, "void m68k_go (int may_quit)")
        self.assertEqual(body.count("p6_memory_rom_status()"), 2)
        self.assertLess(body.index("if (p6_memory_rom_status()"), body.index("reset_frame_rate_hack"))
        self.assertLess(body.index("custom_reset"),
                        body.rindex("if (p6_memory_rom_status()"))
        self.assertLess(body.rindex("if (p6_memory_rom_status()"), body.index("m68k_reset2"))

        with tempfile.TemporaryDirectory(prefix="p6-cpu-rom-guard-") as directory:
            tmp = Path(directory)
            (tmp / "cpu_rom_guard_under_test.h").write_text(body)
            common = ["clang++", "-std=c++17", "-UNDEBUG", "-ffunction-sections",
                      "-fdata-sections", "-fsanitize=address,undefined",
                      "-fno-sanitize-recover=all", "-I" + str(tmp),
                      "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                      "-I" + str(tree / "include"), "-I" + str(tree)]
            strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run([*common, optimization, strip,
                                str(ROOT / "tests/host/cpu_rom_guard_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=30,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
