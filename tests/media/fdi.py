"""A writer of FDI images (T57b) for the tests, from the layout fdi2raw.cpp (WinUAE, the reader the core uses) implements. Nothing binary is committed and nothing is taken from
engine output; where fdi2raw does something the format notes do not say, the comment names it.

Header (a multiple of 512 bytes, 512 for up to 180 tracks; only these fields are read):
    0   "Formatted Disk Image file" (25 bytes)             140  version major (1 or 2)         141  version minor (from 2.1 the CRC-32 at 508 is checked)
    142 u16 last cylinder (cylinders - 1)                  144  last head (heads - 1)           145  disk type            146  rotation speed - 128
    147 flags: bit 0 write protect, bit 2 reversed sides   152  per track {u8 type, u8 size}, in order; 508 u32 CRC-32 of bytes 0..507 (2.1 and later)
Track data follows the header, in order. How much a track takes: type 1 -> (size & 15) * 512 bytes; type 0x80..0xBF -> (((type & 0x3f) << 8) | size) * 256 bytes; any other type
-> size * 256 bytes. The tracks this writer makes, and the ones the core accepts (the rest are refused by the validator, ports/portable/media/trackdisk.h):
    type 0x00   empty (no data, an unformatted track)
    type 0x01   Amiga double density, eleven sectors: size = (first sector << 4) | 11, then 11 * 512 bytes of sector data in order; the reader builds the AmigaDOS MFM track
                from them (headers, checksums, gap), starting at `first`.
    type 0x80+  low level: the track as the flux transitions of a revolution. u32 pulses; four u24 descriptors {avg, min, max, index} each (mode << 22) | length; then the four
                streams. Mode 0 is `pulses` big-endian u32 (a pulse width, in the unit whose sum over the revolution is the revolution time: ns here, 200 ms a turn); mode 1 is a
                Huffman-coded form (a sub-stream header, a tree, values, then a code per pulse), used here for the "index" stream that says, per pulse, how stable it is: a pulse whose
                value is below the largest is present in a read with probability value / largest (a weak bit). Without min and max streams the pulse widths are exact.
"""
import struct
import zlib

MAGIC = b"Formatted Disk Image file"
CELL_NS = 2000                              # one bit cell of double-density MFM


def pulses_from_mfm(words):
    """(widths, positions): the pulse widths (ns) of an MFM bit stream and the bit position of each transition. Every 1 bit is a flux transition and the distance to the previous one is
    (zeros + 1) cells; the stream is circular, so the width of the first pulse is the distance from the last transition of the previous turn."""
    bits = []
    for w in words:
        bits += [(w >> (15 - i)) & 1 for i in range(16)]
    ones = [i for i, b in enumerate(bits) if b]
    if not ones:
        raise ValueError("a track with no transition")
    n = len(bits)
    widths = [(pos - (ones[k - 1] if k else ones[-1] - n)) * CELL_NS for k, pos in enumerate(ones)]
    return widths, ones


def huffman_two_leaf(values, left, right, eight_bit=True):
    """The mode-1 form of a stream whose every value is `left` or `right` (`values` are 0 or 1 for which): one sub-stream, no shift, a tree of two leaves."""
    out = bytearray([0x00, 0x00 if eight_bit else 0x80])          # sub-stream header: sign/shift byte 0 (so the only sub-stream), 8 or 16 bit values
    out.append(0b01100000)                                         # tree bits: internal node (0), leaf (1), leaf (1)
    for v in (left, right):
        out += bytes([v]) if eight_bit else struct.pack(">H", v)
    bits = "".join("1" if v else "0" for v in values)
    bits += "0" * (-len(bits) % 8)
    out += bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    return bytes(out)


def lowlevel_track(pulses, stable=None, weak=None):
    """The data of a low-level track. `stable` is None for exact pulses (no index stream), or (stable value, weak value, set of pulse numbers that are weak)."""
    n = len(pulses)
    avg = b"".join(struct.pack(">I", p) for p in pulses)
    descriptors = struct.pack(">I", n) + (0 << 22 | len(avg)).to_bytes(3, "big") + bytes(6)
    if stable is None:
        index = b""
    else:
        stable_value, weak_value, weak_set = stable
        index = huffman_two_leaf([1 if i in weak_set else 0 for i in range(n)], stable_value, weak_value)
    descriptors += (1 << 22 | len(index)).to_bytes(3, "big") if index else bytes(3)
    data = descriptors + avg + index
    return data + bytes(-len(data) % 256)


def track_entry(track):
    """(type, size byte, data) of a track: ("empty",), ("amiga", 5632 bytes, first sector) or ("lowlevel", data)."""
    if track[0] == "empty":
        return 0x00, 0, b""
    if track[0] == "amiga":
        data, first = track[1], track[2] if len(track) > 2 else 0
        assert len(data) == 5632 and 0 <= first <= 10
        return 0x01, (first << 4) | 11, data
    if track[0] == "lowlevel":
        data = track[1]
        assert len(data) % 256 == 0
        blocks = len(data) // 256
        assert 0 < blocks < 0x4000
        return 0x80 | (blocks >> 8), blocks & 0xFF, data
    raise ValueError(track[0])


def fdi_image(tracks, cylinders=80, heads=2, version=(2, 0), flags=0, rotation=0):
    """An FDI with `cylinders` x `heads` tracks (the list is padded with empty tracks)."""
    count = cylinders * heads
    header = bytearray(512 if count <= 180 else 1024)
    header[0:25] = MAGIC
    header[25:60] = b" PUAE6 test writer, no real disk".ljust(35)
    header[140], header[141] = version
    struct.pack_into(">H", header, 142, cylinders - 1)
    header[144] = heads - 1
    header[146] = rotation
    header[147] = flags
    body = bytearray()
    for i in range(count):
        kind, size, data = track_entry(tracks[i] if i < len(tracks) else ("empty",))
        header[152 + 2 * i] = kind
        header[153 + 2 * i] = size
        body += data
    if version >= (2, 1):
        struct.pack_into(">I", header, 508, zlib.crc32(bytes(header[:508])) & 0xFFFFFFFF)
    return bytes(header) + bytes(body)
