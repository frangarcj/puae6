"""Check the selected savestate object and its real ASF header prefix."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]

PROBE = r'''#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "zfile.h"
#include "savestate.h"
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
static std::vector<uae_u8> asf;
static std::string chunk_name;
static struct zfile *expected_file;
char *uutf8(const TCHAR *s)
{
    const size_t n = strlen(s) + 1;
    char *copy = static_cast<char *>(malloc(n));
    if (copy) memcpy(copy, s, n);
    return copy;
}
TCHAR *zfile_getname(struct zfile *f)
{ static TCHAR name[] = _T("probe.asf"); assert(f == expected_file); return name; }
static std::vector<std::string> logged;
void write_log(const TCHAR *format, ...)
{
    char buffer[256]; va_list ap; va_start(ap, format);
    vsnprintf(buffer, sizeof buffer, format, ap); va_end(ap); logged.emplace_back(buffer);
}
static void observed_save_chunk(struct zfile *f, uae_u8 *data, size_t len,
                                const TCHAR *name, int compress)
{
    assert(f == expected_file && compress == 0);
    chunk_name.assign(name, name + 4);
    asf.assign(data, data + len);
}
#define save_chunk observed_save_chunk
#include "savestate_under_test.h"
#undef save_chunk
static void check(bool ok, const char *message)
{
    if (!ok) { fprintf(stderr, "savestate prefix: %s\n", message); abort(); }
}
static void check_direct_writer_borders()
{
    uae_u8 bytes[] = {0xa5, 0, 0, 0, 0, 0x5a};
    uae_u8 *p = bytes + 1;
    save_u32_func(&p, 0x12345678);
    check(p == bytes + 5 && bytes[0] == 0xa5 && bytes[5] == 0x5a,
          "u32 pointer or surrounding sentinels");
    const uae_u8 expected[] = {0x12, 0x34, 0x56, 0x78};
    check(memcmp(bytes + 1, expected, sizeof expected) == 0, "big endian u32");
    uae_u8 string_bytes[8] = {0xcc, 0, 0, 0, 0xdd, 0xee, 0xff, 0x5a};
    p = string_bytes + 1;
    save_string_func(&p, _T("xy"));
    check(p == string_bytes + 4 && string_bytes[0] == 0xcc &&
          string_bytes[4] == 0xdd && string_bytes[5] == 0xee,
          "string length or surrounding sentinels");
    check(memcmp(string_bytes + 1, "xy", 2) == 0, "string bytes");
    uae_u8 empty_bytes[] = {0xab, 0xcd, 0xef};
    p = empty_bytes + 1;
    save_string_func(&p, _T(""));
    check(p == empty_bytes + 2 && empty_bytes[0] == 0xab &&
          empty_bytes[1] == 0 && empty_bytes[2] == 0xef,
          "empty string terminator and sentinels");
}
int main()
{
    check_direct_writer_borders();
    expected_file = reinterpret_cast<struct zfile *>(uintptr_t(1));
    const int len = save_state_prefix(expected_file, _T("probe description"), 0, false);
    const std::string expected = std::string("\0\0\0\0UAE\0" "6.0.3\0" "probe description\0", 8 + 6 + 18);
    check(len == (int)expected.size(), "ASF header payload length");
    check(chunk_name == "ASF ", "header chunk identifier");
    check(asf.size() == expected.size() &&
          memcmp(asf.data(), expected.data(), expected.size()) == 0,
          "ASF version/description/endian bytes");
    check(logged.size() == 1 && logged[0] == "STATESAVE (probe.asf):\n",
          "observed save log prefix");
    asf.clear();
    chunk_name.clear();
    logged.clear();
    const int empty_len = save_state_prefix(expected_file, _T(""), 0, false);
    const std::string empty_expected("\0\0\0\0UAE\0" "6.0.3\0\0", 8 + 6 + 1);
    check(empty_len == (int)empty_expected.size() && chunk_name == "ASF " &&
          asf.size() == empty_expected.size() &&
          memcmp(asf.data(), empty_expected.data(), empty_expected.size()) == 0,
          "empty ASF description");
    puts("savestate-prefix-ok");
    return 0;
}
'''


def body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def savestate_prefix(source):
    start = source.index("static int save_state_internal (")
    opening = source.index("{", start)
    stop = source.index("\n\tdst = save_cycles (&len, 0);", opening)
    prefix = source[start:stop].replace("static int save_state_internal (",
                                        "static int save_state_prefix (")
    return (body(source, "void save_u32_func(") +
            body(source, "void save_string_func (") +
            prefix + "\n\treturn (int)(dst - header);\n}\n")


class SavestateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        cls.tree = ROOT / "build/upstream"

    def test_complete_object_host_boundary(self):
        with tempfile.TemporaryDirectory(prefix="p6-savestate-object-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                obj, deps = tmp / "savestate.o", tmp / "savestate.d"
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-Werror=format", opt,
                    "-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(self.tree / "include"), "-I" + str(self.tree),
                    "-MMD", "-MF", str(deps), "-c", str(self.tree / "savestate.cpp"),
                    "-o", str(obj)], check=True, timeout=120)
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                # Mach-O reports these defined data symbols as S; never accept U.
                for required in ("savestate_state", "savestate_file"):
                    self.assertRegex(symbols, r"(?m)\b[BDS] _?" + required + r"$")
                for required in ("save_u32_func(", "save_u64_func(", "restore_u32_func(",
                                 "restore_u64_func(", "savestate_free(", "savestate_init(",
                                 "savestate_check("):
                    self.assertRegex(symbols, r"(?m)\bT _?" + re.escape(required))
                # Patch 0055 excludes the legacy .uss file machinery until the native serializer.
                for excluded in ("save_state(", "restore_state(", "save_string_func(",
                                 "save_path_func(", "savestate_initsave(", "savestate_rewind("):
                    self.assertNotIn(excluded, symbols)
                self.assertNotIn("savestate_quick", symbols)
                self.assertNotIn("_stprintf", imports)
                self.assertNotIn("save_cd", imports)
                self.assertNotIn("threaddep", deps.read_text())
                allowed = str(self.tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", deps.read_text().replace(allowed, ""))

    def test_real_asf_header_prefix_and_writers(self):
        sources = [
            (ROOT / "upstream/winuae/savestate.cpp").read_text(),
            (self.tree / "savestate.cpp").read_text(),
        ]
        with tempfile.TemporaryDirectory(prefix="p6-savestate-prefix-") as directory:
            tmp = Path(directory)
            (tmp / "savestate_probe.cpp").write_text(PROBE)
            for opt in ("-O0", "-O2"):
                outputs = []
                for name, source in zip(("reference", "patched"), sources):
                    (tmp / "savestate_under_test.h").write_text(savestate_prefix(source))
                    exe = tmp / name
                    flags = ["-D_stprintf=sprintf"] if name == "reference" else ["-DP6_PORTABLE"]
                    subprocess.run([
                        "clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=all", opt, *flags,
                        "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                        "-I" + str(ROOT / "build/generated"),
                        "-I" + str(self.tree / "include"), "-I" + str(self.tree),
                        str(tmp / "savestate_probe.cpp"), "-o", str(exe)],
                        check=True, timeout=90)
                    result = subprocess.run([str(exe)], check=True, capture_output=True,
                                            text=True, timeout=20,
                                            env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                    outputs.append(result.stdout)
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(outputs[0], "savestate-prefix-ok\n")


if __name__ == "__main__":
    unittest.main()
