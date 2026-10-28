"""FMV-A firmware ownership and pinned scalar decoder closure."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

from tests.build_support import ROOT, build_targets


class FmvFirmwareTests(unittest.TestCase):
    def test_selected_decoder_closure_is_captured_scalar_and_licensed(self):
        study = json.loads((ROOT / "plan/extensions/cd.json").read_text())
        closure = study["decoder_closure"]
        self.assertEqual(("libmpeg2-0.4.1", "generic_C_scalar_only_no_runtime_CPU_detection_or_SIMD_units"),
                         (closure["version"], closure["execution"]))
        self.assertIn("MPEG2_RELEASE MPEG2_VERSION (0, 4, 1)",
                      (ROOT / "references/machawk-uae/deps/libmpeg2/include/mpeg2.h").read_text())
        manifest = {entry["path"]: entry["sha256"] for entry in
                    json.loads((ROOT / closure["source_manifest"]).read_text())["files"]}
        series = json.loads((ROOT / closure["patch_series"]).read_text())
        patched = {item["path"]: item["after_sha256"]
                   for patch in series["patches"] for item in patch["files"]}
        for name in closure["include_paths"] + closure["source_paths"][:-1]:
            relative = name.removeprefix("build/libmpeg2/")
            captured = "deps/libmpeg2/" + relative
            pristine = ROOT / "references/machawk-uae/deps/libmpeg2" / relative
            self.assertIn(captured, manifest)
            self.assertEqual(manifest[captured], hashlib.sha256(pristine.read_bytes()).hexdigest(), name)
            expected = patched.get(relative, manifest[captured])
            self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        selected = {Path(name).name for name in closure["source_paths"]}
        self.assertFalse(selected & {Path(name).name for name in closure["excluded_isa_sources"]})
        license_record = next(item for item in json.loads((ROOT / "manifests/licenses.json").read_text())["components"]
                              if item["id"] == closure["license_record"])
        self.assertIn("GPL-2.0", license_record["declared_license"])
        self.assertIn("eligible_under_GPL-2.0", license_record["build_eligibility"])

    def test_private_known_rom_copy_identity_refusals_and_lifetime(self):
        firmware = os.environ.get("P6_FMV_ROM")
        if not firmware:
            self.skipTest("BLOCKED: set P6_FMV_ROM to an authorized CD32 FMV 40.22/40.30 ROM")
        raw = Path(firmware).read_bytes()
        self.assertIn(len(raw), (262144, 524288))
        canonical = raw[:262144]
        if len(raw) == 524288:
            self.assertEqual(canonical, raw[262144:])
        self.assertIn(hashlib.sha1(canonical).hexdigest(),
                      {"03ca81c7a7b259cf64bc9582863eca0f6529f435", "94e469a76030dcb299ebc7520aaeef9db54284cf"})
        exe = build_targets(ROOT / "build/fmv-owner", ["p6_fmv_firmware_probe"])["p6_fmv_firmware_probe"]
        result = subprocess.run([str(exe), firmware], capture_output=True, text=True, timeout=180,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(0, result.returncode, result.stdout[-1000:] + result.stderr[-5000:])
        self.assertIn("CD32 FMV firmware owner/identity/lifetime PASS", result.stdout)

    def test_waterbox_additive_transport_binds_before_load_and_rolls_back_failure(self):
        source = (ROOT / "adapters/waterbox/puae6_guest.cpp").read_text()
        start = source.index("static int32_t init_machine(")
        end = source.index("ECL_EXPORT int32_t p6w_init_machine(", start)
        body = source[start:end]
        self.assertLess(body.index("p6_init(&args->config"), body.index("p6_cartridge_bind(model"))
        self.assertLess(body.index("p6_cartridge_bind(model"), body.index("p6_load(p6_buffer"))
        self.assertLess(body.index("p6_shutdown(nullptr)"), body.index("loaded = true"))
        header = (ROOT / "adapters/waterbox/p6w.h").read_text()
        self.assertIn("P6_CARTRIDGE_CD32_FMV", header)
        self.assertIn("before Seal", header)


if __name__ == "__main__":
    unittest.main()
