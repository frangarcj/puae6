"""Real blitter data paths and whole-unit compile; no DMA arbitration claim."""
from pathlib import Path
import platform
import subprocess
import unittest
import tempfile

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class BlitterTests(unittest.TestCase):
    def test_without_debugger(self):
        ensure_generated()
        tree, generated = ROOT / "build/upstream", ROOT / "build/generated"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(generated), "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-blitter-") as tmp:
            obj, exe = Path(tmp) / "blitter.o", Path(tmp) / "probe"
            subprocess.run([*common, "-O0", "-c", str(tree / "blitter.cpp"),
                            "-o", str(obj)], check=True, timeout=30)
            imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                     capture_output=True, check=True).stdout
            for forbidden in ("debug_putpeekdma", "record_dma", "activate_debugger", "pthread", "sleep"):
                self.assertNotIn(forbidden, imports)
            # All passive tags retain their definitions, with or without DEBUGGER.
            source = '#include "sysconfig.h"\n#include "sysdeps.h"\n#include "debug.h"\n'
            tags = []
            pristine = ROOT / "upstream/winuae/include/debug.h"
            sources = (source, source, source.replace('"debug.h"', '"' + str(pristine) + '"'))
            for text, extra in zip(sources, ([], ["-DDEBUGGER"], ["-DDEBUGGER"])):
                macros = subprocess.run([*common, *extra, "-x", "c++", "-E", "-dM", "-"],
                                        input=text, text=True, capture_output=True, check=True).stdout
                tags.append({line for line in macros.splitlines() if line.startswith("#define MW_MASK_")})
            self.assertTrue(tags[0]); self.assertEqual(tags[0], tags[1])
            self.assertEqual(tags[0], tags[2])  # Unchanged from the pinned header.
            for optimization in ("-O0", "-O2"):
                flags = [optimization, "-ffunction-sections", "-fdata-sections",
                         "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                # The probe includes the entire source to reach static functions.
                # Generated routines and tables are linked unchanged, with no stubs.
                subprocess.run([*common, *flags, strip, str(ROOT / "tests/host/blitter_probe.cpp"),
                                str(generated / "blitfunc.cpp"), str(generated / "blittable.cpp"),
                                "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
