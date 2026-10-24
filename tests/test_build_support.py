"""Test preparation policy without building an emulator or reusing an emulated machine."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, nullcontext
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import build_support as b
from tests.host import run_parallel as runner


class BuildSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p6-build-support-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.directory = self.root / "build/probe"
        self.directory.mkdir(parents=True)
        (self.root / "bridge").mkdir()
        (self.root / "bridge/test.cpp").write_text("original")
        generated = self.root / "build/generated"
        generated.mkdir()
        (generated / "codegen-receipt.json").write_text("verified")
        environment = dict(os.environ)
        environment.pop(b.RECEIPT_ENV, None)
        compilers = self.root / "compiler-fixtures"; compilers.mkdir()
        for name in ("clang","clang++"):
            (compilers/name).write_text("own compiler fixture, never executed")
            (compilers/name).chmod(0o755)
        environment["PATH"] = str(compilers)+os.pathsep+environment.get("PATH","")
        for mock in (patch.object(b, "ROOT", self.root), patch.dict(b._BUILDS, {}, clear=True), patch.dict(os.environ, environment, clear=True)):
            mock.start(); self.addCleanup(mock.stop)
        self.commands = []
        mocked = patch.object(b, "_command", side_effect=self.command)
        mocked.start(); self.addCleanup(mocked.stop)

    def command(self, args, timeout):
        self.commands.append(args)
        if "--build" in args:
            for target in args[args.index("--target") + 1:]:
                b.output_path(self.directory, target).write_text("executable-" + target)
        else:
            cache = self.directory / "CMakeCache.txt"
            entries = {line.split(":",1)[0]:line for line in cache.read_text().splitlines()
                       if line.startswith(("CMAKE_C_COMPILER:","CMAKE_CXX_COMPILER:"))} if cache.exists() else {}
            for variable in ("CMAKE_C_COMPILER","CMAKE_CXX_COMPILER"):
                values = [arg.split("=",1)[1] for arg in args if arg.startswith("-D"+variable+"=")]
                if values: entries[variable] = variable+":STRING="+values[0]
            cache.write_text("\n".join([*entries.values(),*args]))

    def build(self, targets=("probe",), **kwargs):
        return b.build_targets(self.directory, targets, **kwargs)

    def test_same_probe_builds_once_and_an_extra_target_does_not_reconfigure(self):
        first = self.build()
        self.assertEqual(self.build(), first)
        self.assertEqual(len(self.commands), 2)
        self.build(["probe", "another"])
        self.assertEqual(len(self.commands), 3)
        self.assertEqual(self.commands[-1][-1], "another")

    def test_configurations_do_not_share_a_prepared_result(self):
        self.build()
        self.build(trace=True)
        self.build(sanitizers=True)
        self.assertEqual(len(self.commands), 6)
        self.assertIn("-DP6_SANITIZERS=ON", self.commands[-2])

    def test_dependency_bytes_invalidate_preparation_even_with_original_mtime(self):
        dependency = self.root / "third_party/decoder/source.c"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("original")
        self.build()
        stamp = dependency.stat()
        dependency.write_text("modified")
        os.utime(dependency, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.build()
        self.assertEqual(4, len(self.commands))

    def test_worker_rejects_a_receipt_before_a_dependency_changed(self):
        dependency = self.root / "third_party/decoder/source.h"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("original")
        self.build()
        receipt = self.root / "prepared.json"
        b.write_receipt(receipt)
        dependency.write_text("modified")
        self.commands.clear()
        with patch.dict(os.environ, {b.RECEIPT_ENV: str(receipt)}), patch.dict(b._BUILDS, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "receipt"):
                self.build()
        self.assertEqual([], self.commands)

    def test_an_optimized_build_is_a_different_recipe_with_asserts_kept(self):
        self.build()
        optimized = self.build(optimized=True)
        self.assertEqual(4, len(self.commands), "the Debug result does not stand for the optimized one, even in the same directory")
        configure = self.commands[2]
        self.assertIn("-DCMAKE_BUILD_TYPE=RelWithDebInfo", configure)
        self.assertIn("-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2 -g", configure)
        self.assertIn("-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -g", configure)
        self.assertFalse([a for a in configure if "NDEBUG" in a], "the flags replace RelWithDebInfo's -DNDEBUG")
        self.assertIn("-DP6_SANITIZERS=OFF", configure)
        self.assertIn("-DCMAKE_BUILD_TYPE=Debug", self.commands[0])
        self.assertFalse([a for a in self.commands[0] if "RELWITHDEBINFO" in a])
        self.assertEqual(optimized, self.build(optimized=True))
        self.assertEqual(4, len(self.commands))

    def test_worker_receipt_of_one_build_type_does_not_validate_the_other(self):
        self.build(optimized=True)
        receipt = self.root / "prepared.json"
        b.write_receipt(receipt)
        with patch.dict(os.environ, {b.RECEIPT_ENV: str(receipt)}), patch.dict(b._BUILDS, {}, clear=True):
            self.build(optimized=True)
            with self.assertRaisesRegex(RuntimeError, "receipt"):
                self.build()

    def test_state_probe_tree_follows_the_environment(self):
        with patch.dict(os.environ):
            os.environ.pop(b.STATE_PROBE_ENV, None)
            self.assertEqual((self.root / "build/state-opt", True), b.state_probe_tree())
            os.environ[b.STATE_PROBE_ENV] = "debug"
            self.assertEqual((self.root / "build/link-audit", False), b.state_probe_tree())
            self.assertEqual((self.root / "build/state-opt", True), b.state_probe_tree("optimized"))
            os.environ[b.STATE_PROBE_ENV] = "O2"
            with self.assertRaisesRegex(ValueError, b.STATE_PROBE_ENV):
                b.state_probe_tree()

    def test_shared_library_target_resolves_its_real_artifact(self):
        result = self.build(["puae6_libretro"])
        self.assertTrue(result["puae6_libretro"].is_file())
        self.assertIn(result["puae6_libretro"].suffix, (".so", ".dylib", ".dll"))
        self.assertEqual(result, self.build(["puae6_libretro"]))
        self.assertEqual(len(self.commands), 2)

    def test_source_content_change_invalidates_even_with_preserved_mtime(self):
        self.build()
        source = self.root / "bridge/test.cpp"
        before = source.stat()
        source.write_text("modified")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.build()
        self.assertEqual(len(self.commands), 4)

    def test_failed_build_is_not_remembered(self):
        with patch.object(b, "_command", side_effect=RuntimeError("build failed")):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                self.build()
        self.build()
        self.assertEqual(len(self.commands), 2)

    def test_cold_compilers_follow_path_and_warm_cache_is_only_validated(self):
        own = self.root / "own-tools"; own.mkdir()
        for name in ("clang","clang++"):
            (own / name).write_text("own compiler fixture, never executed")
            (own / name).chmod(0o755)
        with patch.dict(os.environ, {"PATH":str(own)}):
            self.build()
            self.assertIn("-DCMAKE_C_COMPILER="+str(own/"clang"),self.commands[0])
            self.assertIn("-DCMAKE_CXX_COMPILER="+str(own/"clang++"),self.commands[0])
            b._BUILDS.clear(); self.commands.clear(); self.build()
            self.assertFalse(any(arg.startswith(("-DCMAKE_C_COMPILER=","-DCMAKE_CXX_COMPILER=")) for arg in self.commands[0]))
            link = own/"cxx-link"; link.symlink_to(own/"clang++")
            cache = self.directory/"CMakeCache.txt"
            cache.write_text(cache.read_text().replace("CMAKE_CXX_COMPILER:STRING="+str(own/"clang++"),
                                                       "CMAKE_CXX_COMPILER:STRING="+str(link)))
            b._BUILDS.clear(); self.build()  # Explicitly equivalent symlink target.

    def test_missing_duplicate_and_foreign_cached_compilers_fail_before_configure(self):
        self.build(); cache=self.directory/"CMakeCache.txt"; original=cache.read_text()
        for variable in ("CMAKE_C_COMPILER","CMAKE_CXX_COMPILER"):
            line=next(line for line in original.splitlines() if line.startswith(variable+":"))
            for bad in (original.replace(line,""), original+"\n"+line,
                        original.replace(line,variable+":FILEPATH="+str(self.root/"foreign-compiler"))):
                with self.subTest(variable=variable,cache=bad):
                    (self.root/"foreign-compiler").write_text("different compiler")
                    cache.write_text(bad); b._BUILDS.clear(); self.commands.clear()
                    with self.assertRaisesRegex(RuntimeError,"cached test compiler|Cached test compiler"):
                        self.build()
                    self.assertEqual(self.commands,[]); self.assertEqual(cache.read_text(),bad)
        cache.write_text(original); b._BUILDS.clear(); self.commands.clear()
        with patch.object(b.shutil,"which",return_value=None),self.assertRaisesRegex(RuntimeError,"Missing required test compiler"):
            self.build()
        self.assertEqual(self.commands,[])

    def test_deleted_executable_is_built_again_but_altered_one_is_rejected(self):
        self.build()
        (self.directory / "probe").unlink()
        self.build()
        self.assertEqual(len(self.commands), 3)
        (self.directory / "probe").write_text("altered")
        with self.assertRaisesRegex(RuntimeError, "altered"):
            self.build()

    def test_worker_uses_only_a_matching_receipt_and_never_builds(self):
        self.build()
        receipt = self.root / "prepared.json"
        b.write_receipt(receipt)
        self.commands.clear()
        with patch.dict(os.environ, {b.RECEIPT_ENV: str(receipt)}), patch.dict(b._BUILDS, {}, clear=True):
            self.build()
            self.assertEqual(self.commands, [])
            for invalid in ("unknown-target", "source", "executable", "receipt"):
                with self.subTest(invalid=invalid):
                    if invalid == "source":
                        (self.root / "bridge/test.cpp").write_text("changed")
                    if invalid == "executable":
                        (self.root / "bridge/test.cpp").write_text("original")
                        (self.directory / "probe").write_text("altered")
                    if invalid == "receipt":
                        receipt.write_text("{}")
                    with self.assertRaisesRegex(RuntimeError, "receipt"):
                        self.build(["unknown"] if invalid == "unknown-target" else ["probe"])
                    self.assertEqual(self.commands, [])

    def test_threads_share_one_preparation(self):
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.build(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.commands), 2)

    def test_build_lock_excludes_another_process(self):
        ready, entered = self.root / "ready", self.root / "entered"
        code = (f"from pathlib import Path; from tests import build_support as b; b.ROOT=Path({str(self.root)!r}); "
                f"Path({str(ready)!r}).write_text('ready')\n"
                f"with b.build_lock(): Path({str(entered)!r}).write_text('entered')\n")
        with b.build_lock():
            child = subprocess.Popen([sys.executable, "-c", code], cwd=Path(b.__file__).resolve().parents[1])
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                self.assertFalse(entered.exists())
            except BaseException:
                child.kill(); child.wait(); raise
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertTrue(entered.exists())


class RealCompilerCacheTests(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(name) for name in ("cmake","ninja","clang","clang++")),"requires the native CMake/Clang tools")
    def test_cold_and_new_process_reconfigure_preserve_cache_bytes(self):
        # CMake's own compiler ABI checks are tiny; no emulator source is used.
        build = Path(b.__file__).resolve().parents[1]/"build"; build.mkdir(exist_ok=True)
        parent_records = dict(b._BUILDS)
        with tempfile.TemporaryDirectory(prefix="own-cmake-cache-",dir=build) as temporary:
            root=Path(temporary).resolve(); generated=root/"build/generated"; generated.mkdir(parents=True)
            (generated/"codegen-receipt.json").write_text("own fixture, not product codegen")
            (root/"CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.21)\nproject(OwnCache LANGUAGES C CXX)\n'
                'add_custom_target(probe COMMAND "${CMAKE_COMMAND}" -E touch "${CMAKE_BINARY_DIR}/probe")\n')
            code=(f"from pathlib import Path; from tests import build_support as b; b.ROOT=Path({str(root)!r}); "
                  "b.build_targets(b.ROOT/'build/probe',('probe',),optimized=True)")
            cache=root/"build/probe/CMakeCache.txt"
            for iteration in range(2):
                process=subprocess.run([sys.executable,"-B","-c",code],cwd=Path(b.__file__).resolve().parents[1],
                                       capture_output=True,text=True,timeout=90)
                self.assertEqual(process.returncode,0,process.stdout+process.stderr)
                if iteration == 0: first=cache.read_bytes()
                else: self.assertEqual(cache.read_bytes(),first)
            self.assertEqual(b._BUILDS,parent_records,"child builds do not populate the parent record cache")


class RunnerSummaryTests(unittest.TestCase):
    def run_summary(self, output):
        with tempfile.TemporaryDirectory(prefix='p6-runner-summary-') as tmp:
            root = Path(tmp)
            (root / 'build').mkdir()
            (root / 'tests/host').mkdir(parents=True)
            (root / 'tests/host/test_fixture.py').write_text('# module selected by the mocked worker\n')
            printed = io.StringIO()
            with patch.object(runner, 'ROOT', root), \
                    patch.object(sys, 'argv', ['runner', '-j', '1', 'test_fixture']), \
                    patch.object(runner, 'build_lock', nullcontext), \
                    patch.object(runner.subprocess, 'run', return_value=type('Prepared', (), {'returncode': 0})()), \
                    patch.object(runner, 'write_receipt'), \
                    patch.object(runner, 'run_module', return_value=('test_fixture', 0, 0.1, output)), \
                    redirect_stdout(printed):
                code = runner.main()
            return code, printed.getvalue()

    def test_summary_exposes_skips_and_expected_failures(self):
        code, output = self.run_summary('Ran 3 tests in 0.1s\nOK (skipped=1, expected failures=1)\n')
        self.assertEqual(code, 0)
        self.assertIn('3 tests, 1 skipped, 1 expected failures', output)

    def test_empty_or_missing_test_count_fails(self):
        for summary in ('Ran 0 tests in 0.1s\nOK\n', 'no unittest result\n'):
            with self.subTest(summary=summary):
                code, output = self.run_summary(summary)
                self.assertEqual(code, 1)
                self.assertIn('No tests executed', output)


if __name__ == "__main__":
    unittest.main()
