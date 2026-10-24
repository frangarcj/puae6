"""Compile full CPUEMU13; selectively link real A500 handlers for microtests."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CPUHandlerTests(unittest.TestCase):
    def test_generated_handlers_without_fpu_mmu(self):
        # The generator verifies the pristine snapshot and current patch/recipe.
        ensure_generated()
        tree, generated = ROOT / "build/upstream", ROOT / "build/generated"
        common = ["clang++", "-std=c++17", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(generated), "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-cpu-handlers-") as tmp:
            obj, exe = Path(tmp) / "cpu.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-ffunction-sections", "-fdata-sections",
                         "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                subprocess.run([*common, *flags, "-c", str(generated / "cpuemu_13.cpp"),
                                "-o", str(obj)], check=True, timeout=180)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout.lower()
                # CPUEMU13 also contains 68010 handlers. Its four explicit
                # SFC/DFC *nommu* accessors are not an MMU backend.
                mmu_names = {line.split()[-1].lstrip("_") for line in imports.splitlines() if "mmu" in line}
                self.assertLessEqual(mmu_names, {
                    "z18dfc_nommu_put_bytejj", "z18dfc_nommu_put_wordjj",
                    "z18sfc_nommu_get_bytej", "z18sfc_nommu_get_wordj"})
                for forbidden in ("fpu", "pthread", "sleep", "target_", "uae_time", "printf"):
                    self.assertNotIn(forbidden, imports)
                # Other opcodes remain unresolved at the engine gate. Dead stripping
                # is specific to this focused harness, never used as link proof.
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                subprocess.run([*common, *flags, strip, str(ROOT / "tests/host/cpu_handlers_probe.cpp"),
                                str(tree / "od-win32/machdep/m68k.cpp"), str(obj), "-o", str(exe)],
                               check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
