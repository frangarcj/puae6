"""MED01 for the FDI format (T57b): what a guest reads from the tracks of an FDI, through the real bridge and engine (fdi2raw.cpp of WinUAE, patches 0332 to 0334).

The FDI files are written here (tests/media/fdi.py, from the layout fdi2raw implements). The micro-ROM of tests/media/trackdisk.py reads cylinder 0, head 0 and head 1 of DF0 with WORDSYNC DMA;
the expected sectors are the ones of the plain ADF the tracks were made from, and a low-level track is made from the MFM of that ADF's own track 0, so what the guest reads has gone through the
whole decoder (pulses to cells to bits): at least 10 of the 11 sectors have to verify (checksums of header and data) and be those of the ADF.
Set P6_TEST_SANITIZERS=1 to also run the scenarios under ASan/UBSan.
"""
import os
from pathlib import Path
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.media import fdi
from tests.media import trackdisk as td
from tests.media.test_trackdisk import run_scenario
from tests.scenarios.disk import disk

LABEL = 0x61
GAP_WORDS = 160                  # words of gap (0xAAAA, data zeros) after the sectors of the low-level track: the weak pulses are in it


def check_sectors(case, words, adf, track, strict=True):
    """At least 10 of the 11 sectors in the capture verify (header and data checksums) and are the sectors of `track` of `adf`. With `strict` the sectors-until-gap field has to be the usual one
    (11 - sector); a track that starts at another sector numbers them from where it starts."""
    good = {}
    for found in disk.find_sectors(words):
        if found.header_ok and found.data_ok and found.fmt == 0xFF and found.track == track and found.sector < disk.SECTORS and (not strict or found.gap == disk.SECTORS - found.sector):
            good[found.sector] = found.payload
    case.assertGreaterEqual(len(good), 10, sorted(good))
    for sector, payload in good.items():
        case.assertEqual(payload, disk.adf_sector(adf, track, sector), f"sector {sector}")


def track0_words(adf):
    return [w for sector in disk.encode_track(adf, 0) for w in sector]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class FdiReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        cls.adf = disk.make_adf(LABEL)
        amiga = lambda track, first=0: ("amiga", td.dos_track(cls.adf, track), first)
        cls.images = {}
        cls.images["normal"] = fdi.fdi_image([amiga(0), amiga(1)])
        cls.images["crc"] = fdi.fdi_image([amiga(0), amiga(1)], version=(2, 1))
        cls.images["rotated"] = fdi.fdi_image([amiga(0, first=7), amiga(1, first=3)])
        sectors = track0_words(cls.adf)
        # A low-level track: the pulses of the 11 AmigaDOS sectors of track 0, then a gap. Head 1 is a plain Amiga track.
        words = sectors + [0xAAAA] * GAP_WORDS
        widths, positions = fdi.pulses_from_mfm(words)
        cls.images["lowlevel"] = fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths)), amiga(1)])
        # The same with the pulses of the gap at half stability (a weak bit every other revolution): the sectors do not change, the gap does.
        gap_start = len(sectors) * 16
        weak = {k for k, pos in enumerate(positions) if pos >= gap_start}
        assert len(weak) >= 10
        cls.images["weak"] = fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths, (120, 60, weak))), amiga(1)])
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-fdi-")
        cls.runs = {name: run_scenario(cls.exe, cls.temp.name, image) for name, image in cls.images.items()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def assert_reads(self, name, strict=True):
        result, ram = self.runs[name]
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        check_sectors(self, td.capture(ram, "raw"), self.adf, 0, strict)
        check_sectors(self, td.capture(ram, "dos"), self.adf, 1, strict)

    def test_an_fdi_of_amiga_tracks_gives_the_sectors_of_its_disk(self):
        self.assert_reads("normal")

    def test_the_checksum_of_a_version_2_1_header_is_the_crc_32_of_its_first_508_bytes(self):
        self.assert_reads("crc")

    def test_an_amiga_track_that_starts_at_another_sector_has_the_same_sectors(self):
        self.assert_reads("rotated", strict=False)

    def test_a_low_level_track_of_pulses_is_decoded_to_the_sectors_it_was_made_from(self):
        self.assert_reads("lowlevel")

    def test_a_low_level_track_with_weak_pulses_in_its_gap_still_gives_its_sectors(self):
        self.assert_reads("weak")

    def test_the_images_are_never_written_and_two_processes_replay_each_run(self):
        for name in ("normal", "lowlevel", "weak"):
            result, ram = self.runs[name]
            self.assertIn("adf A buffer_unchanged=1 blob_equal=1", result.stdout)
            with tempfile.TemporaryDirectory(prefix="p6-fdi-replay-") as directory:
                again, ram_again = run_scenario(self.exe, directory, self.images[name])
            self.assertEqual(again.stdout, result.stdout, name)
            self.assertEqual(ram_again, ram, name)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenarios_are_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_med01_probe")
        for name in ("normal", "lowlevel", "weak"):
            with tempfile.TemporaryDirectory(prefix="p6-fdi-san-") as directory:
                result, ram = run_scenario(exe, directory, self.images[name])
            self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
            self.assertNotIn("runtime error", result.stderr)
            self.assertTrue(all(td.markers(ram).values()))


if __name__ == "__main__":
    unittest.main()
