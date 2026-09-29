"""Video presentation provider: the renderer's lock/unlock protocol and the bridge hand-over."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]
SAN = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]


class VideoPresentTests(unittest.TestCase):
    def test_provider_under_the_renderers_protocol(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        common = ["-std=c++17", "-UNDEBUG", "-fno-fast-math", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-video-present-") as directory:
            tmp = Path(directory)
            for opt in ("-O0", "-O2"):
                exe = tmp / f"probe{opt}"
                subprocess.run(["clang++", *common, *SAN, opt, "-Wno-unused-parameter",
                                str(ROOT / "ports/portable/video_present.cpp"),
                                str(ROOT / "tests/host/video_present_probe.cpp"),
                                str(tree / "gfxutil.cpp"), str(ROOT / "ports/portable/diagnostics.cpp"),
                                "-o", str(exe)], check=True, timeout=300, capture_output=True)
                run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120,
                                     env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(run.returncode, 0, run.stderr[-800:])

    def test_object_defines_the_presentation_symbols_without_host_imports(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-video-object-") as directory:
            obj = Path(directory) / "video_present.o"
            subprocess.run(["clang++", "-std=c++17", "-O0", "-I" + str(ROOT / "ports/portable"),
                            "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                            "-I" + str(tree), "-c", str(ROOT / "ports/portable/video_present.cpp"),
                            "-o", str(obj)], check=True, timeout=120)
            defined = subprocess.run(["nm", "-C", "-g", "--defined-only", str(obj)], check=True,
                                     capture_output=True, text=True).stdout
            imports = subprocess.run(["nm", "-u", "-C", str(obj)], check=True, capture_output=True,
                                     text=True).stdout
            for name in ("lockscr(", "unlockscr(", "render_screen(", "show_screen(", "graphics_leave(",
                         "graphics_reset(", "check_prefs_changed_gfx(", "target_graphics_buffer_update(",
                         "updatedisplayarea(", "getgfxoffset(", "max_uae_width", "max_uae_height",
                         "flashscreen", "gfx_hdr", "p6_video_present", "p6_video_frame_get",
                         "p6_video_init"):
                self.assertIn(name, defined, name)
            self.assertNotIn("gfxboard_refresh", defined)      # guarded at its only caller (0058)
            names = {l.strip().lstrip("_") for l in imports.splitlines()}
            for forbidden in ("gettimeofday", "clock_gettime", "time", "sleep", "usleep", "setlocale",
                              "pthread_create", "malloc", "calloc"):
                self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
