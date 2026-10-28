"""FMV-D: bounded decoder reconstruction and complete native device state."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.build_support import ROOT, build_targets


class FmvStateTests(unittest.TestCase):
    def firmware(self):
        downloads = Path.home() / "Downloads"
        main = Path(os.environ.get("P6_CD32_ROM_MAIN", downloads / "kick40060.CD32"))
        ext = Path(os.environ.get("P6_CD32_ROM_EXT", downloads / "kick40060.CD32.ext"))
        fmv = Path(os.environ.get("P6_CD32_FMV_ROM", os.environ.get("P6_FMV_ROM", downloads / "FMV.CD32-2.rom")))
        if not all(path.is_file() for path in (main, ext, fmv)):
            self.skipTest("BLOCKED: authorized CD32 main/ext and FMV ROMs required")
        return str(main), str(ext), str(fmv)

    def video(self, directory):
        manifest = json.loads((ROOT / "tests/fmv/vectors/manifest.json").read_text())
        source = ROOT / "tests/fmv/vectors" / manifest["video"]["file"]
        path = Path(directory) / "owned-video.m1v"
        path.write_bytes(base64.b64decode("".join(source.read_text().split()), validate=True))
        return path

    def test_fresh_decoder_future_is_exact_at_header_and_ipb_cuts(self):
        builds = (
            build_targets(ROOT / "build/fmv-state-sanitize", ["p6_fmv_state_probe"], sanitizers=True),
            build_targets(ROOT / "build/fmv-state-opt", ["p6_fmv_state_probe"], optimized=True),
        )
        with tempfile.TemporaryDirectory(prefix="p6-fmv-state-", dir=ROOT / "build") as directory:
            video = self.video(directory)
            outputs = []
            for build in builds:
                result = subprocess.run([str(build["p6_fmv_state_probe"]), str(video)],
                                        capture_output=True, text=True, timeout=120,
                                        env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(0, result.returncode, result.stdout[-4000:] + result.stderr[-8000:])
                outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        lines = outputs[0].splitlines()
        self.assertEqual(["H", "I", "P", "B"], [line.split()[1] for line in lines[:4]])
        self.assertTrue(all("future_frames=" in line and line.endswith("PASS") for line in lines[:4]))
        self.assertEqual("safe sequence-to-I compaction PASS", lines[4])

    def test_journal_and_runtime_patch_are_hash_closed_and_address_free(self):
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"]
        entry = next(item for item in series if item["id"] == "0420-cd32-fmv-decoder-journal")
        patch = (ROOT / entry["file"]).read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(patch).hexdigest())
        header = (ROOT / "ports/portable/fmv_decoder_journal.h").read_text()
        source = (ROOT / "ports/portable/fmv_decoder_journal.cpp").read_text()
        self.assertIn("1194u * 1024u + 4u", header)
        self.assertIn("kJournalEvents = 8192", header)
        self.assertNotRegex(source + header, r"uintptr_t|reinterpret_cast|clock_gettime|mach_absolute_time|sleep\s*\(")
        text = patch.decode()
        self.assertIn("record_feed(mpeg_journal", text)
        self.assertIn("record_parse(mpeg_journal", text)
        self.assertNotIn("mpeg2_internal.h", text)

    def test_complete_device_state_is_exact_fresh_bounded_and_transactional(self):
        main, ext, fmv = self.firmware()
        exe = build_targets(ROOT / "build/fmv-machine-state-sanitize",
                            ["p6_fmv_machine_state_probe"], sanitizers=True)["p6_fmv_machine_state_probe"]
        result = subprocess.run([str(exe), ext, main, fmv], capture_output=True, text=True,
                                timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-10000:])
        self.assertRegex(result.stdout,
                         r"FMV P6FM native state/fresh future/corruption PASS bound=\d+ state=\d+")

    def test_complete_device_patch_is_hash_closed_and_value_only(self):
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"]
        entry = next(item for item in series if item["id"] == "0421-cd32-fmv-state-owner")
        patch = (ROOT / entry["file"]).read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(patch).hexdigest())
        for item in entry["files"]:
            materialized = ROOT / "build/upstream" / item["path"]
            self.assertEqual(item["after_sha256"], hashlib.sha256(materialized.read_bytes()).hexdigest())
        body = (ROOT / "bridge/state_fmv.cpp").read_text()
        self.assertNotRegex(body, r"uintptr_t|\bvoid\s*\*|clock_gettime|mach_absolute_time|sleep\s*\(")
        self.assertIn("p6_fmv_state_prepare", patch.decode())
        self.assertIn("p6_fmv_aux_can_apply", patch.decode())

    def test_waterbox_snapshot_restores_mutable_fmv_allocations_in_a_fresh_sandbox(self):
        main, ext, fmv = self.firmware()
        sys.path.insert(0, str(ROOT / "tools"))
        import build_waterbox
        guest_receipt = ROOT / "build/waterbox/puae6/puae6-receipt.json"
        runtime_receipt = build_waterbox.runtime_receipt_path()
        if not guest_receipt.is_file() or not runtime_receipt.is_file():
            self.skipTest("BLOCKED: build the current Waterbox runtime and puae6.wbx")
        build_waterbox.check_guest_matches_trees(guest_receipt)
        guest = ROOT / "build/waterbox/puae6/release/puae6.wbx"
        guest_sha = json.loads(guest_receipt.read_text())["guest"]["sha256"]
        runtime = build_waterbox.read_runtime_receipt(runtime_receipt)["runtime_dylib"]["path"]
        result = build_waterbox.run_ffi(
            "fmvstate", "--runtime", runtime, "--guest", str(guest), "--sha", guest_sha,
            "--firmware", main, "--firmware-ext", ext, "--cartridge", fmv, "--model", "32",
            timeout=600)
        self.assertEqual(0, result.returncode, result.stdout[-4000:] + result.stderr[-10000:])
        self.assertIn("FMV Waterbox mutable-allocation snapshot/fresh future PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
