"""Synthetic LHA archives for the tests of tools/whdload/lha.py (T30a): a writer of the three header levels and an encoder for the LH5/LH6/LH7 family that is as small as can be.

The encoder writes ONE tree shape per block: 256 literals of 9 bits and one 1-bit symbol for "copy 3 bytes from distance 1" (a run of a byte), with the position tree reduced to a single symbol that
costs no bits. It is not a compressor; it is enough to put the three Huffman tables of a block, the literal path and the overlapping-copy path of the decoder under a test with bytes no one
else wrote. Real archives (the WHDLoad package and a game's install) are the opt-in tests.
"""
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from whdload import lha                 # noqa: E402,F401


class Writer:
    def __init__(self):
        self.value, self.count = 0, 0

    def put(self, bits, n):
        self.value = (self.value << n) | (bits & ((1 << n) - 1))
        self.count += n

    def bytes(self):
        pad = -self.count % 8
        return (self.value << pad).to_bytes((self.count + pad) // 8, "big")


def encode(data, method_bits=13, block_symbols=60000):
    """The LH5 (13), LH6 (15) or LH7 (16) stream of `data`: literals and copies of 3 bytes of the previous byte."""
    pbit = 4 if method_bits == 13 else 5
    symbols, i = [], 0
    while i < len(data):
        if i >= 1 and data[i:i + 3] == bytes([data[i - 1]]) * 3:
            symbols.append(256)
            i += 3
        else:
            symbols.append(data[i])
            i += 1
    out = Writer()
    for start in range(0, len(symbols), block_symbols):
        block = symbols[start:start + block_symbols]
        out.put(len(block), 16)
        out.put(12, 5)                                     # meta tree: 12 entries, symbols 3 and 11 of one bit
        for index in range(12):
            out.put(1 if index in (3, 11) else 0, 3)
            if index == 2:
                out.put(0, 2)                              # no run of zero lengths after the third entry
        out.put(257, 9)                                    # literal tree: 257 lengths
        for _ in range(256):
            out.put(1, 1)                                  # meta symbol 11: length 9
        out.put(0, 1)                                      # meta symbol 3: length 1 (the copy)
        out.put(0, pbit)                                   # position tree: one symbol ...
        out.put(0, pbit)                                   # ... number 0, no bits to read
        for symbol in block:
            if symbol == 256:
                out.put(0, 1)
            else:
                out.put(256 + symbol, 9)
    return out.bytes()


def extensions(items):
    """(size of the first, bytes that follow the base header) for extended headers given as (type, body): each is [type][body][size of the next]."""
    blocks = [bytes([kind]) + body for kind, body in items]
    sizes = [len(block) + 2 for block in blocks]
    tail = b"".join(block + struct.pack("<H", sizes[k + 1] if k + 1 < len(sizes) else 0) for k, block in enumerate(blocks))
    return (sizes[0] if sizes else 0), tail


def member(name, data=b"", method=b"-lh0-", level=0, directory=b"", method_bits=13, crc=None):
    """One archive member with a header of `level` 0, 1 or 2. `name` and `directory` are bytes as they sit in the header (the directory separator of levels 1 and 2 is 0xFF)."""
    if method == b"-lhd-":
        packed, original, file_crc = b"", 0, 0
    else:
        packed = data if method == b"-lh0-" else encode(data, method_bits)
        original, file_crc = len(data), lha.crc16(data) if crc is None else crc
    time = struct.pack("<L", 0x5A210000)
    if level == 0:
        body = method + struct.pack("<LL", len(packed), original) + time + bytes([0x20, 0, len(name)]) + name + struct.pack("<H", file_crc)
        return bytes([len(body), sum(body) & 0xFF]) + body + packed
    if level == 1:
        first, tail = extensions([(2, directory)] if directory else [])
        body = (method + struct.pack("<LL", len(packed) + len(tail), original) + time + bytes([0x20, 1, len(name)]) + name + struct.pack("<H", file_crc) + b"A"
                + struct.pack("<H", first))
        return bytes([len(body), sum(body) & 0xFF]) + body + tail + packed
    first, tail = extensions([(1, name)] + ([(2, directory)] if directory else []))
    header = method + struct.pack("<LL", len(packed), original) + time + bytes([0x20, 2]) + struct.pack("<H", file_crc) + b"A" + struct.pack("<H", first)
    return struct.pack("<H", 2 + len(header) + len(tail)) + header + tail + packed


def archive(*members):
    return b"".join(members) + b"\0"
