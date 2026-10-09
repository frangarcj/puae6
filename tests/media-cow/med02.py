"""The floppy-writing micro-ROM of MED02 (T27) and the oracle of what it leaves on the disk.

The guest is a trackdisk in miniature: it writes whole AmigaDOS tracks by disk DMA, steps the head away and back so that the next read cannot come
from the track buffer the write left, and reads the tracks back. The words it writes are encoded by the independent MFM model of
tests/scenarios/disk/disk.py (from the AmigaDOS format alone, never from engine output), poked into Chip RAM by the host at unit 1.

Timeline (DF0 holds disk A, writable, from power-on; times in vertical blanks, every DMA wait is a STOP woken by DSKBLK):

    motor on, spin up
    write 1      track 0 (cylinder 0, side 0) <- BUF1                             marker "w1"
    step in, step out (the head leaves cylinder 0 and comes back)
    read 1       track 0 -> CAPTURE c1                                            marker "r1"   (must show BUF1's sectors)
    HOLD         the host takes the state S1 in here (r1 reached, w2a not yet)
    write 2a     track 0 <- BUF2: overwrites what write 1 left                    marker "w2a"
    side 1, write 2b: track 1 (cylinder 0, side 1) <- BUF3                        marker "w2b"
    step in, step out on side 1; read 2: track 1 -> c2                            marker "r2"   (BUF3's sectors)
    side 0; read 3: track 0 -> c3                                                 marker "r3"   (BUF2's sectors)
    tail, end marker

The drive status lines of CIA-A are logged once per unit (LOG_BASE), as in disk.py: /WPRO is bit 3.
"""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.scenarios.disk import disk  # noqa: E402

LABEL_A, LABEL_B = 0x41, 0x42
LABELS = {"buf1": 0x51, "buf2": 0x52, "buf3": 0x53}
BUF_ADDR = {"buf1": 0x40000, "buf2": 0x50000, "buf3": 0x60000}
BUF_TRACK = {"buf1": 0, "buf2": 0, "buf3": 1}          # the track each buffer holds (and is written to)
BUF_WORDS = 6334                                       # a PAL revolution: 12668 bytes (an NTSC one is 12784); 11 * 544 words of sectors and a gap
CAPTURE = {"c1": 0x10000, "c2": 0x20000, "c3": 0x30000}
CAPTURE_WORDS = disk.CAPTURE_WORDS
MARKERS = ("w1", "r1", "w2a", "w2b", "r2", "r3", "end")
SPINUP, SETTLE, STEP_WAIT, HOLD, TAIL = 26, 3, 3, 80, 4
STATE_UNIT = 105                                       # inside HOLD for both standards (r1 is reached by unit ~86 at worst, w2a starts at ~139 at best)
END_UNIT = 300                                         # every marker is reached by unit ~262 at worst


def buffer_bytes(name):
    """The big-endian words the guest writes for `name`: the 11 sectors of its track, then the rest of the revolution as MFM zeros."""
    adf = disk.make_adf(LABELS[name])
    sectors = disk.encode_track(adf, BUF_TRACK[name])
    words = [w for sector in sectors for w in sector]
    words += [0xAAAA] * (BUF_WORDS - len(words))
    assert len(words) == BUF_WORDS
    return struct.pack(">%dH" % BUF_WORDS, *words)


def expected_image(base, writes):
    """`base` with the sectors of the given buffers written over it, in order: what the disk has to hold after those writes."""
    image = bytearray(base)
    for name in writes:
        adf = disk.make_adf(LABELS[name])
        track = BUF_TRACK[name]
        for sector in range(disk.SECTORS):
            at = (track * disk.SECTORS + sector) * disk.SECTOR_BYTES
            image[at:at + disk.SECTOR_BYTES] = disk.adf_sector(adf, track, sector)
    return bytes(image)


def sectors_of(capture, name):
    """The (track, sector) -> payload the capture decodes to, checked against the sectors of buffer `name`: the number that verify and equal it."""
    words = struct.unpack(">%dH" % (len(capture) // 2), capture)
    adf = disk.make_adf(LABELS[name])
    found = disk.decode_capture(words)
    return sum(1 for (track, sector), payload in found.items() if track == BUF_TRACK[name] and payload == disk.adf_sector(adf, track, sector)), len(found)


def marker_words(ram):
    """Which markers the 16-bit words at MARK_BASE show as reached."""
    values = struct.unpack(">%dH" % len(MARKERS), ram[:2 * len(MARKERS)])
    return [name for name, v in zip(MARKERS, values) if v == disk.MARK_DONE]


def _mark(a, index):
    a += ["    MOVE.W #$%04X,$%X.L          ; marker %d: %s reached" % (disk.MARK_DONE, disk.MARK_BASE + 2 * index, index, MARKERS[index])]


def _write(a, name):
    disk._dma_prologue(a, 0x9100)                        # SET | MFMPREC | FAST, no WORDSYNC: the transfer starts at once
    a += ["    MOVE.L #$%X,$DFF020.L      ; DSKPTH/L: %s" % (BUF_ADDR[name] if name in BUF_ADDR else RAW_ADDR, name),
          "    MOVE.W #$%04X,$DFF024.L      ; DSKLEN: DMAEN | WRITE | %d words, twice" % (0xC000 | BUF_WORDS, BUF_WORDS),
          "    MOVE.W #$%04X,$DFF024.L" % (0xC000 | BUF_WORDS)]
    disk._dma_epilogue(a)


def _read(a, name, sync=disk.SYNC, words=CAPTURE_WORDS):
    disk._dma_prologue(a, 0x9500)                        # SET | MFMPREC | WORDSYNC | FAST
    a += ["    MOVE.W #$%04X,$DFF07E.L      ; DSKSYNC" % sync,
          "    MOVE.L #$%X,$DFF020.L      ; DSKPTH/L: capture %s" % (CAPTURE[name], name),
          "    MOVE.W #$%04X,$DFF024.L      ; DSKLEN: DMAEN | %d words, twice" % (0x8000 | words, words),
          "    MOVE.W #$%04X,$DFF024.L" % (0x8000 | words)]
    disk._dma_epilogue(a)


def _step(a, slot, side):
    """The head leaves cylinder 0 and returns: an inward pulse, a wait, an outward pulse, a wait. PRB: motor on, DF0 selected, side `side`."""
    inward = (0x74, 0x75) if side == 0 else (0x70, 0x71)
    outward = (0x76, 0x77) if side == 0 else (0x72, 0x73)
    for pulse in (inward, outward):
        a += ["    MOVE.B #$%02X,$BFD100.L        ; /STEP 1 -> 0" % pulse[0], "    MOVE.B #$%02X,$BFD100.L        ; /STEP 0 -> 1: the step" % pulse[1]]
        slot = disk._wait(a, STEP_WAIT, slot)
    return slot


def rom_source():
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/media-cow/med02.py: writes, overwrites and reads back whole AmigaDOS tracks of DF0 by disk DMA (MED02).",
         "entry:", "    MOVE.W #$2700,SR", "    LEA $%X.W,A7" % disk.STACK_TOP,
         "    MOVE.B #3,$BFE201.L          ; CIA-A DDRA: OVL and the power LED are outputs, the drive status bits inputs",
         "    MOVE.B #2,$BFE001.L          ; overlay off",
         "    MOVE.B #$FF,$BFD300.L        ; CIA-B DDRB: all outputs (drive control)",
         "    MOVE.B #$FF,$BFD100.L        ; PRB: nothing selected, motor off, side 0, direction out, /STEP high",
         "    MOVE.L #hdsk,$64.L           ; level 1 autovector: DSKBLK",
         "    MOVE.L #hvert,$6C.L          ; level 3 autovector: VERTB",
         "    MOVE.W #$7FFF,$DFF09A.L      ; INTENA: all off", "    MOVE.W #$7FFF,$DFF09C.L      ; INTREQ: all clear",
         "    MOVE.W #$8210,$DFF096.L      ; DMACON: SET | DMAEN | DSKEN",
         "    MOVE.W #$C020,$DFF09A.L      ; INTENA: SET | INTEN | VERTB", "    MOVE.W #$2000,SR",
         "    MOVE.B #$7F,$BFD100.L        ; motor on (bit 7 low), nothing selected",
         "    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge"]
    slot = disk._wait(a, SPINUP, 0)
    _write(a, "buf1")
    _mark(a, 0)
    slot = disk._wait(a, SETTLE, slot)
    slot = _step(a, slot, 0)
    _read(a, "c1")
    _mark(a, 1)
    slot = disk._wait(a, HOLD, slot)
    _write(a, "buf2")
    _mark(a, 2)
    a += ["    MOVE.B #$73,$BFD100.L        ; side 1 (bit 2 low), direction out, /STEP high"]
    slot = disk._wait(a, SETTLE, slot)
    _write(a, "buf3")
    _mark(a, 3)
    slot = disk._wait(a, SETTLE, slot)
    slot = _step(a, slot, 1)
    _read(a, "c2")
    _mark(a, 4)
    a += ["    MOVE.B #$77,$BFD100.L        ; side 0 again"]
    slot = disk._wait(a, SETTLE, slot)
    _read(a, "c3")
    _mark(a, 5)
    slot = disk._wait(a, TAIL, slot)
    assert disk.LOG_BASE + slot <= disk.MARK_BASE
    _mark(a, 6)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L      ; INTREQ: clear VERTB", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK", "    RTE"]
    return "\n".join(a) + "\n"


# --- A custom-format track (not AmigaDOS): the second guest ---------------------------------------------------------------------------------------------------

RAW_ADDR = 0x70000
RAW_SYNC = 0x8914                                      # a sync word no AmigaDOS track has
RAW_SYNC_AT = 100                                      # index in the written revolution of the sync word; the pattern follows it
RAW_PATTERN_WORDS = 2000                               # what the guest reads back after the sync
CAPTURE["r1"], CAPTURE["r2"] = 0x10000, 0x20000
RAW_SPINUP, RAW_HOLD1, RAW_HOLD2 = 26, 60, 60
RAW_STATE1, RAW_STATE2, RAW_END = 50, 150, 290         # S1: before any write; S2: the raw track written and read, the AmigaDOS overwrite not yet; both inside a hold, in both standards
RAW_MARKERS = ("raw_w", "raw_r", "ad_w", "ad_r", "end")


def raw_pattern():
    """The words that follow the sync in the raw track: not a sector, and without the sync word."""
    words = [(i * 0x9E37 + 0x1234) & 0xFFFF for i in range(RAW_PATTERN_WORDS)]
    assert RAW_SYNC not in words and disk.SYNC not in words
    return words


def raw_buffer_bytes():
    """A revolution of words: MFM zeros, then the sync and the pattern, then zeros: what the guest writes with no regard for the AmigaDOS format."""
    words = [0xAAAA] * BUF_WORDS
    words[RAW_SYNC_AT] = RAW_SYNC
    words[RAW_SYNC_AT + 1:RAW_SYNC_AT + 1 + RAW_PATTERN_WORDS] = raw_pattern()
    return struct.pack(">%dH" % BUF_WORDS, *words)


def _raw_mark(a, index):
    a += ["    MOVE.W #$%04X,$%X.L          ; marker %d: %s reached" % (disk.MARK_DONE, disk.MARK_BASE + 2 * index, index, RAW_MARKERS[index])]


def raw_markers(ram):
    values = struct.unpack(">%dH" % len(RAW_MARKERS), ram[:2 * len(RAW_MARKERS)])
    return [name for name, v in zip(RAW_MARKERS, values) if v == disk.MARK_DONE]


def raw_rom_source():
    """Spins up, waits (S1), writes the custom track and reads it back after the head left (r1, S2 in the hold that follows), then writes track 0 as AmigaDOS (buf1) over it and reads that."""
    a = rom_source().split("    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge\n")[0]
    a = a.splitlines()
    a += ["    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge"]
    slot = disk._wait(a, RAW_SPINUP, 0)
    slot = disk._wait(a, RAW_HOLD1, slot)
    _write(a, "raw")
    _raw_mark(a, 0)
    slot = disk._wait(a, SETTLE, slot)
    slot = _step(a, slot, 0)
    _read(a, "r1", RAW_SYNC, RAW_PATTERN_WORDS)
    _raw_mark(a, 1)
    slot = disk._wait(a, RAW_HOLD2, slot)
    _write(a, "buf1")
    _raw_mark(a, 2)
    slot = disk._wait(a, SETTLE, slot)
    slot = _step(a, slot, 0)
    _read(a, "r2")
    _raw_mark(a, 3)
    slot = disk._wait(a, TAIL, slot)
    assert disk.LOG_BASE + slot <= disk.MARK_BASE
    _raw_mark(a, 4)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L      ; INTREQ: clear VERTB", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK", "    RTE"]
    return "\n".join(a) + "\n"


def same_raw_read(capture, pattern_bytes):
    """None when `capture` (the words the guest read after the sync) is the pattern the guest wrote, the index of the word where the stream slipped when it is the pattern
    except for a bit slip, False when it is neither. The drive jitters when the read passes the index hole (disk.cpp floppy_random_bits 1..3, the engine's emulation of a
    real drive's bit slip): from some bit of some word on, the stream is the pattern shifted left by that many bits."""
    n = min(len(capture), len(pattern_bytes))
    if capture[:n] == pattern_bytes[:n]:
        return None
    k = next(i for i in range(0, n, 2) if capture[i:i + 2] != pattern_bytes[i:i + 2])
    got = "".join(format(b, "08b") for b in capture[k:n])
    want = "".join(format(b, "08b") for b in pattern_bytes[k:n])
    for slip in range(16):
        if got[:slip] != want[:slip]:
            break
        for jitter in (1, 2, 3):
            # from bit `slip` on, capture bit i is pattern bit i + jitter (the last word is left out: the DMA ends in it)
            if got[slip:len(got) - 16] == want[slip + jitter:len(want) - 16 + jitter]:
                return k // 2
    return False
