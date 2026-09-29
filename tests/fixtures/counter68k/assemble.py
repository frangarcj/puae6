"""Assemble the deliberately tiny counter68k raw-program fixture.

Example: python3 assemble.py --output build/counter68k.bin
No output file is written unless --output is supplied.
"""

import argparse
import re
from pathlib import Path


_LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s*(.*))?$")
_MOVEQ = re.compile(r"^MOVEQ\s+#0\s*,\s*D0$", re.IGNORECASE)
_ADDQ = re.compile(r"^ADDQ\.L\s+#1\s*,\s*D0$", re.IGNORECASE)
_BRA = re.compile(r"^BRA\.S\s+([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE)


def _parse(source):
    rows = []
    labels = {}
    offset = 0
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        match = _LABEL.match(line)
        if match:
            name, remainder = match.groups()
            name = name.lower()
            if name in labels:
                raise ValueError("line %d: duplicate label %s" % (line_number, name))
            labels[name] = offset
            line = (remainder or "").strip()
            if not line:
                continue
        if _MOVEQ.fullmatch(line):
            kind, operand = "word", 0x7000
        elif _ADDQ.fullmatch(line):
            kind, operand = "word", 0x5280
        else:
            branch = _BRA.fullmatch(line)
            if branch:
                kind, operand = "branch", branch.group(1).lower()
            else:
                raise ValueError("line %d: unsupported syntax: %s" % (line_number, line))
        rows.append((offset, kind, operand, line_number))
        offset += 2
    return rows, labels


def compile(source, origin=0x1000):
    """Compile supported source text to big-endian 68000 instruction bytes."""
    if type(origin) is not int or origin < 0 or origin > 0xFFFFFFFF or origin & 1:
        raise ValueError("raw program origin must be an even 32-bit address")
    rows, labels = _parse(source)
    if not rows:
        raise ValueError("raw program must contain at least one instruction")
    result = bytearray()
    for offset, kind, operand, line_number in rows:
        if kind == "branch":
            if operand not in labels:
                raise ValueError("line %d: undefined label %s" % (line_number, operand))
            displacement = labels[operand] - (offset + 2)
            if displacement == 0 or displacement < -128 or displacement > 127:
                raise ValueError("line %d: BRA.S displacement out of range: %d" %
                                 (line_number, displacement))
            if displacement & 1:
                raise ValueError("line %d: BRA.S target is not word-aligned" % line_number)
            word = 0x6000 | (displacement & 0xFF)
        else:
            word = operand
        result.extend((word >> 8, word & 0xFF))
    if origin + len(result) > 0x100000000:
        raise ValueError("raw program extends past the 32-bit address space")
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("program.s"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_bytes(compile(args.source.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
