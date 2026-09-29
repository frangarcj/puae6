"""Run real P6 Denise dispatch and RGA helpers without worker services."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


def braced(source, keyword):
    start = source.index(keyword)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class DrawingSyncTests(unittest.TestCase):
    def test_real_sync_dispatch_rga_and_lifecycle(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        source = (tree / "drawing.cpp").read_text()
        self.assertIn("#define ENABLE_MULTITHREADED_DENISE 0", source)
        self.assertIn("#if ENABLE_MULTITHREADED_DENISE\n\tif (denise_thread_state == 1)", source)
        self.assertIn("#if ENABLE_MULTITHREADED_DENISE\n\tif (!denise_thread_state)", source)
        # Complete selected bodies are extracted: preprocessing must leave only
        # their synchronous block and cannot quietly reach the worker side.
        names = [
            "void draw_denise_border_line_fast_queue(",
            "void draw_denise_bitplane_line_fast_queue(",
            "void quick_denise_rga_queue(",
            "void denise_handle_quick_strobe_queue(",
            "void draw_denise_line_queue(",
            "void draw_denise_vsync_queue(",
            "void denise_update_reg_queue(",
            "void denise_store_restore_registers_queue(",
            "void draw_denise_line_queue_flush(",
            "void drawing_init(",
            "void drawing_free(",
        ]
        start = source.index("#ifdef P6_PORTABLE\n#define ENABLE_MULTITHREADED_DENISE")
        bodies = source[start:source.index("#endif", start) + len("#endif")] + "\n"
        bodies += next(line for line in source.splitlines()
                       if line.startswith("#define MAX_RGA_OVERLAPPING_CYCLES ")) + "\n"
        bodies += braced(source, "struct denise_rga_queue") + ";\n"
        bodies += "static struct denise_rga_queue temp_line;\nstatic struct denise_rga_queue *this_line;\n"
        bodies += "static int spr_unalign_reg, spr_unalign_val;\n"
        for sig in ("static void updatelinedata(", "static void quick_denise_rga(",
                    "static void update_overlapped_cycles(", *names):
            bodies += function(source, sig)
        # Guard against future extraction mistakes which omit required paths.
        for call in ("updatelinedata();", "quick_denise_rga(linecnt, startpos, endpos);",
                     "update_overlapped_cycles(endpos);", "refresh_indicator_init();",
                     "gen_pfield_tables();", "reset_drawing();"):
            self.assertIn(call, bodies)
        with tempfile.TemporaryDirectory(prefix="p6-drawing-sync-") as directory:
            tmp = Path(directory)
            (tmp / "drawing_sync_under_test.h").write_text(bodies)
            common = ["-UNDEBUG", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-I" + str(tmp), "-I" + str(ROOT / "ports/portable"),
                      "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"),
                      "-I" + str(tree)]
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run(["clang++", "-std=c++17", *common, optimization,
                                str(ROOT / "tests/host/drawing_sync_probe.cpp"), "-o", str(exe)],
                               check=True, timeout=90)
                subprocess.run([str(exe)], check=True, timeout=30,
                               env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    unittest.main()
