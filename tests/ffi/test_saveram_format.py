"""T63b: the format of the SaveRam of the core with floppy disks and hard files (integration/bizhawk/core/Puae6/SaveRam.cs), on in-memory images, without a guest.

The checks are in tests/ffi/SaveRamFormat.cs and print one `ok ...` line each; this test builds the host with the core and runs them. A floppy-only SaveRam is the version 1 of T28,
byte for byte; a hard file brings version 2 with the 4 KiB chunks that differ, the short last chunk included; and every cut, every flipped byte, other images and the fields a checksum
cannot protect are refused with a SaveRamRejectedException.

    python3 -m unittest discover -s tests/ffi -p "test_saveram_format.py"
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class SaveRamFormatTests(unittest.TestCase):
    def test_the_format_of_floppies_and_hard_files(self):
        result = build_waterbox.run_ffi("saveramformat")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("ok ")]
        self.assertGreaterEqual(len(lines), 25, result.stdout)
        self.assertIn("saveramformat complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
