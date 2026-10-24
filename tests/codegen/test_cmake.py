"""Configure-only checks for the cross-compilation-safe host-tools module."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "cmake/HostTools.cmake"


@unittest.skipUnless(shutil.which("cmake"), "CMake is not installed")
class HostToolsCMakeTests(unittest.TestCase):
    def test_configure_uses_host_tools_without_running_codegen(self):
        with tempfile.TemporaryDirectory(prefix="p6-cmake-hosttools-") as tmp:
            work = Path(tmp)
            project, build = work / "project", work / "build"
            project.mkdir()
            marker = work / "host-tool-ran"
            tools = work / "host-tools"
            tools.mkdir()
            fake_cc, fake_cxx = tools / "cc", tools / "cxx"
            script = f"#!/bin/sh\nprintf called >> '{marker}'\n"
            for executable in (fake_cc, fake_cxx):
                executable.write_text(script)
                executable.chmod(0o755)
            (project / "CMakeLists.txt").write_text(f'''cmake_minimum_required(VERSION 3.19)
project(hosttools LANGUAGES NONE)
include("{MODULE.as_posix()}")
p6_add_host_codegen(host_codegen)
file(WRITE "${{CMAKE_BINARY_DIR}}/exports.txt"
  "${{P6_GENERATED_DIR}}\\n${{P6_UPSTREAM_DIR}}\\n")
file(APPEND "${{CMAKE_BINARY_DIR}}/exports.txt"
  "${{P6_INCLUDE_DIRS}}\\n${{P6_UPSTREAM_SOURCES}}\\n${{P6_GENERATED_SOURCES}}\\n")
list(GET P6_UPSTREAM_SOURCES 0 first_upstream_source)
get_source_file_property(first_upstream_generated "${{first_upstream_source}}" GENERATED)
file(APPEND "${{CMAKE_BINARY_DIR}}/exports.txt" "${{first_upstream_generated}}\\n")
''')
            result = subprocess.run(
                [shutil.which("cmake"), "-S", str(project), "-B", str(build),
                 "-DCMAKE_CXX_COMPILER=/unusable/target",
                 f"-DP6_HOST_CC={fake_cc}", f"-DP6_HOST_CXX={fake_cxx}"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(marker.exists(), "configure ran a host tool or generator")
            exports = (build / "exports.txt").read_text().splitlines()
            self.assertEqual(exports[:2], [str(ROOT / "build/generated"),
                                           str(ROOT / "build/upstream")])
            self.assertEqual(exports[2].split(";"), [str(ROOT / "build/generated"),
                                                       str(ROOT / "build/upstream/include"),
                                                       str(ROOT / "build/upstream")])
            manifest = __import__("json").loads(
                (ROOT / "manifests/source-manifest.json").read_text())
            upstream_count = len(manifest["selection"]["upstream_units"])
            generated_count = len(manifest["selection"]["generated_units"])
            self.assertEqual(len(exports[3].split(";")), upstream_count)
            self.assertEqual(len(exports[4].split(";")), generated_count)
            self.assertEqual(exports[5], "1")
            self.assertFalse(marker.exists())
            cache = (build / "CMakeCache.txt").read_text()
            self.assertIn(f"P6_HOST_CC:FILEPATH={fake_cc}", cache)
            self.assertIn(f"P6_HOST_CXX:FILEPATH={fake_cxx}", cache)


if __name__ == "__main__":
    unittest.main()
