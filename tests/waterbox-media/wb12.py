"""The adversarial writer of WB12 (T28): a micro-ROM that writes EVERY track of both floppies, then tries one more disk.

The overlay of the guest is a fixed pool: 3520 sectors, which is exactly the two disks of the machine (2 * 1760). A guest that rewrites every track of DF0 and DF1 uses all of
it, so this is the worst case the memory of the sandbox, the snapshot and the state bound have to hold. The guest is a straight-line script (no compares, no branches, like the
other micro-ROMs of tests/scenarios/disk), written with the helpers of tests/media-cow/med02.py:

    DF0: for each of the 80 cylinders  side 0 <- BUF, side 1 <- BUF, one step inward      marker "df0"
    DF1: the same, after the motor and the select line move to the second drive             marker "df1"   (the pool is now full)
    HOLD: the host swaps a third disk into DF0 (Next Slot, Next Slot, Insert Disk)           marker "hold"
    DF0 again: PRA sample (the drive reports the third disk write protected: the pool has no room for 11 sectors), a write attempt, another PRA sample   marker "end"

The engine decodes a written track against the head position (decode_buffer: "mismatched track" when the header names another track), so every track needs its own header. The
payload is the same on all tracks (sector s of every track holds payload s of the first track of disk W), which keeps the MFM image of consecutive tracks almost identical: the
guest keeps one track in Chip RAM and, before each write, patches the words that differ from the previous track (at most PATCHES word writes, from a table in the ROM, with a
68000 loop written as words because the assembler has no DBF). The PRA samples are one byte each from PRA_BASE: [df0 ready, df1 ready, after the swap, after the write attempt].
"""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/media-cow"))
from tests.scenarios.disk import disk  # noqa: E402
import med02  # noqa: E402

MARKERS = ("df0", "df1", "hold", "end")
PRA_BASE = 0x3100
PRA_SAMPLES = ("df0", "df1", "swapped", "after_attempt")
CYLINDERS = 80
SETTLE = 2                      # after a side change
STEP_WAIT = 3                   # after a step
SPINUP = 26
HOLD = 600                      # the host swaps during this wait (units)
SWAP_ORDER = ("Next_Slot", "Next_Slot", "Insert_Disk")
DF0, DF1 = 0x77, 0x6F           # PRB with the motor on, the drive selected, side 0 (/SIDE high), direction out, /STEP high


LABEL = 0x51
BUF_ADDR = med02.BUF_ADDR["buf1"]
def _disk_w():
    """Disk W: every track holds the sectors of the first track of make_adf(LABEL), with the first byte of each sector inverted. A sector of make_adf is a fixed pattern shifted by a
    number that depends on label, track and sector, so on some track the plain pattern of one label equals that of another (a sector whose new content equals its base consumes
    no slot of the overlay): with the first byte inverted, W is different from every sector of every make_adf, so the guest uses exactly one slot per sector it writes."""
    first = bytearray(disk.make_adf(LABEL)[:disk.SECTORS * disk.SECTOR_BYTES])
    for sector in range(disk.SECTORS):
        first[sector * disk.SECTOR_BYTES] ^= 0xFF
    return bytes(first) * disk.TRACKS


ADF_W = _disk_w()


def track_words(track):
    """The words the guest has in its buffer to write track `track`: the eleven sectors with that track in their headers, then MFM zeros to the length of a revolution."""
    words = [w for sector in disk.encode_track(ADF_W, track) for w in sector]
    return words + [0xAAAA] * (med02.BUF_WORDS - len(words))


def patch_table():
    """For tracks 1..159, PATCHES (byte offset, word) pairs that turn the buffer of the previous track into this one (padded by repeating a real patch: writes are idempotent)."""
    previous, table = track_words(0), []
    for track in range(1, disk.TRACKS):
        current = track_words(track)
        diff = [(2 * i, b) for i, (a, b) in enumerate(zip(previous, current)) if a != b]
        assert diff and len(diff) <= PATCHES, (track, len(diff))
        table.append(diff + [diff[0]] * (PATCHES - len(diff)))
        previous = current
    return table


PATCHES = 55
MARKERS = ("df0", "df1", "hold", "end")
PRA_BASE = 0x3100
PRA_SAMPLES = ("df0", "df1", "swapped", "after_attempt")
CYLINDERS = 80
SETTLE = 2                      # after a side change
STEP_WAIT = 3                   # after a step
SPINUP = 26
HOLD = 600                      # the host swaps during this wait (units)
SWAP_ORDER = ("Next_Slot", "Next_Slot", "Insert_Disk")
DF0, DF1 = 0x77, 0x6F           # PRB with the motor on, the drive selected, side 0 (/SIDE high), direction out, /STEP high


def mark(a, index):
    a += ["    MOVE.W #$%04X,$%X.L          ; marker %d: %s reached" % (disk.MARK_DONE, disk.MARK_BASE + 2 * index, index, MARKERS[index])]


def sample(a, index):
    a += ["    .word $13F9                  ; MOVE.B $BFE001.L,$%X.L: PRA sample %d (%s)" % (PRA_BASE + index, index, PRA_SAMPLES[index]),
          "    .long $BFE001,$%X" % (PRA_BASE + index)]


def waits(a, units):
    a += ["    STOP #$2000"] * units


def load_first_track(a, label):
    """Track 0 from the ROM into the buffer (the guest cannot be poked), A1 back at the buffer and A0 at the start of the patch table."""
    a += ["    MOVEA.L #track0_data,A0", "    MOVEA.L #$%X,A1" % BUF_ADDR, "    MOVE.L #%d,D0" % (med02.BUF_WORDS // 2 - 1),
          "%s:" % label, "    .word $22D8                  ; MOVE.L (A0)+,(A1)+", "    .word $51C8,$FFFC            ; DBF D0,%s" % label,
          "    MOVEA.L #$%X,A1" % BUF_ADDR, "    MOVEA.L #patch_table,A0"]


def patch(a):
    """Applies the next PATCHES entries of the table (A0) to the buffer (A1): MOVE.W #n-1,D0; loop: MOVE.W (A0)+,D1; MOVE.W (A0)+,(0,A1,D1.W); DBF D0,loop."""
    a += ["    .word $303C,$%04X            ; MOVE.W #%d,D0" % (PATCHES - 1, PATCHES - 1), "    .word $3218                  ; MOVE.W (A0)+,D1: the byte offset",
          "    .word $3398,$1000            ; MOVE.W (A0)+,(0,A1,D1.W): the word", "    .word $51C8,$FFF8            ; DBF D0,back to the first MOVE"]


def write_drive(a, base):
    """Every track of the drive selected by `base`, in order 0..159: the track is patched into the buffer, written, then the side or the cylinder changes."""
    for track in range(disk.TRACKS):
        cylinder, side = divmod(track, 2)
        if track:
            patch(a)
        a += ["    MOVE.B #$%02X,$BFD100.L        ; cylinder %d, side %d" % (base if side == 0 else base & ~0x04, cylinder, side)]
        waits(a, SETTLE)
        med02._write(a, "buf1")
        if side == 1 and cylinder != CYLINDERS - 1:
            a += ["    MOVE.B #$%02X,$BFD100.L        ; side 0" % base]
            for edge in (base & ~3, base & ~2):
                a += ["    MOVE.B #$%02X,$BFD100.L        ; /STEP %s" % (edge, "0" if edge & 1 == 0 else "1: the step")]
            waits(a, STEP_WAIT)
    a += ["    MOVE.B #$%02X,$BFD100.L        ; side 0" % base]


def select(a, base):
    """Motor off, nothing selected; then the motor on and the drive selected (the motor latches at the select edge), and the spin-up."""
    a += ["    MOVE.B #$FF,$BFD100.L        ; nothing selected, motor off", "    MOVE.B #$7F,$BFD100.L        ; motor on, nothing selected",
          "    MOVE.B #$%02X,$BFD100.L        ; select the drive" % base]
    waits(a, SPINUP)


def rom_source():
    head = med02.rom_source().split("    MOVE.B #$77,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge\n")[0].splitlines()
    i = head.index("    MOVE.W #$C020,$DFF09A.L      ; INTENA: SET | INTEN | VERTB")
    tail = head[i:]
    a = head[:i]
    load_first_track(a, "copy0")
    a += tail
    a += ["    MOVE.B #$%02X,$BFD100.L        ; select DF0 (bit 3 low): the motor latches at the select edge" % DF0]
    waits(a, SPINUP)
    write_drive(a, DF0)
    mark(a, 0)
    sample(a, 0)
    # The second drive: the buffer goes back to track 0 and the table to its start; the second drive's head is on cylinder 0.
    a += ["    MOVE.W #$2700,SR"]
    load_first_track(a, "copy1")
    a += ["    MOVE.W #$2000,SR"]
    select(a, DF1)
    write_drive(a, DF1)
    mark(a, 1)
    sample(a, 1)
    waits(a, HOLD)
    mark(a, 2)
    # The third disk is in DF0: acknowledge the disk change with a step (outward), look at the drive, try to write, look again.
    select(a, DF0)
    a += ["    MOVE.B #$%02X,$BFD100.L        ; /STEP 1 -> 0 (direction out)" % (DF0 & ~1), "    MOVE.B #$%02X,$BFD100.L        ; /STEP 0 -> 1: acknowledges /CHNG" % DF0]
    waits(a, STEP_WAIT)
    sample(a, 2)
    med02._write(a, "buf1")
    waits(a, SETTLE)
    sample(a, 3)
    mark(a, 3)
    a += ["done:", "    STOP #$2000", "    BRA.S done",
          "hvert:", "    MOVE.W #$0020,$DFF09C.L      ; INTREQ: clear VERTB", "    RTE",
          "hdsk:", "    MOVE.W #$0002,$DFF09C.L      ; INTREQ: clear DSKBLK", "    RTE"]
    a += ["track0_data:"]
    words = track_words(0)
    for k in range(0, len(words), 16):
        a += ["    .word " + ",".join("$%04X" % w for w in words[k:k + 16])]
    a += ["patch_table:"]
    for group in patch_table():
        for k in range(0, len(group), 8):
            a += ["    .word " + ",".join("$%04X,$%04X" % entry for entry in group[k:k + 8])]
    return "\n".join(a) + "\n"


def expected_full(base=None):
    """The disk W: what a disk holds after the guest wrote every track of it."""
    return ADF_W
