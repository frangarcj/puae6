"""Portable text helpers and the UAE interrupt-request word, under sanitizers."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class TextServicesTests(unittest.TestCase):
    def test_helpers_and_interrupt_word(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["-std=c++17", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                  "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-text-services-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                exe, hooked = tmp / ("probe" + opt), tmp / ("text_services" + opt + ".o")
                # Only the unit under test sees the allocation hook.
                subprocess.run(["clang++", *common, opt, "-Dmalloc=p6_text_test_malloc", "-c",
                                str(ROOT / "ports/portable/text_services.cpp"), "-o", str(hooked)],
                               check=True, timeout=120)
                subprocess.run(["clang++", *common, opt, str(hooked),
                                str(ROOT / "tests/host/text_services_probe.cpp"),
                                str(ROOT / "ports/portable/atomic.cpp"), "-o", str(exe)],
                               check=True, timeout=120)
                run = subprocess.run([str(exe)], check=True, capture_output=True, text=True,
                                     timeout=60, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(run.stdout, "text-services-ok\n")

    def test_object_defines_exactly_the_promised_symbols(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-text-object-") as directory:
            names = {}
            for source in ("text_services.cpp", "atomic.cpp"):
                obj = Path(directory) / (source + ".o")
                subprocess.run(["clang++", "-std=c++17", "-O0", "-I" + str(ROOT / "ports/portable"),
                                "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                                "-I" + str(tree), "-c", str(ROOT / "ports/portable" / source),
                                "-o", str(obj)], check=True, timeout=60)
                out = subprocess.run(["nm", "-C", "-g", "--defined-only", str(obj)], check=True,
                                     capture_output=True, text=True).stdout
                names[source] = {line.split(" ", 2)[2].lstrip("_") for line in out.splitlines()}
                imports = subprocess.run(["nm", "-u", "-C", str(obj)], check=True, capture_output=True,
                                         text=True).stdout
                for forbidden in ("gettimeofday", "time", "setlocale", "iconv", "wcstombs"):
                    self.assertNotIn(forbidden, {l.strip().lstrip("_") for l in imports.splitlines()})
            self.assertEqual(names["text_services.cpp"],
                             {"uaetcslen(char const*)", "my_strdup(char const*)", "au(char const*)", "uaestrlen(char const*)", "ua(char const*)",
                              "ua_copy(char*, int, char const*)", "ua_fs(char const*, int)", "ua_fs_copy(char*, int, char const*, int)",
                              "au_fs(char const*)", "au_fs_copy(char*, int, char const*)", "samepath(char const*, char const*)"})
            self.assertIn("uae_int_requested", names["atomic.cpp"])


if __name__ == "__main__":
    unittest.main()
