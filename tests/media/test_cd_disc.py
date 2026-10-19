"""MED01/T66b: normalized TOC/sectors; fixed upstream encoder, no engine build."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which("clang++"), "clang++ unavailable")
class CdDiscTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd-disc-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        entry = next(p for p in json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"] if p["id"] == "0355-cdrom-mode1-sync")
        source = (ROOT / "upstream/winuae/cdrom.cpp").read_bytes()
        patch = (ROOT / entry["file"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == entry["files"][0]["before_sha256"]
        assert hashlib.sha256(patch).hexdigest() == entry["sha256"]
        target = work / "cdrom.cpp"
        target.write_bytes(source)
        mat._apply(patch, work, True)
        mat._apply(patch, work, False)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry["files"][0]["after_sha256"]
        cls.exe = work / "probe"
        # This pure unit needs only cstring and real uae integer types. Skip the
        # unrelated platform sysdeps header, not any encoder code or algorithm.
        command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
                   "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-DUAE_SYSDEPS_H", "-include", "cstring",
                   "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "ports/portable/media"), "-I" + str(ROOT / "upstream/winuae/include"),
                   str(ROOT / "tests/media/cd_disc_probe.cpp"), str(ROOT / "ports/portable/media/cd_disc.cpp"),
                   str(ROOT / "ports/portable/media/cd_source.cpp"), str(target), "-o", str(cls.exe)]
        out = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise AssertionError(out.stderr)

    def run_probe(self, case):
        out = subprocess.run([str(self.exe), case], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"})
        self.assertEqual(0, out.returncode, out.stderr)

    def test_mixed_toc_raw_payload_ecma_sync_edc_and_both_pregaps(self):
        self.run_probe("reads")

    def test_rejections_faults_and_rebind_are_transactional(self):
        self.run_probe("failures")
