"""T58: DET02 with IPF disks (the cells of every track come from the decoder of MAME, with their timing, the write splice and cells that read differently at each revolution), and what
the track of such a disk adds to the native state.

The programs are the ones of tests/state/test_det02.py that read and seek (a sync-aligned read of $1900 words that goes on from its DSKBLK interrupt, a read from wherever the head is,
the seek ring that steps the head through cylinders 0 to 2 on both sides, and tests/state/test_det02_trackdisk.py's motor ring); only the medium changes. What is decided here:
  * a state taken before, in the middle of and after a read of an IPF track, with weak cells (a track that reads another set of bits at each index: the reader fetches the next revolution
    there, and the state carries its count and the track it belongs to), with timing zones (the tracktiming of a buffered track, a value per byte, is in the state) and while the head
    walks over tracks of different lengths, loads into the same image (a few units on), a restarted one and a new process, and the units that follow are the original's, subsystem by subsystem;
  * the same with the motor off at the time of the save (DSDn is absent: the machine that loads the state decodes the track again, as revolution `fdi_revolution` of `fdi_track`);
  * the words of DSKX that the reader keeps (multi_revolution, the revolution counter and its track) are needed to continue: the state with one of them changed does not continue as the original.
The decoder keeps nothing between two reads, so there is no state of the reader itself to save: the weak cells are a function of the track and the revolution (ipf_track.cpp).
"""
import struct
import unittest

from tests.media import ipf
from tests.media import trackdisk as td
from tests.scenarios.disk import disk
from tests.state import test_det02 as d
from tests.state import test_det02_floppy as floppy
from tests.state import test_det02_trackdisk as trackdisk
from tests.state import test_state_upstream as upstream

LABEL = 0x41


def images():
    adf = disk.make_adf(LABEL)
    build = ipf.build

    def track(t, total=ipf.PAL_CELLS, weak_bytes=0, kind=2, index=0):
        return ipf.Track(t // 2, t % 2, ipf.dos_blocks(adf, t, weak_bytes=weak_bytes, total_cells=total), type=kind, index_cells=index)

    return {
        "ipf-dos": build([track(t) for t in range(6)]),
        # every cylinder and side has a track of its own length (95744 cells of sectors plus a gap): the head scales its position when it moves between them
        "ipf-varied": build([track(t, total=ipf.PAL_CELLS - 3000 + 1100 * t, index=700 * t) for t in range(6)]),
        # a weak area after each sector (20 bytes: 160 cells that read differently at each revolution), in a track whose speed changes in three zones
        "ipf-weak": build([track(t, weak_bytes=20, kind=3 if t < 2 else 2) for t in range(6)]),
        # a lot of weak cells: what the DMA reads after an index differs from the revolution before it, wherever it reads
        "ipf-allweak": build([track(t, weak_bytes=60, total=ipf.PAL_CELLS + 12000) for t in range(6)]),
        # a noise track (the surface has no cell structure) in the place of the first track, and an index in the middle of the next
        "ipf-noise": build([ipf.Track(0, 0, [], type=1, noise_cells=ipf.PAL_CELLS)] + [track(t, index=33333) for t in range(1, 6)]),
    }


class IpfCase(d.Det02Case):
    ROMS = {**{name: d.Det02Tests.ROMS[name] for name in ("disk-dma", "disk-dma-sync", "disk-seek")}, "disk-motor": (trackdisk.MOTOR, None, None)}
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
    "ipf-sync": ("disk-dma-sync", "ipf-dos", (3, 12, 16), d.CORE),
    "ipf-seek": ("disk-seek", "ipf-varied", (5, 7, 9, 12), d.CORE),
    "ipf-weak-dma": ("disk-dma", "ipf-weak", (4, 8, 11, 13, 16, 20), d.CORE),
    "ipf-weak-sync": ("disk-dma-sync", "ipf-weak", (3, 12, 16), d.CORE),
    "ipf-allweak-motor": ("disk-motor", "ipf-allweak", (11, 14, 17, 19, 22, 26), d.CORE),
    "ipf-noise-dma": ("disk-dma", "ipf-noise", (4, 11, 16), d.CORE),
    "ipf-noise-seek": ("disk-seek", "ipf-noise", (5, 9), d.CORE),
    "ipf-dos-motor": ("disk-motor", "ipf-dos", (14, 17, 22), d.CORE),
}


class IpfDet02Tests(IpfCase):
    pass


d.populate(IpfDet02Tests, SCENARIOS, {})


class IpfStateTests(IpfCase):
    ROMS = {**{name: d.Det02Tests.ROMS[name] for name in ("disk-dma", "disk-dma-sync")}, "disk-motor": (trackdisk.MOTOR, None, None)}

    def test_a_state_names_its_medium_by_content(self):
        path = self.dir / "ipf.state"
        self.probe("disk-dma-sync", "--units", "4", *self.arguments("ipf-dos"), "--save-at", "4", str(path))
        again = self.dir / "again.state"
        run = self.probe("disk-dma-sync", "--units", "5", *self.arguments("ipf-dos"), "--load-at", "0", str(path), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        refused = self.probe("disk-dma-sync", "--units", "5", *self.arguments("ipf-weak"), "--load-at", "0", str(path))
        self.assertNotEqual((0, 0, ""), refused.loads[0], "another image is another manifest")

    def test_the_buffered_track_of_an_ipf_is_in_the_state_with_its_timing_and_its_length(self):
        path = self.dir / "timed.state"
        self.probe("disk-dma-sync", "--units", "4", *self.arguments("ipf-weak"), "--save-at", "4", str(path))
        track = upstream.upstream_bodies(path.read_bytes())[b"P6DK"]["DSD0"]
        self.assertTrue(track, "the drive buffers the track at unit 4")
        mode, bit_counter, length, speed, skip, index = struct.unpack_from(">IHIHII", track)
        self.assertEqual(3, mode, "the words and the timing of the track")
        cells, times, weak, splice = ipf.expected(ipf.Track(0, 0, ipf.dos_blocks(disk.make_adf(LABEL), 0, weak_bytes=20), type=3))
        self.assertEqual((len(cells), splice), (length, skip))
        self.assertTrue(weak)

    def needed(self, rom, n, after, words, check_flags=True):
        """The state of an IPF with weak cells everywhere, taken after the reader fetched revolutions: a machine that loads it goes on as the original; the same state with one of the words of DSKX
        changed does not (DSDn brings back the buffer of the track, not the flag that it has several revolutions, their count or the track they belong to)."""
        arguments = self.arguments("ipf-allweak")
        path = self.dir / f"{rom}-{n}.state"
        original = self.probe(rom, "--units", str(n + after), "--every", "1", *arguments, "--save-at", str(n), str(path))
        blob = path.read_bytes()
        controller, dskx_words = floppy.dskx(blob)
        drive = dict(zip(upstream.DSKX_DRIVE, dskx_words[6:6 + 14]))
        if check_flags:
            self.assertEqual(1, drive["multi_revolution"])
        self.assertGreaterEqual(drive["fdi_revolution"], 1)

        def future(state, name):
            file = self.dir / name
            file.write_bytes(state)
            run = self.probe(rom, "--units", str(after), "--every", "1", *arguments, "--load-at", "0", str(file))
            self.assertEqual([(0, 0, "")], run.loads[:1], f"{name}: the state is refused")
            return {key: value for key, value in run.digests.items()}

        def changed(word, value):
            def edit(subs):
                for sub in subs:
                    if sub[0] == "DSKX":
                        at = 4 * (len(upstream.DSKX_GLOBAL) + upstream.DSKX_DRIVE.index(word))
                        sub[1] = sub[1][:at] + struct.pack(">i", value) + sub[1][at + 4:]
            return upstream.with_subs(blob, floppy.DISK_CHUNK, edit)

        def differing(run_digests):
            return [(j, s) for j in range(1, after + 1) for s in ("cpu", "scheduler", "ram") if run_digests[(j, s)] != original.digests[(n + j, s)]]

        self.assertEqual([], differing(future(blob, "same.state")), "the state as saved continues as the original")
        for word, value in words:
            self.assertNotEqual([], differing(future(changed(word, value), f"{word}.state")), f"{word} is part of the future")
        return drive

    def test_the_words_of_the_multi_revolution_reader_are_needed_to_continue_an_ipf_with_weak_cells(self):
        self.needed("disk-dma", 16, 14, (("multi_revolution", 0), ("fdi_revolution", 0)))

    def test_the_revolution_and_the_track_it_belongs_to_are_needed_when_the_motor_is_off(self):
        drive = self.needed("disk-motor", 16, 14, (("fdi_revolution", 0), ("fdi_track", -1)), check_flags=False)
        self.assertEqual(0, drive["fdi_track"])


if __name__ == "__main__":
    unittest.main()
