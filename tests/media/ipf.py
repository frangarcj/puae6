"""IPF (SPS/CAPS) images for the tests: a writer and an oracle that is not the decoder (T58).

The format is the one ports/portable/media/ipf_track.cpp reads through the free decoder of MAME (third_party/mame-ipf): a file is a list of tags (id, size, CRC-32
of the tag with its own CRC field cleared): CAPS, INFO, then for every track an IMGE and a DATA, whose body follows the tag. The data of a track is a table of 32-byte block
descriptors and, at the offsets they name, the description of each block's data (elements: sync cells, MFM-encoded bytes, raw cells, weak bytes) and of its gap.

The images are written from the *intended* content of a track (Track and Block below); `expected()` turns the same content into the cells, times and weak marks the decoder
must produce. Only the cases used by the tests are modelled (gap type 0 and the gap descriptions of types 1 and 2 with one pattern, the track types 2, 3, 6 and 9, the MFM and
raw encoders), and the oracle follows the encoding of the format, not the structure of the decoder.
"""
from __future__ import annotations

import struct
import zlib

END, SYNC, DATA, GAP, RAW, WEAK = 0, 1, 2, 3, 4, 5

CAPS_TAG = b"CAPS"


def tag(identifier: bytes, body: bytes) -> bytes:
    """A tag: id, size, CRC (of the tag with the CRC field zero), body (the part after the CRC field)."""
    size = 12 + len(body)
    raw = identifier + struct.pack(">II", size, 0) + body
    return identifier + struct.pack(">II", size, zlib.crc32(raw) & 0xFFFFFFFF) + body


def element(kind: int, value: int = 0, payload: bytes = b"") -> bytes:
    """One element of a block description: (bytes of parameter << 5 | kind), the parameter big-endian, the payload."""
    count = 0 if value == 0 and kind == END else max(1, (value.bit_length() + 7) // 8)
    return bytes([(count << 5) | kind]) + value.to_bytes(count, "big") + payload


class Block:
    """data: a list of ('sync', bytes) raw cells, ('mfm', bytes) MFM-encoded bytes, ('raw', bytes) raw cells of the raw encoder, ('weak', n) n weak bytes.
    gap: gap_cells cells; gap_type 0 repeats `pattern` (a decoded byte), types 1 and 2 repeat the gap description `gap_pattern` (bytes, bits = 8 * len)."""

    def __init__(self, data, gap_cells=0, gap_type=0, pattern=0, encoder=1, speed_mask=None, gap_pattern=b"\x00"):
        self.data, self.gap_cells, self.gap_type, self.pattern, self.encoder = data, gap_cells, gap_type, pattern, encoder
        self.speed_mask, self.gap_pattern = speed_mask, gap_pattern

    def data_cells(self) -> int:
        n = 0
        for kind, payload in self.data:
            n += 16 * payload if kind == "weak" else (16 if kind == "mfm" else 8) * len(payload)
        return n

    def description(self) -> bytes:
        out = b""
        for kind, payload in self.data:
            if kind == "sync":
                out += element(SYNC, len(payload), payload)
            elif kind == "mfm":
                out += element(DATA, len(payload), payload)
            elif kind == "raw":
                out += element(RAW, len(payload), payload)
            else:
                out += element(WEAK, payload)
        return out + element(END)

    def gap_description(self) -> bytes:
        # forward gap (type 1): one pattern of `gap_pattern`, repeated over the whole gap; the description of a type 2 gap is the same stream read from the end
        return element(DATA, 8 * len(self.gap_pattern), self.gap_pattern) + element(END)


class Track:
    def __init__(self, cylinder, head, blocks, type=2, index_cells=0, noise_cells=0):
        self.cylinder, self.head, self.blocks, self.type, self.index_cells, self.noise_cells = cylinder, head, blocks, type, index_cells, noise_cells

    def cells(self) -> int:
        if self.type == 1:
            return self.noise_cells
        return sum(b.data_cells() + (b.gap_cells if b.gap_cells >= 8 else 0) for b in self.blocks)


def track_body(track: Track, key: int, encoder_type: int = 2):
    """(IMGE tag, DATA tag + data) of one track."""
    blocks = track.blocks
    size = track.cells()
    if track.type == 1:
        data = b""
    else:
        table = len(blocks) * 32
        descriptors, streams = [], b""
        for b in blocks:
            data_offset = table + len(streams)
            streams += b.description()
            gap_offset = 0
            if b.gap_type in (1, 2, 3):
                gap_offset = table + len(streams)
                streams += b.gap_description() * (2 if b.gap_type == 3 else 1)     # type 3: the description of the forward gap and then that of the backward one
            flags = b.gap_type
            pattern = b.speed_mask if b.speed_mask is not None else b.pattern
            descriptors.append(struct.pack(">8I", b.data_cells(), b.gap_cells, gap_offset, 0, b.encoder, flags, pattern, data_offset))
        data = b"".join(descriptors) + streams
    data_cells = sum(b.data_cells() for b in blocks)
    imge = tag(b"IMGE", struct.pack(">17I", track.cylinder, track.head, track.type, 1, (size + 7) // 8, (track.index_cells + 7) // 8, track.index_cells, data_cells, size - data_cells,
                                    size, len(blocks), 0, 0, key, 0, 0, 0))
    data_tag = tag(b"DATA", struct.pack(">IIII", len(data), len(data) * 8, zlib.crc32(data) & 0xFFFFFFFF, key)) + data
    return imge, data_tag


def build(tracks, encoder_type: int = 2, extra_tags=(), order=None) -> bytes:
    """A whole IPF: CAPS, INFO, the IMGE tags, then the DATA tags (the order of a real file: all the descriptions first)."""
    cylinders = [t.cylinder for t in tracks]
    heads = [t.head for t in tracks]
    info = tag(b"INFO", struct.pack(">21I", 1, encoder_type, 1, 1, 1, 0, min(cylinders), max(cylinders), min(heads), max(heads), 20260101, 120000000, 1, 0, 0, 0, 0, 0, 0, 0, 0))
    out = tag(CAPS_TAG, b"") + info
    pairs = [track_body(t, k, encoder_type) for k, t in enumerate(tracks)]
    out += b"".join(p[0] for p in pairs)
    for extra in extra_tags:
        out += extra
    out += b"".join(p[1] for p in pairs)
    return out


# ---- the oracle -----------------------------------------------------------------------------------------------------------------------------------------------------------

def mfm(data: bytes, context: int):
    """MFM-encode decoded bytes: for every data bit a clock cell (1 when the previous and the current data bit are both 0) and the data cell."""
    cells = []
    for byte in data:
        for k in range(8):
            bit = (byte >> (7 - k)) & 1
            cells.append(1 if not (context or bit) else 0)
            cells.append(bit)
            context = bit
    return cells, context


def pattern_cells(pattern: bytes, start: int, count: int, context: int):
    """track_write_mfm of the decoder: the MFM of a looped pattern (decoded bits) from cell `start` of its loop."""
    bits = [(byte >> (7 - k)) & 1 for byte in pattern for k in range(8)]
    period = 2 * len(bits)
    cells = []
    for i in range(count):
        pos = (i + start) % period
        bit = bits[pos >> 1]
        if pos & 1:
            cells.append(bit)
            context = bit
        else:
            cells.append(0 if (context or bit) else 1)
    return cells, context


def expected(track: Track):
    """(cells, times, weak, splice) as the decoder must produce them for the tracks the tests write: cells are 0, 1 or None (weak), index_cells = 0 or the track rotated."""
    cells, context = [], 0
    data_pos, gap_pos, splice = [], [], None
    for index, b in enumerate(track.blocks):
        data_pos.append(len(cells))
        for kind, payload in b.data:
            if kind in ("sync", "raw"):
                bits = [(byte >> (7 - k)) & 1 for byte in payload for k in range(8)]
                cells += bits
                context = bits[-1] if bits else context
            elif kind == "mfm":
                c, context = mfm(payload, context)
                cells += c
            else:
                cells += [None] * (16 * payload)
                context = 0
        gap_pos.append(len(cells))
        gap = b.gap_cells if b.gap_cells >= 8 else 0
        if b.gap_type == 0:
            # the pattern byte and the speed mask of a type 9 track are the same field of the block (+24)
            pattern = bytes([(b.speed_mask if b.speed_mask is not None else b.pattern) & 0xFF])
            half = gap >> 1
            c, context = pattern_cells(pattern, 0, half, context)
            cells += c
            delta = gap & 1
            if delta:
                cells.append(0)
            c, context = pattern_cells(pattern, half + delta - gap, gap - half - delta, context)
            cells += c
            spos = half
        elif b.gap_type == 1:
            c, context = pattern_cells(b.gap_pattern, 0, gap, context)
            cells += c
            spos = 0
        else:
            c, context = pattern_cells(b.gap_pattern, -gap, gap, context)
            cells += c
            spos = gap
        splice = gap_pos[-1] + spos
    data_pos.append(len(cells))
    size = len(cells)
    times = [2000] * size
    # the timing zones of the types the tests write

    def zone(start, end, t):
        for i in range(start, end):
            times[i] = t

    n = len(track.blocks)
    if track.type == 6:
        if n >= 2: zone(data_pos[1], gap_pos[1], 2200)
        if n >= 3: zone(data_pos[2], gap_pos[2], 1800)
    elif track.type == 9:
        mask = track.blocks[0].speed_mask or 0
        for i in range(1, n):
            zone(data_pos[i], gap_pos[i], 1900 if mask & (1 << (i - 1)) else 2100)
    elif track.type == 3:
        if n >= 5: zone(gap_pos[3], gap_pos[4], 1890); zone(data_pos[4], gap_pos[4], 1890)
        if n >= 6: zone(gap_pos[4], gap_pos[5], 1990); zone(data_pos[5], gap_pos[5], 1990)
        if n >= 7: zone(gap_pos[5], gap_pos[6], 2090); zone(data_pos[6], gap_pos[6], 2090)
    # the write splice: three cells that change their value there
    for i in range(3):
        p = (splice + i) % size
        if cells[p] == 0: cells[p] = 1
        elif cells[p] == 1: cells[p] = 0
    shift = track.index_cells
    if shift:
        cut = size - shift
        cells = cells[cut:] + cells[:cut]
        times = times[cut:] + times[:cut]
    weak = any(c is None for c in cells)
    return cells, times, weak, (splice + shift) % size


def words(cells, weak_bit=lambda i: 0):
    """The 16-cell words of a cell list (weak cells as `weak_bit(i)`), the first cell in the top bit."""
    out = [0] * ((len(cells) + 15) // 16)
    for i, c in enumerate(cells):
        bit = weak_bit(i) if c is None else c
        if bit:
            out[i >> 4] |= 0x8000 >> (i & 15)
    return out


def timing(times):
    """One value per 8 cells: the mean time in thousandths of a nominal cell (2000 ns), rounded; the last group is the cells it has; then two copies of the first."""
    out = []
    for i in range(0, len(times), 8):
        group = times[i:i + 8]
        out.append(max(1, (sum(group) + len(group)) // (2 * len(group))))
    return out + [out[0], out[0]]


# ---- the structural oracle: which files the reader opens -----------------------------------------------------------------------------------------------------------------

MAX_FILE = 3 << 20
MAX_CELLS = 160000
MAX_DATA = 256 * 1024
MAX_KEYS = 168


def u32(blob, at):
    return struct.unpack(">I", blob[at:at + 4])[0]


def accepts(blob: bytes) -> bool:
    """True when the reader opens the file (ports/portable/media/ipf_track.cpp, from the description of the format and the limits of the core): a list of tags, each with its size and its CRC, CAPS
    first and alone, INFO once before any IMGE or DATA, every IMGE with exactly one DATA of the same key, the tracks within a drive's cylinders and heads and each one once."""
    n = len(blob)
    if n < 12 + 96 or n > MAX_FILE:
        return False
    pos, info, imge, data = 0, None, {}, {}
    while pos < n:
        if n - pos < 12:
            return False
        ident, size, crc = blob[pos:pos + 4], u32(blob, pos + 4), u32(blob, pos + 8)
        if size < 12 or size > n - pos:
            return False
        if zlib.crc32(blob[pos:pos + 8] + b"\0\0\0\0" + blob[pos + 12:pos + size]) & 0xFFFFFFFF != crc:
            return False
        if pos == 0 and (ident != b"CAPS" or size != 12):
            return False
        end = pos + size
        if ident == b"CAPS":
            if size != 12 or pos != 0:
                return False
        elif ident == b"INFO":
            if size != 96 or info is not None:
                return False
            if u32(blob, pos + 12) != 1 or u32(blob, pos + 16) not in (1, 2) or u32(blob, pos + 20) != 1:
                return False
            info = (u32(blob, pos + 36), u32(blob, pos + 40), u32(blob, pos + 44), u32(blob, pos + 48))
        elif ident == b"IMGE":
            if size != 80 or info is None:
                return False
            key, cylinder, head = u32(blob, pos + 64), u32(blob, pos + 12), u32(blob, pos + 16)
            if key >= MAX_KEYS or key in imge:
                return False
            if not (info[0] <= cylinder <= info[1] and cylinder <= 82 and info[2] <= head <= info[3] and head <= 1):
                return False
            if u32(blob, pos + 24) != 1 or u32(blob, pos + 56) != 0 or u32(blob, pos + 48) > MAX_CELLS:
                return False
            imge[key] = cylinder * 2 + head
        elif ident == b"DATA":
            if size != 28 or info is None:
                return False
            key, length = u32(blob, pos + 24), u32(blob, pos + 12)
            if key >= MAX_KEYS or key in data or length > MAX_DATA or length > n - end:
                return False
            if zlib.crc32(blob[end:end + length]) & 0xFFFFFFFF != u32(blob, pos + 20):
                return False
            data[key] = length
            end += length
        pos = end
    if info is None or set(imge) != set(data) or not imge:
        return False
    return len(set(imge.values())) == len(imge)


# ---- AmigaDOS tracks as IPF blocks ----------------------------------------------------------------------------------------------------------------------------------------

PAL_CELLS = 101344           # 2 * 8 * 6334 words: a revolution of a standard track in the engine's clock


def dos_blocks(adf, track, weak_bytes=0, total_cells=PAL_CELLS):
    """The eleven sectors of a track of a plain ADF as eleven blocks: each sector's MFM words as unencoded cells, followed by `weak_bytes` weak bytes, and a gap of zeros that brings the track
    to `total_cells` cells (the first ten gaps are equal, the last takes the rest)."""
    from tests.scenarios.disk import disk
    sectors = disk.encode_track(adf, track)
    raw = [b"".join(struct.pack(">H", w) for w in sector) for sector in sectors]
    data_cells = sum(8 * len(r) + 16 * weak_bytes for r in raw)
    gap = (total_cells - data_cells) // len(raw)
    blocks = []
    for i, r in enumerate(raw):
        last = total_cells - data_cells - gap * (len(raw) - 1) if i == len(raw) - 1 else gap
        blocks.append(Block([("sync", r)] + ([("weak", weak_bytes)] if weak_bytes else []), gap_cells=last, pattern=0x00))
    return blocks
