"""T15i1 and T15i2: the bodies of WinUAE's own save_* and restore_* functions inside P6DK, P6AU, P6CH and P6VD.

The engine is compiled with P6_STATE_BODIES (patches 0086-0093; SAVESTATE stays undefined) and bridge/state_upstream.cpp writes each body as a sub-body: its tag of 4
characters, a u32 length (little endian, like the container) and the bytes the engine wrote (big endian, upstream's own encoding).

T15i1, what is saved:
  - the sub-bodies of each chunk, in order, with the lengths of a real save of this build, pinned in a table: CHIP 360, BLTX 226, BLIT 63, CHSL 191, SPRn 25, AUDn 25, BPLX 292,
    DISK 17 and DSKn 24 are fixed; DSDn is absent (length 0) or the engine's track buffer, whose length follows from its own header;
  - the disk body carries no path: 24 bytes, the CRC of the mounted image (the one drive_insert measured) and a flag that is always written, as zero;
  - a save does not change the machine or its future: every probe digest of every unit is the same with saves in the middle as without, and the state taken at the end,
    with every body in it, is byte for byte the same; two saves in a row are the same bytes; the scenarios have the blitter in flight, the track buffer filled, a mouse moving
    (save_custom reads JOY0DAT and JOY1DAT; STATE_DOSAVE keeps getjoystate from polling the host's input, which in this profile has nothing to poll, so this scenario
    cannot tell the guard from its absence: it pins that nothing visible moves) and DMACONR's derived bits set (the live dmacon must not take them).

T15i2, what is loaded:
  - a load applies the bodies on the live machine (no custom_reset, no memory_clear, no *_start): the state taken again afterwards re-emits every upstream chunk byte for byte,
    in the same process, k units later, and in a new one (a fresh image runs its reset head first). The only bytes that differ are VPOSR and VHPOSR, which restore_custom
    reads and throws away: the beam of a loaded machine is where the machine was (T15f puts it back), and the tests say so by name;
  - the scheduler keeps what P6SC applied although restore_audio_finish and the register setters schedule events (the digest of the unit comes back);
  - every sub-body, truncated, lengthened, with another tag, out of its place or with a value that would index out of range, is refused before anything is applied (a corrupt
    state, code 9) and the machine goes on as a control run that never saw the attempt; a state taken with another medium in a drive is refused once the machine exists (code 10).
"""
import hashlib
import struct
import zlib

from tests.state import envelope as e
from tests.state.harness import BEAM, ProbeCase, SUBSYSTEMS, parse_chunks, reforge, with_bytes, without_beam

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


def with_subs(blob, chunk_tag, change):
    """The state with the sub-bodies of one chunk changed by `change`, which gets a list of [tag, bytes] to edit; the lengths are written again and every checksum is right."""
    def edit(chunks):
        for chunk in chunks:
            if chunk[0] == chunk_tag:
                subs = [[name, bytes(data)] for name, data in sub_bodies(chunk[2])]
                change(subs)
                chunk[2] = b"".join(name.encode("ascii") + struct.pack("<I", len(data)) + data for name, data in subs)
    return reforge(blob, edit)


def locate(blob):
    """{sub-body tag: offset of its tag in the whole state}: where a length or a value can be written by hand."""
    at, found = e.HEADER_SIZE, {}
    for tag, _, body in parse_chunks(blob):
        if tag in ORDER:
            inner = at + 16
            for name, data in sub_bodies(body):
                found[name] = inner
                inner += 8 + len(data)
        at += 16 + len(body)
    return found


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

    # -- a load applies them ---------------------------------------------------------------------------------------------------------------------

    # name -> (rom, units before the save, units run before the load, extra arguments). The disk is mounted at the first unit as a boot medium, so that a fresh image has it
    # after its reset head. The scenarios have the blit in flight, the DMA of the disk, the audio and the sprites running and a mouse moving.
    ROUND_TRIPS = {"idle": ("vsync", 3, 7, ()), "dma-no-disk": ("busy", 5, 9, ()), "blit-in-flight": ("busy", 2, 6, ()), "disk": ("busy", 8, 12, "disk"),
                   "disk-early": ("busy", 2, 6, "disk"), "mouse": ("input_all", 4, 8, "mouse")}

    def scenario(self, name):
        rom, n, load, args = self.ROUND_TRIPS[name]
        return rom, n, load, self.args(args) if isinstance(args, str) else args

    def upstream_payload(self, blob):
        """The four chunks of the upstream bodies, with the beam registers left out."""
        keep = {tag: (version, body) for tag, version, body in parse_chunks(blob) if tag in ORDER}
        body = bytearray(keep[b"P6CH"][1])
        body[BEAM] = bytes(4)
        return {tag: (version, bytes(body) if tag == b"P6CH" else bytes(b)) for tag, (version, b) in keep.items()}

    def test_the_state_taken_again_after_a_load_re_emits_every_upstream_body_in_the_same_process(self):
        for name in self.ROUND_TRIPS:
            with self.subTest(name):
                rom, n, load, args = self.scenario(name)
                first, second = self.dir / f"{name}.first", self.dir / f"{name}.second"
                run = self.probe(rom, "--units", str(load), "--every", "1", *args, "--save-at", str(n), str(first), "--load-at", str(load), str(first),
                                 "--save-at", str(load), str(second))
                self.assertEqual([(load, 0, "")], run.loads)
                a, b = first.read_bytes(), second.read_bytes()
                self.assertEqual(self.upstream_payload(a), self.upstream_payload(b), "an upstream body was not put back as it was saved")
                self.assertEqual(without_beam(a), without_beam(b), "and neither was any other chunk")
                # What the digests of the unit say: Paula is where she was, and the scheduler is P6SC's although restore_audio_finish and the register setters scheduled.
                for subsystem in ("audio", "scheduler"):
                    self.assertEqual(run.digests[(n, subsystem)], run.after[0][subsystem], subsystem)
                self.assertNotEqual(run.digests[(n, "cpu")], run.digests[(load, "cpu")], "the machine moved on between the two units, or nothing is proven")

    def test_the_state_taken_again_after_a_load_re_emits_every_upstream_body_in_a_new_process(self):
        for name in self.ROUND_TRIPS:
            for skip in (0, 3):
                with self.subTest(name, units_before_the_load=skip):
                    rom, n, load, args = self.scenario(name)
                    source, again = self.dir / f"{name}.source", self.dir / f"{name}.again"
                    saved = self.probe(rom, "--units", str(n), "--every", "1", *args, "--save-at", str(n), str(source))
                    fresh = self.probe(rom, "--units", str(skip), *args, "--load-at", str(skip), str(source), "--save-at", str(skip), str(again))
                    self.assertEqual([(skip, 0, "")], fresh.loads)
                    self.assertEqual(self.upstream_payload(source.read_bytes()), self.upstream_payload(again.read_bytes()))
                    self.assertEqual(without_beam(source.read_bytes()), without_beam(again.read_bytes()))
                    for subsystem in ("audio", "scheduler"):
                        self.assertEqual(saved.digests[(n, subsystem)], fresh.after[0][subsystem], subsystem)

    def test_only_the_beam_registers_of_the_chipset_body_are_not_put_back(self):
        # restore_custom reads VPOSR and VHPOSR and throws them away: the beam is where the loaded machine already was. A unit ends after the instruction that crosses the
        # end of the frame, so the beam at the boundary moves from one unit to the next, and the four bytes are the whole difference. When T15f restores the beam this test
        # is the one to delete, together with without_beam.
        first, second = self.dir / "beam.first", self.dir / "beam.second"
        self.probe("vsync", "--units", "7", "--save-at", "3", str(first), "--load-at", "7", str(first), "--save-at", "7", str(second))
        a, b = first.read_bytes(), second.read_bytes()
        self.assertNotEqual(a, b)
        self.assertEqual(without_beam(a), without_beam(b))
        chip = lambda blob: [body for tag, _, body in parse_chunks(blob) if tag == b"P6CH"][0][BEAM]
        self.assertNotEqual(chip(a), chip(b))

    def test_a_fresh_image_runs_units_after_a_load(self):
        # Until T15i2 the audio chunk was a placeholder and a fresh image could not run on after a load. It can now; that the future is the same bit for bit is DET02 (T15i3).
        for name in ("idle", "dma-no-disk", "disk", "mouse"):
            with self.subTest(name):
                rom, n, load, args = self.scenario(name)
                source = self.dir / f"{name}.run"
                self.probe(rom, "--units", str(n), *args, "--save-at", str(n), str(source))
                run = self.probe(rom, "--units", "5", "--every", "1", *args, "--load-at", "0", str(source))
                self.assertEqual([(0, 0, "")], run.loads)
                for unit in range(1, 6):
                    self.assertIn((unit, "cpu"), run.digests, f"unit {unit} did not run")

    # -- every sub-body is validated before anything is applied ----------------------------------------------------------------------------------

    CHIPS = {b"P6CH": ("chipset", ORDER[b"P6CH"]), b"P6VD": ("video", ORDER[b"P6VD"]), b"P6AU": ("audio", ORDER[b"P6AU"]), b"P6DK": ("disk", ORDER[b"P6DK"])}

    def good_state(self):
        if not hasattr(self, "_good"):
            type(self)._good = self.state_of("good.state", "busy", 8, *self.disk)
        return self._good

    def refuse(self, attempts):
        """Every attempt, as (name, state, text the message must contain), is a corrupt state at unit 8 of the process the good one was taken in."""
        self.refusals([(name, data, 9, text) for name, data, text in attempts], rom="busy", extra=self.disk, unit=8, units=11)

    def test_every_sub_body_with_another_tag_or_out_of_its_place_is_refused(self):
        good = self.good_state()
        attempts = []
        for tag, (chunk, names) in self.CHIPS.items():
            for name in names:
                attempts.append((f"{name} named XXXX", with_subs(good, tag, lambda subs, name=name: [s.__setitem__(0, "XXXX") for s in subs if s[0] == name]),
                                 f"chunk {chunk}: {name}: another body, or this one out of its place"))
            if len(names) > 1:
                attempts.append((f"{names[0]} and {names[1]} swapped", with_subs(good, tag, lambda subs: subs.__setitem__(slice(0, 2), [subs[1], subs[0]])),
                                 f"chunk {chunk}: {names[0]}: another body"))
        self.refuse(attempts)

    def test_every_sub_body_shorter_or_longer_than_its_length_is_refused(self):
        good = self.good_state()
        located = locate(good)
        attempts = []
        for tag, (chunk, names) in self.CHIPS.items():
            for name in names:
                shorter = lambda subs, name=name: [s.__setitem__(1, s[1][:-1]) for s in subs if s[0] == name and s[1]]
                longer = lambda subs, name=name: [s.__setitem__(1, s[1] + b"\0") for s in subs if s[0] == name]
                data = dict(sub_bodies([body for t, _, body in parse_chunks(good) if t == tag][0]))[name]
                if data:
                    attempts.append((f"{name} one byte shorter", with_subs(good, tag, shorter), f"chunk {chunk}: {name}: "))
                # The chunk of a longer body is longer than the largest the envelope allows when the chunk has no absent track buffer to spare it.
                attempts.append((f"{name} one byte longer", with_subs(good, tag, longer), "chunk_length" if tag != b"P6DK" else f"chunk {chunk}: {name}: "))
                # The length of the sub-body says more than the chunk holds, or less than the bytes that follow it.
                for delta in (1, -1):
                    if len(data) + delta >= 0 and name != "BPLX":
                        attempts.append((f"{name} length {delta:+d}", with_bytes(good, e.TAGS.index(tag), located[name] + 4, struct.pack("<I", len(data) + delta)),
                                         "chunk "))
        self.refuse(attempts)

    # name, chunk, sub-body, offset in the body, the bytes written there, what the message says
    VALUES = [
        ("DSK0 of another drive id", b"P6DK", "DSK0", 0, b"\0\0\0\0", "the drive id is not the one of this configuration"),
        ("DSK2 enabled", b"P6DK", "DSK2", 4, b"\0", "the disabled bit is not the one of this configuration"),
        ("DSK0 disabled", b"P6DK", "DSK0", 4, b"\x03", "the disabled bit is not the one of this configuration"),
        ("DSK0 with unknown state bits", b"P6DK", "DSK0", 4, b"\xc1", "unknown bits in the drive state"),
        ("DSK0 beyond the last cylinder", b"P6DK", "DSK0", 5, b"\x54", "the cylinder is beyond the last one"),
        ("DSK0 ready is 2", b"P6DK", "DSK0", 6, b"\x02", "the ready flag is not a flag"),
        ("DSK0 id counter 32", b"P6DK", "DSK0", 7, b"\x20", "the drive id counter is beyond 31"),
        ("DSK0 head beyond the largest track", b"P6DK", "DSK0", 8, struct.pack(">I", 0x40000), "the head position is beyond the largest track"),
        ("DSK0 head negative", b"P6DK", "DSK0", 8, b"\xff\xff\xff\xff", "the head position is beyond the largest track"),
        ("DSK0 with a path flag", b"P6DK", "DSK0", 20, b"\0\0\0\1", "the path flag is set"),
        ("DSD0 of mode 2", b"P6DK", "DSD0", 0, struct.pack(">I", 2), "the mode is neither 1 nor 3"),
        ("DSD0 of mode 0", b"P6DK", "DSD0", 0, struct.pack(">I", 0), "the mode is neither 1 nor 3"),
        ("DSD0 with no track", b"P6DK", "DSD0", 6, struct.pack(">I", 0), "the track length is outside the buffer"),
        ("DSD0 with a track beyond the buffer", b"P6DK", "DSD0", 6, struct.pack(">I", 0x40001), "the track length is outside the buffer"),
        ("DSD0 with a track of another length", b"P6DK", "DSD0", 6, struct.pack(">I", 0x30000), "the length does not follow from the track length"),
        ("DISK with a bit offset of 16", b"P6DK", "DISK", 2, b"\x10", "the bit offset is beyond 15"),
        ("DISK with a sync flag of 2", b"P6DK", "DISK", 3, b"\x02", "the sync flag is not a flag"),
        ("DISK with DMA state 1", b"P6DK", "DISK", 5, b"\x01", "the DMA state is not off, read or write"),
        ("DISK with a FIFO slot of 3", b"P6DK", "DISK", 11, b"\x03", "a FIFO slot is in use in a way"),
        ("DISK with a length of 16384", b"P6DK", "DISK", 15, struct.pack(">H", 0x4000), "the DMA length is beyond 16383 words"),
        ("CHIP of another chipset", b"P6CH", "CHIP", 0, struct.pack(">I", 0x101 | 1), "the chipset is not the one of this configuration"),
        ("CHIP of another video standard", b"P6CH", "CHIP", 350, struct.pack(">H", 0x8001), "the video standard is not the one of this configuration"),
        ("CHIP with FMODE", b"P6CH", "CHIP", 352, struct.pack(">H", 3), "FMODE is set in a chipset that has none"),
        ("BLTX of a non cycle-exact blitter", b"P6CH", "BLTX", 0, b"\0", "the blitter is not the one of this configuration"),
        ("BLTX in state 3", b"P6CH", "BLTX", 1, b"\3", "the state of the blitter is not idle, pending or running"),
        ("BLTX with a channel mask of 16", b"P6CH", "BLTX", 46, b"\x10", "the channel mask of the blit is beyond four bits"),
        ("BLTX with a fill flag of 2", b"P6CH", "BLTX", 51, b"\2", "a fill flag of the blit is not a flag"),
        ("BLTX with a wrong first sentinel", b"P6CH", "BLTX", 84, b"\x12\x35", "a sentinel of the body is not where it must be"),
        ("BLTX with a wrong second sentinel", b"P6CH", "BLTX", 130, b"\x12\x34", "a sentinel of the body is not where it must be"),
        ("BLIT with no flags", b"P6CH", "BLIT", 0, struct.pack(">I", 0), "the flags are not the ones save_blitter writes"),
        ("BLIT with a flag it never writes", b"P6CH", "BLIT", 0, struct.pack(">I", 8 | 16 | 2), "the flags are not the ones save_blitter writes"),
        ("CHSL with no flags", b"P6CH", "CHSL", 0, struct.pack(">I", 0), "the flags are not the ones save_custom_slots writes"),
        ("CHSL with both copper flags", b"P6CH", "CHSL", 0, struct.pack(">I", 15), "the flags are not the ones save_custom_slots writes"),
        ("CHSL with a word that is zero", b"P6CH", "CHSL", 12, b"\0\1", "a word that is always zero is not"),
        ("CHSL slot 0 with a register beyond 0x1FE", b"P6CH", "CHSL", 28 + 4, struct.pack(">H", 0x200), "a slot names a register beyond 0x1FE"),
        ("CHSL slot 1 with a pointer of no register", b"P6CH", "CHSL", 28 + 54 + 50, struct.pack(">H", 0x123), "a slot points at a register the engine has no pointer for"),
        ("CHSL slot 2 with a conflict pointer of no register", b"P6CH", "CHSL", 28 + 108 + 52, struct.pack(">H", 0x206), "a slot points at a register the engine has no pointer for"),
        ("CHSL with a first offset of 4", b"P6CH", "CHSL", 190, b"\4", "the first slot offset is beyond 3"),
        ("SPR3 with flag bits it never writes", b"P6CH", "SPR3", 24, b"\x08", "unknown bits in the sprite flags"),
        ("AUD2 in state 6", b"P6AU", "AUD2", 0, b"\6", "the state of the channel is beyond 5"),
        ("AUD1 with an interrupt flag of 2", b"P6AU", "AUD1", 2, b"\2", "the interrupt flag is not a flag"),
        ("AUD3 without the marker bit", b"P6AU", "AUD3", 3, b"\0", "the flags of the channel are not the ones save_audio writes"),
        ("AUD0 with an unknown flag", b"P6AU", "AUD0", 3, b"\x84", "the flags of the channel are not the ones save_audio writes"),
        ("BPLX of version 2", b"P6VD", "BPLX", 0, struct.pack(">I", 2), "the version of the body is not 1"),
    ]

    def test_every_value_that_ends_up_indexing_or_names_another_configuration_is_refused(self):
        good = self.good_state()
        located = locate(good)
        attempts = []
        for name, tag, sub, offset, value, text in self.VALUES:
            attempts.append((name, with_bytes(good, e.TAGS.index(tag), located[sub] + 8 + offset, value), text))
        self.refuse(attempts)

    def test_a_track_buffer_of_a_drive_that_is_off_or_a_head_past_the_end_of_its_track_is_refused(self):
        good = self.good_state()
        track = dict(sub_bodies([body for tag, _, body in parse_chunks(good) if tag == b"P6DK"][0]))["DSD0"]
        length = struct.unpack_from(">I", track, 6)[0]
        self.assertGreater(length, 0)
        past = struct.pack(">I", length)         # the head of DF0 on the first bit that is not in the track
        located = locate(good)
        self.refuse([
            ("a track buffer for DF2, which is not in the machine", with_subs(good, b"P6DK", lambda subs: [s.__setitem__(1, track) for s in subs if s[0] == "DSD2"]),
             "DSD2: a buffered track for a drive that is off or does not exist"),
            ("a track buffer for DF1, which has no motor on", with_subs(good, b"P6DK", lambda subs: [s.__setitem__(1, track) for s in subs if s[0] == "DSD1"] +
                                                                  [s.__setitem__(1, s[1][:4] + bytes([s[1][4] & ~1]) + s[1][5:]) for s in subs if s[0] == "DSK1"]),
             "DSD1: a buffered track for a drive that is off"),
            ("the head of DF0 past the end of its track", with_bytes(good, e.TAGS.index(b"P6DK"), located["DSK0"] + 8 + 8, past),
             "DSD0: the head position of the drive is beyond the end of the track"),
            ("a track buffer shorter than the header says", with_subs(good, b"P6DK", lambda subs: [s.__setitem__(1, track[:-2]) for s in subs if s[0] == "DSD0"]),
             "DSD0: the length does not follow from the track length"),
        ])

    # -- the medium of a drive is the machine's --------------------------------------------------------------------------------------------------

    def test_a_state_taken_with_another_medium_in_a_drive_is_refused_once_the_machine_exists_and_the_machine_is_untouched(self):
        other = self.dir / "b.adf"
        other.write_bytes(hashlib.sha256(b"disk b").digest() * (901120 // 32))
        with_a = self.state_of("a-in-df0.state", "busy", 8, "--adf", str(self.adf), "--adf", str(other), "--event-at", "0", "0", "0", "1")
        for name, args in (("the other disk in the drive", ("--adf", str(self.adf), "--adf", str(other), "--event-at", "0", "0", "1", "1")),
                           ("the disk loaded but not in the drive", ("--adf", str(self.adf), "--adf", str(other)))):
            with self.subTest(name):
                control = self.probe("busy", "--units", "11", "--every", "1", *args)
                path = self.dir / "refused.state"
                path.write_bytes(with_a)
                run = self.probe("busy", "--units", "11", "--every", "1", *args, "--load-at", "8", str(path))
                (unit, code, message), = run.loads
                self.assertEqual((8, 10), (unit, code), message)          # P6_ERROR_INCOMPATIBLE: a state of this build and these media, taken with another disk in the drive
                self.assertIn("chunk disk: DSK0: the disk in the drive is not the one the state was taken with", message)
                self.assertEqual(control.unit(8), run.after[0], "a refused state changed the machine")
                for later in range(9, 12):
                    self.assertEqual(control.unit(later), run.unit(later), f"unit {later}: the machine did not go on as if nothing had happened")

    def test_a_state_taken_with_no_disk_in_a_drive_is_refused_by_a_machine_that_has_one_in_it(self):
        none = self.state_of("df0-empty.state", "busy", 8, "--adf", str(self.adf))
        path = self.dir / "refused-empty.state"
        path.write_bytes(none)
        run = self.probe("busy", "--units", "9", "--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--load-at", "8", str(path))
        (unit, code, message), = run.loads
        self.assertEqual((8, 10), (unit, code), message)
        self.assertIn("DSK0: the disk in the drive", message)
