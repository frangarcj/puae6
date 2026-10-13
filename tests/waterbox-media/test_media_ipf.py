"""T58: the IPF in the BizHawk/Waterbox core, proved with the real sandbox (WB03).

The core is given the file as a disk, like an ADF (the managed constructor only caps its size; the guest's bridge opens the IPF with the decoder of MAME, decodes every track and says why not),
the guest of tests/media/trackdisk.py reads cylinder 0 on both heads by disk DMA, and the checks are the ones of the native tests (tests/media/test_ipf_engine.py) applied to what the sandbox's
Chip RAM holds. A snapshot of the sandbox (the core's state) taken while an IPF with weak cells is being read, in the middle of a revolution, comes back in the same sandbox and in a new one, and the
units that follow are the same ones (WB03: save, advance, restore, repeat).

    python3 -m unittest discover -s tests/waterbox-media -p "test_media_ipf.py"
"""
import unittest

from support import AVAILABLE, SKIP, Sessions, builder, disk, sha  # noqa: F401
from tests.media import ipf, trackdisk as td

RUN = td.RUN_UNITS


class IpfSandboxTests(Sessions):
    FIRMWARE = {"trackdisk": lambda: td.rom_source()}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        adf = disk.make_adf(0x73)
        cls.disk_adf = adf
        both = lambda **kw: ipf.build([ipf.Track(0, 0, ipf.dos_blocks(adf, 0, **kw)), ipf.Track(0, 1, ipf.dos_blocks(adf, 1, **kw))])
        images = {"dos": both(), "weak": both(weak_bytes=40), "timed": ipf.build([ipf.Track(0, 0, ipf.dos_blocks(adf, 0), type=3), ipf.Track(0, 1, ipf.dos_blocks(adf, 1), type=3, index_cells=40001)])}
        cls.paths = {}
        for name, image in images.items():
            cls.paths[name] = cls.media / f"{name}.ipf"
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

    def test_the_guest_reads_the_sectors_of_an_ipf_with_and_without_weak_areas_and_timing_zones(self):
        for image in ("dos", "weak", "timed"):
            with self.subTest(image):
                marks, raw, dos = self.captured(self.read_session(image), "A")
                self.assertTrue(all(marks), marks)
                self.sectors(raw, 0)
                self.sectors(dos, 1)

    def test_a_snapshot_in_the_middle_of_a_revolution_of_an_ipf_with_weak_cells_comes_back_in_the_same_sandbox_and_in_a_new_one(self):
        out = self.session([self.core_with("A", "weak"), "run A 14", "save A mid", "run A 30", "digest A first", "load A mid", "run A 30", "digest A again",
                            self.core_with("B", "weak"), "run B 3", "load B mid", "run B 30", "digest B other"], firmware="trackdisk")
        digests = self.digests(out, "A")
        self.assertEqual(digests["first"], digests["again"], "the same sandbox goes on from the snapshot as it did the first time")
        self.assertEqual(digests["first"], self.digests(out, "B")["other"], "a new sandbox does too")

    def test_two_sandboxes_with_the_same_ipf_read_the_same_bits(self):
        out = self.session([self.core_with("A", "weak"), "run A 40", "digest A a", self.core_with("B", "weak"), "run B 40", "digest B b"], firmware="trackdisk")
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    def test_a_state_of_one_disk_is_not_taken_by_a_machine_with_another(self):
        out = self.session([self.core_with("A", "weak"), "run A 10", "save A s", self.core_with("B", "dos"), "run B 3", "load B s", "run B 3"], firmware="trackdisk", expect=0)
        self.assertTrue(self.rejections(out, "B"), "the other disk is another medium: the state is refused with its reason")


if __name__ == "__main__":
    unittest.main()
