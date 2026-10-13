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

from tests.media import fdi
from tests.media import trackdisk as td
from tests.scenarios.disk import disk
from tests.state import test_det02 as d
from tests.state import test_det02_floppy as floppy
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
    # FDI (T57b): Amiga tracks, a low-level track of stable pulses, and one whose gap has weak pulses (a different gap each revolution: the reader fetches the next revolution at the index).
    amiga = lambda t: ("amiga", td.dos_track(adf, t), 0)
    sectors = [w for sector in disk.encode_track(adf, 0) for w in sector]
    words = sectors + [0xAAAA] * 160
    widths, positions = fdi.pulses_from_mfm(words)
    weak = {k for k, pos in enumerate(positions) if pos >= len(sectors) * 16}
    return {"ext2": td.ext2_image(raw0), "ext2-varied": td.ext2_image(plain), "ext1": td.ext1_image(raw1), "ext2-odd": td.ext2_image(odd),
            "fdi-amiga": fdi.fdi_image([amiga(t) for t in range(8)]), "fdi-low": fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths)), amiga(1)]),
            "fdi-weak": fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths, (120, 60, weak))), amiga(1)]),
            # every third pulse of the whole track is weak: what the DMA reads after an index differs from the revolution before it, wherever it reads
            "fdi-allweak": fdi.fdi_image([("lowlevel", fdi.lowlevel_track(widths, (120, 60, set(range(0, len(widths), 3))))), amiga(1)])}


def motor_program():
    """The reads of tests/state/test_state_upstream.py DISK_DMA (a read of $1900 words, then another from each DSKBLK interrupt) with the vertical blank turned into a ring of handlers, one
    action each: the motor is turned off at the 12th and on again at the 20th. A state taken in between has a drive that spins no more and a buffered track that DSDn does not carry (upstream saves it
    only while the motor runs); the machine that loads it decodes the track again when the motor comes on."""
    text = upstream.DISK_DMA.replace("MOVE.L #vblank,$6C.L", "MOVE.L #h0,$6C.L")
    old = """vblank:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVEA.L #$30000,A0          ; the window of DSKBYTR samples starts again
    RTE
"""
    assert old in text
    actions = {12: ["MOVE.B #$FF,$BFD100.L       ; deselect", "MOVE.B #$F7,$BFD100.L       ; select DF0 with the motor bit high: the motor is off"],
               20: ["MOVE.B #$FF,$BFD100.L", "MOVE.B #$7F,$BFD100.L       ; motor on", "MOVE.B #$77,$BFD100.L       ; select DF0: the motor latches on"]}
    ring = []
    for k in range(32):
        ring += [f"h{k}:", "    MOVE.W #$0020,$DFF09C.L", "    ADDQ.L #1,D1"] + ["    " + a for a in actions.get(k, [])]
        ring += [f"    MOVE.L #h{min(k + 1, 31)},$6C.L", "    RTE"]
    return text.replace(old, "\n".join(ring) + "\n")


MOTOR = motor_program()


class TrackDiskCase(d.Det02Case):
    ROMS = {**{name: d.Det02Tests.ROMS[name] for name in ("disk-dma", "disk-dma-sync", "disk-seek")}, "disk-motor": (MOTOR, None, None)}
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
    "fdi-amiga-sync": ("disk-dma-sync", "fdi-amiga", (3, 12, 16), d.CORE),
    "fdi-amiga-seek": ("disk-seek", "fdi-amiga", (5, 7, 9, 12), d.CORE),
    "fdi-low-sync": ("disk-dma-sync", "fdi-low", (3, 12, 16), d.CORE),
    "fdi-weak-dma": ("disk-dma", "fdi-weak", (4, 8, 11, 13, 16, 20), d.CORE),
    "fdi-weak-sync": ("disk-dma-sync", "fdi-weak", (3, 12, 16), d.CORE),
    "fdi-allweak-motor": ("disk-motor", "fdi-allweak", (11, 14, 17, 19, 22, 26), d.CORE),
    "ext2-motor": ("disk-motor", "ext2", (14, 17, 22), d.CORE),
}


class TrackDiskDet02Tests(TrackDiskCase):
    pass


d.populate(TrackDiskDet02Tests, SCENARIOS, {})


class TrackDiskStateTests(TrackDiskCase):
    ROMS = {**{name: d.Det02Tests.ROMS[name] for name in ("disk-dma", "disk-dma-sync")}, "disk-motor": (MOTOR, None, None)}

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


    def needed(self, rom, n, after, words, check_flags=True):
        """The state of an FDI with weak pulses everywhere, taken after the reader fetched revolutions: a machine that loads it goes on as the original; the same state with one of the new words of
        DSKX changed does not (DSDn brings back the buffer of the track, not the flag that it has several revolutions, their count or the track they belong to)."""
        arguments = self.arguments("fdi-allweak")
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

    def test_the_words_of_the_multi_revolution_reader_are_needed_to_continue_an_fdi_with_weak_bits(self):
        self.needed("disk-dma", 16, 14, (("multi_revolution", 0), ("fdi_revolution", 0)))

    def test_the_revolution_and_the_track_it_belongs_to_are_needed_when_the_motor_is_off(self):
        """The motor is off at unit 16 of this program, so DSDn is absent: the machine that loads the state decodes the track again, as revolution `fdi_revolution` of `fdi_track` (patch 0334)."""
        drive = self.needed("disk-motor", 16, 14, (("fdi_revolution", 0), ("fdi_track", -1)), check_flags=False)
        self.assertEqual(0, drive["fdi_track"])


if __name__ == "__main__":
    unittest.main()
