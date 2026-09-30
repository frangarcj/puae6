"""Build a Kickstart-shaped 256 KiB micro-ROM from a tiny 68000 assembly subset.

The engine accepts a 256 KiB image when byte 0/1 are 0x11 0x11, the length field at size-20 holds the
size and (for the bridge) the Kickstart checksum is valid. The reset vectors are the first two longs
(a real Kickstart stores 0x11114EF9 and the entry address there), so the entry point is a ROM address.

    python3 build_rom.py boot.s --output boot.rom        # nothing is written without --output
    python3 build_rom.py copper_irq.s --define HP=2 --output copper_irq.rom

Supported subset, encoded as the real 68000 opcodes (anything else is rejected as "unsupported syntax"):
    MOVE.B|W|L #imm,abs.L    MOVE.W #imm,SR    MOVE.L #imm,Dn    MOVEA.L #imm,An    MOVE.L Dn,(An)+
    LEA abs.W,An   DIVU.W Dm,Dn   ADDQ.L #n,Dn   NOP   RTE   STOP #imm   TRAP #n
    BRA.S label    BRA.W label    MOVEM.L list,-(An)    MOVEM.L (An)+,list      (An is A0..A7 or SP)
    .word v,...    .long v,...    .nops N (N NOP words)    .align 2 (every item is word sized: a no-op)
A value is a constant expression over numbers ($hex or decimal), labels (their absolute ROM address) and
( ) | & << >> + - * with C precedence. Register lists are D0-D7/A0-A6 style. Labels are case insensitive.
`@NAME@` in the source is replaced by the value of `--define NAME=value` before assembling; a placeholder
without a value, or a value the source does not use, is an error.
"""
import argparse
import collections
from pathlib import Path
import re

ROM_SIZE = 256 * 1024
ROM_BASE = 0xF80000
ENTRY_OFFSET = 0xD2
VERSION = 34

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_LABEL = re.compile(r"^(" + _IDENT + r"):(?:\s*(.*))?$")
_NUMBER = r"(\$[0-9A-Fa-f]+|\d+)"
_AREG = r"(A[0-7]|SP)"
_PLACEHOLDER = re.compile(r"@(" + _IDENT + r")@")
_TOKEN = re.compile(r"\s*(?:(\$[0-9A-Fa-f]+|\d+)|(" + _IDENT + r")|(<<|>>|[-+*|&()]))")
_LEVELS = (("|",), ("&",), ("<<", ">>"), ("+", "-"), ("*",))  # lowest to highest precedence, as in C
_OPERATORS = {
    "|": lambda a, b: a | b, "&": lambda a, b: a & b, "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b, "+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
}

# A value computed once every label is known (`size` bytes, range-checked to [low, high], then masked).
_Value = collections.namedtuple("_Value", "expr size low high mask what line_number")
# BRA.S (one word) or BRA.W (two words) to a label.
_Branch = collections.namedtuple("_Branch", "label wide line_number")


def _number(text):
    return int(text[1:], 16) if text.startswith("$") else int(text)


def _areg(text):
    return 7 if text.upper() == "SP" else int(text[1])


def _unsupported(line, line_number):
    return ValueError("line %d: unsupported syntax: %s" % (line_number, line))


def _immediate(expr, bits, line_number, what="immediate"):
    """A `bits` wide value; a byte occupies a whole word, like MOVE.B #imm."""
    mask = (1 << bits) - 1
    return _Value(expr, 4 if bits == 32 else 2, -(1 << (bits - 1)), mask, mask, what, line_number)


def _address(expr, line_number):
    return _Value(expr, 4, 0, 0xFFFFFFFF, 0xFFFFFFFF, "absolute address", line_number)


def _short_address(expr, line_number):
    return _Value(expr, 2, 0, 0x7FFF, 0xFFFF, "LEA absolute word", line_number)


def _tokens(text, line_number):
    tokens, at, text = [], 0, text.strip()
    while at < len(text):
        match = _TOKEN.match(text, at)
        if not match:
            raise _unsupported(text, line_number)
        at = match.end()
        if match[1]:
            tokens.append(("number", _number(match[1])))
        elif match[2]:
            tokens.append(("label", match[2].lower()))
        else:
            tokens.append(("op", match[3]))
    return tokens


def _evaluate(text, labels, line_number):
    """Value of a constant expression; a label is its absolute ROM address."""
    tokens, position = _tokens(text, line_number), 0

    def peek():
        return tokens[position] if position < len(tokens) else (None, None)

    def take():
        nonlocal position
        token = peek()
        position += 1
        return token

    def primary():
        kind, value = take()
        if kind == "number":
            return value
        if kind == "label":
            if value not in labels:
                raise ValueError("line %d: undefined label %s" % (line_number, value))
            return ROM_BASE + ENTRY_OFFSET + labels[value]
        if (kind, value) == ("op", "-"):
            return -primary()
        if (kind, value) == ("op", "("):
            result = binary(0)
            if take() != ("op", ")"):
                raise _unsupported(text, line_number)
            return result
        raise _unsupported(text, line_number)

    def binary(level):
        if level == len(_LEVELS):
            return primary()
        result = binary(level + 1)
        while peek()[0] == "op" and peek()[1] in _LEVELS[level]:
            operator = take()[1]
            operand = binary(level + 1)
            if operator in ("<<", ">>") and not 0 <= operand <= 32:
                raise ValueError("line %d: shift count out of range: %d" % (line_number, operand))
            result = _OPERATORS[operator](result, operand)
        return result

    result = binary(0)
    if position != len(tokens):
        raise _unsupported(text, line_number)
    return result


def _resolve(part, labels):
    value = _evaluate(part.expr, labels, part.line_number)
    if not part.low <= value <= part.high:
        raise ValueError("line %d: %s out of range: %d" % (part.line_number, part.what, value))
    return (value & part.mask).to_bytes(part.size, "big")


def _register_index(name):
    """0..7 for D0..D7, 8..15 for A0..A7 (SP is A7); None when `name` is not a register."""
    match = re.fullmatch(r"(?:([DA])([0-7])|(SP))", name, re.I)
    if not match:
        return None
    if match[3]:
        return 15
    return (8 if match[1].upper() == "A" else 0) + int(match[2])


def _register_mask(text):
    """MOVEM mask with bit 0 = D0 ... bit 15 = A7 for D0-D3/A0/A2-A4 style lists; None if malformed."""
    mask = 0
    for item in text.split("/"):
        ends = [_register_index(name) for name in item.split("-")]
        if len(ends) > 2 or None in ends or (len(ends) == 2 and (ends[0] > ends[1] or ends[0] // 8 != ends[1] // 8)):
            return None
        mask |= sum(1 << bit for bit in range(ends[0], ends[-1] + 1))
    return mask


def _parts(line, line_number):
    """Encode one line as a list of parts: 16-bit opcode/extension words, _Value and _Branch."""
    match = re.fullmatch(r"MOVE\.W\s+#(.+?)\s*,\s*SR", line, re.I)
    if match:
        return [0x46FC, _immediate(match[1], 16, line_number)]
    match = re.fullmatch(r"LEA\s+(.+?)\.W\s*,\s*" + _AREG, line, re.I)
    if match:
        return [0x41F8 | (_areg(match[2]) << 9), _short_address(match[1], line_number)]
    match = re.fullmatch(r"MOVE\.([BWL])\s+#(.+?)\s*,\s*(.+?)\.L", line, re.I)
    if match:
        opcode, bits = {"B": (0x13FC, 8), "W": (0x33FC, 16), "L": (0x23FC, 32)}[match[1].upper()]
        return [opcode, _immediate(match[2], bits, line_number), _address(match[3], line_number)]
    match = re.fullmatch(r"MOVE\.L\s+#(.+?)\s*,\s*D([0-7])", line, re.I)
    if match:
        return [0x203C | (int(match[2]) << 9), _immediate(match[1], 32, line_number)]
    match = re.fullmatch(r"MOVE\.([WL])\s+D([0-7])\s*,\s*(.+?)\.L", line, re.I)
    if match:
        opcode = 0x33C0 if match[1].upper() == "W" else 0x23C0
        return [opcode | int(match[2]), _address(match[3], line_number)]
    match = re.fullmatch(r"EXG\s+D([0-7])\s*,\s*D([0-7])", line, re.I)
    if match:
        return [0xC140 | (int(match[1]) << 9) | int(match[2])]
    match = re.fullmatch(r"MOVEA\.L\s+#(.+?)\s*,\s*" + _AREG, line, re.I)
    if match:
        return [0x207C | (_areg(match[2]) << 9), _immediate(match[1], 32, line_number)]
    match = re.fullmatch(r"MOVE\.L\s+D([0-7])\s*,\s*\(" + _AREG + r"\)\+", line, re.I)
    if match:
        return [0x20C0 | (_areg(match[2]) << 9) | int(match[1])]
    match = re.fullmatch(r"DIVU\.W\s+D([0-7])\s*,\s*D([0-7])", line, re.I)
    if match:
        return [0x80C0 | (int(match[2]) << 9) | int(match[1])]
    match = re.fullmatch(r"ADDQ\.L\s+#([1-8])\s*,\s*D([0-7])", line, re.I)
    if match:
        return [0x5080 | ((int(match[1]) & 7) << 9) | int(match[2])]
    if re.fullmatch(r"NOP", line, re.I):
        return [0x4E71]
    if re.fullmatch(r"RTE", line, re.I):
        return [0x4E73]
    match = re.fullmatch(r"STOP\s+#(.+)", line, re.I)
    if match:
        return [0x4E72, _immediate(match[1], 16, line_number)]
    match = re.fullmatch(r"TRAP\s+#" + _NUMBER, line, re.I)
    if match:
        if _number(match[1]) > 15:
            raise ValueError("line %d: TRAP vector out of range: %d" % (line_number, _number(match[1])))
        return [0x4E40 | _number(match[1])]
    match = re.fullmatch(r"BRA\.([SW])\s+(" + _IDENT + ")", line, re.I)
    if match:
        return [_Branch(match[2].lower(), match[1].upper() == "W", line_number)]
    match = re.fullmatch(r"MOVEM\.L\s+([A-Za-z0-9/-]+)\s*,\s*-\(" + _AREG + r"\)", line, re.I)
    if match:
        mask = _register_mask(match[1])
        if mask is None:
            raise _unsupported(line, line_number)
        # Predecrement stores the list mirrored: bit 0 = A7 ... bit 15 = D0.
        return [0x48E0 | _areg(match[2]), int(format(mask, "016b")[::-1], 2)]
    match = re.fullmatch(r"MOVEM\.L\s+\(" + _AREG + r"\)\+\s*,\s*([A-Za-z0-9/-]+)", line, re.I)
    if match:
        mask = _register_mask(match[2])
        if mask is None:
            raise _unsupported(line, line_number)
        return [0x4CD8 | _areg(match[1]), mask]
    match = re.fullmatch(r"\.(word|long)\s+(.+)", line, re.I)
    if match:
        bits = 16 if match[1].lower() == "word" else 32
        return [_immediate(item, bits, line_number, "value") for item in match[2].split(",")]
    match = re.fullmatch(r"\.nops\s+" + _NUMBER, line, re.I)
    if match:
        if _number(match[1]) > ROM_SIZE // 2:
            raise ValueError("line %d: .nops count out of range: %d" % (line_number, _number(match[1])))
        return [0x4E71] * _number(match[1])
    if re.fullmatch(r"\.align\s+2", line, re.I):
        return []  # every item is a whole number of words, so the location counter is already even
    raise _unsupported(line, line_number)


def _size(part):
    if isinstance(part, int):
        return 2
    return part.size if isinstance(part, _Value) else (4 if part.wide else 2)


def _branch(part, at, labels):
    if part.label not in labels:
        raise ValueError("line %d: undefined label %s" % (part.line_number, part.label))
    displacement = labels[part.label] - (at + 2)  # measured from the displacement word / extension word
    limit = 32768 if part.wide else 128
    if displacement == 0 or displacement % 2 or not -limit <= displacement <= limit - 2:
        raise ValueError("line %d: branch displacement out of range: %d" % (part.line_number, displacement))
    if part.wide:
        return (0x6000).to_bytes(2, "big") + (displacement & 0xFFFF).to_bytes(2, "big")
    return (0x6000 | (displacement & 0xFF)).to_bytes(2, "big")


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
        for part in _parts(line, line_number):
            rows.append((offset, part))
            offset += _size(part)
    out = bytearray()
    for at, part in rows:
        if isinstance(part, int):
            out += part.to_bytes(2, "big")
        elif isinstance(part, _Value):
            out += _resolve(part, labels)
        else:
            out += _branch(part, at, labels)
    return bytes(out)


def substitute(source, defines=None):
    """Replace every `@NAME@` by its define; a missing or an unused define is a ValueError."""
    values = {}
    for name, value in (defines or {}).items():
        if not re.fullmatch(_IDENT, str(name)):
            raise ValueError("bad define name: %s" % (name,))
        value = str(value).strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError("bad value for define %s: %r" % (name, value))
        values[str(name)] = value
    used = set()

    def replace(match):
        if match[1] not in values:
            raise ValueError("undefined placeholder @%s@ (pass --define %s=value)" % (match[1], match[1]))
        used.add(match[1])
        return values[match[1]]

    text = _PLACEHOLDER.sub(replace, source)
    if set(values) - used:
        raise ValueError("unused define: %s" % ", ".join(sorted(set(values) - used)))
    return text


def checksum_field(image):
    """Value for the checksum long so that the ones-complement sum of all longs is 0xFFFFFFFF."""
    total = sum(int.from_bytes(image[i:i + 4], "big") for i in range(0, len(image), 4))
    while total >> 32:
        total = (total & 0xFFFFFFFF) + (total >> 32)
    return (~total) & 0xFFFFFFFF


def build(source, defines=None):
    code = assemble(substitute(source, defines))
    if ENTRY_OFFSET + len(code) > ROM_SIZE - 0x100:
        raise ValueError("program does not fit")
    image = bytearray(ROM_SIZE)
    image[0:8] = bytes([0x11, 0x11, 0x4E, 0xF9]) + (ROM_BASE + ENTRY_OFFSET).to_bytes(4, "big")
    image[12:14] = VERSION.to_bytes(2, "big")
    image[ENTRY_OFFSET:ENTRY_OFFSET + len(code)] = code
    # The 68000 interrupt-acknowledge cycle reads the vector number from 0xFFFFF1 | level << 1, which the
    # ROM mirror serves: a real Kickstart ends with these bytes (24 + level for level 1..7).
    for level in range(1, 8):
        image[ROM_SIZE - 16 + 1 + 2 * level] = 24 + level
    image[ROM_SIZE - 20:ROM_SIZE - 16] = ROM_SIZE.to_bytes(4, "big")
    image[ROM_SIZE - 24:ROM_SIZE - 20] = checksum_field(image).to_bytes(4, "big")
    return bytes(image)


def _define(text):
    name, separator, value = text.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("expected NAME=value, got %r" % text)
    return name, value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("--output")
    parser.add_argument("--define", action="append", default=[], type=_define, metavar="NAME=value",
                        help="value for the placeholder @NAME@ of the source (repeatable)")
    args = parser.parse_args()
    names = [name for name, _ in args.define]
    if len(names) != len(set(names)):
        parser.error("a define is given twice")
    try:
        image = build(Path(args.source).read_text(encoding="utf-8"), dict(args.define))
    except ValueError as error:
        raise SystemExit("build_rom.py: %s" % error)
    if args.output:
        Path(args.output).write_bytes(image)


if __name__ == "__main__":
    main()
