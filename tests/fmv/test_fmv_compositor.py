"""FMV-E: exact bounded scalar composition and final composed hand-over."""
import hashlib
import json
import os
import subprocess
import unittest

from tests.build_support import ROOT, build_targets


class FmvCompositorTests(unittest.TestCase):
    def test_scalar_compositor_is_exact_guarded_and_transactional(self):
        builds = (
            build_targets(ROOT / "build/fmv-compositor-sanitize",
                          ["p6_fmv_compositor_probe"], sanitizers=True),
            build_targets(ROOT / "build/fmv-compositor-opt",
                          ["p6_fmv_compositor_probe"], optimized=True),
        )
        outputs = []
        for build in builds:
            result = subprocess.run([str(build["p6_fmv_compositor_probe"])],
                                    capture_output=True, text=True, timeout=120,
                                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-8000:])
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual("FMV compositor exact/canary/rejection/handover PASS cases=6\n", outputs[0])

    def test_compositor_patch_is_hash_closed_and_allocation_free(self):
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"]
        entry = next(item for item in series if item["id"] == "0422-cd32-fmv-compositor")
        patch = (ROOT / entry["file"]).read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(patch).hexdigest())
        self.assertEqual(["cd32_fmv_genlock.cpp"], [item["path"] for item in entry["files"]])
        entry_index = series.index(entry)
        for item in entry["files"]:
            expected = item["after_sha256"]
            for later in series[entry_index + 1:]:
                change = next((candidate for candidate in later["files"]
                               if candidate["path"] == item["path"]), None)
                if change:
                    self.assertEqual(expected, change["before_sha256"], later["id"])
                    expected = change["after_sha256"]
            materialized = ROOT / "build/upstream" / item["path"]
            self.assertEqual(expected, hashlib.sha256(materialized.read_bytes()).hexdigest())
        text = patch.decode()
        compose = text[text.index("+static int compose"):text.index("+#ifdef P6_PORTABLE")]
        self.assertNotRegex(compose, r"xmalloc|xcalloc|xfree|\bnew\b|clock_gettime|sleep\s*\(")
        self.assertIn("P6_FMV_COMPOSE_CAPACITY", text)
        presentation = (ROOT / "ports/portable/video_present.cpp").read_text()
        self.assertIn("!g.aux_buffer", presentation)

    def test_composed_pass_owns_the_public_handover(self):
        source = (ROOT / "ports/portable/video_present.cpp").read_text()
        header = (ROOT / "ports/portable/video_present.h").read_text()
        self.assertIn("const bool composed_fmv = auxiliary_input && currprefs.cs_cd32fmv", source)
        self.assertIn("g.aux_buffer = auxiliary_input && !composed_fmv", source)
        self.assertIn("composes it into the normal buffer before unlock", header)
        self.assertNotIn("CD32 FMV */", header[header.index("P6_VIDEO_CFG_AUX_BUFFER"):])


if __name__ == "__main__":
    unittest.main()
