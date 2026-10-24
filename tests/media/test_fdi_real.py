"""Opt-in: an FDI file made by a real tool (Disk2FDI and the like), given by the owner in P6_FDI_FILE (read in place, never copied or hashed into the repository).

No FDI of a freely redistributable disk was found to download (the free Amiga releases are ADF and DMS), so the writer of tests/media/fdi.py is the fixture of the suite and this test is the bridge
to the real thing: the image goes through the same validator and the same engine, and the micro-ROM of tests/media/trackdisk.py reads cylinder 0 on both heads. What it asserts is that the core
takes the file and the guest finishes both reads; with a file of a disk it recognises (P6_FDI_BOOT_TRACK0_SECTORS=1, a standard AmigaDOS first track) it also asserts that most sectors of
track 0 verify. A file the validator refuses fails with its reason, which is the information the owner needs (an unsupported track type, for one).
"""
import os
from pathlib import Path
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.media import trackdisk as td
from tests.media.test_trackdisk import run_scenario
from tests.scenarios.disk import disk

FILE = os.environ.get("P6_FDI_FILE")


@unittest.skipUnless(TOOLS and FILE and Path(FILE).is_file(), "opt-in: P6_FDI_FILE names an FDI file")
class RealFdiTests(unittest.TestCase):
    def test_the_core_reads_a_real_fdi(self):
        exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        image = Path(FILE).read_bytes()
        with tempfile.TemporaryDirectory(prefix="p6-fdi-real-") as directory:
            result, ram = run_scenario(exe, directory, image)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-600:])
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        if os.environ.get("P6_FDI_BOOT_TRACK0_SECTORS") == "1":
            sectors = [f for f in disk.find_sectors(td.capture(ram, "raw")) if f.header_ok and f.data_ok]
            self.assertGreaterEqual(len(sectors), 8)


if __name__ == "__main__":
    unittest.main()
