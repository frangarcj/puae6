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
        # Normal/Compatible/CE for 000/010/020; Normal only for 030/040/060.
        # JIT, MMU and unsupported combinations remain null.
        table = re.search(r"cputbls\[6\]\[8\]\s*=\s*(\{.*?\});", pp, re.S)
        self.assertIsNotNone(table)
        expected = (
            (5, None, None, 12, 14, None, None, None),
            (4, None, None, 11, 13, None, None, None),
            (3, None, None, 20, 21, None, None, None),
            (2, None, None, None, None, None, None, None),
            (1, None, None, None, None, None, None, None),
            (0, None, None, None, None, None, None, None),
        )
        rows = re.findall(r"\{([^{}]*)\}", table.group(1))
        self.assertEqual(len(rows), len(expected))
        for row, backends in zip(rows, expected):
            cells = [cell.strip() for cell in row.split(",")]
            self.assertEqual(len(cells), len(backends))
            for cell, backend in zip(cells, backends):
                if backend is None:
                    self.assertIn(cell, {"__null", "nullptr", "0"})
                else:
                    self.assertEqual(cell, f"op_smalltbl_{backend}")
        expected_tables = {f"op_smalltbl_{n}" for row in expected for n in row if n is not None}
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
                self.assertEqual(tables, expected_tables)
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
