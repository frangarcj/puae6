"""Compare real OCS color/HAM and generated ECS line paths across layouts."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

from tests.host.test_cpu_rom_guard import function

ROOT = Path(__file__).resolve().parents[2]


class DrawingOCSTests(unittest.TestCase):
    def test_ocs_colors_ham_and_generated_ecs_line(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        original = (ROOT / "upstream/winuae/drawing.cpp").read_text()
        patched = (ROOT / "build/upstream/drawing.cpp").read_text()
        generated = (ROOT / "build/generated/linetoscr_ecs_fast.cpp").read_text()
        for source in (original, patched):
            self.assertIn("static void update_color(", source)
            self.assertIn("static void setlasthamcolor(", source)
            self.assertIn("static uae_u32 decode_ham_pixel_fast(", source)
            self.assertIn("static void bpldat_docopy(", source)
        self.assertIn("color_regs_aga", original)
        self.assertIn("color_regs_aga", patched)
        color_path = "\n".join(function(original, sig) for sig in
                                ("static void update_color(", "static void setlasthamcolor(",
                                 "static uae_u32 decode_ham_pixel_fast(",
                                 "static void bpldat_docopy("))
        p6_path = "\n".join(function(patched, sig) for sig in
                             ("static void update_color(", "static void setlasthamcolor(",
                              "static uae_u32 decode_ham_pixel_fast(",
                              "static void bpldat_docopy("))
        ecs_line = function(generated, "static void lts_ecs_n_ilores_dlores_b1(")

        common = ["clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                  "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                  "-I{tmp}", "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "build/generated"),
                  "-I" + str(ROOT / "build/upstream/include"), "-I" + str(ROOT / "build/upstream")]
        strip = "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections"
        with tempfile.TemporaryDirectory(prefix="p6-drawing-ocs-") as directory:
            tmp = Path(directory)
            (tmp / "ecs_fast_under_test.h").write_text(ecs_line)
            for optimization in ("-O0", "-O2"):
                outputs = []
                for is_reference, bodies in ((True, color_path), (False, p6_path)):
                    (tmp / "drawing_ocs_under_test.h").write_text(bodies)
                    exe = tmp / "probe"
                    extra = ["-DTEST_REFERENCE"] if is_reference else ["-DP6_PORTABLE"]
                    compile_cmd = [arg.format(tmp=tmp) for arg in common]
                    try:
                        subprocess.run([*compile_cmd, optimization, *extra, strip,
                                        str(ROOT / "tests/host/drawing_ocs_probe.cpp"),
                                        "-o", str(exe)], check=True, timeout=90)
                        result = subprocess.run([str(exe)], check=True, capture_output=True,
                                                text=True, timeout=30,
                                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                    except subprocess.CalledProcessError as error:
                        self.fail(f"{error.cmd}:\n{error.stderr or error.stdout or ''}")
                    outputs.append(result.stdout)
                self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
