"""Locale-free integer parsing and array-bounded formatting from ports/portable/tchar.h."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ["-I" + str(ROOT / "ports/portable")]

C_USER = r'''
#include "tchar.h"
int main(void)
{
    char b[8];
    char *end;
    long v = p6_tcstol("12", &end, 10);
    return (int)(v - 12) + p6_tstoi("0") + (int)p6_tstol("0") + (int)_tcstol("0", 0, 10)
         + (int)_tstol("0") + _tstoi("0") + p6_tchar_copy(b, sizeof b, "a", 1)
         + (int)(p6_ascii_toupper('a') - 'A') + p6_ascii_stricmp("a", "A")
         + p6_ascii_strnicmp("a", "A", 1) + (end == 0);
}
'''


class TcharNumericTests(unittest.TestCase):
    def test_probe_under_sanitizers_and_optimization(self):
        with tempfile.TemporaryDirectory(prefix="p6-tchar-num-") as directory:
            for opt in ("-O0", "-O2"):
                exe = Path(directory) / ("probe" + opt)
                result = subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", opt,
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", *INCLUDE,
                    str(ROOT / "tests/host/tchar_numeric_probe.cpp"), "-o", str(exe)],
                    capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60,
                                     env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stdout, "tchar-numeric-ok\n")

    def test_header_stays_valid_c(self):
        # The header is shared with C sources: no C++-only construct may leak into them.
        with tempfile.TemporaryDirectory(prefix="p6-tchar-c-") as directory:
            exe = Path(directory) / "c_user"
            result = subprocess.run(
                ["clang", "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                 "-fsanitize=address,undefined", *INCLUDE, "-", "-o", str(exe)],
                input=C_USER, text=True, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_pointer_destination_is_rejected_at_compile_time(self):
        source = '#include "tchar.h"\nint f(TCHAR *p) { return _stprintf(p, "%d", 5); }\n'
        result = subprocess.run(["clang++", "-std=c++17", "-fsyntax-only", *INCLUDE, "-x", "c++", "-"],
                                input=source, text=True, capture_output=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("p6_stprintf", result.stderr)

    def test_earlier_definition_wins_for_reference_builds(self):
        # Tests compile legacy bodies with -D_stprintf=sprintf; the header must not override it.
        source = '#include "tchar.h"\nint f(TCHAR *p) { return _stprintf(p, "%d", 5); }\n'
        result = subprocess.run(["clang++", "-std=c++17", "-fsyntax-only", "-Werror",
                                 "-Wno-deprecated-declarations", "-D_stprintf=sprintf", "-D_tstol=atol", *INCLUDE, "-x", "c++", "-"],
                                input=source, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_format_string_is_checked(self):
        source = '#include "tchar.h"\nint f(void) { TCHAR a[8]; return _stprintf(a, "%d", "text"); }\n'
        result = subprocess.run(["clang++", "-std=c++17", "-fsyntax-only", "-Werror=format", *INCLUDE,
                                 "-x", "c++", "-"], input=source, text=True, capture_output=True,
                                timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("format specifies type", result.stderr)


if __name__ == "__main__":
    unittest.main()
