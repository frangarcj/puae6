"""Full devices unit: A500 lifecycle ordering and excluded backend imports."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class DeviceTests(unittest.TestCase):
    def test_lifecycle_and_provider_boundary(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        deps = subprocess.run([*common, "-M", str(tree / "devices.cpp")],
                              text=True, capture_output=True, check=True).stdout
        for excluded in ("threaddep/thread.h", "statusline.h", "bsdsocket.h", "windows.h"):
            self.assertNotIn(excluded, deps)
        # Every retained provider has an A500 hardware or cleanup role. This
        # checks the WHOLE object, independent of the instrumented test link.
        required = set("""DISK_hsync DISK_reset a1000_reset audio_hsync audio_vsync
            close_sound dump_counts dongle_reset drawing_free memory_reset rethink_cias
            update_sound init_eventtab decide_blitter graphics_leave memory_cleanup
            savestate_free rethink_uae_int inputdevice_close inputdevice_vsync
            CIA_vsync_prehandler check_prefs_changed_cpu
            check_prefs_changed_audio check_prefs_changed_custom CIA_reset DISK_free
            uae_int_requested""".split())
        with tempfile.TemporaryDirectory(prefix="p6-devices-") as tmp:
            obj, exe = Path(tmp) / "devices.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "devices.cpp"),
                                "-o", str(obj)], check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                names = set()
                for line in imports.splitlines():
                    name = line.split()[-1].lstrip("_")
                    match = re.match(r"Z(\d+)(.*)", name)
                    names.add(match[2][:int(match[1])] if match else name)
                self.assertEqual(names - {"memset", "bzero"}, required)
                # No section GC: all required lifecycle boundaries must link.
                subprocess.run([*common, optimization, "-fsanitize=address,undefined",
                                "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/devices_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
