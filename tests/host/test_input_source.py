"""Check that the selected inputdevice object keeps logical input and drops host services."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]

# Configuration text, custom-event macros and port scripts excluded by patch 0048,
# with their pristine definitions so this test cannot pass vacuously.
EXCLUDED_DEFINITIONS = {
    "write_inputdevice_config": "void write_inputdevice_config (",
    "read_inputdevice_config": "void read_inputdevice_config (",
    "handle_custom_event": "int handle_custom_event (",
    "inputdevice_generate_jport_custom": "void inputdevice_generate_jport_custom(",
    "inputdevice_jportcustom_fixup": "void inputdevice_jportcustom_fixup(",
    "inputdevice_parse_jport_custom": "void inputdevice_parse_jport_custom(",
}
KEPT_DEFINITIONS = ("setjoystickstate", "getjoystickstate", "setmousestate", "send_input_event",
                    "inputdevice_init", "inputdevice_close", "inputdevice_reset",
                    "inputdevice_hsync", "inputdevice_vsync", "inputdevice_do_keyboard",
                    "inputdevice_do_kb_reset", "inputdevice_updateconfig",
                    "inputdevice_fix_prefs", "inputdevice_copyconfig", "inputdevice_geteventid")
# Wall clock, shell, configuration parser and desktop mixer/RTG/debugger state.
FORBIDDEN_IMPORTS = ("gettimeofday", "uae_ShellExecute", "cfgfile_parse_line", "sscanf",
                     "my_strdup_trim", "inject_events", "picasso96_state", "debugging",
                     "sound_mute", "master_sound_volume", "disk_prevnext", "zfile_fputs",
                     "handle_custom_event", "read_inputdevice_config", "write_inputdevice_config",
                     "inputdevice_parse_jport_custom", "inputdevice_jportcustom_fixup")

PROBE = r'''#include "tchar.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
static TCHAR *my_strdup(const TCHAR *s) { return strdup(s); }
#define xfree(p) free(p)
#include "hid_under_test.h"
int main()
{
    const char *names[] = {
        "HID\\VID_045E&PID_02FF&IG_00", "vid_045e&pid_02ff&mi_00&rev_0110", "VID_&PID_", "VID_G",
        "VID_1", "VID_FFFF&PID_0", "XBOX", "", "VID_7FFFFFFF", "VID_0x1F", "VID_ 12", "VID_+12",
        "VID_-0", "MI_00", "REV_0100", "\\\\?\\HID#VID_1234&PID_ABCD&MI_02#7&1f&0&0000#{4d1e}",
        "VID_80000000", "VID_-5", "VID_100000000", "VID_FFFFFFFF",
    };
    for (const char *n : names) {
        int vid, pid, mi, rev;
        gethidvals(n, &vid, &pid, &mi, &rev);
        std::printf("%s|%d|%d|%d|%d\n", n, vid, pid, mi, rev);
    }
    return 0;
}
'''

# Names whose parsed value is not a plausible USB id: the legacy sscanf keeps the raw
# unsigned bits, the portable parse reports "unknown" (-1). Documented, not accidental.
INTENTIONAL_DIFFERENCES = {"VID_80000000", "VID_-5", "VID_100000000"}


def body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class InputSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        cls.tree = ROOT / "build/upstream"

    def test_pristine_source_defines_every_excluded_feature(self):
        pristine = (ROOT / "upstream/winuae/inputdevice.cpp").read_text(errors="replace")
        for name, signature in EXCLUDED_DEFINITIONS.items():
            self.assertIn(signature, pristine, name)

    def test_complete_object_host_boundary(self):
        with tempfile.TemporaryDirectory(prefix="p6-input-object-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                obj, deps = tmp / "inputdevice.o", tmp / "inputdevice.d"
                # No -DP6_PORTABLE: the profile headers must select the portable branches.
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", opt,
                    "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                    "-I" + str(self.tree / "include"), "-I" + str(self.tree),
                    "-MMD", "-MF", str(deps), "-c", str(self.tree / "inputdevice.cpp"),
                    "-o", str(obj)], check=True, timeout=240)
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                imports = subprocess.run(["nm", "-u", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                for name in KEPT_DEFINITIONS:
                    self.assertRegex(symbols, r"(?m)\bT " + re.escape(name) + r"\(", (opt, name))
                for name in EXCLUDED_DEFINITIONS:
                    self.assertNotRegex(symbols, r"\b" + re.escape(name) + r"\b", (opt, name))
                # The integer overload stays; the text-command overload is gone.
                self.assertRegex(symbols, r"(?m)\bT inputdevice_uaelib\(char const\*, int, int, bool\)")
                self.assertNotIn("inputdevice_uaelib(char const*, char const*)", symbols)
                names = {re.sub(r"\(.*", "", line.strip()).lstrip("_") for line in imports.splitlines()}
                for name in FORBIDDEN_IMPORTS:
                    self.assertNotIn(name, names, (opt, name))
                text = deps.read_text()
                allowed = str(self.tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", text.replace(allowed, ""))

    def test_hid_id_parse_matches_legacy_sscanf(self):
        pristine = (ROOT / "upstream/winuae/inputdevice.cpp").read_text(errors="replace")
        patched = (self.tree / "inputdevice.cpp").read_text(errors="replace")
        outputs = {}
        with tempfile.TemporaryDirectory(prefix="p6-input-hid-") as directory:
            tmp = Path(directory)
            (tmp / "probe.cpp").write_text(PROBE)
            for label, source, flags in (("reference", pristine, ["-D_stscanf=sscanf"]),
                                         ("patched", patched, ["-DP6_PORTABLE"])):
                (tmp / "hid_under_test.h").write_text(
                    body(source, "static void gethidvals2(") + body(source, "static void gethidvals("))
                exe = tmp / label
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all", "-O1", *flags, "-I" + str(tmp),
                    "-I" + str(ROOT / "ports/portable"), str(tmp / "probe.cpp"), "-o", str(exe)],
                    check=True, timeout=120)
                run = subprocess.run([str(exe)], check=True, capture_output=True, text=True,
                                     timeout=30, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                outputs[label] = run.stdout.splitlines()
        self.assertEqual(len(outputs["reference"]), len(outputs["patched"]))
        self.assertGreater(len(outputs["patched"]), 10)
        different = set()
        for ref, new in zip(outputs["reference"], outputs["patched"]):
            if ref != new:
                different.add(ref.split("|")[0])
        self.assertEqual(different, INTENTIONAL_DIFFERENCES)
        # Ordinary ids parse identically, including the unknown sentinel.
        by_name = {line.split("|")[0]: line.split("|")[1:] for line in outputs["patched"]}
        self.assertEqual(by_name["HID\\VID_045E&PID_02FF&IG_00"], ["1118", "767", "-1", "-1"])
        self.assertEqual(by_name["vid_045e&pid_02ff&mi_00&rev_0110"], ["1118", "767", "0", "272"])
        self.assertEqual(by_name["VID_G"], ["-1", "-1", "-1", "-1"])
        self.assertEqual(by_name["VID_80000000"], ["-1", "-1", "-1", "-1"])


if __name__ == "__main__":
    unittest.main()
