"""Compile complete expansion and inspect its portable object boundary."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.host.test_cpu_rom_guard import function

ROOT = Path(__file__).resolve().parents[2]


class ExpansionSourceTests(unittest.TestCase):
    def test_complete_object_without_thread_header_or_tchar_formatter(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-expansion-source-") as directory:
            tmp = Path(directory)
            for optimization in ("-O0", "-O2"):
                obj, deps = tmp / "expansion.o", tmp / "expansion.d"
                subprocess.run([*common, optimization, "-MMD", "-MF", str(deps), "-c",
                                str(tree / "expansion.cpp"), "-o", str(obj)],
                               check=True, timeout=90)
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                # No link-time section collection can hide dependencies here.
                for required in ("expamem_reset", "expansion_init", "expansion_get_autoconfig_data",
                                 "expansion_autoconfig_move", "expansion_cpu_fallback"):
                    self.assertIn(required, symbols)
                self.assertIn("snprintf", imports)
                for forbidden in ("_stprintf", "uae_sem_", "uae_start_thread", "sleep_millis",
                                  "target_ethernet_enumerate", "gvp_init", "cdtv_init",
                                  "a2091_init", "expamem_init_filesys"):
                    self.assertNotIn(forbidden, imports)
                content = deps.read_text()
                self.assertIn(str(ROOT / "ports/portable/tchar.h"), content)
                self.assertNotIn("threaddep", content)
                allowed = str(tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", content.replace(allowed, ""))

    def test_actual_empty_catalog_and_bounded_device_lookup(self):
        tree = ROOT / "build/upstream"
        expansion = (tree / "expansion.cpp").read_text()
        rommgr = (tree / "rommgr.cpp").read_text()
        start = expansion.index("// The fixed A500 profile")
        catalog = expansion[start:expansion.index("#else", start)]
        queries = function(rommgr, "const struct expansionromtype *get_unit_expansion_rom(")
        queries += function(rommgr, "const struct expansionromtype *get_device_expansion_rom(")
        exporter = function((tree / "cfgfile.cpp").read_text(), "static void write_filesys_config (")
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                  "-fno-sanitize-recover=all", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-expansion-catalog-") as directory:
            tmp = Path(directory)
            src, exe = tmp / "catalog.cpp", tmp / "probe"
            for nonempty in (False, True):
                data = catalog
                if nonempty:
                    data = data.replace("expansionroms[] = { { NULL } }",
                                        'expansionroms[] = { { "one" }, { "two" }, { NULL } }')
                src.write_text('#include "sysconfig.h"\n#include "sysdeps.h"\n'
                    '#include "options.h"\n#include "memory.h"\n#include "autoconf.h"\n'
                    '#include "rommgr.h"\n#include "filesys.h"\n#include <cassert>\n'
                    'static int warnings; static void cfgfile_warning(const TCHAR *, ...) { ++warnings; }\n'
                    + data + queries + exporter + '''
int main() {
    static uae_prefs p{};
    write_filesys_config(&p, nullptr); assert(warnings==0);
    for (int count: {1, -1, 0x7fffffff}) {
        p.mountitems=count; write_filesys_config(&p, nullptr);
    }
    assert(warnings==3);
    assert(!cpuboards[0].subtypes[0].name && !cpuboards[0].subtypes[0].init);
    assert(!cpuboards[1].name);
    assert(!get_unit_expansion_rom(-1) && !get_unit_expansion_rom(0x7fffffff));
    const int ranges[][2] = {
        {HD_CONTROLLER_TYPE_SCSI_EXPANSION_FIRST, HD_CONTROLLER_TYPE_SCSI_LAST},
        {HD_CONTROLLER_TYPE_IDE_EXPANSION_FIRST, HD_CONTROLLER_TYPE_IDE_LAST},
        {HD_CONTROLLER_TYPE_CUSTOM_FIRST, HD_CONTROLLER_TYPE_CUSTOM_LAST}};
    for (const auto &range: ranges) for (int unit=range[0]; unit<=range[1]; unit++) {
        const int index=unit-range[0];
        const auto *found=get_unit_expansion_rom(unit);
        assert(found == (NONEMPTY && index<2 ? &expansionroms[index] : nullptr));
    }
    if (!NONEMPTY) {
        assert(!expansionroms[0].name);
        assert(!get_device_expansion_rom(ROMTYPE_CPUBOARD));
        assert(!get_device_expansion_rom(ROMTYPE_CD32CART));
    }
}
'''.replace("NONEMPTY", "true" if nonempty else "false"))
                for optimization in ("-O0", "-O2"):
                    subprocess.run([*common, optimization, str(src), "-o", str(exe)],
                                   check=True, timeout=60)
                    subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
