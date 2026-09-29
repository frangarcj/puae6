"""Compile complete OCS/ECS drawing and inspect its disabled host features."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class DrawingSourceTests(unittest.TestCase):
    def test_portable_translation_unit_excludes_worker_and_thread_headers(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = tree / "drawing.cpp"
        common = ["clang++", "-std=c++17", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        preprocessed = subprocess.run([*common, "-E", str(source)], check=True,
                                      capture_output=True, text=True, timeout=60).stdout
        # Common generator still emits AGA helpers, but every 7/8-plane name
        # occurs only at its definition: none is called or selected in this TU.
        helpers = re.findall(r'\b(?:shiftbpl|getbpl|copybpl)[78]\w*\b', preprocessed)
        self.assertEqual(len(set(helpers)), 30)
        self.assertEqual(len(helpers), len(set(helpers)))
        # Keep only drawing's own preprocessed tokens, not unused host declarations
        # inherited from general upstream headers. Do not remove any function bodies.
        active, lines = False, []
        for line in preprocessed.splitlines():
            marker = re.match(r'^# \d+ "([^"]+)"', line)
            if marker:
                active = marker[1] == str(source)
            elif active:
                lines.append(line)
        drawing = "\n".join(lines)
        self.assertIn("void drawing_init(void)", drawing)
        self.assertIn("void draw_denise_line_queue(", drawing)
        self.assertIn("update_overlapped_cycles(endpos)", drawing)
        for forbidden in ("uae_sem_", "uae_start_thread", "sleep_millis",
                          "denise_thread", "read_denise_line_queue", "waitqueue",
                          "addtowritequeue", "rga_queue_read", "rga_queue_write", "gui_sem",
                          "color_regs_aga", "linetoscr_aga", "decode_pixel_aga",
                          "debug_sprite_mask", "statusline_render", "draw_status_line_single"):
            self.assertNotIn(forbidden, drawing)
        deps = subprocess.run([*common, "-M", str(source)], check=True,
                              capture_output=True, text=True, timeout=60).stdout
        self.assertIn(str(ROOT / "ports/portable/tchar.h"), deps)
        self.assertNotIn("threaddep", deps)
        allowed = str(tree / "od-win32/machdep/m68k.h")
        self.assertNotIn("od-win32", deps.replace(allowed, ""))
        for name in ("common", "ocs_ecs", "ocs_ecs_genlock", "ocs_ecs_ntsc",
                     "ocs_ecs_ntsc_genlock", "ecs_shres", "ecs_fast", "ecs_genlock_fast"):
            self.assertIn("linetoscr_" + name + ".cpp", deps)
        self.assertNotIn("linetoscr_aga", deps)
        with tempfile.TemporaryDirectory(prefix="p6-drawing-source-") as directory:
            obj = Path(directory) / "drawing.o"
            for optimization in ("-O0", "-O2"):
                subprocess.run([*common, optimization, "-c", str(source), "-o", str(obj)],
                               check=True, timeout=240)
                imports = subprocess.run(["nm", "-u", str(obj)], check=True,
                                         capture_output=True, text=True).stdout
                # No link-time section collection can hide dependencies here.
                self.assertIn("custom_end_drawing", imports)
                for forbidden in ("uae_sem_", "uae_start_thread", "sleep_millis",
                                  "statusline_render", "draw_status_line_single",
                                  "debug_sprite_mask", "record_dma", "debug_heatmap"):
                    self.assertNotIn(forbidden, imports)
        # The public generic HUD header must be usable without inventing HWND.
        subprocess.run([*common, "-x", "c++", "-fsyntax-only", "-"], check=True,
                       input='#include "sysconfig.h"\n#include "sysdeps.h"\n#include "statusline.h"\n',
                       text=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
