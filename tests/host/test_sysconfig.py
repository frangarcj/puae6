"""Native execution and cross-target syntax checks are reported separately."""
import json
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROBE = '''#include "sysconfig.h"
#if defined(__cplusplus)
#define CHECK static_assert
#else
#define CHECK _Static_assert
#endif
CHECK(sizeof(short) == SIZEOF_SHORT, "short");
CHECK(sizeof(int) == SIZEOF_INT, "int");
CHECK(sizeof(long) == SIZEOF_LONG, "long");
CHECK(sizeof(long long) == SIZEOF_LONG_LONG, "long long");
CHECK(sizeof(void*) == SIZEOF_VOID_P, "pointer");
CHECK(sizeof(uae_atomic) == 4 && (uae_atomic)-1 < 0, "atomic signed32");
CHECK(sizeof(long) == EXPECT_LONG, "data model");
CHECK(CPUEMU_13 == 1 && P6_PORTABLE == 1, "profile");
int main(void) { return 0; }
'''


class SysconfigTests(unittest.TestCase):
    def compile(self, source, *flags, language="c++", output=None):
        return subprocess.run(["clang", "-x", language, "-std=" + ("c++17" if language == "c++" else "c11"),
                               "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "ports/portable"),
                               *flags, *( ["-o", str(output)] if output else ["-fsyntax-only"]), "-"],
                              input=source, capture_output=True, text=True)

    def test_native_c_and_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ("c", "c++"):
                with self.subTest(language=language):
                    output = Path(directory) / "probe"
                    result = self.compile(PROBE, f"-DEXPECT_LONG={ctypes.sizeof(ctypes.c_long)}", language=language, output=output)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    subprocess.run([str(output)], check=True)

    def test_cross_abi_lp64_and_llp64_compile_only(self):
        for target, long_size in (("aarch64-linux-gnu", 8), ("x86_64-pc-windows-msvc", 4)):
            with self.subTest(target=target):
                result = self.compile(PROBE, "--target=" + target, "-ffreestanding", f"-DEXPECT_LONG={long_size}")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_every_excluded_feature_even_when_zero(self):
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        for feature in selection["features_off"] + ["SUPPORT_THREADS", "UAE_MINI"]:
            for value in (0, 1):
                with self.subTest(feature=feature, value=value):
                    result = self.compile('#include "sysconfig.h"', f"-D{feature}={value}")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("excluded feature requested", result.stderr)

    def test_rejects_unsupported_abi(self):
        result = self.compile('#include "sysconfig.h"', "--target=i386-linux-gnu", "-ffreestanding")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported ABI", result.stderr)


if __name__ == "__main__":
    unittest.main()
