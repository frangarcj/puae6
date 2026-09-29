"""Real custom VSync state transitions without clock/GPU host providers."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from tests.host.test_audio_math import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class CustomVSyncTests(unittest.TestCase):
    def test_vsync_preserves_buffer_order_and_hardware_housekeeping(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        drawing = (tree / "drawing.cpp").read_text()
        cfg = (tree / "cfgfile.cpp").read_text()
        original = (ROOT / "upstream/winuae/custom.cpp").read_text()
        header = ''.join(function(drawing, sig) for sig in
                         ("static void count_frame(int monid)",
                          "void check_prefs_picasso(void)", "void vsync_handle_redraw("))
        header += function(cfg, "void config_check_vsync (void)")
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        forbidden = ("uae_time", "read_processor_time", "sleep_millis", "scanlinesleep",
                     "render_screen", "show_screen", "handle_events",
                     "audio_pull", "audio_is_pull", "audio_finish_pull")
        with tempfile.TemporaryDirectory(prefix="p6-custom-vsync-") as tmp:
            tmp = Path(tmp)
            (tmp / "vsync_drawing_under_test.h").write_text(header)
            (tmp / "vsync_present_reference.h").write_text(function(original, "static bool crender_screen("))
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run([*common, optimization, "-I" + str(tmp),
                                "-ffunction-sections", "-fdata-sections", strip,
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/custom_vsync_probe.cpp"),
                                str(tree / "events.cpp"), str(ROOT / "ports/portable/diagnostics.cpp"),
                                "-o", str(exe)], check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)
                obj = tmp / "custom.o"
                subprocess.run([*common, optimization, "-c", str(tree / "custom.cpp"),
                                "-o", str(obj)], check=True, timeout=60)
                for target in (exe, obj):
                    imports = subprocess.run(["nm", "-u", str(target)], check=True,
                                             capture_output=True, text=True).stdout
                    for name in forbidden:
                        self.assertNotIn(name, imports)


if __name__ == "__main__":
    unittest.main()
