"""MED01 for the extended ADF (T57a): what a guest reads from raw MFM tracks and from AmigaDOS tracks of the same image, through the real bridge and engine.

Disks and ROM are generated here (tests/media/trackdisk.py); the expected words come from the format descriptions, not from engine output. The micro-ROM reads cylinder 0,
head 0 (a raw track with one $4489 sync in the middle, 6250 words, shorter than a standard revolution) and head 1 (eleven AmigaDOS sectors) of DF0 with WORDSYNC DMA.
Set P6_TEST_SANITIZERS=1 to also run one PAL scenario under ASan/UBSan.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe, builder
from tests.media import trackdisk as td
from tests.scenarios.disk import disk

PLAIN_LABEL = 0x51
RAW_SEED = 0xA11CE


def run_scenario(exe, directory, image, standard="pal", seed=1, other=None):
    directory = Path(directory)
    rom = directory / "trackdisk.rom"
    rom.write_bytes(builder.build(td.rom_source()))
    first = directory / "first.img"
    first.write_bytes(image)
    second = directory / "second.adf"
    second.write_bytes(other if other is not None else disk.make_adf(0x52))
    dump = directory / f"chip-{standard}-{seed}.bin"
    result = subprocess.run([str(exe), str(rom), standard, str(seed), str(td.RUN_UNITS), str(td.RUN_UNITS + 50), str(first), str(second), str(dump)],
                            capture_output=True, text=True, timeout=600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    return result, (dump.read_bytes() if dump.exists() else b"")


def sector_payload_check(case, words, adf, track):
    """At least 10 of the 11 sectors the capture holds verify and are the sectors of `track` of `adf`, word for word."""
    sectors = disk.decode_capture(words)
    case.assertGreaterEqual(len(sectors), 10)
    for (found_track, sector), payload in sectors.items():
        case.assertEqual(found_track, track)
        case.assertEqual(payload, disk.adf_sector(adf, track, sector))


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ExtendedAdfReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        cls.adf = disk.make_adf(PLAIN_LABEL)
        cls.raw = td.raw_track(RAW_SEED)
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-trackdisk-")
        # Cylinder 0 head 0 raw, head 1 (track 1) AmigaDOS from a plain ADF; the rest of the 160 tracks are AmigaDOS tracks of the ADF as well.
        tracks = [("raw", cls.raw, len(cls.raw) * 16)] + [("dos", td.dos_track(cls.adf, t)) for t in range(1, 160)]
        cls.ext2 = td.ext2_image(tracks)
        # UAE--ADF: the raw track is a sync word and the data after it, which the drive reads from its start.
        cls.raw1_data = bytes((i * 7 + 3) & 0xFF for i in range(10000))
        cls.ext1 = td.ext1_image([("raw", td.SYNC, cls.raw1_data)] + [("dos", td.dos_track(cls.adf, t)) for t in range(1, 160)])
        cls.runs = {}
        for name, image in (("ext2", cls.ext2), ("ext1", cls.ext1)):
            cls.runs[name] = run_scenario(cls.exe, cls.temp.name, image)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_guest_reads_a_raw_track_word_for_word_from_the_sync_on_and_wraps_at_the_index(self):
        result, ram = self.runs["ext2"]
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        words = td.capture(ram, "raw")
        n = len(self.raw)
        start = td.RAW_SYNC_AT + 1                       # the DMA begins after the word that matched
        model = [self.raw[(start + i) % n] for i in range(len(words))]
        # The first words up to the end of the track are exact; the track is shorter than the read, so the read goes through the index and on to the sync again.
        before_index = n - start
        self.assertEqual(words[:before_index], model[:before_index])
        self.assertEqual(words[:64], [self.raw[start + i] for i in range(64)])
        self.assertEqual(words[before_index:], model[before_index:])
        self.assertIn(td.SYNC, words[before_index:])      # the sync word is data this time: the read did not stop at it

    def test_the_other_head_of_the_same_image_is_an_amigados_track(self):
        result, ram = self.runs["ext2"]
        sector_payload_check(self, td.capture(ram, "dos"), self.adf, 1)

    def test_a_uae_adf_version_1_raw_track_is_read_from_its_start(self):
        result, ram = self.runs["ext1"]
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertTrue(all(td.markers(ram).values()), td.markers(ram))
        words = td.capture(ram, "raw")
        data = [int.from_bytes(self.raw1_data[i:i + 2], "big") for i in range(0, len(self.raw1_data), 2)]
        # The track is the sync word and then the data; the drive starts at the first word (the sync) and the DMA begins after it.
        self.assertEqual(words[:len(data)], data)
        sector_payload_check(self, td.capture(ram, "dos"), self.adf, 1)

    def test_the_images_are_never_written_and_two_processes_replay_the_run(self):
        for name in ("ext2", "ext1"):
            result, _ = self.runs[name]
            self.assertIn("adf A buffer_unchanged=1 blob_equal=1", result.stdout)
        with tempfile.TemporaryDirectory(prefix="p6-trackdisk-replay-") as directory:
            again, ram_again = run_scenario(self.exe, directory, self.ext2)
        first, ram = self.runs["ext2"]
        self.assertEqual(again.stdout, first.stdout)
        self.assertEqual(ram_again, ram)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenario_is_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_med01_probe")
        for image in (self.ext2, self.ext1):
            with tempfile.TemporaryDirectory(prefix="p6-trackdisk-san-") as directory:
                result, ram = run_scenario(exe, directory, image)
            self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
            self.assertNotIn("runtime error", result.stderr)
            self.assertTrue(all(td.markers(ram).values()))


if __name__ == "__main__":
    unittest.main()
