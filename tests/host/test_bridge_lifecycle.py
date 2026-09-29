"""p6_init / p6_load / p6_unload / p6_shutdown against the real engine with the micro-ROM.

The probe (bridge_lifecycle_probe.cpp) links the whole A500 engine, so the build is shared with the
link-audit tree the codegen tests use. It stops before running any unit: advancing is T07c.
Set P6_TEST_SANITIZERS=1 to also run it under ASan/UBSan (the native-debug preset tree, P6_SANITIZERS=ON).
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = all(shutil.which(t) for t in ("cmake", "ninja", "clang", "clang++"))

spec = importlib.util.spec_from_file_location("microrom_builder", ROOT / "tests/fixtures/microrom/build_rom.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def build_probe(directory, sanitizers):
    subprocess.run(["cmake", "-S", str(ROOT), "-B", str(directory), "-G", "Ninja",
                    "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                    "-DCMAKE_BUILD_TYPE=Debug", f"-DP6_SANITIZERS={'ON' if sanitizers else 'OFF'}"],
                   check=True, capture_output=True, text=True, timeout=120)
    subprocess.run(["cmake", "--build", str(directory), "--target", "p6_lifecycle_probe"],
                   check=True, capture_output=True, text=True, timeout=1500)
    return directory / "p6_lifecycle_probe"


def run_probe(exe):
    source = (ROOT / "tests/fixtures/microrom/boot.s").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="p6-lifecycle-") as directory:
        rom = Path(directory) / "boot.rom"
        rom.write_bytes(builder.build(source))
        return subprocess.run([str(exe), str(rom)], capture_output=True, text=True, timeout=300,
                              env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BridgeLifecycleTests(unittest.TestCase):
    def test_init_load_apply_unload_and_reload_on_the_real_engine(self):
        result = run_probe(build_probe(ROOT / "build/link-audit", False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bridge lifecycle ok", result.stdout)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenario_is_clean_under_asan_and_ubsan(self):
        result = run_probe(build_probe(ROOT / "build/native-debug", True))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("runtime error", result.stderr)


if __name__ == "__main__":
    unittest.main()
