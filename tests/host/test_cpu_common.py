"""Whole common CPU unit compilation; selective execution is not engine linkage."""
import hashlib
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


class CPUCommonTests(unittest.TestCase):
    def test_common_helpers_without_excluded_backends(self):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, tree = materialize(ROOT, "build/t06-common/" + key)
        common = ["clang++", "-std=c++17", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-cpu-common-") as tmp:
            obj, exe = Path(tmp) / "common.o", Path(tmp) / "probe"
            subprocess.run([*common, "-O0", "-c", str(tree / "newcpu_common.cpp"),
                            "-o", str(obj)], check=True, timeout=30)
            result = subprocess.run(["nm", "-u", str(obj)], text=True,
                                    capture_output=True, check=True)
            imports = {line.split()[-1].lstrip("_") for line in result.stdout.splitlines() if line.strip()}
            # Exact imports of the entire object, before any test dead stripping.
            # mmufixup is common CPU storage defined in newcpu.cpp, not a backend.
            # The 68040 cache fetch helpers are not imported: patch 0050 guards x_get_disp_ea_040.
            self.assertEqual(imports, {
                "Z14set_cpu_cachesb", "Z15memory_get_bytej", "Z15memory_get_longj",
                "Z15memory_get_wordj", "Z15memory_put_bytejj", "Z15memory_put_longjj",
                "Z15memory_put_wordjj", "Z19Exception_cpu_oldpcij", "Z7op_illgj", "Z9Exceptioni", "Z9write_logPKcz",
                "currprefs", "mmufixup", "regflags", "regs", "table68k", "x_cp_get_byte",
                "x_cp_get_long", "x_cp_get_word", "x_cp_put_byte", "x_cp_put_long",
                "x_cp_put_word", "x_get_long", "x_next_ilong", "x_next_iword", "x_put_long", "x_put_word"})
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-ffunction-sections", "-fdata-sections",
                         "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                subprocess.run([*common, *flags, "-c", str(tree / "newcpu_common.cpp"),
                                "-o", str(obj)], check=True, timeout=30)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                subprocess.run([*common, *flags, strip, str(ROOT / "tests/host/cpu_common_probe.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"), str(obj), "-o", str(exe)],
                               check=True, timeout=30)
                subprocess.run([str(exe)], check=True, timeout=20)


if __name__ == "__main__":
    unittest.main()
