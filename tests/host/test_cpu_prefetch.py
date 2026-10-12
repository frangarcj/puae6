"""Whole CPU translation plus real shared 68000 prefetch/trace helpers."""
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CPUPrefetchTests(unittest.TestCase):
    def test_complete_unit_and_68000_prefetch(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format", "-Werror=uninitialized",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        pp = subprocess.run([*common, "-E", "-P", str(tree / "newcpu.cpp")],
                            text=True, capture_output=True, check=True).stdout
        # Real translation: all absent cells are zero-initialized by C++, and
        # the sole compiled dispatch target is the generated 68000 CE table.
        table = re.search(r"cputbls\[6\]\[8\]\s*=\s*(\{.*?\});", pp, re.S)
        self.assertIsNotNone(table)
        self.assertEqual(re.findall(r"op_smalltbl_\d+", table.group(1)), ["op_smalltbl_14", "op_smalltbl_13", "op_smalltbl_21"])   # 68000 CE, 68010 CE (T52, patch 0310) and 68020 CE (T24a)
        cells = re.search(r"\{\s*\{(.*?)\}", table.group(1), re.S).group(1).split(",")
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells[4].strip(), "op_smalltbl_14")
        # Check the entire supported assignment block stayed byte-for-byte.
        pristine = (ROOT / "upstream/winuae/newcpu.cpp").read_text()
        source = (tree / "newcpu.cpp").read_text()
        start = "\t\t\tx_prefetch = get_word_ce000_prefetch;"
        end = "\t\t\tx_do_cycles_post = do_cycles_ce_post;"
        self.assertEqual(source.split(start)[1].split(end)[0], pristine.split(start)[1].split(end)[0])
        with tempfile.TemporaryDirectory(prefix="p6-prefetch-") as tmp:
            obj, exe = Path(tmp) / "cpu.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "newcpu.cpp"), "-o", str(obj)], check=True)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True, capture_output=True, check=True).stdout
                tables = set(re.findall(r"op_smalltbl_\d+\b", imports))
                self.assertLessEqual(tables, {"op_smalltbl_13", "op_smalltbl_14", "op_smalltbl_21"})
                for forbidden in ("pthread", "is_mainthread", "statusline", "gettimeofday", "clock_gettime"):
                    self.assertNotIn(forbidden, imports)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                # Selective linkage belongs only to this microtest, not the core.
                subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/cpu_prefetch_probe.cpp"),
                                str(ROOT / "ports/portable/atomic.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"),
                                str(tree / "od-win32/machdep/m68k.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
