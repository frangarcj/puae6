"""Fixtures of the track-level floppy formats (T57), written from the format descriptions alone: nothing binary is committed and nothing is taken from engine output.

* `ext2_image` / `ext1_image` write WinUAE's extended ADF ("UAE-1ADF", "UAE--ADF"; the layouts are in ports/portable/media/trackdisk.h) from a list of tracks.
* `raw_track` makes a raw MFM track: valid MFM (every clock bit follows the rule) of pseudo-random data with ONE $4489 sync word at a known place, which the rule makes
  unique in the stream at every bit alignment (`sync_positions` proves it for the track at hand), so a WORDSYNC read of it starts at a known word.
* `rom_source` is a micro-ROM that reads cylinder 0, head 0 and then head 1 of DF0 by disk DMA with WORDSYNC on $4489, and stores both captures.
"""
import struct

from tests.scenarios.disk import disk

SYNC = disk.SYNC
RAW_WORDS = 6250                      # 100000 bits: a little shorter than a standard revolution (a real raw capture is any length)
RAW_SYNC_AT = 1000                    # the word of the track that is the sync
CAPTURE_WORDS = disk.CAPTURE_WORDS
CAPTURE_BASE = {"raw": 0x10000, "dos": 0x20000}
MARK_BASE, MARK_DONE = disk.MARK_BASE, disk.MARK_DONE
MARKERS = ("raw_read", "dos_read", "end")
SPINUP_UNITS, SETTLE_UNITS, TAIL_UNITS = 26, 4, 4
RUN_UNITS = SPINUP_UNITS + 2 * 27 + SETTLE_UNITS + TAIL_UNITS + 24       # two reads of up to 27 NTSC units, with margin


def _spread(byte):
    """The 8 data bits of a byte at the even bit positions of a 16-bit word (what disk._add_clocks takes)."""
    word = 0
    for k in range(8):
        if byte >> k & 1:
            word |= 1 << (2 * k)
    return word


def mfm_encode(data, prev_bit=0):
    """MFM words of `data` bytes (one word per byte): clock bits by the rule, `prev_bit` the data bit before the first."""
    words, _ = disk._add_clocks([_spread(b) for b in data], prev_bit)
    return words


def sync_positions(words):
    """Bit positions, in the circular stream of the words, where the 16 bits equal the sync word (the shift register of the DMA sees all of them)."""
    bits = []
    for w in words:
        bits += [(w >> (15 - i)) & 1 for i in range(16)]
    n = len(bits)
    pattern = [(SYNC >> (15 - i)) & 1 for i in range(16)]
    return [p for p in range(n) if all(bits[(p + i) % n] == pattern[i] for i in range(16))]


def raw_track(seed, words=RAW_WORDS, sync_at=RAW_SYNC_AT):
    """`words` MFM words with the sync at word `sync_at` (and nowhere else, at any alignment: asserted). The pattern overlaps itself 7 bits on, so a sync can be followed by
    data that makes a second one; the byte after the sync is tried until the stream has one only."""
    state = seed & 0xFFFFFFFF
    data = bytearray()
    for _ in range(words):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        data.append(state >> 24)
    for attempt in range(256):
        data[sync_at + 1] = (data[sync_at + 1] + (1 if attempt else 0)) & 0xFF
        out = mfm_encode(bytes(data))
        out[sync_at] = SYNC
        # The clock bit above the first data bit of the word after the sync depends on the last data bit of the sync (1): encode that word with prev_bit 1.
        out[sync_at + 1] = mfm_encode(bytes([data[sync_at + 1]]), 1)[0]
        if sync_positions(out) == [16 * sync_at]:
            return out
    raise AssertionError("no byte after the sync gives a stream with one sync only")


def words_to_bytes(words):
    return struct.pack(">%dH" % len(words), *words)


def ext2_image(tracks, count=160, revolutions=1):
    """UAE-1ADF. `tracks` is a list of ("dos", 5632 bytes) / ("raw", words, bits) / None (an empty AmigaDOS-less track: type 1 with no bits is refused by the core, so
    None is a blank AmigaDOS track of zeros); the table has `count` entries."""
    table, body = bytearray(), bytearray()
    for i in range(count):
        track = tracks[i] if i < len(tracks) else None
        if track is None:
            track = ("dos", bytes(5632))
        if track[0] == "dos":
            data = bytes(track[1])
            assert len(data) == 5632
            table += struct.pack(">HBBII", 0, revolutions - 1, 0, len(data), 0)
        else:
            _, words, bits = track
            data = words_to_bytes(words)
            assert (bits + 7) // 8 <= len(data)
            table += struct.pack(">HBBII", 0, revolutions - 1, 1, len(data), bits)
        body += data
    return b"UAE-1ADF" + struct.pack(">HH", 0, count) + bytes(table) + bytes(body)


def ext1_image(tracks):
    """UAE--ADF: 160 tracks, ("dos", 5632 bytes) or ("raw", sync, data bytes)."""
    table, body = bytearray(), bytearray()
    for i in range(160):
        track = tracks[i] if i < len(tracks) else ("dos", bytes(5632))
        if track[0] == "dos":
            table += struct.pack(">HH", 0, len(track[1]))
            body += track[1]
        else:
            table += struct.pack(">HH", track[1], len(track[2]))
            body += track[2]
    return b"UAE--ADF" + bytes(table) + bytes(body)


def dos_track(adf, track):
    """The 5632 bytes of the eleven sectors of a track of a plain ADF."""
    return adf[track * 5632:(track + 1) * 5632]


# --- the guest -----------------------------------------------------------------------------------------------------------


def _read(a, name):
    disk._dma_prologue(a, 0x9500)                       # SET | MFMPREC | WORDSYNC | FAST
    a += ["    MOVE.W #$%04X,$DFF07E.L      ; DSKSYNC" % SYNC,
          "    MOVE.L #$%X,$DFF020.L      ; DSKPTH/L: %s capture" % (CAPTURE_BASE[name], name),
          "    MOVE.W #$%04X,$DFF024.L      ; DSKLEN: DMAEN | %d words, twice" % (0x8000 | CAPTURE_WORDS, CAPTURE_WORDS),
          "    MOVE.W #$%04X,$DFF024.L" % (0x8000 | CAPTURE_WORDS)]
    disk._dma_epilogue(a)


def _mark(a, index):
    a += ["    MOVE.W #$%04X,$%X.L          ; marker %d: %s reached" % (MARK_DONE, MARK_BASE + 2 * index, index, MARKERS[index])]


def rom_source():
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/media/trackdisk.py: reads cylinder 0 head 0 and head 1 of DF0 by disk DMA (WORDSYNC on $4489) into Chip RAM.",
         "entry:", "    MOVE.W #$2700,SR", "    LEA $%X.W,A7" % disk.STACK_TOP,
         "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L", "    MOVE.B #$FF,$BFD300.L", "    MOVE.B #$FF,$BFD100.L",
         "    MOVE.L #hdsk,$64.L", "    MOVE.L #hvert,$6C.L",
         "    MOVE.W #$7FFF,$DFF09A.L", "    MOVE.W #$7FFF,$DFF09C.L", "    MOVE.W #$8210,$DFF096.L",
         "    MOVE.W #$C020,$DFF09A.L      ; INTENA: SET | INTEN | VERTB", "    MOVE.W #$2000,SR",
         "    MOVE.B #$7F,$BFD100.L        ; motor on, nothing selected",
         "    MOVE.B #$77,$BFD100.L        ; select DF0, head 0 (bit 2 high)"]
    a += ["    STOP #$2000"] * SPINUP_UNITS
    _read(a, "raw")
    _mark(a, 0)
    a += ["    MOVE.B #$73,$BFD100.L        ; head 1 (bit 2 low): the other side of cylinder 0"]
    a += ["    STOP #$2000"] * SETTLE_UNITS
    _read(a, "dos")
    _mark(a, 1)
    a += ["    STOP #$2000"] * TAIL_UNITS
    _mark(a, 2)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L", "    RTE"]
    return "\n".join(a) + "\n"


def capture(ram, name):
    base = CAPTURE_BASE[name]
    return list(struct.unpack(">%dH" % CAPTURE_WORDS, ram[base:base + 2 * CAPTURE_WORDS]))


def markers(ram):
    return {name: struct.unpack(">H", ram[MARK_BASE + 2 * i:MARK_BASE + 2 * i + 2])[0] == MARK_DONE for i, name in enumerate(MARKERS)}


def sweep_rom_source():
    """A guest that walks the head inward cylinder by cylinder and reads both heads of each with disk DMA (FUZZ01: it makes the engine read every track of a mutated image the
    validator accepted). Four vertical blanks per cylinder, one write of CIA-B port B each (the ring of tests/state/test_det02.py DISK_SEEK): head 0 (a read that waits for
    $4489), head 1 (a read from wherever the head is), step line low, step line high (the step). Run it with the floppy at turbo speed to keep the units few."""
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/media/trackdisk.py: reads head 0 and head 1 of every cylinder, walking inward.",
         "entry:", "    MOVE.W #$2700,SR", "    LEA $400.W,A7",
         "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L", "    MOVE.B #$FF,$BFD300.L", "    MOVE.B #$FF,$BFD100.L",
         "    MOVE.B #$7F,$BFD100.L        ; motor on", "    MOVE.B #$77,$BFD100.L        ; select DF0",
         "    MOVE.L #dskblk,$64.L", "    MOVE.L #h0,$6C.L",
         "    MOVE.W #$C022,$DFF09A.L      ; INTENA: vertical blank and disk block", "    MOVE.W #$8210,$DFF096.L      ; DMACON: SET, master and disk",
         "    .nops 40000                  ; the medium is in the drive by now", "    MOVE.W #$2000,SR",
         "loop:", "    ADDQ.L #1,D0", "    BRA.S loop",
         "dskblk:", "    MOVE.W #$0002,$DFF09C.L", "    ADDQ.L #1,D2", "    RTE"]

    def read(label, nxt, port, sync, pointer):
        a.extend(["%s:" % label, "    MOVE.W #$0020,$DFF09C.L", "    MOVE.B #$%02X,$BFD100.L" % port, "    MOVE.L #%s,$6C.L" % nxt])
        if nxt is not None and pointer:
            a.extend(["    MOVE.W #$7F00,$DFF09E.L", "    MOVE.W #$%04X,$DFF09E.L" % (0x9500 if sync else 0x9100)])
            if sync:
                a.append("    MOVE.W #$4489,$DFF07E.L")
            a.extend(["    MOVE.L #$%X,$DFF020.L" % pointer, "    MOVE.W #$4000,$DFF024.L", "    MOVE.W #$9900,$DFF024.L", "    MOVE.W #$9900,$DFF024.L"])
        a.append("    RTE")

    read("h0", "h1", 0x75, True, 0x30000)          # head 0, step line high (no edge)
    read("h1", "h2", 0x71, False, 0x34000)         # head 1
    read("h2", "h3", 0x70, False, 0)               # step line low
    read("h3", "h0", 0x71, False, 0)               # step line high: one step inward
    return "\n".join(a) + "\n"
