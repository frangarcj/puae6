"""T15i1, T15i2, T15f1, T15f2, T15f3, T15e and T15g: the bodies of WinUAE's own save_* and restore_* functions, the beam, the Copper, the blitter's and Paula's phase and Denise's picture state, inside P6DK, P6AU, P6CH and P6VD.

The engine is compiled with P6_STATE_BODIES (patches 0086-0093; SAVESTATE stays undefined; patches 0094, 0095, 0097, 0099 and 0100 add the accessors of BEAM, COPR, BLTP, AUDP, DNSP and GEOM) and bridge/state_upstream.cpp writes each body as a sub-body: its tag of 4
characters, a u32 length (little endian, like the container) and the bytes the engine wrote (big endian, upstream's own encoding).

T15i1, what is saved:
  - the sub-bodies of each chunk, in order, with the lengths of a real save of this build, pinned in a table: CHIP 360, BLTX 226, BLIT 63, CHSL 245, SPRn 25, AUDn 25, BPLX 292,
    DISK 17 and DSKn 24 are fixed; DSDn is absent (length 0) or the engine's track buffer, whose length follows from its own header;
  - the disk body carries no path: 24 bytes, the CRC of the mounted image (the one drive_insert measured) and a flag that is always written, as zero;
  - a save does not change the machine or its future: every probe digest of every unit is the same with saves in the middle as without, and the state taken at the end,
    with every body in it, is byte for byte the same; two saves in a row are the same bytes; the scenarios have the blitter in flight, the track buffer filled, a mouse moving
    (save_custom reads JOY0DAT and JOY1DAT; STATE_DOSAVE keeps getjoystate from polling the host's input, which in this profile has nothing to poll, so this scenario
    cannot tell the guard from its absence: it pins that nothing visible moves) and DMACONR's derived bits set (the live dmacon must not take them).

T15f1, the body that is not WinUAE's: BEAM (244 bytes, the last of P6CH; patch 0094) holds what restore_custom throws away (VPOSR and VHPOSR) and what no body names: the beam and the
line history, the counters of the frame phase, the strobe that raises the vertical blank, the DMA list of the line in flight and the sync latches of Agnus, as 61 words of 32 bits.

T15f2, the other body that is not WinUAE's: COPR (60 bytes, after BEAM; patch 0095) holds the Copper between two of its cycles, which CHSL does not carry (it keeps ip and the two
instruction words and puts the state back as stopped, or as the last cycle of a MOVE): the state and state_prev, the strobe in flight, the compare values of a WAIT or SKIP, the
latched COP1LC, the flag that lets the Copper run this line and the two cycle stamps, as 15 words of 32 bits. It is applied as values: the Copper does not run on load. The
refusals include the one that makes CHSL and COPR one machine (a Copper in the last cycles of a MOVE has to be so in both).

T15f3, the third: BLTP (72 bytes, after COPR; patch 0097) holds the phase of the blitter that BLTX does not carry (restore_blitter_new sets blit_queued to a constant, derives
blt_change_cycles from the cycle of the load and drops blit_dmacount and blit_cyclecount): blit_queued, blit_count_done, blt_change_cycles, the pending BLTCON/modulo flag, the held
A, B and C words, the line's D latch, the pointers a copper write of the same cycle restores and the two channel counts, as 18 words of 32 bits. It is applied as values after
restore_blitter_finish. A blit that the live image has in flight is replaced by the state's: a state with no blit leaves none running, and BLTP says the same as BLTX.
The fourth RGA slot goes with it (CHSL, 245 bytes, patch 0096): rga_pipe has RGA_SLOT_TOTAL + 1 slots and upstream saves three, which three are live depends on the first offset, and the
slot left out holds a blit D write in flight: a loaded machine kept the one of the live image (its pointer) and wrote the rest of the blit somewhere else.

T15g, what Denise and the presentation keep outside every body (patch 0100). Two bodies of P6VD that are not WinUAE's, plus one derived value rebuilt after CHIP:
  - DNSP (64 bytes) is the four-pixel output pipeline (dtbuf and dtgbuf): the first pixels of a line leave from what the previous colour clock left, so a new image put zero
    where the original put the colour of its border;
  - GEOM (76 bytes) is the raster size the engine has measured and adopted (current_linear_* with its candidate and countdown, and the in and out geometry of the drawing
    buffer): a machine that had adopted another frame kept it, and a new image adopted the saved one units later with a blank picture, 576 lines where the original showed 574;
  - the border colour (bordercolor, derived from COLOR00 and BPLCON3) is recomputed after CHIP: restore_custom flushes its register writes before it rebuilds the colour table.

T15j, what the first picture after a load and the next state need (patch 0103):
  - PIXL (the last of P6VD, 4 + 4 + the groups of pixels that the rows of the drawing buffer take) is the buffer itself: a unit in which the engine draws no line hands over the rows as
    the units before left them, so a load that did not put them back showed the picture of the abandoned history, or the black of a machine that had not drawn; it is tagged and measured like
    the others and decoded here, with the format of the C++ and not its code, against the picture the unit handed over;
  - GEOM (104 bytes) also says whether the presentation refused the mode as larger than its buffer and the allocation of the last mode it accepted (the sizes it clamped to look like a mode that
    fits): a machine that had not refused it handed over pictures that the original never did; and the flags of the drawing (the three requests to clear the buffer that wait for the first line of the next frame, and whether the frame being drawn holds the buffer);
  - BEAM (268 bytes; 260 before patch 0104, which added the two programmed-beam flags) also carries the interlace the display has seen, the first and last line of the vertical display window and the flag that tests them: custom_reset leaves the lines at 0 and 0 and calcvdiw, which the
    load runs, derives 0 and 256 from the same zero registers, so a state saved, loaded and saved again differed from one saved without the load in the vertical window bit of DIWHIGH.

T15i2, what is loaded:
  - a load applies the bodies on the live machine (no custom_reset, no memory_clear, no *_start): the state taken again afterwards re-emits every upstream chunk byte for byte,
    in the same process, k units later, and in a new one (a fresh image runs its reset head first), the beam included: the whole state is the same bytes;
  - the scheduler keeps what P6SC applied although restore_audio_finish and the register setters schedule events (the digest of the unit comes back);
  - every sub-body, truncated, lengthened, with another tag, out of its place or with a value that would index out of range, is refused before anything is applied (a corrupt
    state, code 9) and the machine goes on as a control run that never saw the attempt; a state whose drives do not hold the media its manifest names (DSKn against P6BR's drive_media) is refused before anything is applied (code 10),
    and one whose drives hold other media than the machine's is loaded: the load puts into each drive the medium the state names (T15d2).
"""
import hashlib
import struct
import zlib

from tests.state import envelope as e
from tests.state.harness import ProbeCase, SUBSYSTEMS, parse_chunks, reforge, with_bytes

CHIPSET, VIDEO, AUDIO, DISK = (e.TAGS.index(tag) for tag in (b"P6CH", b"P6VD", b"P6AU", b"P6DK"))

# Confirmed against a real save of this build (CHIP is 360, not the 424 the research counted: with full=0 the 32 words of 0x0A0-0x0DE are not written).
FIXED = {"CHIP": 360, "BLTX": 226, "BLIT": 63, "CHSL": 245, "BPLX": 292, "DISK": 17, "BEAM": 320, "COPR": 60, "BLTP": 72, "AUDP": 340, "DNSP": 64, "GEOM": 104, "DSKX": 164,
         **{f"SPR{i}": 25 for i in range(8)}, **{f"AUD{i}": 25 for i in range(4)}, **{f"DSK{i}": 24 for i in range(4)}}
ORDER = {
    b"P6CH": ["CHIP", "BLTX", "BLIT", "CHSL"] + [f"SPR{i}" for i in range(8)] + ["BEAM", "COPR", "BLTP"],
    b"P6VD": ["BPLX", "DNSP", "GEOM", "PIXL"],
    b"P6AU": [f"AUD{i}" for i in range(4)] + ["AUDP"],
    b"P6DK": [f"DSK{i}" for i in range(4)] + [f"DSD{i}" for i in range(4)] + ["DISK", "DSKX"],
}
DSD_FIXED = 4 + 2 + 4 + 2 + 4 + 4 + 2       # mode, bit counter, track length, speed, skip offset, index offset, revolutions

# The words of BEAM, in the order of p6_custom_beam_get (custom.cpp, patch 0094, four words more in patch 0103) and of the enum of bridge/state_upstream.cpp: 41 words, the twenty sync
# latches of Agnus, the first and last line of the vertical display window with the flag that tests them, and the interlace the display has seen.
BEAM_WORDS = ["vpos", "vpos_prev", "agnus_hpos", "agnus_hpos_prev", "agnus_hpos_next", "agnus_vpos_next", "agnus_pos_change", "hhpos",
              "linear_vpos", "linear_hpos", "linear_vpos_prev0", "linear_vpos_prev1", "linear_vpos_prev2", "linear_hpos_prev0", "linear_hpos_prev1", "linear_hpos_prev2",
              "linear_vpos_vsync", "linear_display_vpos", "linear_vpos_visible", "current_linear_vpos_visible",
              "display_hsync_counter", "display_vsync_counter", "maxvpos_display_vsync_next", "linetoggle", "lof_display",
              "lof_detect", "lof_pdetect", "lof_detect_vsync", "prevlof0", "prevlof1", "prevlof2",
              "display_last_hsync_low", "display_last_hsync_high", "display_last_vsync_low", "display_last_vsync_high",
              "agnus_vb", "agnus_pvb", "prev_strobe", "dmal_shifter", "dmal", "dmal_next"] + [f"latch{i}" for i in range(20)] + ["plffirstline", "plflastline", "need_vdiw_check", "interlace_seen",
              "programmed_register_accessed_h", "programmed_register_accessed_v", "nosignal_cnt", "nosignal_status", "nosignal_trigger", "lines_after_beamcon_change", "syncs_stopped"] + [f"rga_bltadd{i}" for i in range(4)] + [f"rga_conflictaddr{i}" for i in range(4)]

# The words of COPR, in the order of p6_custom_copper_get (custom.cpp, patch 0095) and of the enum of bridge/state_upstream.cpp.
COPR_WORDS = ["state", "state_prev", "strobe_next", "strobeip", "vblankip", "ignore_next", "vcmp", "hcmp", "strobe", "strobetype", "enabled_thisline",
              "strobe_cycles_low", "strobe_cycles_high", "dma_change_cycle_low", "dma_change_cycle_high"]
# The words of BLTP, in the order of p6_blitter_phase_get (blitter.cpp, patch 0097) and of the enum of bridge/state_upstream.cpp.
BLTP_WORDS = ["queued", "count_done", "change_cycles_low", "change_cycles_high", "delayed_update", "dmacount", "cyclecount", "ahold2", "bhold2", "chold2", "bhold",
              "ddatl", "dptl", "linepixel2", "apt_prev", "bpt_prev", "cpt_prev", "dpt_prev"]
# The words of AUDP, in the order of p6_audio_phase_get (audio.cpp, patch 0099) and of the enums of bridge/state_upstream.cpp: 25 of Paula, then 15 per channel.
AUDP_GLOBAL = ["next_sample_evtime", "pending_cycles_low", "pending_cycles_high", "work_to_do", "led_filter"] + [f"filter{i}_rc{j}" for i in range(4) for j in range(1, 6)]
AUDP_CHANNEL = ["flags", "irqcheck", "per", "dat2", "ptx", "dmaoff_active", "dmaoff_cpu_count", "dmaoff_pc", "minperloop", "current_sample", "last_sample", "new_sample",
                "sample_accum", "sample_accum_time", "adk_mask"]
# The words of GEOM, in the order of p6_frame_geometry_get (custom.cpp, patch 0100, three words more in patch 0103) and of the enum of bridge/state_upstream.cpp.
GEOM_WORDS = ["hpos", "vpos", "hpos_short", "vpos_nom", "hpos_temp", "vpos_temp", "temp_change", "in_width", "in_height", "in_width2", "in_height2", "out_width", "out_height",
              "extra_width", "extra_height", "x_offset", "y_offset", "in_x_offset", "in_y_offset", "refused", "alloc_width", "alloc_height", "display_redraw", "erase_next_draw", "center_y_erase", "locked"]
PERIOD_SENTINEL = -2            # PERIOD_MAX - 1 as an int: the period of a channel audio_reset leaves and nothing has written since


def audp_at(word, channel=None):
    """Byte offset of a word of AUDP: of Paula, or of a channel."""
    return 4 * (AUDP_GLOBAL.index(word) if channel is None else len(AUDP_GLOBAL) + channel * len(AUDP_CHANNEL) + AUDP_CHANNEL.index(word))


# The words of DSKX, in the order of p6_disk_phase_get (disk.cpp, patches 0101 and 0105) and of the enums of bridge/state_upstream.cpp: 5 of the controller, then 9 per drive.
DSKX_GLOBAL = ["hpos", "dsklen2", "dsksync_on", "indexdecay", "select"]
DSKX_DRIVE = ["change_time", "motor_delay", "step_limit", "step_age", "bit_counter", "track_length", "track_speed", "skip_offset", "index_offset"]
CHANGE_MAX = 2 * 50 * 312 + 4   # the longest countdown of a delayed disk change, in lines


def dskx_at(word, drive=None):
    """Byte offset of a word of DSKX: of the controller, or of a drive."""
    return 4 * (DSKX_GLOBAL.index(word) if drive is None else len(DSKX_GLOBAL) + drive * len(DSKX_DRIVE) + DSKX_DRIVE.index(word))


BLT_RUNNING = 2                 # BLTX byte 1: idle, pending, running
COP_WAIT1 = 8                   # COP_wait1 of custom.cpp: the Copper waits for the beam to reach the position of its WAIT

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


# A read that completes: $1900 words of DF0 from unit 2 to the DSKBLK interrupt in unit 12, and from there one of $800 words every three units (the interrupt starts it). ADKCON has
# WORDSYNC cleared and MFMPREC and FAST set, as trackdisk leaves it; the program samples nothing and steps nothing, and the variants of tests/state/test_det02.py replace its loop.
DISK_DMA = """; PUAE6 test program: a disk DMA read that completes, and goes on with another from its DSKBLK interrupt.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$FF,$BFD300.L       ; CIAB DDRB: all outputs
    MOVE.B #$FF,$BFD100.L       ; deselect every drive
    MOVE.B #$7F,$BFD100.L       ; motor on
    MOVE.B #$77,$BFD100.L       ; select DF0: the motor latches on
    MOVE.L #dskblk,$64.L
    MOVE.L #vblank,$6C.L
    MOVE.W #$C022,$DFF09A.L     ; INTENA: vertical blank and disk block
    MOVE.W #$2200,$DFF100.L
    MOVE.W #$2C81,$DFF08E.L
    MOVE.W #$2CC1,$DFF090.L
    MOVEA.L #$30000,A0
    MOVE.W #$2000,SR
    .nops 40000                 ; the medium is in the drive by now
    MOVE.W #$7F00,$DFF09E.L     ; ADKCON: clear WORDSYNC and the rest
    MOVE.W #$9100,$DFF09E.L     ; ADKCON: SET | MFMPREC | FAST, as trackdisk
    MOVE.L #$8000,$DFF020.L     ; DSKPT
    MOVE.W #$9900,$DFF024.L     ; DSKLEN twice: DMA on, a read of $1900 words
    MOVE.W #$9900,$DFF024.L
    MOVE.W #$8210,$DFF096.L     ; DMACON: SET, master and disk
loop:
    ADDQ.L #1,D0
    BRA.S loop
dskblk:
    MOVE.W #$0002,$DFF09C.L     ; INTREQ: clear DSKBLK
    ADDQ.L #1,D2
    MOVE.L #$20000,$DFF020.L    ; DSKPT
    MOVE.W #$8800,$DFF024.L     ; DSKLEN twice: the next transfer, $800 words
    MOVE.W #$8800,$DFF024.L
    RTE
vblank:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVEA.L #$30000,A0          ; the window of DSKBYTR samples starts again
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
    ROMS = {"vsync": ("vsync", None, None), "input_all": ("input_all", None, None), "busy": (BUSY, None, None), "disk-dma": (DISK_DMA, None, None), "copper_irq": ("copper_irq", {"HP": "2"}, None),
            "lof_stuck": ("lof_stuck", None, None), "long_frame": ("long_frame", None, None), "boot": ("boot", None, None)}

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
        bodies = upstream_bodies(blob)
        for tag, names in ORDER.items():
            # DSDn is absent: its length is 0. PIXL, the rows of the picture, is the one body of no fixed length.
            self.assertEqual(sum(8 + (len(bodies[tag][n]) if n == "PIXL" else FIXED.get(n, 0)) for n in names), sizes[tag], tag)

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
                   "disk-early": ("busy", 2, 6, "disk"), "disk-dma-mid": ("disk-dma", 5, 9, "disk"), "disk-dma-end": ("disk-dma", 12, 16, "disk"), "mouse": ("input_all", 4, 8, "mouse"), "copper-wait": ("copper_irq", 2, 6, ())}

    def scenario(self, name):
        rom, n, load, args = self.ROUND_TRIPS[name]
        return rom, n, load, self.args(args) if isinstance(args, str) else args

    def upstream_payload(self, blob):
        """The four chunks of the upstream bodies, with the sub-bodies of P6CH, BEAM included."""
        return {tag: (version, bytes(body)) for tag, version, body in parse_chunks(blob) if tag in ORDER}

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
                self.assertEqual(a, b, "and neither was any other chunk: the whole state is the same bytes, the beam included")
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
                    self.assertEqual(source.read_bytes(), again.read_bytes(), "the whole state is the same bytes, the beam included")
                    for subsystem in ("audio", "scheduler"):
                        self.assertEqual(saved.digests[(n, subsystem)], fresh.after[0][subsystem], subsystem)

    def beam_of(self, blob):
        """{word name: signed value} of the BEAM body of a state."""
        body = upstream_bodies(blob)[b"P6CH"]["BEAM"]
        self.assertEqual(4 * len(BEAM_WORDS), len(body))
        return dict(zip(BEAM_WORDS, struct.unpack(">%di" % len(BEAM_WORDS), body)))

    def test_the_beam_and_the_frame_phase_are_put_back_where_the_state_had_them(self):
        # VPOSR and VHPOSR are what restore_custom throws away. The beam at a boundary moves from one unit to the next (the unit ends after the instruction that crosses the
        # vertical blank), and so do the counters of the frame, so a machine that kept its own beam would give another BEAM body when the state is taken again.
        first, second, again = self.dir / "beam.first", self.dir / "beam.second", self.dir / "beam.again"
        self.probe("vsync", "--units", "7", "--save-at", "3", str(first), "--save-at", "7", str(second), "--load-at", "7", str(first), "--save-at", "7", str(again))
        a, b, c = (self.beam_of(path.read_bytes()) for path in (first, second, again))
        self.assertEqual((4, 4), (a["vpos"], b["vpos"]), "the unit ends in the vertical blank")
        moved = [name for name in BEAM_WORDS if a[name] != b[name]]
        self.assertIn("agnus_hpos", moved, "the beam is not where it was at unit 3: putting it back is something")
        self.assertIn("display_vsync_counter", moved, "nor are the counters of the frame")
        self.assertEqual(a, c, "the BEAM body taken again after the load is the one that was loaded")

    def copper_of(self, blob):
        """{word name: signed value} of the COPR body of a state."""
        body = upstream_bodies(blob)[b"P6CH"]["COPR"]
        self.assertEqual(4 * len(COPR_WORDS), len(body))
        return dict(zip(COPR_WORDS, struct.unpack(">%di" % len(COPR_WORDS), body)))

    def test_a_copper_that_waits_for_the_beam_comes_back_waiting(self):
        # CHSL says "stopped" for every state but the last cycles of a MOVE (its flags are 9 here), so a machine that had only CHSL would have a stopped Copper and never raise
        # the interrupt of the list. COPR is what says the Copper is in COP_wait1 with the compare values of its WAIT, and a new image takes it from the state.
        first, again = self.dir / "copper.first", self.dir / "copper.again"
        self.probe("copper_irq", "--units", "2", "--save-at", "2", str(first))
        waiting = self.copper_of(first.read_bytes())
        self.assertEqual(9, struct.unpack_from(">I", upstream_bodies(first.read_bytes())[b"P6CH"]["CHSL"])[0], "CHSL has no way to say what the Copper waits for")
        self.assertEqual((COP_WAIT1, 4), (waiting["state"], waiting["vcmp"]), "the list of the program waits for line 4")
        self.assertEqual(1, waiting["enabled_thisline"])
        run = self.probe("copper_irq", "--units", "0", "--load-at", "0", str(first), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(waiting, self.copper_of(again.read_bytes()), "a fresh image has the Copper of the state, not a stopped one")

    def paula_of(self, blob):
        """({AUDP word: signed value}, [{AUDP channel word: signed value}] * 4, [AUDn period] * 4) of a state."""
        subs = upstream_bodies(blob)[b"P6AU"]
        words = struct.unpack(">%di" % (len(subs["AUDP"]) // 4), subs["AUDP"])
        self.assertEqual(len(AUDP_GLOBAL) + 4 * len(AUDP_CHANNEL), len(words))
        channels = [dict(zip(AUDP_CHANNEL, words[len(AUDP_GLOBAL) + n * len(AUDP_CHANNEL):])) for n in range(4)]
        return dict(zip(AUDP_GLOBAL, words)), channels, [struct.unpack_from(">H", subs[f"AUD{n}"], 8)[0] for n in range(4)]

    def test_paula_comes_back_with_its_resampler_phase_and_the_period_sentinel_of_a_channel_never_programmed(self):
        # restore_audio rounds the period to colour clocks (PERIOD_MAX - 1 comes back as PERIOD_MAX) and restore_audio_finish restarts last_cycles at the load; the phase of the
        # resampler is in no upstream body. A new image takes all of it from AUDP: the state it saves right after the load is the one it loaded.
        first, again = self.dir / "paula.first", self.dir / "paula.again"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(first))
        paula, channels, periods = self.paula_of(first.read_bytes())
        self.assertEqual([PERIOD_SENTINEL] * 4, [c["per"] for c in channels], "vsync never writes a period")
        self.assertEqual([0] * 4, periods, "AUDn cannot tell the sentinel from PERIOD_MAX")
        phase = struct.unpack(">f", struct.pack(">i", paula["next_sample_evtime"]))[0]
        self.assertTrue(0 < phase <= 3546895 * 512 / 44100 + 1, phase)
        self.assertEqual((0, 0), (paula["pending_cycles_low"], paula["pending_cycles_high"]), "a boundary has integrated Paula up to its tick")
        self.assertNotEqual(0, paula["work_to_do"], "audio was activated in the reset head")
        run = self.probe("vsync", "--units", "0", "--load-at", "0", str(first), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(upstream_bodies(first.read_bytes())[b"P6AU"], upstream_bodies(again.read_bytes())[b"P6AU"], "a fresh image has the Paula of the state")

    def geometry_of(self, blob):
        """{GEOM word name: signed value} of a state."""
        body = upstream_bodies(blob)[b"P6VD"]["GEOM"]
        self.assertEqual(4 * len(GEOM_WORDS), len(body))
        return dict(zip(GEOM_WORDS, struct.unpack(">%di" % len(GEOM_WORDS), body)))

    def test_the_raster_a_machine_has_adopted_is_the_one_a_new_image_loads_and_draws(self):
        # The long frame of lof_stuck (313 lines, 576 rows of picture) is adopted by the engine after a few frames and nothing but GEOM carries it: a fresh image that loads the
        # state used to keep the size of its own reset (574) and switch units later with a blank picture. Now the state it saves right after the load is the one it loaded, and
        # the pictures of the units that follow are the original's, the size and the pixels.
        first, again = self.dir / "geom.first", self.dir / "geom.again"
        saved = self.probe("lof_stuck", "--units", "7", "--every", "1", "--save-at", "4", str(first))
        geometry = self.geometry_of(first.read_bytes())
        self.assertEqual((313, 576, 756), (geometry["vpos"], geometry["out_height"], geometry["out_width"]), "the long frame has been adopted at unit 4")
        self.assertEqual(geometry["out_height"], geometry["in_height"])
        run = self.probe("lof_stuck", "--units", "3", "--every", "1", "--load-at", "0", str(first), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(upstream_bodies(first.read_bytes())[b"P6VD"], upstream_bodies(again.read_bytes())[b"P6VD"], "a fresh image has the raster of the state")
        for unit in (1, 2, 3):
            self.assertEqual(saved.digests[(4 + unit, "video")], run.digests[(unit, "video")], f"the picture of unit {unit} after the load")

    def test_the_border_and_the_pipeline_of_the_first_picture_after_a_load_are_the_originals(self):
        # busy draws a border of COLOR00 = $0123 (written by its Copper list). A new image has the colour table of the state (restore_custom rebuilds it) but drew its first border
        # with the colour its own reset left in bordercolor: black. In NTSC (a long first line) the first pixels also leave from the pipeline of the previous colour clock, which
        # held the border colour in the original and zeros in a new image. The first pixels of the picture, dumped by the probe, are the original's.
        for name, args, unit in (("pal", (), 2), ("ntsc", ("--ntsc",), 2)):
            with self.subTest(name):
                state = self.dir / f"border.{name}"
                saved = self.probe("busy", "--units", str(unit + 1), "--every", "1", "--dump", "video", *args, "--save-at", str(unit), str(state))
                run = self.probe("busy", "--units", "1", "--every", "1", "--dump", "video", *args, "--load-at", "0", str(state))
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(saved.pictures[unit + 1], run.pictures[1], "and so is the whole picture")
                original, loaded = saved.dumps[(unit + 1, "video")], run.dumps[(1, "video")]
                pixels = lambda dump: struct.unpack_from("<%dI" % 64, dump, 9)        # the rows of the buffer, after the flag and the allocation
                self.assertEqual(0x112233, pixels(original)[10], "the border of the original is COLOR00 = $0123")
                self.assertEqual(pixels(original), pixels(loaded), "the first line is the same, pixel for pixel")

    # -- T15j: the rows of the picture, the mode the presentation refused and the vertical window (patch 0103) -----------------------------------------------

    @staticmethod
    def raster_of(blob, width, height):
        """The rows of the PIXL body of a state as the renderer wrote them (width x height XRGB pixels), decoded here from the format of bridge/state_upstream.cpp."""
        body = upstream_bodies(blob)[b"P6VD"]["PIXL"]
        out, at = bytearray(), 0

        def pixel():
            nonlocal at
            (code,) = struct.unpack_from("<H", body, at)
            at += 2
            if code == 0xffff:
                (word,) = struct.unpack_from("<I", body, at)
                at += 4
                return word
            assert code < 0x1000, code
            return ((code >> 8) * 17) << 16 | (((code >> 4) & 15) * 17) << 8 | (code & 15) * 17

        for _ in range(height):
            x = 0
            while x < width:
                token = body[at]
                at += 1
                count = (token & 0x7f) + 1
                if token & 0x80:
                    out += struct.pack("<I", pixel()) * count
                else:
                    for _ in range(count):
                        out += struct.pack("<I", pixel())
                x += count
            assert x == width
        assert at == len(body), "the rows use all the bytes of the body"
        return bytes(out)

    def test_the_rows_a_state_carries_are_the_picture_the_unit_handed_over(self):
        # The state is taken in the boundary where the unit's picture is handed over, so the rows of the body are that picture: its SHA-256 is the one the probe printed (the active raster
        # is the allocation in this profile). Checked on a program that draws (busy: bitplanes, sprites, Copper) and on the long frame, whose odd units draw no line.
        for rom, unit in (("busy", 4), ("long_frame", 1), ("long_frame", 2)):
            with self.subTest(rom=rom, unit=unit):
                path = self.dir / f"pixl.{rom}.{unit}"
                run = self.probe(rom, "--units", str(unit), "--save-at", str(unit), str(path))
                blob = path.read_bytes()
                geometry = self.geometry_of(blob)
                width, height = geometry["alloc_width"], geometry["alloc_height"]
                picture = run.pictures[unit].split()
                self.assertEqual(f"{width}x{height}", picture[0])
                self.assertEqual(picture[-1], hashlib.sha256(self.raster_of(blob, width, height)).hexdigest())

    def test_a_unit_without_lines_hands_over_the_rows_of_the_state_it_was_loaded_from(self):
        # long_frame draws a frame every other unit: the units in between hand over the buffer as it was. Run ahead from unit 1 to 3 and come back: the first unit after the load is the one
        # the original made from unit 1 (the picture of the unit before, not the one of the unit 3 it was abandoned in), and so in a new image, which has drawn nothing yet.
        state = self.dir / "long_frame.1"
        original = self.probe("long_frame", "--units", "6", "--save-at", "1", str(state))
        self.assertNotEqual(original.pictures[1], original.pictures[3], "the picture changes between the unit saved and the one the machine is abandoned in")
        rewound = self.probe("long_frame", "--units", "7", "--save-at", "1", str(self.dir / "long_frame.again"), "--load-at", "3", str(state))
        self.assertEqual([(3, 0, "")], rewound.loads)
        fresh = self.probe("long_frame", "--units", "5", "--load-at", "0", str(state))
        for unit in (2, 3, 4, 5):
            self.assertEqual(original.pictures[unit], rewound.pictures[unit + 2], f"unit {unit} after the rewind")
            self.assertEqual(original.pictures[unit], fresh.pictures[unit - 1], f"unit {unit} in a new image")

    def test_a_raster_the_presentation_refused_is_refused_again_after_the_load(self):
        # From unit 10 the long frame asks for more rows than the presentation buffer has: no unit has a picture, and compute_framesync clamps the sizes afterwards, so GEOM alone looks like a
        # mode that fits. A state taken there says so, and a machine that loads it hands over no picture either, in the same image (that was drawing) and in a new one.
        state, early = self.dir / "long_frame.12", self.dir / "long_frame.3"
        original = self.probe("long_frame", "--units", "16", "--save-at", "12", str(state))
        self.probe("long_frame", "--units", "3", "--save-at", "3", str(early))
        self.assertEqual(1, self.geometry_of(state.read_bytes())["refused"])
        self.assertEqual(0, self.geometry_of(early.read_bytes())["refused"])
        self.assertEqual(["none"] * 4, [original.pictures[unit] for unit in (13, 14, 15, 16)])
        rewound = self.probe("long_frame", "--units", "9", "--save-at", "3", str(self.dir / "long_frame.x"), "--load-at", "5", str(state))      # drawing at 5
        fresh = self.probe("long_frame", "--units", "4", "--load-at", "0", str(state))
        self.assertEqual(["none"] * 4, [rewound.pictures[unit] for unit in (6, 7, 8, 9)])
        self.assertEqual(["none"] * 4, [fresh.pictures[unit] for unit in (1, 2, 3, 4)])

    def test_the_vertical_window_a_program_never_set_is_the_one_the_state_had(self):
        # boot never writes DIWSTRT or DIWSTOP: custom_reset leaves the first and last line of the window at 0 and 0 and the window closed, where calcvdiw (the load runs it) derives 0 and 256
        # from the same registers and opens it in line 0. The state saved in unit 1, loaded and saved again in unit 4 is the one saved in unit 4 without the load, and so is the BEAM body.
        first, rewound, plain = self.dir / "boot.1", self.dir / "boot.4.loaded", self.dir / "boot.4"
        self.probe("boot", "--units", "4", "--save-at", "1", str(first), "--load-at", "1", str(first), "--save-at", "4", str(rewound))
        self.probe("boot", "--units", "4", "--save-at", "4", str(plain))
        beam = self.beam_of(plain.read_bytes())
        self.assertEqual((0, 0), (beam["plffirstline"], beam["plflastline"]), "the lines the reset leaves, which no register derives")
        self.assertEqual(self.beam_of(plain.read_bytes()), self.beam_of(rewound.read_bytes()))
        self.assertEqual(plain.read_bytes(), rewound.read_bytes(), "the whole state after a load and three units is the one of a machine that never loaded")

    def floppy_of(self, blob):
        """({DSKX word: signed value}, [{DSKX drive word: signed value}] * 4, {DISK field: value}, [DSKn CRC] * 4) of a state."""
        subs = upstream_bodies(blob)[b"P6DK"]
        words = struct.unpack(">%di" % (len(subs["DSKX"]) // 4), subs["DSKX"])
        self.assertEqual(len(DSKX_GLOBAL) + 4 * len(DSKX_DRIVE), len(words))
        drives = [dict(zip(DSKX_DRIVE, words[len(DSKX_GLOBAL) + n * len(DSKX_DRIVE):])) for n in range(4)]
        word, bit_offset, sync, hpos, dma, *_ = struct.unpack_from(">HBBBB", subs["DISK"])
        (length,) = struct.unpack_from(">H", subs["DISK"], 15)
        return dict(zip(DSKX_GLOBAL, words)), drives, dict(word=word, bit_offset=bit_offset, sync=sync, hpos=hpos, dma=dma, length=length), \
            [struct.unpack_from(">I", subs[f"DSK{n}"], 12)[0] for n in range(4)]

    def test_a_transfer_in_flight_comes_back_with_the_registers_of_its_controller(self):
        # DISK keeps the length left and the low byte of disk_hpos; the previous DSKLEN (which decides whether one write or two start the next transfer) and the rest of the
        # position in the line are in DSKX, and a new image takes them from there: the state it saves right after the load is the one it loaded.
        first, again = self.dir / "floppy.first", self.dir / "floppy.again"
        self.probe("disk-dma", "--units", "5", *self.disk, "--save-at", "5", str(first))
        controller, drives, disk, crcs = self.floppy_of(first.read_bytes())
        self.assertEqual((2, True), (disk["dma"], 0 < disk["length"] < 0x1900), "a read in flight: DMA state 2 and some of its $1900 words left")
        self.assertEqual(0x9900, controller["dsklen2"], "the last write to DSKLEN, the one that started the transfer")
        self.assertEqual(disk["hpos"], controller["hpos"] & 0xff, "DISK carries the fraction of a colour clock of the position in the line")
        self.assertTrue(0 <= controller["hpos"] < 256 << 8)
        self.assertEqual([0] * 4, [d["change_time"] for d in drives], "no medium is changing")
        self.assertEqual([zlib.crc32(self.adf.read_bytes()), 0, 0, 0], crcs)
        track = upstream_bodies(first.read_bytes())[b"P6DK"]["DSD0"]
        _, bit_counter, track_length, speed, skip, index = struct.unpack_from(">IHIHII", track)
        self.assertEqual((bit_counter, track_length, speed, skip, index), tuple(drives[0][w] for w in DSKX_DRIVE[4:]), "the track under the head is the one DSD0 buffers")
        run = self.probe("disk-dma", "--units", "0", *self.disk, "--load-at", "0", str(first), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(upstream_bodies(first.read_bytes())[b"P6DK"], upstream_bodies(again.read_bytes())[b"P6DK"], "a fresh image has the controller of the state")

    def test_the_transfer_that_ends_leaves_the_previous_dsklen_it_started_with(self):
        # disk_dmafinished clears dsklen and not dsklen2: after the DSKBLK interrupt of unit 12 (which writes $8800 twice) the state says $8800, not the $9900 of the first transfer.
        before = self.floppy_of(self.state_of("floppy-11.state", "disk-dma", 11, *self.disk))
        after = self.floppy_of(self.state_of("floppy-13.state", "disk-dma", 13, *self.disk))
        self.assertEqual((0x9900, 0x8800), (before[0]["dsklen2"], after[0]["dsklen2"]))
        self.assertEqual((2, 2), (before[2]["dma"], after[2]["dma"]), "a transfer is in flight at both boundaries")
        self.assertTrue(0 < before[2]["length"] < 0x1900 and 0 < after[2]["length"] <= 0x800, "the first has a few words left at unit 11 and the second is one of $800 words")

    def test_a_delayed_disk_change_is_a_countdown_in_a_drive_that_holds_nothing(self):
        # The medium goes out in unit 9 and the mount of unit 11 starts the 2 * 50 * 312 lines of the delay (disk_insert_2 -> DISK_check_change): restore_disk zeroes it, DSKX has it.
        change = ("--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--event-at", "9", "0", "-1", "2", "--event-at", "11", "0", "0", "3")
        states = {n: self.floppy_of(self.state_of(f"change-{n}.state", "busy", n, *change)) for n in (8, 10, 12, 13)}
        self.assertEqual([0, 0], [states[n][1][0]["change_time"] for n in (8, 10)], "before the mount of unit 11 nothing counts down")
        self.assertEqual(0, states[10][3][0], "the medium is out in unit 10")
        t12, t13 = states[12][1][0]["change_time"], states[13][1][0]["change_time"]
        self.assertTrue(0 < t13 < t12 <= CHANGE_MAX, (t12, t13))
        self.assertIn(t12 - t13, (312, 313), "a PAL unit is a frame of 312 or 313 lines, and the countdown loses one per line")
        self.assertEqual([0, 0, 0], [d["change_time"] for d in states[12][1][1:]], "only DF0")

    @staticmethod
    def chunk_bodies(blob):
        return {tag: body for tag, _, body in parse_chunks(blob)}

    @classmethod
    def drive_media_at(cls, blob):
        """The offset in the whole state of bridge.drive_media[0]: after the 8 bytes of the audio frame count, four flags and the 8 of the sequence in P6BR."""
        chunks = parse_chunks(blob)
        before = e.HEADER_SIZE + sum(16 + len(body) for _, _, body in chunks[:e.TAGS.index(b"P6BR")])
        assert struct.unpack_from("<i", chunks[e.TAGS.index(b"P6BR")][2], 20)[0] in range(-1, 2)
        return before + 16 + 20

    def assert_loaded_as_saved(self, saved, again, crcs):
        """A save right after a load re-emits the disk chunk (DSKn with the CRC of the medium each drive holds, DSDn, DISK and DSKX) and the bridge chunk (which medium the drives hold)
        byte for byte: the drives of the machine are the state's. `crcs` is what DSKn says for DF0 and DF1."""
        before, after = self.chunk_bodies(saved.read_bytes()), self.chunk_bodies(again.read_bytes())
        for tag in (b"P6DK", b"P6BR"):
            self.assertEqual(before[tag], after[tag], f"{tag.decode()} comes back as it was saved")
        self.assertEqual(list(crcs), self.floppy_of(again.read_bytes())[3][:2], "the drives hold the media the state says")

    def test_a_state_taken_in_a_delayed_change_is_loaded_by_a_machine_that_has_the_medium_in_the_drive(self):
        # The machine is one of the manifest of the state and its DF0 holds the medium the state is waiting for: the load takes it out (the state's drive is empty and counts down) and the
        # countdown goes on from the state's. The state taken right after is the state loaded, and the next units are the original's.
        change = ("--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--event-at", "9", "0", "-1", "2", "--event-at", "11", "0", "0", "3")
        waiting, again = self.dir / "waiting.state", self.dir / "waiting.again"
        original = self.probe("busy", "--units", "14", "--every", "1", *change, "--save-at", "12", str(waiting))
        run = self.probe("busy", "--units", "14", "--every", "1", *self.disk, "--load-at", "12", str(waiting), "--save-at", "12", str(again))
        self.assertEqual([(12, 0, "")], run.loads)
        self.assert_loaded_as_saved(waiting, again, (0, 0))
        self.assertGreater(self.floppy_of(again.read_bytes())[1][0]["change_time"], 0, "the countdown of the state goes on")
        for later in (13, 14):
            self.assertEqual(original.unit(later), run.unit(later), f"unit {later}: the machine is not the original")

    def blitter_of(self, blob):
        """({BLTP word name: signed value}, BLTX state byte) of a state."""
        subs = upstream_bodies(blob)[b"P6CH"]
        self.assertEqual(4 * len(BLTP_WORDS), len(subs["BLTP"]))
        return dict(zip(BLTP_WORDS, struct.unpack(">%di" % len(BLTP_WORDS), subs["BLTP"]))), subs["BLTX"][1]

    def test_a_blit_in_flight_comes_back_in_flight_with_its_phase(self):
        # restore_blitter_new restores a running blit with blit_queued = 4, and the engine keeps 3 while a cycle goes through the pipeline: a machine that had only BLTX would
        # be one cycle ahead in the queue. count_done, the held A word (all ones: the blit's source) and the pointers of a copper write of the same cycle are not in BLTX either.
        flight, again = self.dir / "flight.first", self.dir / "flight.again"
        self.probe("busy", "--units", "2", "--save-at", "2", str(flight))
        phase, state = self.blitter_of(flight.read_bytes())
        self.assertEqual(BLT_RUNNING, state, "BLTX: a blit in flight")
        self.assertEqual((3, 0, 0xffff), (phase["queued"], phase["count_done"], phase["ahold2"]), "the blit is mid-pipeline: one cycle taken from the queue, not counted done, A held")
        self.assertEqual((2, 2), (phase["dmacount"], phase["cyclecount"]), "A and D")
        self.assertNotEqual(0, phase["change_cycles_low"], "the cycle of the last BLTCON write, a time stamp of the saved machine")
        for skip in (0, 3):
            with self.subTest(units_before_the_load=skip):
                run = self.probe("busy", "--units", str(skip), "--load-at", str(skip), str(flight), "--save-at", str(skip), str(again))
                self.assertEqual([(skip, 0, "")], run.loads)
                self.assertEqual(phase, self.blitter_of(again.read_bytes())[0], "a blit in flight comes back with the phase it was saved with")
                self.assertEqual(flight.read_bytes(), again.read_bytes())

    def test_a_blit_the_live_image_has_in_flight_does_not_survive_a_state_without_one(self):
        # The state of unit 4 has the blit done (the blit lasts two units); the image is at unit 2 with its blit in flight. The load does not finish that blit and does not leave it
        # running (it does not drain the pipeline either): the state taken right after is the state loaded, BLTP included, which has the queue empty and the count that says the
        # blit is done.
        idle, live, again = self.dir / "idle.state", self.dir / "live.state", self.dir / "live.again"
        self.probe("busy", "--units", "4", "--save-at", "4", str(idle))
        run = self.probe("busy", "--units", "2", "--save-at", "2", str(live), "--load-at", "2", str(idle), "--save-at", "2", str(again))
        self.assertEqual([(2, 0, "")], run.loads)
        before, was = self.blitter_of(idle.read_bytes())
        in_flight, running = self.blitter_of(live.read_bytes())
        self.assertEqual((0, BLT_RUNNING), (was, running), "the saved machine has no blit and the live one has it running")
        self.assertNotEqual((before["queued"], before["count_done"]), (in_flight["queued"], in_flight["count_done"]), "the two phases differ, or nothing is proven")
        after, now = self.blitter_of(again.read_bytes())
        self.assertEqual((0, before), (now, after), "the blit of the live image is gone and the phase is the one of the state")
        self.assertEqual(idle.read_bytes(), again.read_bytes())

    def test_the_beam_of_a_new_image_in_ntsc_is_the_line_the_state_had(self):
        # NTSC lines alternate between 227 and 228 colour clocks (lol and linetoggle): the loaded machine takes the line length from them, and a state taken again says the same.
        first, again = self.dir / "ntsc.first", self.dir / "ntsc.again"
        self.probe("busy", "--ntsc", "--units", "2", "--save-at", "2", str(first))
        self.probe("busy", "--ntsc", "--units", "0", "--load-at", "0", str(first), "--save-at", "0", str(again))
        self.assertEqual(first.read_bytes(), again.read_bytes())
        self.assertEqual(1, self.beam_of(first.read_bytes())["linetoggle"], "an NTSC machine has the line toggle on")

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
                    attempts.append((f"{name} one byte shorter", with_subs(good, tag, shorter), f"chunk {chunk}: " if name == "PIXL" else f"chunk {chunk}: {name}: "))
                # The chunk of a longer body is longer than the largest the envelope allows when the chunk has no absent track buffer to spare it.
                # PIXL is the end of P6VD, which has the room of the largest picture: its chunk takes the byte and the body itself says the rows do not use it.
                attempts.append((f"{name} one byte longer", with_subs(good, tag, longer), "chunk_length" if tag not in (b"P6DK", b"P6VD") else f"chunk {chunk}: {name}: "))
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
        ("CHSL slot 3 with a pointer of no register", b"P6CH", "CHSL", 28 + 162 + 50, struct.pack(">H", 0x123), "a slot points at a register the engine has no pointer for"),
        ("CHSL with a first offset of 4", b"P6CH", "CHSL", 244, b"\4", "the first slot offset is beyond 3"),
        ("SPR3 with flag bits it never writes", b"P6CH", "SPR3", 24, b"\x08", "unknown bits in the sprite flags"),
        ("AUD2 in state 6", b"P6AU", "AUD2", 0, b"\6", "the state of the channel is beyond 5"),
        ("AUD1 with an interrupt flag of 2", b"P6AU", "AUD1", 2, b"\2", "the interrupt flag is not a flag"),
        ("AUD3 without the marker bit", b"P6AU", "AUD3", 3, b"\0", "the flags of the channel are not the ones save_audio writes"),
        ("AUD0 with an unknown flag", b"P6AU", "AUD0", 3, b"\x84", "the flags of the channel are not the ones save_audio writes"),
        ("BPLX of version 2", b"P6VD", "BPLX", 0, struct.pack(">I", 2), "the version of the body is not 1"),
        ("BEAM on a line beyond the frame", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("vpos"), struct.pack(">i", 313), "the line of the beam is beyond the last one of the standard"),
        ("BEAM on a negative line", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("vpos_prev"), struct.pack(">i", -1), "the line of the beam is beyond the last one of the standard"),
        ("BEAM at a colour clock beyond the longest line", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("agnus_hpos"), struct.pack(">i", 228), "the colour clock of the beam is beyond the longest line"),
        ("BEAM with a pending write of line 2048", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("agnus_vpos_next"), struct.pack(">i", 2048), "a pending beam write is out of range"),
        ("BEAM with a countdown of 3", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("agnus_pos_change"), struct.pack(">i", 3), "a pending beam write is out of range"),
        ("BEAM with a horizontal counter of 17 bits", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("hhpos"), struct.pack(">i", 0x10000), "the horizontal counter is not 16 bits"),
        ("BEAM with a negative linear counter", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("linear_vpos"), struct.pack(">i", -1), "a linear counter is negative"),
        ("BEAM with a display line counter beyond the frame", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("display_hsync_counter"), struct.pack(">i", 314),
         "the display line counter is beyond the frame"),
        ("BEAM with a flag of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("maxvpos_display_vsync_next"), struct.pack(">i", 2), "a flag of the frame phase is not a flag"),
        ("BEAM with a vertical blank latch of 3", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("agnus_vb"), struct.pack(">i", 3), "the vertical blank latch is not off, on or starting"),
        ("BEAM with a strobe that is none of the four", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("prev_strobe"), struct.pack(">i", 0x3d), "the strobe of the last line is not one of the four"),
        ("BEAM with a DMA list of 17 bits", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("dmal"), struct.pack(">i", 0x10000), "the DMA list of the line is not 16 bits"),
        ("BEAM with a sync latch of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("latch7"), struct.pack(">i", 2), "a sync latch is not a flag"),
        ("BEAM with a negative time stamp", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("display_last_vsync_high"), struct.pack(">i", -1), "a time stamp of the display is negative"),
        ("COPR in state 24", b"P6CH", "COPR", 4 * COPR_WORDS.index("state"), struct.pack(">i", 24), "a state of the Copper is not one of its states"),
        ("COPR in a negative state", b"P6CH", "COPR", 4 * COPR_WORDS.index("state"), struct.pack(">i", -1), "a state of the Copper is not one of its states"),
        ("COPR with a previous state of 24", b"P6CH", "COPR", 4 * COPR_WORDS.index("state_prev"), struct.pack(">i", 24), "a state of the Copper is not one of its states"),
        ("COPR with a next strobe state that the strobe never sets", b"P6CH", "COPR", 4 * COPR_WORDS.index("strobe_next"), struct.pack(">i", 9),
         "the state after the strobe is not one the strobe leads to"),
        ("COPR in the last cycles of a MOVE that CHSL does not know", b"P6CH", "COPR", 4 * COPR_WORDS.index("state"), struct.pack(">i", 2),
         "the state of the Copper is not the one CHSL says"),
        ("COPR with an odd strobe pointer", b"P6CH", "COPR", 4 * COPR_WORDS.index("strobeip"), struct.pack(">i", 0x2001), "the strobe pointer is odd"),
        ("COPR with an odd vertical blank pointer", b"P6CH", "COPR", 4 * COPR_WORDS.index("vblankip"), struct.pack(">i", 0x2001), "the pointer latched at the vertical blank is odd"),
        ("COPR with a skip flag of 2", b"P6CH", "COPR", 4 * COPR_WORDS.index("ignore_next"), struct.pack(">i", 2), "the flag that skips the next register write"),
        ("COPR with a line to wait for beyond 255", b"P6CH", "COPR", 4 * COPR_WORDS.index("vcmp"), struct.pack(">i", 256), "a compare value of the Copper is beyond what a WAIT writes"),
        ("COPR with an odd colour clock to wait for", b"P6CH", "COPR", 4 * COPR_WORDS.index("hcmp"), struct.pack(">i", 5), "a compare value of the Copper is beyond what a WAIT writes"),
        ("COPR with a strobe of 4", b"P6CH", "COPR", 4 * COPR_WORDS.index("strobe"), struct.pack(">i", 4), "the strobe of the Copper is out of range"),
        ("COPR with a strobe type of 3", b"P6CH", "COPR", 4 * COPR_WORDS.index("strobetype"), struct.pack(">i", 3), "the strobe of the Copper is out of range"),
        ("COPR with a line flag of 2", b"P6CH", "COPR", 4 * COPR_WORDS.index("enabled_thisline"), struct.pack(">i", 2), "the flag that lets the Copper run this line is not a flag"),
        ("BLTP with a queue of 5", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("queued"), struct.pack(">i", 5), "the blitter queue is deeper than its pipeline"),
        ("BLTP with a negative queue", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("queued"), struct.pack(">i", -1), "the blitter queue is deeper than its pipeline"),
        ("BLTP with a count of cycles after the end of 16", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("count_done"), struct.pack(">i", 16), "the count of cycles after the end of a blit"),
        ("BLTP with a pending flag of 2", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("delayed_update"), struct.pack(">i", 2), "a flag of the blitter is not a flag"),
        ("BLTP with a line pixel flag of 2", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("linepixel2"), struct.pack(">i", 2), "a flag of the blitter is not a flag"),
        ("BLTP with five channels", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("dmacount"), struct.pack(">i", 5), "the channel counts of the blit are beyond"),
        ("BLTP with a blit of one cycle per word", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("cyclecount"), struct.pack(">i", 1), "the channel counts of the blit are beyond"),
        ("BLTP with the channel count of another blit than BLTX's", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("dmacount"), struct.pack(">i", 3), "the channel counts are not the ones BLTX carries"),
        ("BLTP with a held word of 17 bits", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("ahold2"), struct.pack(">i", 0x10000), "a held word of the blitter is wider than 16 bits"),
        ("BLTP with a negative held word", b"P6CH", "BLTP", 4 * BLTP_WORDS.index("ddatl"), struct.pack(">i", -1), "a held word of the blitter is wider than 16 bits"),
        ("AUDP with a resampler phase that is not a number", b"P6AU", "AUDP", audp_at("next_sample_evtime"), struct.pack(">f", float("nan")),
         "the resampler phase is not between two samples"),
        ("AUDP with a resampler phase below -0.5", b"P6AU", "AUDP", audp_at("next_sample_evtime"), struct.pack(">f", -1.0), "the resampler phase is not between two samples"),
        ("AUDP with a resampler phase two samples ahead", b"P6AU", "AUDP", audp_at("next_sample_evtime"), struct.pack(">f", 90000.0),
         "the resampler phase is not between two samples"),
        ("AUDP with negative cycles not integrated", b"P6AU", "AUDP", audp_at("pending_cycles_high"), struct.pack(">i", -1), "the cycles Paula has not integrated are negative"),
        ("AUDP with a negative activity counter", b"P6AU", "AUDP", audp_at("work_to_do"), struct.pack(">i", -1), "the activity counter of the audio is negative"),
        ("AUDP with an LED filter of 2", b"P6AU", "AUDP", audp_at("led_filter"), struct.pack(">i", 2), "the LED filter is not a flag"),
        ("AUDP with an infinite filter stage", b"P6AU", "AUDP", audp_at("filter1_rc3"), struct.pack(">f", float("inf")), "a filter stage is not a finite number"),
        ("AUDP channel 2 with an unknown flag", b"P6AU", "AUDP", audp_at("flags", 2), struct.pack(">i", 32), "unknown bits in the flags of a channel"),
        ("AUDP channel 0 with an interrupt check of 2", b"P6AU", "AUDP", audp_at("irqcheck", 0), struct.pack(">i", 2), "the interrupt check of a channel is not -1, 0 or 1"),
        ("AUDP channel 1 with a DMA-off latch of 2", b"P6AU", "AUDP", audp_at("dmaoff_active", 1), struct.pack(">i", 2), "the DMA-off latch of a channel is not a flag"),
        ("AUDP channel 3 with a dat2 of 17 bits", b"P6AU", "AUDP", audp_at("dat2", 3), struct.pack(">i", 0x10000), "a data latch of a channel is wider than its register"),
        ("AUDP channel 3 with a new sample of 9 bits", b"P6AU", "AUDP", audp_at("new_sample", 3), struct.pack(">i", 0x100), "a data latch of a channel is wider than its register"),
        ("AUDP channel 1 with a partial modulation mask", b"P6AU", "AUDP", audp_at("adk_mask", 1), struct.pack(">i", 0xFF), "the modulation mask of a channel is neither all nor nothing"),
        ("AUDP channel 2 with a period AUDn does not round to", b"P6AU", "AUDP", audp_at("per", 2), struct.pack(">i", 12345 * 512),
         "the exact period of a channel is not the one AUDn rounds"),
        ("DNSP with a genlock pixel number of 9 bits", b"P6VD", "DNSP", 4 * 9, struct.pack(">i", 0x100), "a pixel number of the genlock buffer is wider than a byte"),
        ("DNSP with a negative genlock pixel number", b"P6VD", "DNSP", 4 * 15, struct.pack(">i", -1), "a pixel number of the genlock buffer is wider than a byte"),
        ("GEOM with a negative adopted height", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("vpos"), struct.pack(">i", -1), "a size of the raster is negative or beyond 16 bits"),
        ("GEOM with an output height taller than the buffer", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("out_height"), struct.pack(">i", 627),
         "a height of the picture is negative or taller than the drawing buffer"),
        ("GEOM with an input height taller than the buffer", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("in_height"), struct.pack(">i", 627),
         "a height of the picture is negative or taller than the drawing buffer"),
        ("GEOM with an input width wider than the buffer", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("in_width"), struct.pack(">i", 1825),
         "a width of the picture is negative or wider than the drawing buffer"),
        ("GEOM with a negative input width", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("in_width"), struct.pack(">i", -756), "a width of the picture is negative or wider than the drawing buffer"),
        ("GEOM with an offset beyond 16 bits", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("y_offset"), struct.pack(">i", 0x10000), "an offset of the raster is beyond 16 bits"),
        ("GEOM with a countdown of 3", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("temp_change"), struct.pack(">i", 3), "the countdown of the candidate raster is not between 0 and 2"),
        ("GEOM with a flag of the refused mode of 2", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("refused"), struct.pack(">i", 2), "a flag of the presentation is not a flag"),
        ("GEOM with a request to erase of 2", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("erase_next_draw"), struct.pack(">i", 2), "a flag of the presentation is not a flag"),
        ("GEOM with an empty allocation", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("alloc_width"), struct.pack(">i", 0),
         "the allocation of the drawing buffer is empty or larger than the presentation buffer"),
        ("GEOM with an allocation taller than the buffer", b"P6VD", "GEOM", 4 * GEOM_WORDS.index("alloc_height"), struct.pack(">i", 627),
         "the allocation of the drawing buffer is empty or larger than the presentation buffer"),
        ("BEAM with a line of the vertical window beyond 12 bits", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("plflastline"), struct.pack(">i", 0x1000),
         "a line of the vertical display window is beyond what DIWSTRT, DIWSTOP and DIWHIGH can name"),
        ("BEAM with a negative first line of the vertical window", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("plffirstline"), struct.pack(">i", -1),
         "a line of the vertical display window is beyond what DIWSTRT, DIWSTOP and DIWHIGH can name"),
        ("BEAM with an interlace of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("interlace_seen"), struct.pack(">i", 2), "the interlace the display has seen is not a flag"),
        ("BEAM with a programmed-beam flag of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("programmed_register_accessed_h"), struct.pack(">i", 2), "a flag of the programmed beam registers is not a flag"),
        ("BEAM with a vertical programmed-beam flag of -1", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("programmed_register_accessed_v"), struct.pack(">i", -1), "a flag of the programmed beam registers is not a flag"),
        ("BEAM with a no-signal status of 3", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("nosignal_status"), struct.pack(">i", 3),
         "the no-signal status is not one of -1 (ending), 0, 1 and 2, or its request or the stopped syncs are not flags"),
        ("BEAM with a no-signal status of -2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("nosignal_status"), struct.pack(">i", -2),
         "the no-signal status is not one of -1 (ending), 0, 1 and 2, or its request or the stopped syncs are not flags"),
        ("BEAM with a no-signal request of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("nosignal_trigger"), struct.pack(">i", 2),
         "the no-signal status is not one of -1 (ending), 0, 1 and 2, or its request or the stopped syncs are not flags"),
        ("BEAM with stopped syncs of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("syncs_stopped"), struct.pack(">i", 2),
         "the no-signal status is not one of -1 (ending), 0, 1 and 2, or its request or the stopped syncs are not flags"),
        ("BEAM with a pipeline increment of 4", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("rga_bltadd2"), struct.pack(">i", 4),
         "the pointer increment of a slot of the DMA pipeline is not one the blitter sets (-2, 0 or 2)"),
        ("BEAM with an odd pipeline increment", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("rga_bltadd0"), struct.pack(">i", 1),
         "the pointer increment of a slot of the DMA pipeline is not one the blitter sets (-2, 0 or 2)"),
        ("BEAM with a negative no-signal countdown", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("nosignal_cnt"), struct.pack(">i", -1),
         "a count of vertical blanks of the no-signal state is beyond what the engine sets"),
        ("BEAM with six lines after a beam change", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("lines_after_beamcon_change"), struct.pack(">i", 6),
         "a count of vertical blanks of the no-signal state is beyond what the engine sets"),
        ("BEAM with a test of the vertical window of 2", b"P6CH", "BEAM", 4 * BEAM_WORDS.index("need_vdiw_check"), struct.pack(">i", 2), "the flag that tests the vertical display window is not a flag"),
        ("DSKX with a position beyond a line", b"P6DK", "DSKX", dskx_at("hpos"), struct.pack(">i", 256 << 8), "the position of the floppy controller in its line is not inside a line"),
        ("DSKX with a negative position", b"P6DK", "DSKX", dskx_at("hpos"), struct.pack(">i", -1), "the position of the floppy controller in its line is not inside a line"),
        ("DSKX with a fraction of a colour clock that DISK does not carry", b"P6DK", "DSKX", dskx_at("hpos") + 3, b"\xff", "not the fraction DISK carries"),
        ("DSKX with a previous DSKLEN of 17 bits", b"P6DK", "DSKX", dskx_at("dsklen2"), struct.pack(">i", 0x10000), "the previous DSKLEN is wider than the register"),
        ("DSKX with a wordsync flag of 2", b"P6DK", "DSKX", dskx_at("dsksync_on"), struct.pack(">i", 2), "the wordsync flag is not a flag"),
        ("DSKX with an index decay of 3", b"P6DK", "DSKX", dskx_at("indexdecay"), struct.pack(">i", 3), "the index pulse decay is beyond 2 lines"),
        ("DSKX with a disk change longer than the engine arms", b"P6DK", "DSKX", dskx_at("change_time", 1), struct.pack(">i", CHANGE_MAX + 1), "the delay of a disk change is beyond"),
        ("DSKX with a disk change counting down in a drive that holds a disk", b"P6DK", "DSKX", dskx_at("change_time", 0), struct.pack(">i", 5), "counting down in a drive that is absent or holds a disk"),
        ("DSKX with a disk change counting down in a drive that is not there", b"P6DK", "DSKX", dskx_at("change_time", 2), struct.pack(">i", 5), "counting down in a drive that is absent or holds a disk"),
        ("DSKX with a motor delay of 2", b"P6DK", "DSKX", dskx_at("motor_delay", 1), struct.pack(">i", 2), "the motor delay of a drive is not a flag"),
        ("DSKX with a step limiter of 5 lines", b"P6DK", "DSKX", dskx_at("step_limit", 0), struct.pack(">i", 5), "the step limiter of a drive is beyond four lines"),
        ("DSKX with the age of a step that is not limiting", b"P6DK", "DSKX", dskx_at("step_age", 1), struct.pack(">i", 10), "a drive has the age of a step it is not limiting"),
        ("DSKX with a bit counter of 17 bits", b"P6DK", "DSKX", dskx_at("bit_counter", 1), struct.pack(">i", 0x10000), "the bit counter or the speed of a track is wider"),
        ("DSKX with a track longer than the buffer", b"P6DK", "DSKX", dskx_at("track_length", 1), struct.pack(">i", 0x40001), "the track length is outside the buffer"),
        ("DSKX with a skip position below -1", b"P6DK", "DSKX", dskx_at("skip_offset", 1), struct.pack(">i", -2), "an offset in the track is outside the buffer"),
        ("DSKX with a bit counter that DSD0 does not have", b"P6DK", "DSKX", dskx_at("bit_counter", 0), struct.pack(">i", 0xffff), "the track of a drive is not the one its buffer describes"),
        ("DSKX with the speed of another track than DSD0's", b"P6DK", "DSKX", dskx_at("track_speed", 0), struct.pack(">i", 1), "the track of a drive is not the one its buffer describes"),
        ("BLTX running while BLTP says the blit is done", b"P6CH", "BLTX", 1, b"\2", "BLTX says a blit is running and BLTP says it is done or has nothing queued"),
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

    def test_a_state_taken_with_another_medium_in_a_drive_is_loaded_into_a_machine_that_holds_another(self):
        # The manifest has two media; the state was taken with A in DF0. Whatever the machine holds (B, or A not mounted and DF0 empty) the load puts A in, and the machine goes on
        # as the original did.
        other = self.dir / "b.adf"
        other.write_bytes(hashlib.sha256(b"disk b").digest() * (901120 // 32))
        both = ("--adf", str(self.adf), "--adf", str(other))
        with_a = self.dir / "a-in-df0.state"
        original = self.probe("busy", "--units", "11", "--every", "1", *both, "--event-at", "0", "0", "0", "1", "--save-at", "8", str(with_a))
        for name, args in (("the other disk in the drive", ("--event-at", "0", "0", "1", "1")), ("the disk loaded but not in the drive", ())):
            with self.subTest(name):
                again = self.dir / "a-in-df0.again"
                run = self.probe("busy", "--units", "11", "--every", "1", *both, *args, "--load-at", "8", str(with_a), "--save-at", "8", str(again))
                self.assertEqual([(8, 0, "")], run.loads)
                self.assert_loaded_as_saved(with_a, again, (zlib.crc32(self.adf.read_bytes()), 0))
                for later in range(9, 12):
                    self.assertEqual(original.unit(later), run.unit(later), f"unit {later}: the machine is not the original")

    def test_a_state_taken_with_no_disk_in_a_drive_is_loaded_into_a_machine_that_has_one_in_it(self):
        none, again = self.dir / "df0-empty.state", self.dir / "df0-empty.again"
        original = self.probe("busy", "--units", "11", "--every", "1", "--adf", str(self.adf), "--save-at", "8", str(none))
        run = self.probe("busy", "--units", "11", "--every", "1", *self.disk, "--load-at", "8", str(none), "--save-at", "8", str(again))
        self.assertEqual([(8, 0, "")], run.loads)
        self.assert_loaded_as_saved(none, again, (0, 0))
        for later in range(9, 12):
            self.assertEqual(original.unit(later), run.unit(later), f"unit {later}: the machine is not the original")

    def test_a_state_whose_disk_is_not_the_one_its_manifest_names_is_refused_and_the_machine_is_untouched(self):
        # The identity is the manifest; inside it DSKn carries the CRC of the image in the drive and P6BR says which medium that is. A state in which they disagree was written by no
        # machine of this manifest: it is refused before any drive is touched, whatever the live drives hold.
        other = self.dir / "b.adf"
        other.write_bytes(hashlib.sha256(b"disk b").digest() * (901120 // 32))
        both = ("--adf", str(self.adf), "--adf", str(other), "--event-at", "0", "0", "0", "1")
        good = self.state_of("two-media.state", "busy", 8, *both)
        located = locate(good)
        self.assertEqual(zlib.crc32(self.adf.read_bytes()), struct.unpack_from(">I", upstream_bodies(good)[b"P6DK"]["DSK0"], 12)[0])
        drive_media_at = self.drive_media_at(good)
        self.assertEqual(0, struct.unpack_from("<i", self.chunk_bodies(good)[b"P6BR"], 20)[0], "DF0 holds medium 0")
        wrong = "the CRC of the disk in the drive is not the one of the medium the manifest gives it"
        attempts = [
            ("the CRC of another image in DF0", with_bytes(good, DISK, located["DSK0"] + 8 + 12, struct.pack(">I", zlib.crc32(other.read_bytes()))), wrong),
            ("an empty DF0 in a state whose manifest puts a medium in it", with_bytes(good, DISK, located["DSK0"] + 8 + 12, struct.pack(">I", 0)), wrong),
            ("a medium in DF0 that is not the one P6BR names (B)", with_bytes(good, e.TAGS.index(b"P6BR"), drive_media_at, struct.pack("<i", 1)), wrong),
            ("a medium in DF0 that P6BR says is empty", with_bytes(good, e.TAGS.index(b"P6BR"), drive_media_at, struct.pack("<i", -1)), wrong),
            ("a medium in DF1 that no DSK1 holds", with_bytes(good, e.TAGS.index(b"P6BR"), drive_media_at + 4, struct.pack("<i", 1)), "DSK1: the CRC of the disk in the drive"),
        ]
        # The machine that loads them holds the same medium in DF0 or the other one: nothing is compared with it, and it goes on as if nothing had happened.
        for name, extra in (("the same", both), ("the other", (*both[:4], "--event-at", "0", "0", "1", "1"))):
            with self.subTest(live_df0=name):
                self.refusals([(name, data, 10, text) for name, data, text in attempts], rom="busy", extra=extra, unit=8, units=11)

    def test_a_countdown_in_a_drive_that_the_manifest_says_is_empty_is_refused(self):
        change = ("--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--event-at", "9", "0", "-1", "2", "--event-at", "11", "0", "0", "3")
        waiting = self.state_of("waiting-forged.state", "busy", 12, *change)
        drive_media_at = self.drive_media_at(waiting)
        self.assertEqual(0, struct.unpack_from("<i", self.chunk_bodies(waiting)[b"P6BR"], 20)[0], "DF0 waits for medium 0")
        self.refusals([("a countdown towards no medium", with_bytes(waiting, e.TAGS.index(b"P6BR"), drive_media_at, struct.pack("<i", -1)), 10,
                        "DSK0: a disk change counts down in a drive that the manifest says is empty")], rom="busy", extra=self.disk, unit=12, units=13)

    def test_a_state_of_another_manifest_is_refused_whatever_the_drives_hold(self):
        other = self.dir / "b.adf"
        other.write_bytes(hashlib.sha256(b"disk b").digest() * (901120 // 32))
        a_only = self.state_of("manifest-a.state", "busy", 8, *self.disk)
        # A manifest only grows (T17e): the state of [a] fits [a, b] (tests/state/test_state_media_append.py), not [b, a].
        self.refusals([("a state of a manifest with one medium, in a machine whose manifest does not start with it", a_only, 10, "media_manifest_hash")], rom="busy",
                      extra=["--adf", str(other), "--adf", str(self.adf), "--event-at", "0", "0", "1", "1"], unit=8, units=11)

