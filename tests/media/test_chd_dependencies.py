"""MED01/T66t1: pinned dependency, four real CD codecs; no product/guest build."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
            'target_include_directories(probe PRIVATE "$<TARGET_PROPERTY:p6_chd,INCLUDE_DIRECTORIES>")\n'
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


class ChdMaterializeTests(unittest.TestCase):
    """Own source copies, process races and two decoder objects; no engine build."""
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="chd-publish-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("tools", "cmake", "third_party"):
            (self.root / name).mkdir()
        shutil.copytree(ROOT / "third_party/libchdr", self.root / "third_party/libchdr")
        for name in ("tools/import_chd.py", "cmake/Chd.cmake"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.importer = load("fixture_import_chd", self.root / "tools/import_chd.py")
        self.target = self.root / "build/p6-chd"

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_concurrent_publish_once_and_cache_hits_preserve_bytes_and_mtimes(self):
        # Each process still runs the real patcher, lock, validation and publication.
        code = '''import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("chd", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
stage = module.stage_derived
def observed(*args):
    with open(sys.argv[3], "a") as stream: stream.write("stage\\n")
    stage(*args)
module.stage_derived = observed
module.materialize(pathlib.Path(sys.argv[2]))
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code,
                        str(self.root / "tools/import_chd.py"), str(self.target), str(self.root / "stages")],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        for process in processes:
            self.addCleanup(lambda p=process: p.kill() if p.poll() is None else None)
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode())
        self.assertEqual((self.root / "stages").read_text(), "stage\n")
        before = self.snapshot()
        with mock.patch.object(self.importer, "stage_derived", side_effect=AssertionError("cache wrote")):
            self.importer.materialize(self.target)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.name for p in self.target.parent.iterdir()), [".p6-chd.lock", "p6-chd"])

    def test_tampered_cache_and_forged_receipt_are_rejected_without_writes(self):
        self.importer.materialize(self.target)
        source = self.target / "include/dr_libs/dr_flac.h"
        source.write_bytes(source.read_bytes() + b"\n")
        receipt = self.target / "receipt.json"
        forged = json.loads(receipt.read_text())
        forged["files"]["include/dr_libs/dr_flac.h"] = hashlib.sha256(source.read_bytes()).hexdigest()
        receipt.write_text(json.dumps(forged))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "derived receipt differs"):
            self.importer.materialize(self.target)
        self.assertEqual(self.snapshot(), before)

    def test_destination_parent_cache_member_and_lock_symlinks_are_rejected(self):
        self.importer.materialize(self.target)
        link = self.target.parent / "linked"
        link.symlink_to(self.target, target_is_directory=True)
        for path in (link, link / "child", self.root / "outside", self.target.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.importer.materialize(path)
        receipt = self.target / "receipt.json"
        receipt.rename(self.root / "receipt")
        receipt.symlink_to(self.root / "receipt")
        with self.assertRaisesRegex(ValueError, "linked/nonregular"):
            self.importer.materialize(self.target)
        victim = self.root / "victim"; victim.write_bytes(b"own unrelated data")
        (self.target.parent / ".other.lock").symlink_to(victim)
        with self.assertRaises(OSError):
            self.importer.materialize(self.target.parent / "other")
        self.assertEqual(victim.read_bytes(), b"own unrelated data")

    def test_patch_failure_cleans_staging_and_never_publishes_partial_tree(self):
        with mock.patch.object(self.importer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.importer.materialize(self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual([p.name for p in self.target.parent.iterdir()], [".p6-chd.lock"])
        self.importer.materialize(self.target)  # Failure released the process lock.

    def test_verification_stays_available_without_platform_file_locking(self):
        before = self.snapshot()
        for unavailable in (None, object()):
            with self.subTest(locking=unavailable), mock.patch.dict(sys.modules, {"fcntl": unavailable}):
                self.assertEqual(self.importer.verify()["commit"], self.importer.PIN)
                with self.assertRaisesRegex(ValueError, "requires POSIX file locking"):
                    self.importer.materialize(self.target)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.target.parent.exists())

    def test_two_clean_object_trees_compile_the_same_verified_decoder_source(self):
        # A minimal CMake consumer inherits the package's -O2/assert/precision recipe.
        # Compile only its FLAC translation unit, never the engine or an executable.
        (self.root / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.19)\nproject(chd_repro C)\n'
            'set(CMAKE_C_EXTENSIONS OFF)\n'
            'add_compile_options(-fno-fast-math -ffp-contract=off)\n'
            'if(CMAKE_SYSTEM_NAME STREQUAL "Linux")\n'
            'add_compile_options(-ffunction-sections -fdata-sections)\nendif()\n'
            'include(cmake/Chd.cmake)\n')
        objects = []
        for name in ("first", "second"):
            tree = self.root / "build" / name
            ChdDependencyTests.run_ok(["cmake", "-S", str(self.root), "-B", str(tree), "-G", "Ninja",
                "-DCMAKE_C_COMPILER=/usr/bin/clang", "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_C_FLAGS_RELEASE=-O2", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"])
            entries = json.loads((tree / "compile_commands.json").read_text())
            entry, = [entry for entry in entries if entry["file"].endswith("/libchdr_flac.c")]
            self.assertEqual(Path(entry["file"]), self.target / "src/libchdr_flac.c")
            command = shlex.split(entry["command"])
            self.assertEqual(command[0], "/usr/bin/clang")
            for flag in ("-O2", "-fPIC", "-fvisibility=hidden", "-std=c99", "-fno-fast-math", "-ffp-contract=off",
                         "-D_7ZIP_ST", "-DDR_FLAC_NO_STDIO", "-DDR_FLAC_NO_OGG"):
                self.assertIn(flag, command)
            self.assertNotIn("-DNDEBUG", command)
            output = Path(entry["directory"]) / command[command.index("-o") + 1]
            output.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(command, cwd=entry["directory"], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            objects.append(output.read_bytes())
            self.assertFalse((tree / "p6-chd").exists())
        self.assertEqual(objects[0], objects[1])
        if sys.platform.startswith("linux"):
            self.assertIn(str(self.target / "include/dr_libs/dr_flac.h").encode() + b"\0", objects[0])
