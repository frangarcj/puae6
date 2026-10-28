"""FMV-B1 native device graph, bounds and Waterbox source closure."""
import json
import math
import os
from pathlib import Path
import subprocess
import unittest

from tests.build_support import ROOT, build_targets


class FmvDeviceTests(unittest.TestCase):
    def test_scalar_graph_and_bounds_are_selected_without_host_player_or_simd(self):
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        self.assertTrue({"cd32_fmv.cpp", "cd32_fmv_genlock.cpp", "archivers/mp2/kjmp2.cpp"}
                        <= set(selection["upstream_units"]))
        cmake = (ROOT / "CMakeLists.txt").read_text()
        closure = json.loads((ROOT / "plan/extensions/cd.json").read_text())["decoder_closure"]
        for source in closure["source_paths"][:-1]:
            self.assertIn(source, cmake)
        for source in closure["excluded_isa_sources"]:
            self.assertNotIn("deps/libmpeg2/src/" + source, cmake)
        self.assertNotIn("od-win32/cda_play.cpp", cmake)
        portable = (ROOT / "ports/portable/fmv_audio_buffer.cpp").read_text()
        self.assertNotRegex(portable, r"pthread|std::thread|uae_sem|sleep|clock_gettime")
        patch = (ROOT / "patches/winuae/0362-cd32-without-fmv.patch").read_text()
        self.assertIn("sequence->width > 352 || mpeg_info->sequence->height > 288", patch)
        self.assertIn("w > MAX_MPEG_WIDTH || h > MAX_MPEG_HEIGHT", patch)
        self.assertIn("d != MPEG_PIXBYTES_16 && d != MPEG_PIXBYTES_32", patch)
        fixed = (ROOT / "ports/portable/kjmp2_n.inc").read_text()
        values = [int(token) for token in fixed.replace("{", " ").replace("}", " ").replace(",", " ").split()
                  if token.lstrip("-").isdigit()]
        expected = [int(256.0 * math.cos(((16 + i) * ((j << 1) + 1)) * 0.0490873852123405))
                    for i in range(64) for j in range(32)]
        self.assertEqual(expected, values)
        fixed_patch = (ROOT / "patches/winuae/0418-cd32-fmv-fixed-mp2-table.patch").read_text()
        self.assertIn('#include "kjmp2_n.inc"', fixed_patch)
        self.assertIn("#ifndef P6_PORTABLE", fixed_patch)

    def test_private_cd32_firmware_enumerates_real_board_and_reset(self):
        main = os.environ.get("P6_CD32_ROM_MAIN")
        ext = os.environ.get("P6_CD32_ROM_EXT")
        fmv = os.environ.get("P6_FMV_ROM")
        if not all((main, ext, fmv)):
            self.skipTest("BLOCKED: set authorized P6_CD32_ROM_MAIN/P6_CD32_ROM_EXT/P6_FMV_ROM")
        exe = build_targets(ROOT / "build/fmv-device", ["p6_fmv_device_probe"])["p6_fmv_device_probe"]
        result = subprocess.run([str(exe), ext, main, fmv], capture_output=True, text=True,
                                timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-8000:])
        self.assertIn("CD32 FMV Autoconfig/ROM/MMIO/RAM/reset PASS", result.stdout)

    def test_waterbox_recipe_captures_decoder_headers_and_sources(self):
        from tools import build_waterbox
        sources = set(build_waterbox.puae6_sources())
        closure = json.loads((ROOT / "plan/extensions/cd.json").read_text())["decoder_closure"]
        selected = {path.replace("upstream/winuae/", "build/upstream/")
                    for path in closure["source_paths"]}
        self.assertTrue(selected <= sources)
        manual = build_waterbox.puae6_manual_inputs()
        for path in closure["include_paths"]:
            pristine = path.replace("build/libmpeg2/", "references/machawk-uae/deps/libmpeg2/")
            self.assertIn(pristine, manual)
            self.assertTrue((ROOT / path).is_file())
        self.assertIn("patches/libmpeg2/series.json", manual)
        self.assertIn("tools/materialize_libmpeg2.py", manual)

    def test_fmv_staging_is_bounded_and_only_presented_after_composition(self):
        source = (ROOT / "ports/portable/video_present.cpp").read_text()
        self.assertIn("aux_pixels[(P6_VIDEO_BUFFER_BYTES) / P6_VIDEO_PIXEL_BYTES]", source)
        self.assertIn("return g.ready && !g.mode_too_large && !g.aux_buffer", source)
        self.assertIn("const bool auxiliary_input = vi.inbuffer && vi.inbuffer != vb", source)
        self.assertIn("const bool composed_fmv = auxiliary_input && currprefs.cs_cd32fmv", source)
        self.assertIn("g.aux_buffer = auxiliary_input && !composed_fmv", source)
        validate = source[source.index("int p6_video_validate_config"):source.index("int p6_video_init")]
        self.assertNotIn("p->cs_cd32fmv", validate)


if __name__ == "__main__":
    unittest.main()
