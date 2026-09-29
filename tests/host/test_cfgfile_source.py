"""Exercise cfgfile's real diagnostic queue and audit its complete P6 object."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

ERROR_LOG_PROBE = r'''#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
static std::vector<std::string> logged;
TCHAR *my_strdup(const TCHAR *s)
{
    const size_t n = strlen(s) + 1;
    TCHAR *copy = static_cast<TCHAR *>(malloc(n));
    if (copy) memcpy(copy, s, n);
    return copy;
}
int uaetcslen(const TCHAR *s) { return (int)strlen(s); }
void write_log(const TCHAR *format, ...)
{
    char buffer[8192];
    va_list ap; va_start(ap, format);
    int n = vsnprintf(buffer, sizeof buffer, format, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= sizeof buffer) abort();
    logged.emplace_back(buffer);
}
#include "cfgfile_error_log_under_test.h"
static void check(bool ok, const char *message)
{
    if (!ok) { fprintf(stderr, "error-log content mismatch: %s\n", message); abort(); }
}
int main()
{
    error_log(NULL);
    check(!is_error_log() && get_error_log() == NULL, "initial/null clear");
    logged.clear();
    const std::string left(700, 'L'), right(540, 'r');
    const std::string long_line = left + ":0012abcd:" + right;
    error_log("alpha");
    error_log("%s-%04d", "beta", 7);
    error_log("%s:%08x:%s", left.c_str(), 0x12abcd, right.c_str());
    error_log("");
    check(is_error_log(), "queue nonempty");
    TCHAR *actual = get_error_log();
    const std::string expected = "\n" + long_line + "\nbeta-0007\nalpha\n";
    check(actual && expected == actual, "LIFO/list formatting, empty and long args");
    xfree(actual);
    check(logged.size() == 4 && logged[0] == "alpha\n" &&
          logged[1] == "beta-0007\n" && logged[2] == long_line + "\n" &&
          logged[3] == "\n", "write_log order and formatted messages");
    error_log(NULL);
    check(!is_error_log() && get_error_log() == NULL, "clear releases queue");
    return 0;
}
'''


def braced(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def error_log_source(source):
    bodies = [braced(source, sig) for sig in (
        "bool is_error_log (void)", "TCHAR *get_error_log (void)",
        "void error_log (const TCHAR *format, ...)")]
    return "static struct strlist *error_lines;\n" + "".join(bodies)


class CfgfileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        cls.tree = ROOT / "build/upstream"

    def test_real_error_log_long_format_order_and_clear(self):
        patched = (self.tree / "cfgfile.cpp").read_text()
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                  "-fno-sanitize-recover=all", "-I{tmp}",
                  "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(self.tree / "include"), "-I" + str(self.tree)]
        with tempfile.TemporaryDirectory(prefix="p6-cfgfile-error-log-") as directory:
            tmp = Path(directory)
            (tmp / "cfgfile_error_log_probe.cpp").write_text(ERROR_LOG_PROBE)
            env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
            (tmp / "cfgfile_error_log_under_test.h").write_text(error_log_source(patched))
            for opt in ("-O0", "-O2"):
                exe = tmp / "patched"
                args = [arg.format(tmp=tmp) for arg in common]
                subprocess.run([*args, opt, "-DP6_PORTABLE",
                                str(tmp / "cfgfile_error_log_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=90)
                after = subprocess.run([str(exe)], capture_output=True, text=True,
                                       env=env, timeout=20)
                self.assertEqual(after.returncode, 0, after.stderr)

    def test_complete_cfgfile_object_host_boundary(self):
        with tempfile.TemporaryDirectory(prefix="p6-cfgfile-object-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                obj, deps = tmp / "cfgfile.o", tmp / "cfgfile.d"
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", opt,
                    "-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(self.tree / "include"), "-I" + str(self.tree),
                    "-MMD", "-MF", str(deps), "-c", str(self.tree / "cfgfile.cpp"),
                    "-o", str(obj)], check=True, timeout=120)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                depfile = deps.read_text()
                self.assertIn("write_log", imports)
                self.assertNotIn("threaddep", depfile)
                self.assertNotIn("ide.h", depfile)
                allowed = str(self.tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", depfile.replace(allowed, ""))
                for required in ("default_prefs(", "built_in_prefs(", "built_in_chipset_prefs(",
                                 "copy_prefs(", "discard_prefs(",
                                 "cfgfile_compatibility_romtype(", "config_check_vsync(",
                                 "error_log(", "get_error_log(", "is_error_log("):
                    self.assertRegex(symbols, r"(?m)\bT _?" + re.escape(required))
                for forbidden in ("cfgfile_load(", "cfgfile_save(",
                                  "cfgfile_getconfigdata(", "parse_cmdline_option(",
                                  "cfgfile_save_options(", "cfgfile_parse_line(",
                                  "cfgfile_parse_option(", "cmdlineparser("):
                    self.assertNotIn(forbidden, symbols)
                for forbidden in ("pthread_", "clock_gettime", "gettimeofday",
                                  "parse_cmdline", "cmdlineparser", "socket", "getaddrinfo",
                                  "ethernet", "zfile_", "fopen", "fread", "fwrite",
                                  "strtol", "strtoul", "atoi", "atof"):
                    self.assertNotIn(forbidden, imports)


if __name__ == "__main__":
    unittest.main()
