"""Check that the selected disk object keeps drive emulation and drops host services."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]

# Features excluded for read-only asset media (patch 0047), with their definitions
# in the pristine WinUAE file so this test cannot pass vacuously.
EXCLUDED_DEFINITIONS = {
    "disk_creatediskfile": "bool disk_creatediskfile (",
    "disk_setwriteprotect": "int disk_setwriteprotect (",
    "disk_getwriteprotect": "int disk_getwriteprotect (",
    "DISK_get_saveimagepath": "TCHAR *DISK_get_saveimagepath(",
    "disk_prevnext": "int disk_prevnext (",
    "disk_prevnext_name": "int disk_prevnext_name (",
}
# Host clock, filesystem, plugin XML and bridge catalogue must not be referenced.
FORBIDDEN_IMPORTS = ("gettimeofday", "_timezone", "_wunlink", "unlink", "chmod", "fopen",
                     "get_plugin_path", "fetch_saveimagepath", "saveimageoriginalpath",
                     "my_chmod", "my_stat", "timeval_to_amiga", "bridgeprofiles",
                     "disk_creatediskfile", "notify_user")
# save_disk/restore_disk are SAVESTATE-gated and stay out until the T15 state profile.
KEPT_DEFINITIONS = ("DISK_validate_filename", "DISK_init", "DISK_reset", "DISK_hsync",
                    "DISK_update", "DSKLEN", "DSKBYTR", "disk_insert", "disk_eject",
                    "DISK_history_add", "DISK_examine_image")


class DiskSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        cls.tree = ROOT / "build/upstream"

    def test_pristine_source_defines_every_excluded_feature(self):
        pristine = (ROOT / "upstream/winuae/disk.cpp").read_text(errors="replace")
        for name, signature in EXCLUDED_DEFINITIONS.items():
            self.assertIn(signature, pristine, name)

    def test_complete_object_host_boundary(self):
        with tempfile.TemporaryDirectory(prefix="p6-disk-object-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                obj, deps = tmp / "disk.o", tmp / "disk.d"
                # No -DP6_PORTABLE: the profile headers must select the portable branches.
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", opt,
                    "-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(self.tree / "include"), "-I" + str(self.tree),
                    "-MMD", "-MF", str(deps), "-c", str(self.tree / "disk.cpp"),
                    "-o", str(obj)], check=True, timeout=180)
                symbols = subprocess.run(["nm", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                imports = subprocess.run(["nm", "-u", "-C", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                for name in KEPT_DEFINITIONS:
                    self.assertRegex(symbols, r"(?m)\bT " + re.escape(name) + r"\(", (opt, name))
                for name in EXCLUDED_DEFINITIONS:
                    self.assertNotRegex(symbols, r"\b" + re.escape(name) + r"\b", (opt, name))
                # Exact symbol names: zfile_fopen is the portable asset provider, not fopen.
                names = {re.sub(r"\(.*", "", line.strip()).lstrip("_") for line in imports.splitlines()}
                for name in FORBIDDEN_IMPORTS:
                    self.assertNotIn(name, names, (opt, name))
                self.assertFalse([n for n in names if "tinyxml2" in n or "FloppyBridge" in n])
                text = deps.read_text()
                self.assertNotIn("tinyxml2", text)
                self.assertNotIn("floppybridge", text)
                allowed = str(self.tree / "od-win32/machdep/m68k.h")
                self.assertNotIn("od-win32", text.replace(allowed, ""))

    def test_exclusions_are_preprocessor_regions(self):
        # Every exclusion stays visible upstream text inside a P6_PORTABLE region.
        patched = (self.tree / "disk.cpp").read_text()
        pristine = (ROOT / "upstream/winuae/disk.cpp").read_text(errors="replace")
        for signature in EXCLUDED_DEFINITIONS.values():
            self.assertIn(signature, patched, signature)
        # 14 regions in patch 0047, 2 in 0052 (input-record hooks) and 8 in 0056 (recent list,
        # OSD text, path history); a new one must be a deliberate, documented change.
        self.assertEqual(24, patched.count("#ifndef P6_PORTABLE"))
        self.assertEqual(pristine.count("#ifndef P6_PORTABLE"), 0)


if __name__ == "__main__":
    unittest.main()
