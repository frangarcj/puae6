"""Compare real default_prefs callbacks across the P6 desktop-cache guard."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


PROBE = r'''
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "custom.h"
#include "gfxboard.h"
#include "rommgr.h"
#include "savestate.h"
#include "zfile.h"
#include "inputdevice.h"
#include "blkdev.h"
#include "audio.h"
#include "gui.h"
#include "sounddep/sound.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
static struct uae_prefs prefs;
static int events[32], event_count, joy_count, cache_count;
static char fake_token;
static struct zfile *const fake_file = reinterpret_cast<struct zfile *>(&fake_token);
static struct zfile *default_file;
static int uaeconfig;
int savestate_state;
static size_t cfg_write(const void *, struct zfile *);
#include "default_prefs_under_test.h"
static void event(int n) { assert(event_count < 32); events[event_count++] = n; }
void reset_inputdevice_config(struct uae_prefs *p, bool reset)
{ assert(p == &prefs); event(10 + reset); }
void inputdevice_joyport_config_store(struct uae_prefs *p, const TCHAR *v,
 int port, int mode, int submode, int type, int subdev)
{ assert(p == &prefs && port == joy_count && mode == -1 && submode == -1 && type == 0 && subdev == 0);
  assert(!_tcscmp(v, joy_count ? _T("kbd1") : _T("mouse"))); event(12); ++joy_count; }
int configure_rom(struct uae_prefs *p, const int *rom, int msg)
{ static const int expected[] = {6,7,8,9,10,14,5,4,3,2,1,-1};
  assert(p == &prefs && msg == 0 && !memcmp(rom, expected, sizeof expected)); event(13); return 1; }
void inputdevice_default_prefs(struct uae_prefs *p) { assert(p == &prefs); event(14); }
void blkdev_default_prefs(struct uae_prefs *p) { assert(p == &prefs); event(15); }
void target_default_options(struct uae_prefs *p, int type)
{ assert(p == &prefs && type == 7); event(16); }
void zfile_fclose(struct zfile *z) { assert(z == nullptr); ++cache_count; event(20); }
struct zfile *zfile_fopen_empty(struct zfile *prev, const TCHAR *name)
{ assert(prev == nullptr && !_tcscmp(name, _T("configstore"))); ++cache_count; event(21); return fake_file; }
void cfgfile_save_options(struct zfile *z, struct uae_prefs *p, int type)
{ assert(z == fake_file && p == &prefs && type == 0 && uaeconfig == 1); ++cache_count; event(22); }
static size_t cfg_write(const void *data, struct zfile *z)
{ assert(z == fake_file && *(const TCHAR *)data == 0 && uaeconfig == 0); ++cache_count; event(23); return 1; }
static unsigned long long digest()
{ const unsigned char *b = reinterpret_cast<const unsigned char *>(&prefs);
  unsigned long long h = 1469598103934665603ULL;
  for (size_t i=0; i<sizeof prefs; ++i) { h ^= b[i]; h *= 1099511628211ULL; }
  return h; }
int main()
{ for (int reset=0; reset<2; ++reset) {
    memset(&prefs, 0xa5, sizeof prefs); event_count=joy_count=cache_count=0; default_file=nullptr;
    savestate_state = -1;
    default_prefs(&prefs, reset != 0, 7);
    int expected[7], n = 0;
    expected[n++] = 10 + reset;
    if (reset) { expected[n++] = 12; expected[n++] = 12; }
    expected[n++] = 13; expected[n++] = 14;
#ifndef P6_EXCLUDED_HOOKS
    // Patch 0053 excludes the block-device defaults (SCSI unit 0 after the memset) and the
    // desktop host defaults from the portable profile.
    expected[n++] = 15; expected[n++] = 16;
#endif
    assert(event_count == n + (cache_count ? 4 : 0));
    for (int i=0; i<n; ++i) assert(events[i] == expected[i]);
    if (cache_count) {
      assert(events[n] == 20 && events[n+1] == 21 && events[n+2] == 22 && events[n+3] == 23);
      assert(default_file == fake_file);
    } else assert(default_file == nullptr);
    assert(savestate_state == 0);
    assert(prefs.mountitems == 0);
    printf("%d %llu %d %d\n", reset, digest(), event_count, cache_count);
  } }
'''


class CfgfileDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        materialize()

    def test_default_prefs_callbacks_and_p6_cache_difference(self):
        reference = ROOT / "upstream/winuae/cfgfile.cpp"
        patched = ROOT / "build/upstream/cfgfile.cpp"
        self.assertTrue(reference.is_file(), "missing pristine upstream cfgfile.cpp")
        self.assertTrue(patched.is_file(), "missing materialized cfgfile.cpp")
        includes = ["-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(ROOT / "build/upstream/include"),
                    "-I" + str(ROOT / "build/upstream")]
        with tempfile.TemporaryDirectory(prefix="p6-cfgfile-defaults-") as td:
            tmp = Path(td)
            (tmp / "probe.cpp").write_text(PROBE)
            outputs = []
            for opt in ("-O0", "-O2"):
                pair = []
                for label, source in (("reference", reference), ("patched", patched)):
                    text = source.read_text()
                    extracted = function(text, "static void clearmountitems(") + "\n"
                    extracted += function(text, "void default_prefs (") + "\n"
                    (tmp / "default_prefs_under_test.h").write_text(extracted)
                    exe = tmp / (label + opt[2:])
                    hooks = ["-DP6_EXCLUDED_HOOKS"] if label == "patched" else []
                    subprocess.run(["clang++", "-std=c++17", opt, "-UNDEBUG", *hooks,
                                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    *includes, str(tmp / "probe.cpp"), "-o", str(exe)],
                                   check=True, timeout=120)
                    run = subprocess.run([str(exe)], check=True, capture_output=True,
                                         text=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
                                         timeout=30)
                    pair.append(run.stdout)
                outputs.append(pair)
            for reference_out, patched_out in outputs:
                ref = [line.split()[:2] for line in reference_out.splitlines()]
                new = [line.split()[:2] for line in patched_out.splitlines()]
                self.assertEqual(ref, new, "default_prefs state/callback result changed")
                self.assertEqual([int(line.split()[3]) for line in reference_out.splitlines()], [4, 4])
                self.assertEqual([int(line.split()[3]) for line in patched_out.splitlines()], [0, 0])


if __name__ == "__main__":
    unittest.main()
