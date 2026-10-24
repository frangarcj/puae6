"""Disk containers built at run time for T17d: nothing binary is committed, and no image of a game is needed.

A disk is a 901120-byte ADF whose bytes come from a hash chain (distinct seeds are distinct disks, as in tests/state/test_det02.py). The containers are
written with the Python standard library (gzip for an ADZ, zipfile for a ZIP) and by the small encoders below for DMS, which has no encoder in the tree:
stored tracks, the RLE mode, the Quick mode (an LZ77 with a 256-byte window, a few lines) and Heavy for a track of a single byte value (the Huffman
trees of that case are empty, so the stream is a few bits). The other DMS modes are checked against WinUAE's own unpackers by dms_differential.cpp.
"""
import gzip
import hashlib
import io
import struct
import zipfile
import zlib

ADF_BYTES = 901120
TRACK_BYTES = 11264


def adf(seed):
    """An ADF whose bytes are the SHA-256 of the seed repeated: very compressible."""
    return hashlib.sha256(seed.encode()).digest() * (ADF_BYTES // 32)


def varied_adf(seed):
    """An ADF with all kinds of content: random-looking runs, text, zeros, a repeated byte and 0x90 bytes (the DMS RLE escape), so that every
    compression stage sees literals, matches and runs; tracks 4 and 40 are a single byte value (zeros and 0xE5) for the formatted-but-empty case."""
    out = bytearray()
    counter = 0
    while len(out) < ADF_BYTES:
        block = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
        kind = block[0] % 5
        if kind == 0:
            out += hashlib.sha256(block).digest() * 8                          # 256 bytes of noise
        elif kind == 1:
            out += (b"The Amiga was ahead of its time. " * 8)[: 100 + block[1]]
        elif kind == 2:
            out += bytes(200 + block[1])
        elif kind == 3:
            out += bytes([0x90]) * (1 + block[2] % 9) + bytes([block[3]]) * (5 + block[1])
        else:
            out += block * (1 + block[1] % 6)
    out = bytearray(out[:ADF_BYTES])
    out[4 * TRACK_BYTES:5 * TRACK_BYTES] = bytes(TRACK_BYTES)
    out[40 * TRACK_BYTES:41 * TRACK_BYTES] = bytes([0xE5]) * TRACK_BYTES
    return bytes(out)


# --- gzip and zip ---------------------------------------------------------------------------------------------------------------------------------


def adz(data, level=9):
    return gzip.compress(data, compresslevel=level, mtime=0)


def gzip_with(data, level, strategy, memlevel=8, name=None, comment=None, extra=None, header_crc=False):
    """A gzip member by zlib with the given strategy, optionally with the optional header fields."""
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15, memlevel, strategy)
    body = compressor.compress(data) + compressor.flush()
    flags = (2 if header_crc else 0) | (4 if extra is not None else 0) | (8 if name is not None else 0) | (16 if comment is not None else 0)
    head = b"\x1f\x8b\x08" + bytes([flags]) + b"\0\0\0\0\0\x03"
    if extra is not None:
        head += struct.pack("<H", len(extra)) + extra
    if name is not None:
        head += name.encode() + b"\0"
    if comment is not None:
        head += comment.encode() + b"\0"
    if header_crc:
        head += struct.pack("<H", zlib.crc32(head) & 0xFFFF)
    return head + body + struct.pack("<II", zlib.crc32(data), len(data) & 0xFFFFFFFF)


def zip_file(entries, method=zipfile.ZIP_DEFLATED, comment=b""):
    """entries: (name, bytes) or (name, bytes, method). Directories end in '/'."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.comment = comment
        for entry in entries:
            name, data = entry[0], entry[1]
            info = zipfile.ZipInfo(name, date_time=(1990, 1, 1, 0, 0, 0))
            info.compress_type = entry[2] if len(entry) > 2 else method
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def raw_zip(name, method, data, crc, compressed_size, uncompressed_size, flags=0):
    """One-entry zip with the sizes written as given (they may lie): the local header, the data, the central directory and the end record."""
    n = name.encode()
    local = struct.pack("<4sHHHHHIIIHH", b"PK\3\4", 20, flags, method, 0, 0x21, crc, compressed_size, uncompressed_size, len(n), 0) + n
    central = struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\1\2", 20, 20, flags, method, 0, 0x21, crc, compressed_size, uncompressed_size, len(n), 0, 0, 0, 0, 0, 0) + n
    end = struct.pack("<4sHHHHIIH", b"PK\5\6", 0, 0, 1, 1, len(central), len(local) + len(data), 0)
    return local + data + central + end


# --- DMS --------------------------------------------------------------------------------------------------------------------------------------------


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def rle_encode(data):
    """The RLE of DMS: 0x90 n value (n < 255), 0x90 0xFF value hi lo, and a literal 0x90 as 0x90 0x00."""
    out = bytearray()
    i = 0
    while i < len(data):
        value = data[i]
        run = 1
        while i + run < len(data) and data[i + run] == value and run < 65535:
            run += 1
        if run >= 4:
            out += bytes([0x90, run, value]) if run < 255 else bytes([0x90, 0xFF, value, run >> 8, run & 0xFF])
        else:
            out += (bytes([0x90, 0]) if value == 0x90 else bytes([value])) * run
        i += run
    return bytes(out)


def quick_encode(data):
    """The Quick mode of DMS: 1 + 8 bits for a literal, 0 + 2 bits (length - 2) + 8 bits (distance - 1) for a match of 2 to 5 bytes in a 256-byte ring."""
    ring = bytearray(256)
    loc = 251
    bits = []

    def put(value, count):
        bits.extend((value >> (count - 1 - k)) & 1 for k in range(count))

    i = 0
    n = len(data)
    while i < n:
        best_length, best_distance = 0, 0
        for distance in range(256):
            source = loc - distance - 1
            if ring[source & 0xFF] != data[i]:
                continue
            length = 0
            while length < 5 and i + length < n:
                # Bytes the match itself has written so far are read back from the ring as the unpacker does.
                position = (source + length) & 0xFF
                expected = ring[position] if source + length < loc else data[i + (source + length - loc)]
                if expected != data[i + length]:
                    break
                length += 1
            if length > best_length:
                best_length, best_distance = length, distance
                if length == 5:
                    break
        if best_length >= 2:
            put(0, 1)
            put(best_length - 2, 2)
            put(best_distance, 8)
            for k in range(best_length):
                ring[loc & 0xFF] = data[i + k]
                loc += 1
            i += best_length
        else:
            put(1, 1)
            put(data[i], 8)
            ring[loc & 0xFF] = data[i]
            loc += 1
            i += 1
    while len(bits) % 8:
        bits.append(0)
    return bytes(sum(bit << (7 - k) for k, bit in enumerate(bits[at:at + 8])) for at in range(0, len(bits), 8))


def heavy_constant(value, rle):
    """A Heavy track of one byte value: both Huffman trees are the 'single symbol' case (n = 0 and the symbol), which costs no bits per byte."""
    bits = [0] * 9 + [(value >> (8 - k)) & 1 for k in range(9)] + [0] * 5 + [0] * 5
    return bytes(sum(bit << (7 - k) for k, bit in enumerate(bits[at:at + 8])) for at in range(0, len(bits), 8)), 2 | (4 if rle else 0)


def track(number, data, mode=0, unpacked=None):
    """The 20-byte header and the packed data of one track. `data` is what the track unpacks to."""
    flags = 0
    middle = len(data)
    if mode == 0:
        packed = data
    elif mode == 1:
        packed = rle_encode(data)
    elif mode == 2:
        first = rle_encode(data)
        packed, middle = quick_encode(first), len(first)
    elif mode in (5, 6, 7):                    # 5 and 6 are Heavy 1 and 2; 7 is Heavy 2 with the RLE stage
        assert len(set(data)) == 1, "the test encoder writes Heavy only for a single byte value"
        packed, flags = heavy_constant(data[0], rle=mode == 7)
        mode = 5 if mode == 5 else 6
    else:
        raise ValueError(mode)
    header = b"TR" + struct.pack(">HHHHHBBHH", number, 0, len(packed), middle, len(data) if unpacked is None else unpacked, flags, mode, sum(data) & 0xFFFF,
                                 crc16(packed))
    return header + struct.pack(">H", crc16(header)) + packed


def dms_file(image, modes=None, extras=(), no_zero=False, info=0, disk_type=1, tracks=range(80), banner=False):
    """A DMS archive of `image` (901120 bytes). `modes(n)` gives the mode of track n (default stored). With no_zero the all-zero tracks are left out,
    as DMS files marked "NoZero" do. `extras` are raw track blobs (a fake boot block, FILE_ID.DIZ) written before the real tracks."""
    modes = modes or (lambda n: 0)
    body = b"".join(extras)
    if banner:
        body += track(0xFFFF, b"Packed with a test encoder\n".ljust(2100, b" "), 0)
    for n in tracks:
        data = image[n * TRACK_BYTES:(n + 1) * TRACK_BYTES]
        if no_zero and not any(data):
            continue
        body += track(n, data, modes(n))
    head = bytearray(56)
    head[0:4] = b"DMS!"
    struct.pack_into(">H", head, 10, info | (1 if no_zero else 0))
    struct.pack_into(">HH", head, 16, tracks[0] if tracks else 0, tracks[-1] if tracks else 0)
    struct.pack_into(">I", head, 20, len(body))
    struct.pack_into(">I", head, 24, len(image))
    struct.pack_into(">H", head, 46, 111)
    struct.pack_into(">H", head, 50, disk_type)
    struct.pack_into(">H", head, 52, 0)
    struct.pack_into(">H", head, 54, crc16(bytes(head[4:54])))
    return bytes(head) + body
