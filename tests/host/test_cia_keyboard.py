"""Mode0 CIA keyboard on real key queue and emulated event scheduler."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CIAKeyboardTests(unittest.TestCase):
    def test_serial_keyboard_without_mcu(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-Werror=format",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        pristine = (ROOT / "upstream/winuae/cia.cpp").read_text()
        # Patch 0011 already corrected evt_t's diagnostic format, independently
        # of the keyboard MCU exclusion. Preserve that exact known difference.
        pristine = pristine.replace(
            'length %d < %d (CCKs)\\n"), len / CYCLE_UNIT,',
            'length %lld < %d (CCKs)\\n"), (long long)(len / CYCLE_UNIT),')
        current = (tree / "cia.cpp").read_text()
        for start, end in (
            ("static void check_keyboard(void)", "static void cia_delayed_tod(int num)"),
            ("void cia_keyreq(uae_u8 code)", "/* All this complexity"),
            ("int resetwarning_do(int canreset)", "void cia_keyreq(uae_u8 code)"),
            ("\t\t\tif (currprefs.keyboard_mode == 0) {\n\t\t\t\t// keyboard handshake handling",
             "\t\t\tWriteCIAReg(0, reg, val);")
        ):
            self.assertEqual(current.split(start)[1].split(end)[0],
                             pristine.split(start)[1].split(end)[0])
        deps = subprocess.run([*common, "-M", str(tree / "cia.cpp")],
                              text=True, capture_output=True, check=True).stdout
        self.assertNotIn("keyboard_mcu.h", deps)
        with tempfile.TemporaryDirectory(prefix="p6-keyboard-") as tmp:
            obj, exe = Path(tmp) / "cia.o", Path(tmp) / "probe"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(tree / "cia.cpp"), "-o", str(obj)],
                               check=True, timeout=60)
                imports = subprocess.run(["nm", "-u", str(obj)], text=True,
                                         capture_output=True, check=True).stdout
                self.assertNotIn("keymcu", imports)
                strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
                # Selective link is only for this keyboard probe, not the core.
                subprocess.run([*common, optimization, "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/cia_keyboard_probe.cpp"),
                                str(tree / "events.cpp"), str(tree / "keybuf.cpp"),
                                str(ROOT / "ports/portable/atomic.cpp"),
                                str(ROOT / "ports/portable/diagnostics.cpp"), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
