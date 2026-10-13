"""T57: DET02 with extended ADF disks (raw MFM tracks of any length mixed with AmigaDOS tracks), and what the track of such a disk adds to the native state.

The programs are the ones of tests/state/test_det02.py that read and seek (a sync-aligned read of $1900 words that goes on from its DSKBLK interrupt, a read from wherever the
head is, and the seek ring that steps the head through cylinders 0 to 2 on both sides with a sync-aligned read at two of its steps); only the medium changes. A raw track is not
the 11-sector track the controller assumes everywhere else: its length in bits is the image's, so a state taken in the middle of a read carries the buffered track (DSDn), the
position in it (mfmpos), its length and the factor that scales the position when the head moves to a track of another length (`prevtracklen`, which restore_disk set from the
live machine's track and not from the state's: a machine rewound from another cylinder scaled the position of the state by the ratio of two lengths that had nothing to do with it).
What is decided here:
  * a state taken before, in the middle of and after a raw-track read, and while the head walks over tracks of different lengths, loads into the same image (a few units on), a
    restarted one and a new process, and the units that follow are the original's, subsystem by subsystem (patch 0330: the seek ring over six lengths diverged without it);
  * the buffer of a raw track whose length is not a multiple of 16 bits has nothing past its last bit (patch 0331): the state carries the buffer, so its bytes are a function of the image;
  * a state taken with an extended ADF in the drive names that medium by its content, so another image (even the plain disk of the same sectors) is a different manifest and is refused.
"""
import struct
import unittest

from tests.media import trackdisk as td
from tests.scenarios.disk import disk
from tests.state import test_det02 as d
from tests.state import test_state_upstream as upstream

LABEL = 0x31
LENGTHS = (6250, 6100, 6400, 6000, 6300, 6200)      # words of the raw tracks 0 to 5 of the varied image: every cylinder and side has a track of its own length


def images():
    adf = disk.make_adf(LABEL)
    dos = [("dos", td.dos_track(adf, t)) for t in range(160)]
    plain = [("raw", td.raw_track(0xD00 + t, words=n, sync_at=500 + 17 * t), n * 16) if t < len(LENGTHS) else dos[t] for t, n in enumerate(list(LENGTHS) + [0] * 154)]
    raw0 = [("raw", td.raw_track(0xA11CE), td.RAW_WORDS * 16)] + dos[1:]
    raw1 = [("raw", td.SYNC, bytes((i * 7 + 3) & 0xFF for i in range(10000)))] + dos[1:]
    # A raw track of 100008 bits: 12501 bytes, so the last word of its buffer holds one byte of the track and 8 bits that are not (every data byte is 0xFF, so a stale 0xAA would show).
    odd = [("raw", [0xFFFF] * 6251, 100008)] + dos[1:]
    return {"ext2": td.ext2_image(raw0), "ext2-varied": td.ext2_image(plain), "ext1": td.ext1_image(raw1), "ext2-odd": td.ext2_image(odd)}


class TrackDiskCase(d.Det02Case):
    ROMS = {name: d.Det02Tests.ROMS[name] for name in ("disk-dma", "disk-dma-sync", "disk-seek")}
    IMAGES = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name, image in images().items():
            path = cls.dir / f"{name}.adf"
            path.write_bytes(image)
            cls.IMAGES[name] = path

    def arguments(self, extra):
        if extra in self.IMAGES:
            return ["--adf", str(self.IMAGES[extra]), "--event-at", "0", "0", "0", "1"]
        return super().arguments(extra)


SCENARIOS = {
    "ext2-sync": ("disk-dma-sync", "ext2", (2, 3, 12, 16), d.CORE),
    "ext2-dma": ("disk-dma", "ext2", (11, 13, 16), d.CORE),
    "ext1-sync": ("disk-dma-sync", "ext1", (12, 16), d.CORE),
    "ext2-seek": ("disk-seek", "ext2-varied", (4, 5, 6, 7, 9, 12), d.CORE),
}


class TrackDiskDet02Tests(TrackDiskCase):
    pass


d.populate(TrackDiskDet02Tests, SCENARIOS, {})


class TrackDiskStateTests(TrackDiskCase):
    ROMS = {name: d.Det02Tests.ROMS[name] for name in ("disk-dma-sync",)}

    def state_of(self, image, unit, name):
        path = self.dir / name
        self.probe("disk-dma-sync", "--units", str(unit), *self.arguments(image), "--save-at", str(unit), str(path))
        return path.read_bytes()

    def test_the_bits_past_the_end_of_a_raw_track_are_zero_in_the_state(self):
        blob = self.state_of("ext2-odd", 4, "odd.state")
        track = upstream.upstream_bodies(blob)[b"P6DK"]["DSD0"]
        self.assertTrue(track, "the drive buffers the track at unit 4")
        mode, bit_counter, length, speed, skip, index = struct.unpack_from(">IHIHII", track)
        self.assertEqual((1, 100008), (mode, length))
        words = struct.unpack_from(">%dH" % ((length + 15) // 16), track, 20)
        self.assertEqual(0xFF00, words[-1], "the last word holds 8 bits of the track and nothing after them")
        self.assertEqual({0xFFFF}, set(words[:-1]))

    def test_a_state_names_its_medium_by_content(self):
        blob = self.state_of("ext2", 4, "ext2.state")
        # The same image, loaded in a new process, takes the state; a different image does not (the media manifest is part of the identity).
        again = self.dir / "again.state"
        run = self.probe("disk-dma-sync", "--units", "5", *self.arguments("ext2"), "--load-at", "0", str(self.dir / "ext2.state"), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        refused = self.probe("disk-dma-sync", "--units", "5", *self.arguments("ext2-varied"), "--load-at", "0", str(self.dir / "ext2.state"))
        self.assertNotEqual((0, 0, ""), refused.loads[0], "another image is another manifest")
        self.assertTrue(blob)


if __name__ == "__main__":
    unittest.main()
