"""T84e2: a 68000 guest drives the portable CD through Gayle ATAPI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.media import ide_cd_rom


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class IdeCdTests(unittest.TestCase):
    def test_guest_identifies_reads_and_plays_cd_while_four_hdfs_stay_unchanged(self):
        exe = build_probe(ROOT / "build/link-audit", False, "p6_media_probe")
        with tempfile.TemporaryDirectory(prefix="p6-ide-cd-") as directory:
            rom = Path(directory) / "ide-cd.rom"
            rom.write_bytes(ide_cd_rom.rom())
            result = subprocess.run(
                [str(exe), str(rom), "--ide-cd"],
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ide cd ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
