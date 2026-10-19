"""MED01/T66a: bounded ISO2048 source, real ASan/UBSan probe without an engine."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLANG = shutil.which("clang++")


@unittest.skipUnless(CLANG, "clang++ is unavailable")
class CdSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd-source-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.exe = Path(cls.temp.name) / "probe"
        result = subprocess.run([CLANG, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                                 "-fno-exceptions", "-fno-rtti", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                 "-I", str(ROOT / "ports/portable/media"), str(ROOT / "tests/media/cd_source_probe.cpp"),
                                 str(ROOT / "ports/portable/media/cd_source.cpp"), "-o", str(cls.exe)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_probe(self, case):
        result = subprocess.run([str(self.exe), case], capture_output=True, text=True, timeout=30,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_large_sources_and_unordered_exact_reads(self):
        self.run_probe("reads")

    def test_ranges_overflow_and_provider_failure_leave_output_unchanged(self):
        self.run_probe("failures")

    def test_identical_rebinding_and_transactional_rejections(self):
        self.run_probe("rebinds")
