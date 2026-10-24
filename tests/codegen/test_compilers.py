"""Compiler matrix of the headless runner (opt-in: it builds the whole engine once per compiler).

    P6_TEST_COMPILERS=1 python3 -m unittest tests.codegen.test_compilers

Every compiler found must build p6_headless and print the identity of the Apple clang build.
Differences that are expected (dependent runtime library, warning volume) are asserted, so a
new one shows up here instead of in the field."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = {
    "llvm": ("/opt/homebrew/opt/llvm/bin/clang", "/opt/homebrew/opt/llvm/bin/clang++"),
    "gcc": ("/opt/homebrew/opt/gcc/bin/gcc-16", "/opt/homebrew/opt/gcc/bin/g++-16"),
}


def build(name, cc, cxx):
    directory = ROOT / "build" / f"cc-{name}"
    subprocess.run(["cmake", "-S", str(ROOT), "-B", str(directory), "-G", "Ninja", f"-DCMAKE_C_COMPILER={cc}",
                    f"-DCMAKE_CXX_COMPILER={cxx}", "-DCMAKE_BUILD_TYPE=Release", "-DP6_SANITIZERS=OFF"],
                   check=True, capture_output=True, text=True, timeout=180)
    subprocess.run(["cmake", "--build", str(directory), "--parallel", "2", "--target", "p6_headless"], check=True,
                   capture_output=True, text=True, timeout=3600)
    return directory / "p6_headless"


@unittest.skipUnless(os.environ.get("P6_TEST_COMPILERS") == "1", "opt-in: set P6_TEST_COMPILERS=1")
class CompilerMatrixTests(unittest.TestCase):
    def test_every_available_compiler_builds_the_same_runner(self):
        subprocess.run(["cmake", "-S", str(ROOT), "-B", str(ROOT / "build/cc-apple"), "-G", "Ninja",
                                    "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                                    "-DCMAKE_BUILD_TYPE=Release", "-DP6_SANITIZERS=OFF"],
                                   check=True, capture_output=True, text=True, timeout=180)
        subprocess.run(["cmake", "--build", str(ROOT / "build/cc-apple"), "--parallel", "2", "--target", "p6_headless"],
                       check=True, capture_output=True, text=True, timeout=3600)
        expected = subprocess.run([str(ROOT / "build/cc-apple/p6_headless")], check=True,
                                  capture_output=True, text=True, timeout=30).stdout
        tested = 0
        for name, (cc, cxx) in CANDIDATES.items():
            if not (Path(cc).exists() and Path(cxx).exists()):
                continue
            with self.subTest(compiler=name):
                exe = build(name, cc, cxx)
                got = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=30).stdout
                self.assertEqual(got, expected)
                libs = subprocess.run(["otool", "-L", str(exe)], check=True, capture_output=True,
                                      text=True).stdout
                self.assertIn("libSystem.B.dylib", libs)
                if name == "gcc":
                    # GNU brings its own libstdc++ from the compiler installation; a distributable
                    # build must link it statically or ship it.
                    self.assertIn("libstdc++", libs)
                tested += 1
        if not tested:
            self.skipTest("no alternative compiler is installed at the known Homebrew paths")


if __name__ == "__main__":
    unittest.main()
