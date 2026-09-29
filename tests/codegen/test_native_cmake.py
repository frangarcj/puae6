"""Native bootstrap graph checks; these do not execute an emulator."""
import json
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(all(shutil.which(t) for t in ("cmake", "ninja", "clang", "clang++")),
                     "Native CMake toolchain is unavailable")
class NativeCMakeTests(unittest.TestCase):
    def configure(self, build, *options):
        return subprocess.run(
            ["cmake", "-S", str(ROOT), "-B", str(build), "-G", "Ninja",
             "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++", *options],
            capture_output=True, text=True, timeout=60)

    def test_excluded_desktop_helpers_have_no_selected_callers(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        manifest = json.loads((ROOT / "manifests/source-manifest.json").read_text())
        tree = ROOT / "build/upstream"
        api = "\n".join((tree / "include" / name).read_text()
                        for name in ("calc.h", "ini.h"))
        names = sorted(set(re.findall(r"\b(calc|iscalcformula|ini_\w+)\s*\(", api)))
        self.assertIn("calc", names)
        self.assertIn("ini_load", names)
        pattern = "(?:" + "|".join(names) + ")"
        # Only remove function declarations. Calls or definitions in ANY source
        # or included header remain visible, including address-taking uses.
        declaration = re.compile(
            r"(?m)^\s*(?:extern\s+)?(?:int|bool|void|struct\s+ini_data\s*\*)"
            r"\s*" + pattern + r"\s*\([^;{}]*\)\s*;\s*$")
        common = ["clang++", "-std=c++17", "-E",
                  "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"),
                  "-I" + str(tree / "include"), "-I" + str(tree)]
        for key, folder in (("upstream_units", tree),
                            ("generated_units", ROOT / "build/generated")):
            for name in manifest["selection"][key]:
                with self.subTest(unit=name):
                    result = subprocess.run([*common, str(folder / name)], check=True,
                                            capture_output=True, text=True, timeout=60)
                    code = re.sub(r"(?m)^#.*$", "", result.stdout)
                    code = declaration.sub("", code)
                    self.assertNotRegex(code, r"\b" + pattern + r"\b")

    def test_graph_and_active_assertions_in_both_presets(self):
        manifest = json.loads((ROOT / "manifests/source-manifest.json").read_text())
        selection = manifest["selection"]
        expected = {str(ROOT / "build" / directory / name)
                    for key, directory in (("upstream_units", "upstream"),
                                           ("generated_units", "generated"))
                    for name in selection[key]}
        expected_includes = [str(ROOT / "ports/portable")] + [
            str(ROOT / p) for p in selection["include_directories"]]
        presets = json.loads((ROOT / "CMakePresets.json").read_text())
        debug, release = presets["configurePresets"]
        for preset in (debug, release):
            with self.subTest(preset=preset["name"]), tempfile.TemporaryDirectory() as tmp:
                variables = dict(debug["cacheVariables"], **preset["cacheVariables"])
                result = self.configure(tmp, *[f"-D{k}={v}" for k, v in variables.items()])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                commands = json.loads((Path(tmp) / "compile_commands.json").read_text())
                engine = [c for c in commands if "p6_engine.dir/" in c["command"]]
                self.assertEqual({c["file"] for c in engine}, expected)
                self.assertEqual(len(engine), len(expected))
                for command in engine:
                    args = shlex.split(command["command"])
                    self.assertEqual([a[2:] for a in args if a.startswith("-I")], expected_includes)
                    self.assertIn("-fno-fast-math", args)
                    self.assertIn("-std=c++17", args)
                    self.assertEqual("-fsanitize=address,undefined" in args,
                                     variables["P6_SANITIZERS"] == "ON")
                probe = next(c for c in commands if c["file"].endswith("portable_services_probe.cpp"))
                args = shlex.split(probe["command"])
                self.assertIn("-UNDEBUG", args)
                if "-DNDEBUG" in args:
                    self.assertGreater(args.index("-UNDEBUG"), args.index("-DNDEBUG"))
                listing = subprocess.run(["ctest", "--test-dir", tmp, "--show-only=json-v1"],
                                         capture_output=True, text=True, check=True)
                self.assertEqual([t["name"] for t in json.loads(listing.stdout)["tests"]],
                                 ["portable-services"])

    def test_unsupported_configurations_fail_explicitly(self):
        cases = (("-DP6_PROFILE=accelerated", "Unsupported P6_PROFILE"),
                 ("-DJIT=0", "Excluded feature requested: JIT"),
                 ("-DCMAKE_SYSTEM_NAME=Generic", "Only the native host architecture"))
        for option, diagnostic in cases:
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                result = self.configure(tmp, option)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stdout + result.stderr)

    def test_ninja_fresh_build_and_clean_with_atomic_publication(self):
        # Isolate CMake's directory/order/clean behavior from the real shared
        # cache. The tiny publisher rejects partial trees just like codegen.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("cmake", "manifests", "tools"):
                (root / name).mkdir()
            shutil.copyfile(ROOT / "cmake/HostTools.cmake", root / "cmake/HostTools.cmake")
            (root / "manifests/source-manifest.json").write_text(json.dumps({
                "selection": {"upstream_units": ["main.c"], "generated_units": ["value.c"]}}))
            (root / "tools/generate_cpu.py").write_text('''from pathlib import Path
import tempfile
root = Path.cwd()
for directory, filename, content in (
    ("upstream", "main.c", "int value(void); int main(void) { return value() != 42; }"),
    ("generated", "value.c", "int value(void) { return 42; }")):
    dest = root / "build" / directory
    if dest.exists():
        assert (dest / filename).read_text() == content, "Partial or changed cache"
    else:
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            stage = Path(tmp) / "tree"
            stage.mkdir()
            (stage / filename).write_text(content)
            dest.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(dest)
''')
            (root / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.21)
project(atomic_publication C)
include(cmake/HostTools.cmake)
p6_add_host_codegen(codegen)
add_executable(probe ${P6_UPSTREAM_SOURCES} ${P6_GENERATED_SOURCES})
add_dependencies(probe codegen)
''')
            binary = root / "build/native"
            def run(*args):
                result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run("cmake", "-S", str(root), "-B", str(binary), "-G", "Ninja")
            self.assertFalse((root / "build/upstream").exists())
            for iteration in range(2):
                run("cmake", "--build", str(binary))
                run(str(binary / "probe"))
                run("cmake", "--build", str(binary), "--target", "clean")
                self.assertFalse((root / "build/upstream").exists())
                self.assertFalse((root / "build/generated").exists())


if __name__ == "__main__":
    unittest.main()
