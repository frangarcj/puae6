"""Opt-in: an IPF file given by its owner in P6_IPF_FILE (read in place, never copied or hashed into the repository).

No IPF of a freely redistributable disk was found to download (the IPF the preservation project publishes are commercial games), so the writer of tests/media/ipf.py is the fixture of
the suite and this test is the bridge to the real thing: the file goes through the same validator, the same decoder and the same engine, and the micro-ROM of tests/media/trackdisk.py
reads cylinder 0 on both heads. What it asserts is that the core takes the file and the guest finishes both reads; with P6_IPF_BOOT_TRACK0_SECTORS=1 (a disk whose first track is a
standard AmigaDOS one) it also asserts that most sectors of track 0 verify. A file the core refuses fails with its reason, which is what the owner needs to know. NOT RUN: no IPF has been
authorized for the project yet.
"""
import os
from pathlib import Path
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.media import trackdisk as td
from tests.media.test_trackdisk import run_scenario
from tests.scenarios.disk import disk

FILE = os.environ.get("P6_IPF_FILE")


@unittest.skipUnless(TOOLS and FILE and Path(FILE).is_file(), "opt-in: P6_IPF_FILE names an IPF file")
class RealIpfTests(unittest.TestCase):
    def test_the_core_reads_a_real_ipf(self):
        exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        image = Path(FILE).read_bytes()
        with tempfile.TemporaryDirectory(prefix="p6-ipf-real-") as directory:
            result, ram = run_scenario(exe, directory, image)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-600:])
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        if os.environ.get("P6_IPF_BOOT_TRACK0_SECTORS") == "1":
            sectors = [f for f in disk.find_sectors(td.capture(ram, "raw")) if f.header_ok and f.data_ok]
            self.assertGreaterEqual(len(sectors), 8)


if __name__ == "__main__":
    unittest.main()
