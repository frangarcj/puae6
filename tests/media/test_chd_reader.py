"""MED01/T66t2: bounded offset I/O and ownership, before full map/codec hardening."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests.media import test_chd_dependencies as dependency

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/chd"


class ChdReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="chd-reader-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.expected = cls.work / "expected.bin"
        cls.pattern = dependency.load("chd_pattern", FIXTURES / "generate.py")
        cls.expected.write_bytes(cls.pattern.expected())
        (cls.work / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.19)\nproject(chd_reader C CXX)\n'
            f'include("{ROOT}/cmake/Chd.cmake")\n'
            f'add_executable(probe "{ROOT}/tests/media/chd_reader_probe.cpp")\n'
            'target_link_libraries(probe PRIVATE p6_chd_reader)\n')
        flags = "-O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer"
        dependency.ChdDependencyTests.run_ok(["cmake", "-S", str(cls.work), "-B", str(cls.work / "out"),
            "-DCMAKE_C_FLAGS=" + flags, "-DCMAKE_CXX_FLAGS=" + flags, "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined"])
        dependency.ChdDependencyTests.run_ok(["cmake", "--build", str(cls.work / "out"), "--target", "probe", "-j2"])

    def probe(self, case, fixture, expected):
        env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=" + ("0" if sys.platform == "darwin" else "1")}
        result = subprocess.run([str(self.work / "out/probe"), case, str(fixture), str(expected)],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_four_codecs_offsets_crossings_repeat_and_duplicate_codec_ownership(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                self.probe("positive", FIXTURES / (codec + ".chd"), self.expected)

    def test_false_and_short_io_at_each_open_and_cross_hunk_read_step_are_atomic(self):
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                self.probe("faults", FIXTURES / (codec + ".chd"), self.expected)

    def test_headers_geometry_resource_limits_parent_and_write_rejections(self):
        self.probe("headers", FIXTURES / "none.chd", FIXTURES / "cdzl.chd")
