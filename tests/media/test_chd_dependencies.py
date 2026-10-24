"""MED01/T66t1: pinned dependency, four real CD codecs; no product/guest build."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/chd"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChdDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="chd-dependencies-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.importer = load("import_chd", ROOT / "tools/import_chd.py")
        cls.importer.verify()
        cls.pattern = load("chd_pattern", FIXTURES / "generate.py")
        cls.expected = cls.work / "expected.bin"
        cls.expected.write_bytes(cls.pattern.expected())
        (cls.work / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.19)\nproject(chd_probe C)\n'
            f'include("{ROOT}/cmake/Chd.cmake")\n'
            f'add_executable(probe "{ROOT}/tests/media/chd_dependencies_probe.c")\n'
            'target_include_directories(probe PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/p6-chd/deps/zlib")\n'
            'target_link_libraries(probe PRIVATE p6_chd)\n')
        cls.run_ok(["cmake", "-S", str(cls.work), "-B", str(cls.work / "out"),
                    "-DCMAKE_BUILD_TYPE=Release"])
        cls.run_ok(["cmake", "--build", str(cls.work / "out"), "--target", "probe", "-j2"])

    @staticmethod
    def run_ok(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(f"exit {result.returncode}: " + result.stdout[-3000:] + result.stderr[-3000:])

    def test_four_real_cd_codecs_match_every_byte(self):
        hashes = json.loads((FIXTURES / "sha256.json").read_text())
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                fixture = FIXTURES / (codec + ".chd")
                self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), hashes[fixture.name])
                self.run_ok([str(self.work / "out/probe"), str(fixture), str(self.expected), codec])

    def test_offline_import_integrity_rejects_mutation_and_extra_files(self):
        copy = self.work / "mutated"
        shutil.copytree(ROOT / "third_party/libchdr", copy)
        self.importer.verify(copy)
        source = copy / "src/libchdr_chd.c"
        source.write_bytes(source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            self.importer.verify(copy)
        shutil.copyfile(ROOT / "third_party/libchdr/src/libchdr_chd.c", source)
        (copy / "unexpected.c").write_text("extra")
        with self.assertRaisesRegex(ValueError, "extra files"):
            self.importer.verify(copy)

    def test_adler32_and_crc32_abi_match_python_independently(self):
        data = bytes((n * 31 + n // 251) & 255 for n in range(65539))
        self.run_ok([str(self.work / "out/probe"), "checksums", str(zlib.adler32(data)), str(zlib.crc32(data))])

    def test_cmake_and_integrity_work_without_reference_snapshot(self):
        minimal = self.work / "package"
        for path in ("third_party/libchdr", "cmake", "tools"):
            (minimal / path).mkdir(parents=True)
        shutil.copytree(ROOT / "third_party/libchdr", minimal / "third_party/libchdr", dirs_exist_ok=True)
        shutil.copyfile(ROOT / "tools/import_chd.py", minimal / "tools/import_chd.py")
        shutil.copyfile(ROOT / "cmake/Chd.cmake", minimal / "cmake/Chd.cmake")
        (minimal / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.19)\nproject(chd C)\ninclude(cmake/Chd.cmake)\n')
        self.run_ok(["cmake", "-S", str(minimal), "-B", str(minimal / "build")])
        (minimal / "build/p6-chd/deps/zlib/adler32.c").write_text("altered")
        receipt = minimal / "build/p6-chd/receipt.json"
        forged = json.loads(receipt.read_text())
        forged["files"]["deps/zlib/adler32.c"] = hashlib.sha256(b"altered").hexdigest()
        receipt.write_text(json.dumps(forged))
        result = subprocess.run(["cmake", "-S", str(minimal), "-B", str(minimal / "build")],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("derived receipt differs", result.stderr)
