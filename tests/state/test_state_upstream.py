"""T15i1: the bodies of WinUAE's own save_* functions inside P6DK, P6AU, P6CH and P6VD, saved without touching the machine.

The engine is compiled with P6_STATE_BODIES (patches 0086-0089; SAVESTATE stays undefined) and bridge/state_upstream.cpp writes each body as a sub-body: its tag of 4
characters, a u32 length (little endian, like the container) and the bytes the engine wrote (big endian, upstream's own encoding). Validate and Apply of these chunks are T15i2:
a load carries them without reading them, and the public API stays UNSUPPORTED.

What is proven here:
  - the sub-bodies of each chunk, in order, with the lengths of a real save of this build, pinned in a table: CHIP 360, BLTX 226, BLIT 63, CHSL 191, SPRn 25, AUDn 25, BPLX 292,
    DISK 17 and DSKn 24 are fixed; DSDn is absent (length 0) or the engine's track buffer, whose length follows from its own header;
  - the disk body carries no path: 24 bytes, the CRC of the mounted image (the one drive_insert measured) and a flag that is always written, as zero;
  - a save does not change the machine or its future: every probe digest of every unit is the same with saves in the middle as without, and the state taken at the end,
    with every body in it, is byte for byte the same; two saves in a row are the same bytes; the scenarios have the blitter in flight, the track buffer filled, a mouse moving
    (save_custom reads JOY0DAT and JOY1DAT; STATE_DOSAVE keeps getjoystate from polling the host's input, which in this profile has nothing to poll, so this scenario
    cannot tell the guard from its absence: it pins that nothing visible moves) and DMACONR's derived bits set (the live dmacon must not take them);
  - a load of a state that carries these chunks still works as it did and leaves what they describe where it was: they are carried, not applied.
"""
import hashlib
import struct
import zlib

from tests.state import envelope as e
from tests.state.harness import ProbeCase, SUBSYSTEMS, parse_chunks

CHIPSET, VIDEO, AUDIO, DISK = (e.TAGS.index(tag) for tag in (b"P6CH", b"P6VD", b"P6AU", b"P6DK"))

# Confirmed against a real save of this build (CHIP is 360, not the 424 the research counted: with full=0 the 32 words of 0x0A0-0x0DE are not written).
FIXED = {"CHIP": 360, "BLTX": 226, "BLIT": 63, "CHSL": 191, "BPLX": 292, "DISK": 17,
         **{f"SPR{i}": 25 for i in range(8)}, **{f"AUD{i}": 25 for i in range(4)}, **{f"DSK{i}": 24 for i in range(4)}}
ORDER = {
    b"P6CH": ["CHIP", "BLTX", "BLIT", "CHSL"] + [f"SPR{i}" for i in range(8)],
    b"P6VD": ["BPLX"],
    b"P6AU": [f"AUD{i}" for i in range(4)],
    b"P6DK": [f"DSK{i}" for i in range(4)] + [f"DSD{i}" for i in range(4)] + ["DISK"],
}
DSD_FIXED = 4 + 2 + 4 + 2 + 4 + 4 + 2       # mode, bit counter, track length, speed, skip offset, index offset, revolutions

# Bitplane, sprite, audio, copper, blitter and disk DMA on; the motor on and a step pulse every frame, so that the medium (mounted by an event) is stepped and its track is
# read into the buffer; a blit of 1024 lines of 64 words that lasts more than a frame, and a result of zeros, so that DMACONR's zero bit is set when it ends. The wait of
# 40000 NOPs is the time the engine takes to put the medium in the drive.
BUSY = """; PUAE6 test program: every DMA channel on, a blit in flight and the disk motor running.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$FF,$BFD300.L       ; CIAB DDRB: all outputs
    MOVE.B #$FF,$BFD100.L       ; deselect every drive
    MOVE.B #$7F,$BFD100.L       ; motor on
    MOVE.B #$77,$BFD100.L       ; select DF0: the motor latches on
    MOVE.L #handler,$6C.L
    MOVE.L #$AAAA5555,$4000.L
    MOVE.L #$12345678,$5000.L
    MOVE.W #$2200,$DFF100.L     ; BPLCON0: 2 planes, colour
    MOVE.W #$0038,$DFF092.L     ; DDFSTRT
    MOVE.W #$00D0,$DFF094.L     ; DDFSTOP
    MOVE.W #$2C81,$DFF08E.L     ; DIWSTRT
    MOVE.W #$2CC1,$DFF090.L     ; DIWSTOP
    MOVE.L #$4000,$DFF0E0.L     ; BPL1PT
    MOVE.L #$5000,$DFF0E4.L     ; BPL2PT
    MOVE.W #$0F00,$DFF180.L     ; COLOR00
    MOVE.W #$00F0,$DFF182.L
    MOVE.W #$0123,$DFF1A2.L     ; a sprite colour
    MOVE.L #$AAAAAAAA,$6000.L   ; SPR0 list: position and control, one line of data, end
    MOVE.L #$0F0F0F0F,$6004.L
    MOVE.L #$00000000,$6008.L
    MOVE.L #$6000,$DFF120.L     ; SPR0PT
    MOVE.L #$7000,$DFF0A0.L     ; AUD0LC
    MOVE.W #$0008,$DFF0A4.L     ; AUD0LEN
    MOVE.W #$00C8,$DFF0A6.L     ; AUD0PER
    MOVE.W #$0040,$DFF0A8.L     ; AUD0VOL
    MOVE.L #$7100,$DFF0B0.L     ; AUD1LC
    MOVE.W #$0004,$DFF0B4.L
    MOVE.W #$0123,$DFF0B6.L
    MOVE.W #$0030,$DFF0B8.L
    MOVE.L #$7000,$7000.L
    MOVE.L #$2000,$DFF080.L     ; COP1LC
    MOVE.L #$01800123,$2000.L   ; copper: MOVE #$0123,COLOR00
    MOVE.L #$FFFFFFFE,$2004.L
    MOVE.W #$83FF,$DFF096.L     ; DMACON: SET, every channel
    MOVE.W #$0900,$DFF040.L     ; BLTCON0: A and D, minterm 0: D is zero
    MOVE.W #$0000,$DFF042.L
    MOVE.W #$FFFF,$DFF044.L
    MOVE.W #$FFFF,$DFF046.L
    MOVE.L #$10000,$DFF050.L    ; BLTAPT
    MOVE.L #$40000,$DFF054.L    ; BLTDPT
    MOVE.W #$0000,$DFF064.L
    MOVE.W #$0000,$DFF066.L
    MOVE.W #$C020,$DFF09A.L     ; INTENA: vertical blank
    MOVE.W #$0000,$DFF058.L     ; BLTSIZE 0: 1024 lines of 64 words
    MOVE.W #$2000,SR
    .nops 40000                 ; the medium is in the drive by now
    MOVE.W #$4489,$DFF07E.L     ; DSKSYNC
    MOVE.W #$8400,$DFF09E.L     ; ADKCON: WORDSYNC
    MOVE.L #$8000,$DFF020.L     ; DSKPT
    MOVE.W #$9900,$DFF024.L     ; DSKLEN twice: DMA on, a read of $1900 words
    MOVE.W #$9900,$DFF024.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVE.B #$74,$BFD100.L       ; a step pulse every frame: the first one after the medium is in the drive clears the disk-change flag
    MOVE.B #$75,$BFD100.L
    RTE
"""


def sub_bodies(body):
    """[(tag, bytes)] of the body of one chunk, checking that the sub-bodies are laid end to end."""
    out, at = [], 0
    while at < len(body):
        tag = body[at:at + 4].decode("ascii")
        (length,) = struct.unpack_from("<I", body, at + 4)
        assert at + 8 + length <= len(body), (tag, length, len(body))
        out.append((tag, body[at + 8:at + 8 + length]))
        at += 8 + length
    assert at == len(body)
    return out


def upstream_bodies(blob):
    """{chunk tag: {sub-body tag: bytes}} of the four chunks that carry WinUAE's own bodies."""
    chunks = {tag: body for tag, _, body in parse_chunks(blob)}
    return {tag: dict(sub_bodies(chunks[tag])) for tag in ORDER}


class UpstreamBodiesTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "input_all": ("input_all", None, None), "busy": (BUSY, None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adf = cls.dir / "a.adf"
        cls.adf.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))
        cls.disk = ("--adf", str(cls.adf), "--event-at", "0", "0", "0", "1")      # the medium goes into DF0 at the first unit
        cls.mouse = ("--port", "0:2:1:40:-25", "--port", "1:1:65:0:0")

    # -- what is saved --------------------------------------------------------------------------------------------------------------------------

    def layout(self, blob):
        """The order of the sub-bodies of each chunk, checked against the table, and the sub-bodies themselves."""
        chunks = {tag: body for tag, _, body in parse_chunks(blob)}
        for tag, expected in ORDER.items():
            tags = [name for name, _ in sub_bodies(chunks[tag])]
            self.assertEqual(expected, tags, tag)
        bodies = upstream_bodies(blob)
        for tag, subs in bodies.items():
            for name, data in subs.items():
                if name in FIXED:
                    self.assertEqual(FIXED[name], len(data), f"{tag} {name}: the length of a real save of this build")
        return bodies

    def test_every_chunk_holds_its_bodies_in_order_with_the_lengths_of_a_real_save(self):
        for name, rom, args, unit in (("vsync", "vsync", (), 3), ("busy-no-disk", "busy", (), 2), ("busy-disk", "busy", self.disk, 8)):
            with self.subTest(name):
                blob = self.state_of(f"{name}.state", rom, unit, *args)
                bodies = self.layout(blob)
                chunk_sizes = {tag: len(body) for tag, _, body in parse_chunks(blob)}
                for tag, subs in bodies.items():
                    self.assertEqual(sum(8 + len(data) for data in subs.values()), chunk_sizes[tag], f"{tag} is its sub-bodies and nothing else")
                chunk_versions = {tag: version for tag, version, _ in parse_chunks(blob)}
                self.assertEqual({tag: 1 for tag in ORDER}, {tag: chunk_versions[tag] for tag in ORDER})

    def test_the_fixed_lengths_add_up_to_the_chunks_of_a_machine_with_no_medium(self):
        blob = self.state_of("vsync.state", "vsync", 3)
        sizes = {tag: len(body) for tag, _, body in parse_chunks(blob)}
        for tag, names in ORDER.items():
            self.assertEqual(sum(8 + FIXED.get(n, 0) for n in names), sizes[tag], tag)       # DSDn is absent: its length is 0

    def test_a_disk_in_the_drive_fills_dsk0_and_dsd0_and_only_those(self):
        bodies = self.layout(self.state_of("busy-disk.state", "busy", 8, *self.disk))[b"P6DK"]
        none = self.layout(self.state_of("busy-none.state", "busy", 8))[b"P6DK"]
        dsk = bodies["DSK0"]
        self.assertEqual(24, len(dsk))
        drive_id, state, cylinder, ready, scnt, mfmpos, crc, up, down, flag = struct.unpack(">IBBBBIIHHI", dsk)
        self.assertEqual(zlib.crc32(self.adf.read_bytes()), crc, "the CRC of the mounted image, as drive_insert measured it")
        self.assertEqual(0, flag, "the flag that restore_disk reads is always written, as zero: no path follows")
        self.assertEqual(1, state & 1, "motor on")
        self.assertEqual(0, state & 2, "DF0 is not disabled")
        self.assertGreater(cylinder, 0, "the head was stepped")
        self.assertEqual(0, struct.unpack(">I", none["DSK0"][12:16])[0], "no medium, no CRC")
        for n in (2, 3):
            self.assertEqual(2, bodies[f"DSK{n}"][4] & 2, f"DF{n} is not in the machine: disabled")
        for n in (1, 2, 3):
            self.assertEqual(b"", bodies[f"DSD{n}"], f"DF{n} has no buffered track")
        for n in range(4):
            self.assertEqual(b"", none[f"DSD{n}"], "motor on but no medium: no track in the buffer")
        dsd = bodies["DSD0"]
        mode, bit_counter, track_length, speed, skip, index = struct.unpack_from(">IHIHII", dsd)
        self.assertIn(mode, (1, 3))
        words = (track_length + 15) // 16
        self.assertEqual(DSD_FIXED + words * (2 if mode == 1 else 4), len(dsd), "the length follows from its own header")

    def test_the_bound_covers_a_state_with_a_buffered_track(self):
        run = self.probe("busy", "--units", "8", *self.disk, "--save-at", "8", str(self.dir / "b.state"))
        (unit, size, _, bound), = run.saves
        self.assertGreater(size, 1064000)
        self.assertLessEqual(size, bound)

    # -- a save does not change the machine -----------------------------------------------------------------------------------------------------

    SCENARIOS = {"busy-disk": ("busy", "disk"), "busy": ("busy", None), "mouse": ("input_all", "mouse"), "vsync": ("vsync", None)}

    def args(self, kind):
        return {"disk": self.disk, "mouse": self.mouse, None: ()}[kind]

    def test_saving_does_not_change_any_digest_or_the_future(self):
        for name, (rom, kind) in self.SCENARIOS.items():
            with self.subTest(name):
                args = self.args(kind)
                plain = self.probe(rom, "--units", "9", "--every", "1", *args, "--save-at", "9", str(self.dir / f"{name}-plain.state"))
                saved = self.probe(rom, "--units", "9", "--every", "1", *args, "--save-at", "2", str(self.dir / f"{name}-2.state"), "--save-at", "4",
                                   str(self.dir / f"{name}-4.state"), "--save-at", "5", str(self.dir / f"{name}-5.state"), "--save-at", "9",
                                   str(self.dir / f"{name}-saved.state"))
                self.assertEqual(plain.digests, saved.digests, "a save in the middle changed what the machine does")
                self.assertEqual(plain.saves[-1][2:], saved.saves[-1][2:], "the state at the end, with every body in it, is the same")
                self.assertEqual((self.dir / f"{name}-plain.state").read_bytes(), (self.dir / f"{name}-saved.state").read_bytes())

    def test_two_saves_in_a_row_are_the_same_bytes(self):
        for name, (rom, kind) in self.SCENARIOS.items():
            with self.subTest(name):
                first, second = self.dir / f"{name}-first.state", self.dir / f"{name}-second.state"
                self.probe(rom, "--units", "5", *self.args(kind), "--save-at", "5", str(first), "--save-at", "5", str(second))
                self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_the_scenarios_have_something_for_a_save_to_disturb(self):
        # DMACONR's busy and zero bits: the blit has ended with a zero result, so the register image has bit 13 set and the live dmacon must not.
        blob = self.state_of("busy-5.state", "busy", 5)
        chip = upstream_bodies(blob)[b"P6CH"]["CHIP"]
        self.assertEqual(0x2000, struct.unpack_from(">H", chip, 4 + 2)[0] & 0x2000, "DMACONR of the saved register image has the zero bit")
        self.assertEqual(0, struct.unpack_from(">H", chip, 4 + 2)[0] & 0x4000, "the blit is over")
        # The blit is in flight at unit 2, with a state of the blitter machine to save.
        flight = upstream_bodies(self.state_of("busy-2.state", "busy", 2))[b"P6CH"]["BLTX"]
        self.assertEqual(2, flight[1], "BLTX: a blit in flight")
        # A mouse that is moving: JOY0DAT of the register image is not zero, so saving would consume what the host sent if it read the input.
        mouse = upstream_bodies(self.state_of("mouse-4.state", "input_all", 4, *self.mouse))[b"P6CH"]["CHIP"]
        self.assertNotEqual(0, struct.unpack_from(">H", mouse, 4 + 2 * 5)[0], "JOY0DAT")

    # -- a load carries the chunks and does not read them ----------------------------------------------------------------------------------------

    def test_a_load_carries_these_chunks_without_reading_or_applying_them(self):
        path = self.dir / "carry.state"
        run = self.probe("busy", "--units", "9", "--every", "1", *self.disk, "--save-at", "4", str(path), "--load-at", "9", str(path))
        self.assertEqual([(9, 0, "")], run.loads)
        # The chipset digest also hashes the counters of the beam that the scheduler chunk owns, so Paula's channels and the picture are what shows that P6AU, P6VD and P6CH were not applied.
        for subsystem in ("audio", "video"):
            self.assertEqual(run.digests[(9, subsystem)], run.after[0][subsystem], f"{subsystem}: the chunk is not applied, the machine is where it was")
        self.assertNotEqual(run.digests[(4, "audio")], run.digests[(9, "audio")], "the machine moved on between the two units, or nothing is proven")
        self.assertNotEqual(run.digests[(4, "cpu")], run.digests[(9, "cpu")])
        self.assertEqual(run.digests[(4, "cpu")], run.after[0]["cpu"], "the chunks that exist are applied as before")
