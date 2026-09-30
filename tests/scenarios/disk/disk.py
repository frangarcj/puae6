"""Floppy micro-ROM and the AmigaDOS MFM oracle for the read-only media scenario (T12c).

Nothing binary is committed: `make_adf` writes an original 880 KiB pattern (a raw sector pattern, not a file system) and
`rom_source` writes assembly for tests/fixtures/microrom/build_rom.py. The guest is a straight-line script without compares
or branches; time passes with `STOP #$2000` (one vertical blank each), so the script is fully described by its counts.

Guest timeline (DF0 holds disk A from power-on; the host mounts disk B at MOUNT_UNIT):

    motor on, SPINUP_UNITS waits          PRA is logged after every wait, one byte per unit, from LOG_BASE
    read 1: track 0 into CAPTURE_BASE["boot"]       (WORDSYNC DMA on $4489, 0x1900 words, completion marker)
    HOLD_UNITS waits                      the host swaps the disk here: /CHNG falls, the drive is empty, the disk comes back
    step pulse (1 -> 0 -> 1 on /STEP)     with the disk back this acknowledges /CHNG; the head stays on track 0
    SETTLE_UNITS waits
    read 2: track 0 into CAPTURE_BASE["swapped"]    (must show disk B)
    write attempt: WRITE_WORDS words from WRITE_BUFFER, no WORDSYNC (the drive is write protected)
    read 3: track 0 into CAPTURE_BASE["readback"]   (must still show disk B, unchanged by the write)
    TAIL_UNITS waits, end marker, then it idles

Hardware facts used, from the Amiga Hardware Reference Manual and independent of the engine: CIA-A PRA bits 2..5 are the
drive status lines /CHNG, /WPRO, /TK0 and /RDY (active low); CIA-B PRB bits 7..0 are /MTR, /SEL3../SEL0, /SIDE, DIR, /STEP;
a step is the 0 -> 1 edge of /STEP; DSKLEN needs DMAEN written twice; the track is AmigaDOS trackdisk MFM (below).
Facts that come from the engine and not from the hardware are marked as such (the swap delay).

AmigaDOS sector, 544 words = 1088 bytes on disk:
    2 words   the two 0x00 bytes as MFM (0xAAAA, or 0x2AAA when the previous data bit was 1)
    2 words   0x4489 0x4489 (an A1 byte with a missing clock bit: the sync)
    header    info long 0xFF, track, sector, sectors-until-gap  -> odd long, even long
    label     16 bytes (zero) -> 4 odd longs, then 4 even longs
    header checksum  -> odd long, even long   (XOR of the 10 encoded header longs, & 0x55555555)
    data checksum    -> odd long, even long   (XOR of the 256 encoded data longs, & 0x55555555)
    data      512 bytes -> 128 odd longs, then 128 even longs
    odd long = (v >> 1) & 0x55555555, even long = v & 0x55555555
MFM: between two data bits a clock bit is 1 exactly when both neighbouring data bits are 0.
"""
from collections import namedtuple
import struct

SECTORS = 11
TRACKS = 160
SECTOR_BYTES = 512
ADF_BYTES = TRACKS * SECTORS * SECTOR_BYTES         # 901120
SYNC = 0x4489
SECTOR_WORDS = 544
BODY_WORDS = 540                                     # after the syncs: 4 + 16 + 4 + 4 + 512
CHIP_RAM_BYTES = 512 * 1024

# Chip RAM layout of the guest (the probe dumps Chip RAM after the run).
STACK_TOP = 0x400                                    # the vector table is below; the stack grows down from here
LOG_BASE = 0x2000                                    # PRA bytes, one per vertical-blank wait (LOG_SLOTS of them)
MARK_BASE = 0x3000                                   # one word per finished phase (MARKERS), MARK_DONE when reached
CAPTURE_WORDS = 0x1900                               # DSKLEN length of a track read: 6400 words = 12800 bytes
CAPTURE_BASE = {"boot": 0x10000, "swapped": 0x20000, "readback": 0x30000}
WRITE_BUFFER = 0x40000                               # source of the write attempt
WRITE_WORDS = 1024
WRITE_PATTERN = b"WRITE-ATTEMPT:NOT-TO-BE-KEPT-ON-"  # 32 bytes, repeated over the write buffer
MARKERS = ("boot_read", "swapped_read", "write", "readback_read", "end")
MARK_DONE = 0xD15C

# Timeline, in vertical blanks (units). Every wait logs one PRA byte; a DMA wait (DSKBLK) does not.
SPINUP_UNITS = 26                                    # 18 * 312 + <512 hsyncs after motor-on: up to 19.6 PAL / 23.4 NTSC units
HOLD_UNITS = 210                                     # covers eject + the swap delay + spin-up in NTSC with margin
SETTLE_UNITS = 4
TAIL_UNITS = 4
SLOT_HOLD = SPINUP_UNITS                             # first log slot after read 1
SLOT_SETTLE = SLOT_HOLD + HOLD_UNITS                 # first log slot after the step pulse
SLOT_TAIL = SLOT_SETTLE + SETTLE_UNITS               # first log slot after read 3
LOG_SLOTS = SLOT_TAIL + TAIL_UNITS
MOUNT_UNIT = 80                                      # the host mounts disk B before this unit; read 1 has ended by ~unit 52
RUN_UNITS = 340                                      # waits (244) + three reads of up to 25 NTSC units + the write: 322 at worst

# The engine (not the hardware) keeps a drive empty for 2 * 50 * 312 hsyncs after an eject before the new disk shows up
# (WinUAE DISK_check_change); /RDY then needs 18 * 312 + (0..511) hsyncs of spin-up (drive_insert / DISK_hsync).
EJECT_TO_INSERT_HSYNCS = 2 * 50 * 312
SPINUP_HSYNCS = (18 * 312, 18 * 312 + 511)
LINES_PER_UNIT = {"pal": 312, "ntsc": 262}

DriveState = namedtuple("DriveState", "changed write_protected track0 ready")
Found = namedtuple("Found", "at fmt track sector gap payload header_ok data_ok")


def make_adf(label):
    """The 901120 bytes of disk `label`: sector byte i is (label*37 + track*13 + sector*7 + i*3 + (i >> 8)*11) & 255."""
    base = bytes((i * 3 + (i >> 8) * 11) & 0xFF for i in range(SECTOR_BYTES))
    out = bytearray()
    for track in range(TRACKS):
        for sector in range(SECTORS):
            shift = (label * 37 + track * 13 + sector * 7) & 0xFF
            out += base.translate(bytes((v + shift) & 0xFF for v in range(256)))
    return bytes(out)


def adf_sector(adf, track, sector):
    """The 512 payload bytes of a sector of an ADF."""
    offset = (track * SECTORS + sector) * SECTOR_BYTES
    return adf[offset:offset + SECTOR_BYTES]


# --- The MFM oracle -----------------------------------------------------------------------------------------------------


def _odd(v):
    return (v >> 1) & 0x55555555


def _even(v):
    return v & 0x55555555


def _combine(odd, even):
    return ((odd & 0x55555555) << 1) | (even & 0x55555555)


def _longs_to_words(longs):
    words = []
    for v in longs:
        words += [(v >> 16) & 0xFFFF, v & 0xFFFF]
    return words


def _add_clocks(data_words, prev_bit):
    """Insert clock bits into words that hold data bits at the even positions (mask 0x5555).

    The stream is MSB first: clock, data, clock, data ... A clock bit sits above the data bit it precedes and is 1 when that
    data bit and the previous one are both 0; `prev_bit` is the data bit before the first word. Returns (words, last bit)."""
    out = []
    for word in data_words:
        data = word & 0x5555
        raw = data
        for k in range(8):                           # data bit k is raw bit 2k, its clock is raw bit 2k + 1
            current = (data >> (2 * k)) & 1
            previous = (data >> (2 * k + 2)) & 1 if k < 7 else prev_bit
            if not (previous or current):
                raw |= 1 << (2 * k + 1)
        out.append(raw)
        prev_bit = data & 1
    return out, prev_bit


def _sector(adf, track, sector, prev_bit):
    """(544 words, last data bit) of a sector encoded from the ADF bytes."""
    data = adf_sector(adf, track, sector)
    info = (0xFF << 24) | (track << 16) | (sector << 8) | (SECTORS - sector)
    header = [_odd(info), _even(info)] + [0] * 8     # then the label: 4 odd longs and 4 even longs, all zero
    header_sum = 0
    for v in header:
        header_sum ^= v
    longs = [int.from_bytes(data[i:i + 4], "big") for i in range(0, SECTOR_BYTES, 4)]
    payload = [_odd(v) for v in longs] + [_even(v) for v in longs]
    data_sum = 0
    for v in payload:
        data_sum ^= v
    body = header + [_odd(header_sum), _even(header_sum), _odd(data_sum), _even(data_sum)] + payload
    lead, _ = _add_clocks([0x0000, 0x0000], prev_bit)
    tail, last = _add_clocks(_longs_to_words(body), 1)   # the last data bit of the sync is 1
    return lead + [SYNC, SYNC] + tail, last


def mfm_words_for_sector(adf, track, sector, prev_bit=0):
    """The 544 MFM words of one sector; `prev_bit` (the data bit before it) only changes the first two words."""
    return _sector(adf, track, sector, prev_bit)[0]


def encode_track(adf, track):
    """The 11 sectors of a track, each a list of 544 words, chained as on the disk (no gap between sectors)."""
    sectors, prev_bit = [], 0
    for sector in range(SECTORS):
        words, prev_bit = _sector(adf, track, sector, prev_bit)
        sectors.append(words)
    return sectors


def encode_track0(adf):
    """Track 0 as 11 lists of 544 words: what a full, aligned revolution of DF0 has to contain."""
    return encode_track(adf, 0)


def find_sectors(words):
    """Every complete sector in a captured word stream, verified or not.

    A sector starts at a run of one or more $4489 words (the DMA does not transfer the sync that matched, and the drive's
    jitter can swallow the first one after the gap). The 540 words after the run are decoded as documented; `at` is the
    index of the first of them. A sector cut by the end of the capture is skipped."""
    found = []
    i, n = 0, len(words)
    while i < n:
        if words[i] != SYNC:
            i += 1
            continue
        while i < n and words[i] == SYNC:
            i += 1
        p = i
        if p + BODY_WORDS > n:
            break

        def long_at(k):
            return (words[p + k] << 16) | words[p + k + 1]

        info = _combine(long_at(0), long_at(2))
        header_raw = 0
        for k in range(0, 20, 2):
            header_raw ^= long_at(k) & 0x55555555
        header_sum = _combine(long_at(20), long_at(22))
        data_sum = _combine(long_at(24), long_at(26))
        base = p + 28
        odd_longs = [(words[base + 2 * k] << 16) | words[base + 2 * k + 1] for k in range(128)]
        even_longs = [(words[base + 256 + 2 * k] << 16) | words[base + 256 + 2 * k + 1] for k in range(128)]
        data_raw = 0
        for v in odd_longs + even_longs:
            data_raw ^= v & 0x55555555
        payload = b"".join(_combine(o, e).to_bytes(4, "big") for o, e in zip(odd_longs, even_longs))
        found.append(Found(p, (info >> 24) & 255, (info >> 16) & 255, (info >> 8) & 255, info & 255, payload,
                           header_raw == header_sum, data_raw == data_sum))
        i = base + 512
    return found


def decode_capture(words):
    """{(track, sector): 512 payload bytes} of every sector whose header and data checksums and header fields verify.

    A sector with a bad checksum, format byte other than 0xFF, an out-of-range track or sector, or a wrong
    sectors-until-gap count is left out. The same sector twice with different payloads is a ValueError."""
    result = {}
    for s in find_sectors(words):
        if not (s.header_ok and s.data_ok and s.fmt == 0xFF and s.track < TRACKS and s.sector < SECTORS
                and s.gap == SECTORS - s.sector):
            continue
        key = (s.track, s.sector)
        if result.setdefault(key, s.payload) != s.payload:
            raise ValueError("sector %s captured twice with different data" % (key,))
    return result


def body_words(words, found):
    """The 540 captured words of a `Found` sector (after its syncs), for comparing with the model word for word."""
    return words[found.at:found.at + BODY_WORDS]


def rotate(words, start, count):
    """`count` words of a circular track starting at `start`: what a read that begins mid-track captures."""
    return [words[(start + i) % len(words)] for i in range(count)]


# --- The guest program --------------------------------------------------------------------------------------------------


def _log_pra(a, slot):
    """One PRA byte into the log: MOVE.B $BFE001.L,LOG_BASE+slot.L (opcode $13F9), spelt with the directives."""
    a += ["    .word $13F9                  ; MOVE.B $BFE001.L,$%X.L: CIA-A PRA into log slot %d" % (LOG_BASE + slot, slot),
          "    .long $BFE001,$%X" % (LOG_BASE + slot)]


def _wait(a, units, slot):
    """`units` vertical blanks, logging PRA after each; returns the next free slot."""
    for _ in range(units):
        a += ["    STOP #$2000"]
        _log_pra(a, slot)
        slot += 1
    return slot


def _mark(a, index):
    a += ["    MOVE.W #$%04X,$%X.L          ; marker %d: %s reached" % (MARK_DONE, MARK_BASE + 2 * index, index, MARKERS[index])]


def _dma_prologue(a, adkcon):
    """Only DSKBLK may wake the STOP that follows: VERTB is masked, the request cleared, ADKCON set for this transfer."""
    a += ["    MOVE.W #$0020,$DFF09A.L      ; INTENA: VERTB off", "    MOVE.W #$8002,$DFF09A.L      ; INTENA: SET | DSKBLK",
          "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK",
          "    MOVE.W #$7FFF,$DFF09E.L      ; ADKCON: clear all", "    MOVE.W #$%04X,$DFF09E.L      ; ADKCON: SET | ..." % adkcon]


def _dma_epilogue(a):
    a += ["    STOP #$2000                  ; wakes on DSKBLK, the handler clears it",
          "    MOVE.W #$0002,$DFF09A.L      ; INTENA: DSKBLK off", "    MOVE.W #$8020,$DFF09A.L      ; INTENA: SET | VERTB"]


def _read_track(a, name):
    _dma_prologue(a, 0x9500)                        # SET | MFMPREC | WORDSYNC | FAST
    a += ["    MOVE.W #$%04X,$DFF07E.L      ; DSKSYNC" % SYNC,
          "    MOVE.L #$%X,$DFF020.L      ; DSKPTH/L: %s capture" % (CAPTURE_BASE[name], name),
          "    MOVE.W #$%04X,$DFF024.L      ; DSKLEN: DMAEN | %d words, twice" % (0x8000 | CAPTURE_WORDS, CAPTURE_WORDS),
          "    MOVE.W #$%04X,$DFF024.L" % (0x8000 | CAPTURE_WORDS)]
    _dma_epilogue(a)


def _write_attempt(a):
    _dma_prologue(a, 0x9100)                        # SET | MFMPREC | FAST, no WORDSYNC: the transfer starts at once
    a += ["    MOVE.L #$%X,$DFF020.L      ; DSKPTH/L: the write buffer" % WRITE_BUFFER,
          "    MOVE.W #$%04X,$DFF024.L      ; DSKLEN: DMAEN | WRITE | %d words, twice" % (0xC000 | WRITE_WORDS, WRITE_WORDS),
          "    MOVE.W #$%04X,$DFF024.L" % (0xC000 | WRITE_WORDS)]
    _dma_epilogue(a)


def rom_source():
    """Assembly of the whole scenario; it takes no parameters, every count is a module constant."""
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/scenarios/disk/disk.py: reads track 0 of DF0 (boot disk, swapped disk, read-back after a write",
         "; attempt on a write-protected disk) into Chip RAM and logs the drive status lines of CIA-A once per unit.",
         "entry:", "    MOVE.W #$2700,SR", "    LEA $%X.W,A7" % STACK_TOP,
         "    MOVE.B #3,$BFE201.L          ; CIA-A DDRA: OVL and the power LED are outputs, the drive status bits inputs",
         "    MOVE.B #2,$BFE001.L          ; overlay off",
         "    MOVE.B #$FF,$BFD300.L        ; CIA-B DDRB: all outputs (drive control)",
         "    MOVE.B #$FF,$BFD100.L        ; PRB: nothing selected, motor off, side 0, direction out, /STEP high",
         "    MOVE.L #hdsk,$64.L           ; level 1 autovector: DSKBLK",
         "    MOVE.L #hvert,$6C.L          ; level 3 autovector: VERTB",
         "    MOVE.W #$7FFF,$DFF09A.L      ; INTENA: all off", "    MOVE.W #$7FFF,$DFF09C.L      ; INTREQ: all clear",
         "    MOVE.W #$8210,$DFF096.L      ; DMACON: SET | DMAEN | DSKEN"]
    # The write buffer holds a pattern that is not a sector, so a write that reached the disk could not go unnoticed.
    longs = struct.unpack(">8L", WRITE_PATTERN)
    a += ["    MOVE.L #$%08X,D%d" % (v, i) for i, v in enumerate(longs)]
    a += ["    MOVEA.L #$%X,A0             ; fill the write buffer downwards, 32 bytes per MOVEM" % (WRITE_BUFFER + 2 * WRITE_WORDS)]
    a += ["    MOVEM.L D0-D7,-(A0)"] * (2 * WRITE_WORDS // 32)
    a += ["    MOVE.W #$C020,$DFF09A.L      ; INTENA: SET | INTEN | VERTB", "    MOVE.W #$2000,SR",
          "    MOVE.B #$7F,$BFD100.L        ; motor on (bit 7 low), nothing selected",
          "    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge"]
    slot = _wait(a, SPINUP_UNITS, 0)
    _read_track(a, "boot")
    _mark(a, 0)
    slot = _wait(a, HOLD_UNITS, slot)
    a += ["    MOVE.B #$76,$BFD100.L        ; /STEP 1 -> 0, direction out, DF0 selected", "    MOVE.B #$77,$BFD100.L        ; /STEP 0 -> 1: the step"]
    slot = _wait(a, SETTLE_UNITS, slot)
    _read_track(a, "swapped")
    _mark(a, 1)
    _write_attempt(a)
    _mark(a, 2)
    _read_track(a, "readback")
    _mark(a, 3)
    slot = _wait(a, TAIL_UNITS, slot)
    assert slot == LOG_SLOTS and LOG_BASE + LOG_SLOTS <= MARK_BASE
    _mark(a, 4)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L      ; INTREQ: clear VERTB", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK", "    RTE"]
    return "\n".join(a) + "\n"


# --- Reading the RAM dump the probe writes ------------------------------------------------------------------------------


def _check_ram(ram):
    if len(ram) != CHIP_RAM_BYTES:
        raise ValueError("expected %d bytes of Chip RAM, got %d" % (CHIP_RAM_BYTES, len(ram)))


def pra_log(ram):
    """The LOG_SLOTS CIA-A PRA bytes the guest logged, one per unit."""
    _check_ram(ram)
    return list(ram[LOG_BASE:LOG_BASE + LOG_SLOTS])


def capture_words(ram, name):
    """The CAPTURE_WORDS big-endian words the disk DMA stored for "boot", "swapped" or "readback"."""
    _check_ram(ram)
    base = CAPTURE_BASE[name]
    return list(struct.unpack(">%dH" % CAPTURE_WORDS, ram[base:base + 2 * CAPTURE_WORDS]))


def markers(ram):
    """{name: reached?} for the MARKERS: False means the guest never got there (a DMA that never finished, for one)."""
    _check_ram(ram)
    return {name: struct.unpack(">H", ram[MARK_BASE + 2 * i:MARK_BASE + 2 * i + 2])[0] == MARK_DONE
            for i, name in enumerate(MARKERS)}


def pra_state(byte):
    """The drive lines of a PRA byte; True means asserted (the pins are active low)."""
    return DriveState(not byte & 0x04, not byte & 0x08, not byte & 0x10, not byte & 0x20)


def swap_timeline(log):
    """Slots of the four steps of a swap in a PRA log, None for one that never happened.

    ready (first /RDY), ejected (then /CHNG asserted, drive empty), reinserted (then /RDY again with /CHNG still asserted)
    and acknowledged (then /CHNG released by the step pulse)."""
    states = [pra_state(b) for b in log]

    def first(after, wanted):
        if after is None:
            return None
        return next((k for k in range(after, len(states)) if wanted(states[k])), None)

    ready = first(0, lambda s: s.ready)
    ejected = first(None if ready is None else ready + 1, lambda s: s.changed and not s.ready)
    reinserted = first(None if ejected is None else ejected + 1, lambda s: s.ready and s.changed)
    acknowledged = first(None if reinserted is None else reinserted + 1, lambda s: not s.changed)
    return {"ready": ready, "ejected": ejected, "reinserted": reinserted, "acknowledged": acknowledged}


def swap_gap_bounds(standard):
    """(low, high) for `reinserted - ejected` in units: the engine's swap delay plus /RDY spin-up, sampled once per unit."""
    lines = LINES_PER_UNIT[standard]
    low = (EJECT_TO_INSERT_HSYNCS + SPINUP_HSYNCS[0]) // lines - 1
    high = -(-(EJECT_TO_INSERT_HSYNCS + SPINUP_HSYNCS[1]) // lines) + 2
    return low, high
