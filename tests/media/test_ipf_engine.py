"""MED01 for the IPF format (T58b): what a guest reads from the tracks of an IPF, through the real bridge and engine (the decoder of MAME, third_party/mame-ipf, and patch 0340).

The IPF files are written here (tests/media/ipf.py) from the content the tracks are meant to have: the eleven sectors of a plain ADF as unencoded cells with a gap of zeros, the same with
a weak area after every sector, with the index in the middle of the track, with the timing zones of a track type, and a raw track whose cells are a known stream. The micro-ROM of
tests/media/trackdisk.py reads cylinder 0, head 0 and head 1 of DF0 with WORDSYNC DMA; the expected sectors are the ones of the ADF (at least 10 of the 11 have to verify, header and data),
and the raw track is compared word for word with the cells the oracle of tests/media/ipf.py gives for it.
Set P6_TEST_SANITIZERS=1 to also run the scenarios under ASan/UBSan.
"""
import os
from pathlib import Path
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.media import ipf
from tests.media import trackdisk as td
from tests.media.test_fdi import check_sectors
from tests.media.test_trackdisk import run_scenario
from tests.scenarios.disk import disk

LABEL = 0x71
RAW_SEED = 0xA11CE


def dos_image(adf, **kwargs):
    kind = kwargs.pop("type", 2)
    index = kwargs.pop("index_cells", 0)
    return ipf.build([ipf.Track(0, 0, ipf.dos_blocks(adf, 0, **kwargs), type=kind, index_cells=index), ipf.Track(0, 1, ipf.dos_blocks(adf, 1, **kwargs), type=kind, index_cells=index)])


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class IpfReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        cls.adf = disk.make_adf(LABEL)
        cls.raw = td.raw_track(RAW_SEED)
        raw_bytes = b"".join(w.to_bytes(2, "big") for w in cls.raw)
        cls.raw_track = ipf.Track(0, 0, [ipf.Block([("sync", raw_bytes)])])
        cls.images = {
            "dos": dos_image(cls.adf),
            "rotated": dos_image(cls.adf, index_cells=41234),
            "timed": dos_image(cls.adf, type=3),
            "weak": dos_image(cls.adf, weak_bytes=20),
            "raw": ipf.build([cls.raw_track, ipf.Track(0, 1, ipf.dos_blocks(cls.adf, 1))]),
        }
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-ipf-")
        cls.runs = {name: run_scenario(cls.exe, cls.temp.name, image) for name, image in cls.images.items()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def assert_reads(self, name):
        result, ram = self.runs[name]
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        check_sectors(self, td.capture(ram, "raw"), self.adf, 0)
        check_sectors(self, td.capture(ram, "dos"), self.adf, 1)

    def test_an_ipf_of_amigados_sectors_gives_the_sectors_of_its_disk(self):
        self.assert_reads("dos")

    def test_the_index_in_the_middle_of_the_track_changes_where_the_read_starts_not_what_it_reads(self):
        self.assert_reads("rotated")

    def test_a_track_type_with_zones_of_other_speed_still_gives_its_sectors(self):
        self.assert_reads("timed")

    def test_weak_areas_between_the_sectors_do_not_disturb_them(self):
        self.assert_reads("weak")

    def test_the_guest_reads_a_raw_track_word_for_word_from_the_sync_on_and_wraps_at_the_index(self):
        result, ram = self.runs["raw"]
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        cells, _, _, _ = ipf.expected(self.raw_track)
        model_words = ipf.words(cells)                    # with the three cells of the write splice flipped, as the format has them
        words = td.capture(ram, "raw")
        n = len(model_words)
        start = td.RAW_SYNC_AT + 1                        # the DMA begins after the word that matched
        before_index = n - start
        self.assertEqual(words[:before_index], model_words[start:])
        self.assertEqual(words[:64], [model_words[start + i] for i in range(64)])
        # The track has a write splice (the cell where it was written first and last, here the index), and the drive skips a few cells there, 1 to 3 as WinUAE's floppy_random_bits do
        # (the draw comes from the engine's generator, which is seeded): the stream after the index is the track again from cell 1, 2 or 3, bit for bit, up to the sync word, which
        # the transfer finds again (WORDSYNC puts the words back in step with it); after the sync the words are the track's, word for word. The last word of a transfer is not always
        # written when the DSKBLK interrupt comes, so it is left out.
        def bits(ws):
            return "".join(format(w, "016b") for w in ws)
        after = words[before_index:-1]
        sync_at = td.RAW_SYNC_AT
        prefix = bits(after[:sync_at - 2])
        track_bits = bits(model_words)
        skipped = [k for k in (1, 2, 3) if prefix == (track_bits * 2)[k:k + len(prefix)]]
        self.assertEqual(len(skipped), 1, "the splice skips 1 to 3 cells and the read goes on as the track")
        self.assertEqual(after[sync_at:], model_words[sync_at + 1:sync_at + 1 + len(after) - sync_at])
        self.assertNotEqual(model_words[0], self.raw[0], "the splice changed the first cells of the track, and the guest read them after the index")

    def test_the_images_are_never_written_and_two_processes_replay_each_run(self):
        for name in ("dos", "weak", "timed"):
            result, ram = self.runs[name]
            self.assertIn("adf A buffer_unchanged=1 blob_equal=1", result.stdout)
            with tempfile.TemporaryDirectory(prefix="p6-ipf-replay-") as directory:
                again, ram_again = run_scenario(self.exe, directory, self.images[name])
            self.assertEqual(again.stdout, result.stdout, name)
            self.assertEqual(ram_again, ram, name)

    def test_an_ipf_the_decoder_cannot_read_is_refused_with_its_reason(self):
        blob = bytearray(self.images["dos"])
        blob[40] ^= 0x01                                   # a byte of the INFO tag: its checksum is wrong
        with tempfile.TemporaryDirectory(prefix="p6-ipf-bad-") as directory:
            result, _ = run_scenario(self.exe, directory, bytes(blob))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum is wrong", result.stdout + result.stderr)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenarios_are_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_med01_probe")
        for name in ("dos", "weak", "raw"):
            with tempfile.TemporaryDirectory(prefix="p6-ipf-san-") as directory:
                result, ram = run_scenario(exe, directory, self.images[name])
            self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
            self.assertNotIn("runtime error", result.stderr)
            self.assertTrue(all(td.markers(ram).values()))


if __name__ == "__main__":
    unittest.main()
