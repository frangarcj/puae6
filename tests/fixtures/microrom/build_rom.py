"""Build a Kickstart-shaped 256 KiB micro-ROM from a tiny 68000 assembly subset.

The engine accepts a 256 KiB image when byte 0/1 are 0x11 0x11, the length field at size-20 holds the
size and (for the bridge) the Kickstart checksum is valid. The reset vectors are the first two longs
(a real Kickstart stores 0x11114EF9 and the entry address there), so the entry point is a ROM address.

    python3 build_rom.py boot.s --output boot.rom        # nothing is written without --output
"""
import argparse
from pathlib import Path
import re

ROM_SIZE = 256 * 1024
ROM_BASE = 0xF80000
ENTRY_OFFSET = 0xD2
VERSION = 34

_LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s*(.*))?$")
_NUMBER = r"(\$[0-9A-Fa-f]+|\d+)"


def _number(text):
    return int(text[1:], 16) if text.startswith("$") else int(text)


def _words(line, line_number):
    """Encode one instruction as a list of 16-bit words, or ('bra', label) for a short branch."""
    match = re.fullmatch(r"MOVE\.W\s+#" + _NUMBER + r"\s*,\s*SR", line, re.I)
    if match:
        return [0x46FC, _number(match[1]) & 0xFFFF]
    match = re.fullmatch(r"LEA\s+" + _NUMBER + r"\.W\s*,\s*A([0-7])", line, re.I)
    if match:
        address = _number(match[1])
        if address >= 0x8000:
            raise ValueError("line %d: LEA absolute word out of range" % line_number)
        return [0x41F8 | (int(match[2]) << 9), address]
    match = re.fullmatch(r"MOVE\.B\s+#" + _NUMBER + r"\s*,\s*" + _NUMBER + r"\.L", line, re.I)
    if match:
        address = _number(match[2])
        return [0x13FC, _number(match[1]) & 0xFF, address >> 16, address & 0xFFFF]
    match = re.fullmatch(r"ADDQ\.L\s+#([1-8])\s*,\s*D([0-7])", line, re.I)
    if match:
        return [0x5080 | ((int(match[1]) & 7) << 9) | int(match[2])]
    if re.fullmatch(r"NOP", line, re.I):
        return [0x4E71]
    match = re.fullmatch(r"BRA\.S\s+([A-Za-z_][A-Za-z0-9_]*)", line, re.I)
    if match:
        return ("bra", match[1].lower())
    raise ValueError("line %d: unsupported syntax: %s" % (line_number, line))


def assemble(source):
    """Machine code of `source`, laid out from the entry point."""
    rows, labels, offset = [], {}, 0
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        label = _LABEL.match(line)
        if label:
            name = label[1].lower()
            if name in labels:
                raise ValueError("line %d: duplicate label %s" % (line_number, name))
            labels[name] = offset
            line = (label[2] or "").strip()
            if not line:
                continue
        encoded = _words(line, line_number)
        rows.append((offset, encoded, line_number))
        offset += 2 if isinstance(encoded, tuple) else 2 * len(encoded)
    out = bytearray()
    for at, encoded, line_number in rows:
        if isinstance(encoded, tuple):
            if encoded[1] not in labels:
                raise ValueError("line %d: undefined label %s" % (line_number, encoded[1]))
            displacement = labels[encoded[1]] - (at + 2)
            if displacement == 0 or displacement % 2 or not -128 <= displacement <= 126:
                raise ValueError("line %d: branch displacement out of range: %d" % (line_number, displacement))
            out += (0x6000 | (displacement & 0xFF)).to_bytes(2, "big")
        else:
            for word in encoded:
                out += word.to_bytes(2, "big")
    return bytes(out)


def checksum_field(image):
    """Value for the checksum long so that the ones-complement sum of all longs is 0xFFFFFFFF."""
    total = sum(int.from_bytes(image[i:i + 4], "big") for i in range(0, len(image), 4))
    while total >> 32:
        total = (total & 0xFFFFFFFF) + (total >> 32)
    return (~total) & 0xFFFFFFFF


def build(source):
    code = assemble(source)
    if ENTRY_OFFSET + len(code) > ROM_SIZE - 0x100:
        raise ValueError("program does not fit")
    image = bytearray(ROM_SIZE)
    image[0:8] = bytes([0x11, 0x11, 0x4E, 0xF9]) + (ROM_BASE + ENTRY_OFFSET).to_bytes(4, "big")
    image[12:14] = VERSION.to_bytes(2, "big")
    image[ENTRY_OFFSET:ENTRY_OFFSET + len(code)] = code
    image[ROM_SIZE - 20:ROM_SIZE - 16] = ROM_SIZE.to_bytes(4, "big")
    image[ROM_SIZE - 24:ROM_SIZE - 20] = checksum_field(image).to_bytes(4, "big")
    return bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("--output")
    args = parser.parse_args()
    image = build(Path(args.source).read_text(encoding="utf-8"))
    if args.output:
        Path(args.output).write_bytes(image)


if __name__ == "__main__":
    main()
