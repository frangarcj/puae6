"""p6_memory_regions and P6_FRAME_DRIVE_LIGHT against the real engine (T20a).

The probe (bridge_memory_probe.cpp) links the whole A500 engine, so the build is shared with the link-audit tree. It runs the
chipcell micro-ROM (a marker in Chip RAM and a colour word the guest copies into COLOR00) and the floppy guest of T12c.
Set P6_TEST_SANITIZERS=1 to also run it under ASan/UBSan (the native-debug preset tree).
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
spec = importlib.util.spec_from_file_location("disk_fixture", ROOT / "tests/scenarios/disk/disk.py")
disk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disk)

from tests.host.test_bridge_lifecycle import build_probe  # noqa: E402


def run_probe(exe):
    with tempfile.TemporaryDirectory(prefix="p6-memory-") as directory:
        directory = Path(directory)
        (directory / "chipcell.rom").write_bytes(builder.build((ROOT / "tests/waterbox-smoke/chipcell.s").read_text(encoding="utf-8")))
        (directory / "disk.rom").write_bytes(builder.build(disk.rom_source()))
        (directory / "a.adf").write_bytes(disk.make_adf(0x41))
        return subprocess.run([str(exe), *(str(directory / name) for name in ("chipcell.rom", "disk.rom", "a.adf"))],
                              capture_output=True, text=True, timeout=600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BridgeMemoryTests(unittest.TestCase):
    def test_regions_are_the_machine_memory_and_the_drive_light_follows_the_led(self):
        result = run_probe(build_probe(ROOT / "build/link-audit", False, "p6_memory_probe"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bridge-memory-ok", result.stdout)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenario_is_clean_under_asan_and_ubsan(self):
        result = run_probe(build_probe(ROOT / "build/native-debug", True, "p6_memory_probe"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("runtime error", result.stderr)


if __name__ == "__main__":
    unittest.main()
