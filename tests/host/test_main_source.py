"""Whole engine main object, without the desktop launcher or seed clock."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]



def without_p6_guards(text):
    """Text with P6_PORTABLE guard lines and comment-only lines removed.

    `#ifndef P6_PORTABLE` regions keep their upstream lines; `#ifdef P6_PORTABLE ... #else`
    keeps only the upstream branch. What is left must equal the pristine function.
    """
    out, stack = [], []          # stack entries: "ifndef", "ifdef" (in P6 branch), "else", "other"
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#ifndef P6_PORTABLE"):
            stack.append("ifndef")
        elif s.startswith("#ifdef P6_PORTABLE"):
            stack.append("ifdef")
        elif s.startswith("#if"):
            stack.append("other")
            out.append(line)
        elif s.startswith("#else") and stack and stack[-1] in ("ifdef", "else"):
            stack[-1] = "else"
        elif s.startswith("#endif"):
            kind = stack.pop()
            if kind == "other":
                out.append(line)
        elif not s or s.startswith("//") or "ifdef" in stack:
            continue
        else:
            out.append(line)
    return "\n".join(out)

class MainSourceTests(unittest.TestCase):
    def test_complete_object_and_disabled_autoconfig_preferences(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "main.cpp").read_text()
        original = (ROOT / "upstream/winuae/main.cpp").read_text()
        for signature in ("void uae_reset (", "void uae_quit (", "void uae_restart("):
            self.assertEqual(without_p6_guards(function(source, signature)),
                             without_p6_guards(function(original, signature)))
        # Exercise the actual conditional block using the actual prefs layout.
        start = source.index("#ifndef AUTOCONFIG\n")
        end = source.index("#if !defined (BSDSOCKET)", start)
        cleanup = source[start:end]
        common = ["clang++", "-std=c++17", "-UNDEBUG",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-main-source-") as directory:
            tmp = Path(directory)
            probe = tmp / "prefs.cpp"
            probe.write_text('''#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include <cassert>
#include <cstring>
static void cleanup(uae_prefs *p) {
''' + cleanup + '''}
int main() {
    static uae_prefs prefs{}, expected{};
    prefs.chipmem.size=0x80000; prefs.bogomem.size=0x80000;
    prefs.seed=0x81234567; prefs.cpu_model=68000;
    expected=prefs;
    for (int i=0;i<MAX_RAM_BOARDS;i++) {
        prefs.fastmem[i].size=0x100000*(i+1);
        prefs.z3fastmem[i].size=0x1000000*(i+1);
    }
    for (int i=0;i<MAX_RTG_BOARDS;i++) prefs.rtgboards[i].rtgmem_size=0x200000*(i+1);
    cleanup(&prefs);
    assert(!memcmp(&prefs,&expected,sizeof(prefs)));
    cleanup(&prefs);
    assert(!memcmp(&prefs,&expected,sizeof(prefs)));
}
''')
            for optimization in ("-O0", "-O2"):
                obj, dep, exe = tmp / "main.o", tmp / "main.d", tmp / "prefs"
                subprocess.run([*common, optimization, "-MMD", "-MF", str(dep), "-c",
                                str(tree / "main.cpp"), "-o", str(obj)], check=True, timeout=90)
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                for required in ("currprefs", "changed_prefs", "uaerand", "uae_reset", "fixup_prefs"):
                    self.assertIn(required, symbols)
                for forbidden in ("getlocaltime", "real_main", "start_program(", "leave_program(",
                                  "parse_cmdline", "parse_diskswapper", "target_run", "graphics_setup",
                                  "setup_sound", "gui_init", "uae_sem_", "uae_start_thread",
                                  "cfgfile_createconfigstore", "cfgfile_addcfgparam"):
                    self.assertNotIn(forbidden, symbols)
                self.assertNotRegex(symbols, r"(?m)\bT _?main$")
                self.assertNotIn("m68k_go", imports)
                self.assertNotIn("threaddep", dep.read_text())
                allowed = str(tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", dep.read_text().replace(allowed, ""))
                subprocess.run([*common, optimization, "-fsanitize=address,undefined",
                                "-fno-sanitize-recover=all", str(probe), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
