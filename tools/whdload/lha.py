"""Bounded LHA reader in pure Python: header levels 0, 1 and 2, methods -lh0-, -lh5-, -lh6-, -lh7- and -lhd- (T30a).

It is the decoder of the offline WHDLoad preparation (tools/prepare_whdload.py), which reads archives that are NOT trusted: every size is checked against a limit before anything
is allocated, a name is refused unless it is a plain relative path, the CRC-16 of the file is verified and nothing of the archive is ever run. The method decoders follow the public
description of the LH5 family (static Huffman over a sliding window, each block with its own trees); the window is 8 KiB (lh5), 32 KiB (lh6) or 64 KiB (lh7).
"""
from dataclasses import dataclass
import struct

MAX_ENTRIES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_NAME_PART = 107
METHOD_BITS = {b"-lh5-": 13, b"-lh6-": 15, b"-lh7-": 16}
THRESHOLD, NC, NT, TBIT, CBIT = 3, 510, 19, 5, 9


class LhaError(ValueError):
    """The archive is malformed, uses something this reader does not do, or exceeds a limit."""


@dataclass(frozen=True)
class Entry:
    name: str           # relative path with "/" separators
    is_dir: bool
    data: bytes


def _crc_table():
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc_table()


def crc16(data, crc=0):
    for byte in data:
        crc = (crc >> 8) ^ _CRC[(crc ^ byte) & 0xFF]
    return crc


class _Bits:
    """MSB-first bit reader; past the end it yields zero bits and counts them, so that a truncated stream fails instead of looping."""

    def __init__(self, data):
        self.data, self.pos, self.buf, self.count, self.over = data, 0, 0, 0, 0

    def peek(self, n):
        while self.count < n:
            if self.pos < len(self.data):
                self.buf = ((self.buf << 8) | self.data[self.pos]) & 0xFFFFFFFFFFFF
                self.pos += 1
            else:
                self.buf = (self.buf << 8) & 0xFFFFFFFFFFFF
                self.over += 1
            self.count += 8
        return (self.buf >> (self.count - n)) & ((1 << n) - 1)

    def skip(self, n):
        self.peek(n)
        self.count -= n

    def bits(self, n):
        if n == 0:
            return 0
        value = self.peek(n)
        self.count -= n
        return value


class _Huffman:
    """Canonical code: the codes of one length are consecutive and follow the symbol order. `constant` is the tree of one symbol that costs no bits."""

    def __init__(self, lengths, constant=None):
        self.constant = constant
        self.longest = max(lengths) if constant is None else 0
        longest = self.longest
        if longest > 16:
            raise LhaError("code length over 16")
        self.table = [-1] * (1 << longest) if longest else []
        counts = [0] * (longest + 1)
        for length in lengths:
            counts[length] += 1 if length else 0
        code, symbols = 0, sorted((i for i, length in enumerate(lengths) if length), key=lambda i: (lengths[i], i))
        index = 0
        for length in range(1, longest + 1):
            code = (code + (counts[length - 1] if length > 1 else 0)) << 1 if length > 1 else 0
            if code + counts[length] > (1 << length):
                raise LhaError("over-subscribed Huffman code")
            for k in range(counts[length]):
                span = 1 << (longest - length)
                start = (code + k) * span
                self.table[start:start + span] = [(symbols[index] << 5) | length] * span
                index += 1

    def read(self, bits):
        if self.constant is not None:
            return self.constant
        if not self.longest:
            raise LhaError("Huffman table without codes")
        entry = self.table[bits.peek(self.longest)]
        if entry < 0:
            raise LhaError("invalid Huffman code")
        bits.count -= entry & 31
        return entry >> 5


def _length_table(bits, size, nbit, special):
    """Code lengths of a small alphabet; `special` is the index after which a run of zeros may be coded. Returns (lengths, constant symbol or None)."""
    n = bits.bits(nbit)
    if n == 0:
        return [0] * size, bits.bits(nbit)
    if n > size:
        raise LhaError("length table too long")
    lengths, i = [0] * size, 0
    while i < n:
        c = bits.peek(3)
        if c == 7:
            window = bits.peek(16)
            mask = 0x1000
            while window & mask:
                mask >>= 1
                c += 1
                if not mask:
                    raise LhaError("length code too long")
            bits.skip(c - 3)
        else:
            bits.skip(3)
        lengths[i] = c
        i += 1
        if i == special:
            run = bits.bits(2)
            if i + run > size:
                raise LhaError("length table overflow")
            i += run
    return lengths, None


def decode(data, method_bits, out_size):
    """The `out_size` bytes that the LH5/6/7 stream `data` expands to."""
    position_symbols, position_bits = method_bits + 1, 4 if method_bits == 13 else 5
    bits, out, window = _Bits(data), bytearray(), 1 << method_bits
    block, literal, position = 0, None, None
    while len(out) < out_size:
        if block == 0:
            block = bits.bits(16)
            if block == 0:
                raise LhaError("empty block")
            lengths, constant = _length_table(bits, NT, TBIT, 3)
            meta = _Huffman(lengths, constant)
            n = bits.bits(CBIT)
            if n == 0:
                literal = _Huffman([0] * NC, bits.bits(CBIT))
            else:
                if n > NC:
                    raise LhaError("too many literal lengths")
                lengths, i = [0] * NC, 0
                while i < n:
                    c = meta.read(bits)
                    if c <= 2:
                        run = 1 if c == 0 else bits.bits(4) + 3 if c == 1 else bits.bits(CBIT) + 20
                        if i + run > NC:
                            raise LhaError("literal length run overflow")
                        i += run
                    else:
                        lengths[i] = c - 2
                        i += 1
                literal = _Huffman(lengths)
            lengths, constant = _length_table(bits, position_symbols, position_bits, -1)
            position = _Huffman(lengths, constant)
        block -= 1
        symbol = literal.read(bits)
        if symbol < 256:
            out.append(symbol)
        else:
            length = symbol - 256 + THRESHOLD
            j = position.read(bits)
            distance = (j if j < 2 else (1 << (j - 1)) + bits.bits(j - 1)) + 1
            if distance > len(out) or distance > window:
                raise LhaError("match points before the start of the data")
            if len(out) + length > out_size:
                raise LhaError("data longer than the header says")
            start = len(out) - distance
            if distance >= length:
                out += out[start:start + length]
            else:
                for k in range(length):
                    out.append(out[start + k])
        if bits.over > 8:
            raise LhaError("compressed data ends early")
    return bytes(out)


def _parts(raw):
    return raw.replace(b"\xff", b"/").replace(b"\\", b"/").decode("latin-1").split("/")


def _member_name(directory_raw, name_raw, is_dir):
    parts = _parts(directory_raw) if directory_raw else []
    if parts and parts[-1] == "":
        parts.pop()                 # the directory header of a member ends with a separator
    parts += _parts(name_raw) if name_raw else []
    if is_dir:
        parts = [p for p in parts if p != ""]
    for part in parts:
        if part in ("", ".", "..") or ":" in part or any(ord(ch) < 32 for ch in part) or len(part) > MAX_NAME_PART:
            raise LhaError("unsafe member name %r" % "/".join(parts))
    if not parts:
        raise LhaError("member without a name")
    return "/".join(parts)


def _extended(blob, cursor, limit):
    """Walk the chain of extended headers that starts at the 2-byte size at `cursor`. Returns (file name, directory name, bytes used after the first size, end)."""
    name, directory, used = b"", b"", 0
    size = struct.unpack_from("<H", blob, cursor)[0]
    cursor += 2
    while size:
        if size < 3 or cursor + size > limit:
            raise LhaError("bad extended header")
        kind, body = blob[cursor], bytes(blob[cursor + 1:cursor + size - 2])
        if kind == 0x01:
            name = body
        elif kind == 0x02:
            directory = body
        used += size
        cursor += size - 2
        size = struct.unpack_from("<H", blob, cursor)[0]
        cursor += 2
    return name, directory, used, cursor


def read_archive(blob, *, max_entries=MAX_ENTRIES, max_file=MAX_FILE_BYTES, max_total=MAX_TOTAL_BYTES):
    """The members of an LHA archive in archive order. Raises LhaError for anything unsafe, damaged, unsupported or over a limit."""
    entries, pos, total = [], 0, 0
    while pos < len(blob) and blob[pos] != 0:
        if len(entries) >= max_entries:
            raise LhaError("too many entries")
        if pos + 24 > len(blob):
            raise LhaError("truncated header")
        level, method = blob[pos + 20], bytes(blob[pos + 2:pos + 7])
        name_raw, directory_raw, file_crc, skip = b"", b"", None, 0
        if level in (0, 1):
            header_len = blob[pos] + 2
            if header_len < 24 or pos + header_len > len(blob):
                raise LhaError("truncated header")
            if sum(blob[pos + 2:pos + header_len]) & 0xFF != blob[pos + 1]:
                raise LhaError("header checksum")
            name_len = blob[pos + 21]
            tail = pos + 22 + name_len
            if tail + (2 if level == 0 else 5) > pos + header_len:
                raise LhaError("name beyond the header")
            name_raw = bytes(blob[pos + 22:tail])
            file_crc = struct.unpack_from("<H", blob, tail)[0]
            if level == 1:
                ext_name, ext_dir, skip, _ = _extended(blob, tail + 3, len(blob))
                name_raw, directory_raw = ext_name or name_raw, ext_dir
        elif level == 2:
            header_len = struct.unpack_from("<H", blob, pos)[0]
            if header_len < 26 or pos + header_len > len(blob):
                raise LhaError("truncated header")
            file_crc = struct.unpack_from("<H", blob, pos + 21)[0]
            name_raw, directory_raw, _, end = _extended(blob, pos + 24, pos + header_len)
            if end > pos + header_len:
                raise LhaError("extended headers beyond the header")
        else:
            raise LhaError("header level %d is not supported" % level)
        packed, original = struct.unpack_from("<LL", blob, pos + 7)
        if skip > packed:
            raise LhaError("extended headers longer than the packed size")
        packed -= skip
        data_at = pos + header_len + skip
        is_dir = method == b"-lhd-"
        if not is_dir and method != b"-lh0-" and method not in METHOD_BITS:
            raise LhaError("method %s is not supported" % method.decode("latin-1", "replace"))
        if data_at + packed > len(blob):
            raise LhaError("packed data beyond the end of the archive")
        if original > max_file:
            raise LhaError("member larger than the limit")
        total += original
        if total > max_total:
            raise LhaError("archive larger than the limit")
        name = _member_name(directory_raw, name_raw, is_dir)
        if is_dir:
            entries.append(Entry(name, True, b""))
        else:
            payload = bytes(blob[data_at:data_at + packed])
            if method == b"-lh0-":
                if packed != original:
                    raise LhaError("stored member with different sizes")
                data = payload
            else:
                data = decode(payload, METHOD_BITS[method], original)
            if crc16(data) != file_crc:
                raise LhaError("CRC of %s" % name)
            entries.append(Entry(name, False, data))
        pos = data_at + packed
    return entries
