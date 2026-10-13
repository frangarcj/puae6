"""MED01 for the IPF reader (T58a): the free decoder of MAME behind ports/portable/media/ipf_track.h, built with ASan and UBSan and no engine (tests/media/ipf_probe.cpp).

The images are written by tests/media/ipf.py from the intended content of a track, and the cells, times and weak marks the reader must produce come from the same content by an
oracle that encodes MFM itself. What is looked at: the standard tracks, the gaps, the index rotation and the write splice, the timing zones of the track types, the weak bytes and the
noise tracks (they differ from one revolution to the next and are the same in every process), the raw encoder, and which files the reader opens at all. (The engine reading a track and a
state taken in the middle of one are tests/media/test_ipf_engine.py and tests/state/test_det02_ipf.py.)
Run: python3 -m unittest tests.media.test_ipf
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.media import ipf

ROOT = Path(__file__).resolve().parents[2]
COMPILER = shutil.which("clang++")
SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
ENV = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"}
SYNC = bytes([0x44, 0x89, 0x44, 0x89])


def build_probe(directory):
    exe = Path(directory) / "ipf_probe"
    subprocess.run([COMPILER, "-std=c++17", "-g", "-O1", *SANITIZE, "-Wall", "-Wextra", f"-I{ROOT / 'ports/portable/media'}", str(ROOT / "tests/media/ipf_probe.cpp"),
                    str(ROOT / "ports/portable/media/ipf_track.cpp"), "-o", str(exe)], check=True, capture_output=True, text=True)
    return exe


class Probe:
    """One build of the probe in a temporary directory, shared by the tests of a class."""

    @classmethod
    def setUpClass(cls):
        if not COMPILER:
            raise unittest.SkipTest("clang++ is not available")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-ipf-")
        cls.exe = build_probe(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decode(self, blob, revolutions=1):
        path = Path(self.tmp.name) / "image.ipf"
        path.write_bytes(blob)
        result = subprocess.run([str(self.exe), str(path), str(revolutions)], capture_output=True, text=True, env=ENV, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        lines = result.stdout.splitlines()
        tracks = {}
        for line in lines[1:]:
            f = line.split()
            if f[4] in ("failed", "absent"):
                tracks[(int(f[1]), int(f[3]))] = f[4]
                continue
            w, t = line.split(" words ")[1].split(" timing ")
            tracks[(int(f[1]), int(f[3]))] = {"cells": int(f[5]), "splice": int(f[7]), "weak": f[9] == "1",
                                              "words": [int(w[i:i + 4], 16) for i in range(0, len(w), 4)], "timing": [int(t[i:i + 4], 16) for i in range(0, len(t), 4)]}
        return lines[0], tracks

    def assert_matches(self, track, blob=None, revolutions=1):
        """The decoded track equals the oracle's: every cell that is not weak, the timing, the splice, the length."""
        verdict, decoded = self.decode(blob or ipf.build([track]), revolutions)
        self.assertEqual(verdict, "ok")
        cells, times, weak, splice = ipf.expected(track)
        got = decoded[(track.cylinder * 2 + track.head, 0)]
        self.assertEqual(got["cells"], len(cells))
        self.assertEqual(got["splice"], splice)
        self.assertEqual(got["weak"], weak)
        self.assertEqual(got["timing"], ipf.timing(times))
        for i, c in enumerate(cells):
            if c is not None:
                self.assertEqual((got["words"][i >> 4] >> (15 - (i & 15))) & 1, c, f"cell {i}")
        self.assertEqual(len(got["words"]), (len(cells) + 15) // 16)
        # past the end of the track the last word is zero
        if len(cells) % 16:
            self.assertEqual(got["words"][-1] & (0xFFFF >> (len(cells) % 16)), 0)
        return decoded


def sector_blocks(count=11, gap=200, pattern=0x00):
    return [ipf.Block([("sync", SYNC), ("mfm", bytes((i * 17 + k) & 0xFF for k in range(40)))], gap_cells=gap, pattern=pattern) for i in range(count)]


class DecodeTests(Probe, unittest.TestCase):
    def test_a_track_of_sectors_with_gaps_is_the_mfm_of_its_content(self):
        self.assert_matches(ipf.Track(0, 0, sector_blocks()))

    def test_the_gap_pattern_and_odd_and_short_gaps(self):
        blocks = [ipf.Block([("sync", SYNC), ("mfm", b"\xA5\x5A\xFF\x00")], gap_cells=g, pattern=p) for g, p in ((201, 0x00), (8, 0x00), (7, 0xAA), (64, 0xFF), (130, 0x12))]
        self.assert_matches(ipf.Track(1, 1, blocks))

    def test_the_index_turns_the_track_and_the_splice_goes_with_it(self):
        for index_cells in (1, 777, 1999):
            with self.subTest(index_cells=index_cells):
                self.assert_matches(ipf.Track(2, 0, sector_blocks(), index_cells=index_cells))

    def test_forward_and_backward_gap_descriptions(self):
        for gap_type in (1, 2):
            with self.subTest(gap_type=gap_type):
                blocks = [ipf.Block([("sync", SYNC), ("mfm", b"\x12\x34\x56\x78")], gap_cells=g, gap_type=gap_type, gap_pattern=bytes([0x4E, 0x00])) for g in (96, 160)]
                self.assert_matches(ipf.Track(3, 0, blocks))

    def test_the_raw_encoder_writes_the_cells_it_is_given(self):
        raw = bytes([0x92, 0x49, 0x24, 0x92, 0xAA, 0xAA, 0x55, 0x55])
        blocks = [ipf.Block([("raw", raw)], gap_cells=96, pattern=0x00, encoder=2), ipf.Block([("raw", raw[::-1])], gap_cells=96, pattern=0x00, encoder=2)]
        self.assert_matches(ipf.Track(4, 1, blocks))

    def test_the_timing_zones_of_the_track_types(self):
        for kind, count in ((6, 3), (3, 7)):
            with self.subTest(type=kind):
                self.assert_matches(ipf.Track(5, 0, sector_blocks(count, gap=160), type=kind))
        blocks = sector_blocks(6, gap=120)
        blocks[0].speed_mask = 0b10110
        self.assert_matches(ipf.Track(5, 1, blocks, type=9))

    def test_nominal_tracks_have_a_timing_of_one_thousand(self):
        _, decoded = self.decode(ipf.build([ipf.Track(0, 0, sector_blocks())]))
        timing = decoded[(0, 0)]["timing"]
        self.assertEqual(set(timing), {1000})

    def test_a_track_not_in_the_image_is_not_decoded(self):
        _, decoded = self.decode(ipf.build([ipf.Track(0, 0, sector_blocks()), ipf.Track(7, 1, sector_blocks(3))]))
        self.assertEqual(sorted(decoded), [(0, 0), (15, 0)])

    def test_unknown_tags_are_skipped(self):
        track = ipf.Track(0, 0, sector_blocks(4))
        blob = ipf.build([track], extra_tags=[ipf.tag(b"CTEX", b"\x01\x02\x03\x04" * 5), ipf.tag(b"TRCK", b"")])
        self.assert_matches(track, blob)


class WeakTests(Probe, unittest.TestCase):
    def weak_track(self):
        blocks = [ipf.Block([("sync", SYNC), ("mfm", b"\xAA" * 8), ("weak", 6), ("mfm", b"\x55" * 4)], gap_cells=120, pattern=0x00),
                  ipf.Block([("sync", SYNC), ("weak", 3)], gap_cells=100, pattern=0x00)]
        return ipf.Track(6, 0, blocks)

    def test_the_cells_that_are_not_weak_are_those_of_the_content_in_every_revolution(self):
        track = self.weak_track()
        decoded = self.assert_matches(track, revolutions=3)
        self.assertTrue(all(decoded[(12, r)]["weak"] for r in range(3)))

    def test_weak_cells_read_differently_at_each_revolution_and_the_same_in_every_process(self):
        track = self.weak_track()
        blob = ipf.build([track])
        _, first = self.decode(blob, 4)
        _, again = self.decode(blob, 4)
        self.assertEqual(first, again, "a revolution of a track is the same bits in any process")
        revs = [first[(12, r)]["words"] for r in range(4)]
        for a in range(4):
            for b in range(a + 1, 4):
                self.assertNotEqual(revs[a], revs[b], f"revolutions {a} and {b} read the same weak cells")
        cells, *_ = ipf.expected(track)
        weak = [i for i, c in enumerate(cells) if c is None]
        ones = sum((revs[r][i >> 4] >> (15 - (i & 15))) & 1 for r in range(4) for i in weak)
        self.assertTrue(0.3 < ones / (4 * len(weak)) < 0.7, "a weak cell reads 0 or 1 with the same odds")

    def test_the_weak_cells_of_two_tracks_differ(self):
        _, decoded = self.decode(ipf.build([ipf.Track(6, 0, self.weak_track().blocks), ipf.Track(6, 1, self.weak_track().blocks)]))
        self.assertNotEqual(decoded[(12, 0)]["words"], decoded[(13, 0)]["words"])

    def test_a_noise_track_is_all_weak(self):
        track = ipf.Track(9, 0, [], type=1, noise_cells=50000)
        blob = ipf.build([track])
        _, decoded = self.decode(blob, 2)
        a, b = decoded[(18, 0)], decoded[(18, 1)]
        self.assertTrue(a["weak"] and a["cells"] == 50000 and a["splice"] < 50000)
        self.assertNotEqual(a["words"], b["words"])
        ones = sum(bin(w).count("1") for w in a["words"])
        self.assertTrue(0.45 < ones / 50000 < 0.55)
        self.assertEqual(set(a["timing"]), {1000})


class RefusalTests(Probe, unittest.TestCase):
    def good(self):
        return ipf.build([ipf.Track(0, 0, sector_blocks(3))])

    def verdict(self, blob):
        return self.decode(blob)[0]

    def test_the_good_image_is_opened(self):
        self.assertEqual(self.verdict(self.good()), "ok")
        self.assertTrue(ipf.accepts(self.good()))

    def test_every_truncation_of_a_small_image_is_refused(self):
        blob = ipf.build([ipf.Track(0, 0, sector_blocks(2, gap=40))])
        for cut in list(range(0, 200, 7)) + [len(blob) - 1, len(blob) - 30, len(blob) - 200]:
            with self.subTest(cut=cut):
                self.assertTrue(self.verdict(blob[:cut]).startswith("refused"))
                self.assertFalse(ipf.accepts(blob[:cut]))

    def test_a_flipped_byte_in_any_tag_breaks_its_checksum(self):
        blob = self.good()
        for at in (0, 5, 9, 12, 20, 40, 80, 120, 130, 160, 180, 200, 260, len(blob) - 1):
            with self.subTest(at=at):
                bad = bytearray(blob)
                bad[at] ^= 0x10
                self.assertEqual(self.verdict(bytes(bad)).startswith("refused"), not ipf.accepts(bytes(bad)))
                self.assertTrue(self.verdict(bytes(bad)).startswith("refused"))

    def test_the_structure_rules(self):
        track = ipf.Track(0, 0, sector_blocks(2))
        imge, data = ipf.track_body(track, 0)
        info_caps = ipf.build([track])[:12 + 96]
        caps = info_caps[:12]
        info = info_caps[12:]
        cases = {
            "no CAPS": info + imge + data,
            "no INFO": caps + imge + data,
            "IMGE before INFO": caps + imge + info + data,
            "two INFO": caps + info + info + imge + data,
            "IMGE without DATA": caps + info + imge,
            "DATA without IMGE": caps + info + data,
            "two IMGE": caps + info + imge + imge + data,
            "two CAPS": caps + caps + info + imge + data,
        }
        for name, blob in cases.items():
            with self.subTest(name):
                self.assertTrue(self.verdict(blob).startswith("refused"), name)
                self.assertFalse(ipf.accepts(blob))

    def test_limits_of_a_drive(self):
        for name, track in (("cylinder 83", ipf.Track(83, 0, sector_blocks(2))), ("head 2", ipf.Track(0, 2, sector_blocks(2)))):
            blob = ipf.build([track])
            with self.subTest(name):
                self.assertTrue(self.verdict(blob).startswith("refused"))
                self.assertFalse(ipf.accepts(blob))
        both = ipf.build([ipf.Track(0, 0, sector_blocks(2)), ipf.Track(0, 0, sector_blocks(3))])
        self.assertTrue(self.verdict(both).startswith("refused"))
        too_big = ipf.Track(0, 0, [ipf.Block([("mfm", bytes(4000))], gap_cells=8)])
        self.assertTrue(self.verdict(ipf.build([too_big])).startswith("ok"), "a track of 64000 cells is fine")
        huge = ipf.build([ipf.Track(0, 0, [ipf.Block([("mfm", bytes(11000))], gap_cells=8)])])
        self.assertTrue(self.verdict(huge).startswith("refused"), "176000 cells are more than a drive's track")


class HostileBlocks(Probe, unittest.TestCase):
    """Descriptions that lie, with the checksums put right: the reader must refuse (or fail the track) and read nothing outside its buffers."""

    def lying(self, edit):
        track = ipf.Track(0, 0, sector_blocks(3, gap=64))
        imge, data_tag = ipf.track_body(track, 0)
        head = data_tag[:28]
        extra = bytearray(data_tag[28:])
        edit(extra)
        import struct
        import zlib
        body = bytes(extra)
        data_tag = ipf.tag(b"DATA", struct.pack(">IIII", len(body), len(body) * 8, zlib.crc32(body) & 0xFFFFFFFF, 0)) + body
        full = ipf.build([track])
        return full[:12 + 96] + imge + data_tag

    def test_block_fields_set_to_the_extremes(self):
        import struct
        for field in range(8):
            for value in (0, 1, 7, 8, 15, 16, 0xFFFF, 0x7FFFFFFF, 0x80000000, 0xFFFFFFF0, 0xFFFFFFFF):
                def edit(extra, field=field, value=value):
                    struct.pack_into(">I", extra, 4 * field, value)
                with self.subTest(field=field, value=value):
                    blob = self.lying(edit)
                    verdict, tracks = self.decode(blob)
                    self.assertTrue(verdict.startswith("ok"), verdict)       # the file is well formed; whether a track decodes is the decoder's answer
                    self.assertIn(tracks[(0, 0)] if isinstance(tracks[(0, 0)], str) else "decoded", ("failed", "decoded", "absent"))

    def test_the_streams_of_a_block_set_to_the_extremes(self):
        for at in range(96, 200, 3):
            for value in (0x00, 0x01, 0x1F, 0x20, 0x3F, 0x7F, 0xE0, 0xE1, 0xFF):
                def edit(extra, at=at, value=value):
                    extra[at] = value
                with self.subTest(at=at, value=value):
                    verdict, tracks = self.decode(self.lying(edit))
                    self.assertTrue(verdict.startswith("ok"), verdict)


if __name__ == "__main__":
    unittest.main()
