"""Compile real rommgr naming/configuration bodies under the P6 text contract."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

ROOT = Path(__file__).resolve().parents[2]

PROBE = r'''
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "rommgr.h"
#include <cassert>
#include <climits>
#include <clocale>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

static struct romdata *metadata[8];
static TCHAR source_path[MAX_DPATH] = _T("selected.rom");
static bool path_available = true;
static unsigned path_queries, warning_calls;

#include "rommgr_portable_under_test.h"

struct romdata *getromdatabyid(int id)
{
    for (auto *rd : metadata) if (rd && rd->id == id) return rd;
    return nullptr;
}
TCHAR *romlist_get(const struct romdata *rd)
{
    assert(rd); ++path_queries;
    return path_available ? source_path : nullptr;
}
void romwarning(const int *ids) { assert(ids && ids[0] >= 0); ++warning_calls; }
void romwarning(int) { std::abort(); }
struct boardromconfig *get_device_rom_new(struct uae_prefs *, int, int, int *)
{ std::abort(); }
void set_device_rom(struct uae_prefs *, const TCHAR *, int, int) { std::abort(); }
void fetch_nvrampath(TCHAR *, int) { std::abort(); }
void fetch_videopath(TCHAR *, int) { std::abort(); }

static void check_name()
{
    TCHAR out[512];
    memset(out, 'x', sizeof out); getromname(nullptr, out); assert(!out[0]);

    struct romdata grouped[2]{};
    grouped[0].name = _T("Kickstart"); grouped[0].group = 0;
    grouped[1].name = _T("subentry"); grouped[1].group = 1;
    grouped[0].ver = 3; grouped[0].subver = 4; grouped[0].subrev = 5;
    grouped[0].size = 262145; grouped[0].partnumber = _T("315093-02");
    getromname(&grouped[1], out);
    assert(_tcscmp(out, _T("Kickstart rev 4.5 (257k) [315093-02]")) == 0);

    grouped[0].subver = grouped[0].ver; grouped[0].subrev = 0;
    grouped[0].size = 0; grouped[0].partnumber = nullptr;
    getromname(&grouped[1], out); assert(_tcscmp(out, _T("Kickstart")) == 0);
    grouped[0].subver = INT_MIN; grouped[0].subrev = INT_MAX;
    grouped[0].ver = INT_MAX; grouped[0].size = UINT32_MAX;
    getromname(&grouped[1], out);
    assert(_tcscmp(out, _T("Kickstart rev -2147483648.2147483647 (4194304k)")) == 0);

    std::string long_part(300, 'P'); grouped[0].subver = grouped[0].ver;
    grouped[0].partnumber = long_part.c_str();
    getromname(&grouped[1], out);
    assert(strlen(out) == strlen("Kickstart (4194304k) [") + long_part.size() + 1);
    assert(strstr(out, long_part.c_str()) != nullptr);

    const char *locales[] = {"C", "C.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8"};
    for (const char *locale : locales) {
        if (setlocale(LC_NUMERIC, locale)) {
            grouped[0].size = UINT32_MAX; grouped[0].partnumber = nullptr;
            getromname(&grouped[1], out);
            assert(strstr(out, "(4194304k)") != nullptr);
        }
    }
    setlocale(LC_NUMERIC, "C");
    grouped[0].name = "Kick \xc3\xb1";
    grouped[0].size = 0; grouped[0].partnumber = "\xff";
    getromname(&grouped[1], out);
    assert(!strcmp(out, "Kick \xc3\xb1 [\xff]")); // Opaque bytes, no transcoding.
}

static struct romdata devices[4];
static struct uae_prefs configure_prefs{}, configure_snapshot{};
static void configure_case(int device_index, unsigned type)
{
    auto &prefs = configure_prefs;
    memset(&prefs, 0, sizeof prefs);
    _tcscpy(prefs.romfile, _T("old-rom"));
    _tcscpy(prefs.romextfile, _T("old-ext"));
    _tcscpy(prefs.cartfile, _T("old-cart"));
    _tcscpy(prefs.flashfile, _T("old-flash"));
    _tcscpy(prefs.genlock_video_file, _T("old-video"));
    prefs.cs_compatible = 17; prefs.cpu_model = 19;
    memcpy(&configure_snapshot, &prefs, sizeof prefs);
    devices[device_index] = {};
    devices[device_index].id = 100 + device_index;
    devices[device_index].type = type;
    devices[device_index].name = _T("Arcadia/ALG fixture");
    metadata[device_index] = &devices[device_index];
    metadata[4] = nullptr;
    const int ids[] = {100 + device_index, -1};
    const unsigned before_paths = path_queries;
    assert(configure_rom(&prefs, ids, 0) == 0);
    assert(memcmp(&prefs, &configure_snapshot, sizeof prefs) == 0);
    assert(path_queries == before_paths + 1);
    assert(warning_calls == 0);
}

static void check_configure()
{
    struct romdata kick{};
    kick.id = 7; kick.type = ROMTYPE_KICK; kick.name = _T("Kickstart 1.3");
    metadata[0] = &kick; metadata[1] = metadata[2] = metadata[3] = nullptr;
    const int ids[] = {7, -1};
    auto &prefs = configure_prefs;
    memset(&prefs, 0, sizeof prefs);
    _tcscpy(prefs.romfile, _T("before"));
    _tcscpy(prefs.romextfile, _T("ext-sentinel"));
    _tcscpy(prefs.cartfile, _T("cart-sentinel"));
    _tcscpy(prefs.flashfile, _T("flash-sentinel"));
    _tcscpy(prefs.genlock_video_file, _T("video-sentinel"));
    assert(configure_rom(&prefs, ids, 0) == 1);
    assert(_tcscmp(prefs.romfile, _T("selected.rom")) == 0);
    assert(_tcscmp(prefs.romextfile, _T("ext-sentinel")) == 0);
    assert(_tcscmp(prefs.cartfile, _T("cart-sentinel")) == 0);
    assert(_tcscmp(prefs.flashfile, _T("flash-sentinel")) == 0);
    assert(_tcscmp(prefs.genlock_video_file, _T("video-sentinel")) == 0);

    configure_case(0, ROMTYPE_ARCADIABIOS);
    configure_case(1, ROMTYPE_ARCADIAGAME);
    configure_case(2, ROMTYPE_ALG);

    metadata[0] = &kick; metadata[1] = metadata[2] = metadata[3] = nullptr;
    path_available = false;
    const unsigned absent_path_queries = path_queries;
    auto &unchanged = configure_prefs;
    memset(&unchanged, 0, sizeof unchanged);
    _tcscpy(unchanged.romfile, _T("unchanged"));
    memcpy(&configure_snapshot, &unchanged, sizeof unchanged);
    const unsigned warnings = warning_calls;
    assert(configure_rom(&unchanged, ids, 0) == 0);
    assert(warning_calls == warnings && memcmp(&unchanged, &configure_snapshot, sizeof unchanged) == 0);
    assert(configure_rom(&unchanged, ids, 1) == 0);
    assert(warning_calls == warnings + 1 && memcmp(&unchanged, &configure_snapshot, sizeof unchanged) == 0);
    assert(path_queries == absent_path_queries + 2);
    path_available = true;

    const int missing[] = {9999, -1};
    const unsigned before = warning_calls;
    assert(configure_rom(&unchanged, missing, 0) == 0 && warning_calls == before);
    assert(configure_rom(&unchanged, missing, 1) == 0 && warning_calls == before + 1);
}

int main()
{
    check_name();
    check_configure();
}
'''


class RommgrPortableTests(unittest.TestCase):
    def test_names_and_excluded_device_configuration(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        source = (ROOT / "build/upstream/rommgr.cpp").read_text()
        bodies = function(source, "void getromname") + function(source, "int configure_rom (")
        with tempfile.TemporaryDirectory(prefix="p6-rommgr-portable-") as directory:
            tmp = Path(directory)
            (tmp / "rommgr_portable_under_test.h").write_text(bodies)
            probe = tmp / "probe.cpp"
            probe.write_text(PROBE)
            common = ["-UNDEBUG", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"),
                      "-I" + str(ROOT / "build/upstream/include"),
                      "-I" + str(ROOT / "build/upstream")]
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                str(probe), "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
