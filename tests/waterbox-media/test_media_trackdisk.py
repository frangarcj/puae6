"""T57: the extended ADF and the FDI in the BizHawk/Waterbox core, proved with the real sandbox (WB03).

The core is given the file as a disk, like an ADF (the managed constructor only caps its size; the guest's bridge decides the format and says why not), the guest of tests/media/trackdisk.py
reads cylinder 0 on both heads by disk DMA, and the checks are the ones of the native tests (tests/media/test_trackdisk.py, test_fdi.py) applied to what the sandbox's Chip RAM holds. A snapshot
of the sandbox (the core's state) taken while an FDI with weak pulses is being read, in the middle of a revolution, comes back in the same sandbox and in a new one, and the units that follow
are the same ones.

    python3 -m unittest discover -s tests/waterbox-media -p "test_media_trackdisk.py"
"""
import unittest

from support import AVAILABLE, SKIP, Sessions, builder, disk, sha  # noqa: F401
from tests.media import fdi, trackdisk as td

RUN = td.RUN_UNITS


class TrackDiskSandboxTests(Sessions):
    FIRMWARE = {"trackdisk": lambda: td.rom_source()}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        adf = disk.make_adf(0x72)
        cls.disk_adf = adf
        dos = [("dos", td.dos_track(adf, t)) for t in range(160)]
        cls.raw = td.raw_track(0x2B)
        sectors = [w for sector in disk.encode_track(adf, 0) for w in sector]
        widths, positions = fdi.pulses_from_mfm(sectors + [0xAAAA] * 160)
        images = {
            "ext2": td.ext2_image([("raw", cls.raw, len(cls.raw) * 16)] + dos[1:]),
            "ext1": td.ext1_image([("raw", td.SYNC, bytes((i * 3 + 1) & 0xFF for i in range(9000)))] + dos[1:]),
            "amiga": fdi.fdi_image([("amiga", td.dos_track(adf, 0), 0), ("amiga", td.dos_track(adf, 1), 0)]),
            "weak": fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths, (120, 60, set(range(0, len(widths), 3))))), ("amiga", td.dos_track(adf, 1), 0)]),
        }
        cls.paths = {}
        for name, image in images.items():
            cls.paths[name] = cls.media / f"{name}.img"
            cls.paths[name].write_bytes(image)
        cls.files_before = {**cls.files_before, **{str(p.relative_to(cls.media)): (p.stat().st_size, p.stat().st_mtime_ns, sha(p.read_bytes())) for p in cls.paths.values()}}

    def core_with(self, name, image):
        return f"core {name} adf={self.paths[image]}"

    def captured(self, out, name):
        peeks = self.peeks(out, name)
        marks = [int.from_bytes(peeks[0][2 * i:2 * i + 2], "big") == td.MARK_DONE for i in range(len(td.MARKERS))]
        words = lambda blob: [int.from_bytes(blob[i:i + 2], "big") for i in range(0, len(blob), 2)]
        return marks, words(peeks[1]), words(peeks[2])

    def read_session(self, image):
        return self.session([self.core_with("A", image), f"run A {RUN}", f"peek A Chip_RAM {td.MARK_BASE:x} {2 * len(td.MARKERS)}",
                             f"peek A Chip_RAM {td.CAPTURE_BASE['raw']:x} {2 * td.CAPTURE_WORDS}", f"peek A Chip_RAM {td.CAPTURE_BASE['dos']:x} {2 * td.CAPTURE_WORDS}"], firmware="trackdisk")

    def sectors(self, words, track):
        good = {}
        for found in disk.find_sectors(words):
            if found.header_ok and found.data_ok and found.fmt == 0xFF and found.track == track and found.sector < disk.SECTORS:
                good[found.sector] = found.payload
        self.assertGreaterEqual(len(good), 10, sorted(good))
        for sector, payload in good.items():
            self.assertEqual(payload, disk.adf_sector(self.disk_adf, track, sector))

    def test_the_guest_reads_a_raw_track_word_for_word_and_an_amigados_track_of_the_same_extended_adf(self):
        marks, raw, dos = self.captured(self.read_session("ext2"), "A")
        self.assertTrue(all(marks), marks)
        n, start = len(self.raw), td.RAW_SYNC_AT + 1
        model = [self.raw[(start + i) % n] for i in range(len(raw))]
        self.assertEqual(model[:-1], raw[:-1])
        self.assertIn(raw[-1], (model[-1], 0), "the last word of a transfer is not always written (the engine's FIFO, as in the native run)")
        self.sectors(dos, 1)

    def test_a_version_1_extended_adf_and_the_fdi_tracks_give_their_sectors(self):
        marks, raw, dos = self.captured(self.read_session("ext1"), "A")
        self.assertTrue(all(marks), marks)
        self.assertEqual([int.from_bytes(bytes((i * 3 + 1) & 0xFF for i in range(9000))[j:j + 2], "big") for j in range(0, 8000, 2)], raw[:4000])
        self.sectors(dos, 1)
        for image in ("amiga", "weak"):
            with self.subTest(image):
                marks, raw, dos = self.captured(self.read_session(image), "A")
                self.assertTrue(all(marks), marks)
                self.sectors(dos, 1)
        marks, raw, dos = self.captured(self.read_session("amiga"), "A")
        self.sectors(raw, 0)

    def test_a_snapshot_in_the_middle_of_a_revolution_of_an_fdi_with_weak_pulses_comes_back_in_the_same_sandbox_and_in_a_new_one(self):
        out = self.session([self.core_with("A", "weak"), "run A 14", "save A mid", "run A 30", "digest A first", "load A mid", "run A 30", "digest A again",
                            self.core_with("B", "weak"), "run B 3", "load B mid", "run B 30", "digest B other"], firmware="trackdisk")
        digests = self.digests(out, "A")
        self.assertEqual(digests["first"], digests["again"], "the same sandbox goes on from the snapshot as it did the first time")
        self.assertEqual(digests["first"], self.digests(out, "B")["other"], "a new sandbox does too")

    def test_a_state_of_one_disk_is_not_taken_by_a_machine_with_another(self):
        out = self.session([self.core_with("A", "weak"), "run A 10", "save A s", self.core_with("B", "amiga"), "run B 3", "load B s", "run B 3"], firmware="trackdisk", expect=0)
        self.assertTrue(self.rejections(out, "B"), "the other disk is another medium: the state is refused with its reason")


if __name__ == "__main__":
    unittest.main()
