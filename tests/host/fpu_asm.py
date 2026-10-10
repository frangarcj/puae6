"""Encoders for the M68881/M68882 coprocessor instructions as `.word` lines of the micro-ROM assembler (T54).

The assembler of tests/fixtures/microrom only knows the 68000 subset, so a coprocessor instruction is spelled as its words with the
disassembly in the comment (the 68020 program of T24a does the same). Everything here is table-driven from the Motorola manual and is
checked against Capstone's M68K disassembler when it is installed (tests/host/test_cpu_fpu.py).

An effective address is a (mode/register bits, extension words) pair; the helpers below build the ones the corpus needs.
"""

# source / destination format field
LONG, SINGLE, EXTENDED, PACKED, WORD, DOUBLE, BYTE, PACKED_DYNAMIC = range(8)
FORMAT_NAME = {LONG: "l", SINGLE: "s", EXTENDED: "x", PACKED: "p", WORD: "w", DOUBLE: "d", BYTE: "b", PACKED_DYNAMIC: "p"}
FORMAT_BYTES = {LONG: 4, SINGLE: 4, EXTENDED: 12, PACKED: 12, WORD: 2, DOUBLE: 8, BYTE: 1, PACKED_DYNAMIC: 12}

# operation field of the general instruction (cpGEN) of the FPU
OPMODE = {
    "fmove": 0x00, "fint": 0x01, "fsinh": 0x02, "fintrz": 0x03, "fsqrt": 0x04, "flognp1": 0x06, "fetoxm1": 0x08, "ftanh": 0x09, "fatan": 0x0A,
    "fasin": 0x0C, "fatanh": 0x0D, "fsin": 0x0E, "ftan": 0x0F, "fetox": 0x10, "ftwotox": 0x11, "ftentox": 0x12, "flogn": 0x14, "flog10": 0x15,
    "flog2": 0x16, "fabs": 0x18, "fcosh": 0x19, "fneg": 0x1A, "facos": 0x1C, "fcos": 0x1D, "fgetexp": 0x1E, "fgetman": 0x1F, "fdiv": 0x20,
    "fmod": 0x21, "fadd": 0x22, "fmul": 0x23, "fsgldiv": 0x24, "frem": 0x25, "fscale": 0x26, "fsglmul": 0x27, "fsub": 0x28, "fcmp": 0x38, "ftst": 0x3A,
}
FSINCOS = 0x30            # 0x30 + the register that receives the cosine

# the 32 predicates of FBcc, FScc, FDBcc and FTRAPcc
CONDITIONS = ["f", "eq", "ogt", "oge", "olt", "ole", "ogl", "or", "un", "ueq", "ugt", "uge", "ult", "ule", "ne", "t",
              "sf", "seq", "gt", "ge", "lt", "le", "gl", "gle", "ngle", "ngl", "ngt", "nge", "nlt", "nle", "sne", "st"]

# FPCR / FPSR / FPIAR selection bits of the control register move
FPCR, FPSR, FPIAR = 4, 2, 1


def dn(n):
    return (n, [], "d%d" % n)


def ind(n):
    return (0x10 | n, [], "(a%d)" % n)


def postinc(n):
    return (0x18 | n, [], "(a%d)+" % n)


def predec(n):
    return (0x20 | n, [], "-(a%d)" % n)


def disp16(n, d):
    return (0x28 | n, [d & 0xFFFF], "%d(a%d)" % (d, n))


def abs_long(address):
    return (0x39, [address >> 16 & 0xFFFF, address & 0xFFFF], "$%x.l" % address)


def imm_long(value):
    """An immediate longword (FMOVE.L #imm,FPcr and the long and single sources)."""
    return (0x3C, [value >> 16 & 0xFFFF, value & 0xFFFF], "#$%x" % (value & 0xFFFFFFFF))


def imm_words(*words):
    """An immediate operand given as its words (a byte or a word is one word, a double four, an extended or packed operand six)."""
    return (0x3C, list(words), "#imm")


def _line(words, text):
    return "    .word " + ",".join("$%04X" % (w & 0xFFFF) for w in words) + "            ; " + text


def cpgen_reg(op, src, dst, text=None, k=0):
    """FOP.X FPsrc,FPdst (register to register)."""
    ext = src << 10 | dst << 7 | (OPMODE[op] if isinstance(op, str) else op)
    name = op if isinstance(op, str) else "fsincos"
    return _line([0xF200, ext], text or "%s.x fp%d,fp%d" % (name, src, dst))


def cpgen_mem(op, size, ea, dst, text=None):
    """FOP.<size> <ea>,FPdst (memory or immediate to register)."""
    mode, words, ea_text = ea
    ext = 0x4000 | size << 10 | dst << 7 | (OPMODE[op] if isinstance(op, str) else op)
    name = op if isinstance(op, str) else "fsincos"
    return _line([0xF200 | mode, ext, *words], text or "%s.%s %s,fp%d" % (name, FORMAT_NAME[size], ea_text, dst))


def fmove_out(size, src, ea, k=0, text=None):
    """FMOVE.<size> FPsrc,<ea> (the packed formats take the k-factor in the low 7 bits)."""
    mode, words, ea_text = ea
    return _line([0xF200 | mode, 0x6000 | size << 10 | src << 7 | (k & 0x7F), *words], text or "fmove.%s fp%d,%s" % (FORMAT_NAME[size], src, ea_text))


def fmovecr(offset, dst, text=None):
    return _line([0xF200, 0x5C00 | dst << 7 | offset], text or "fmovecr.x #$%02x,fp%d" % (offset, dst))


def _control_names(regs):
    return "/".join(name for bit, name in ((4, "fpcr"), (2, "fpsr"), (1, "fpiar")) if regs & bit)


def fmove_ctrl_in(regs, ea, text=None):
    """FMOVE(M) <ea>,FPcr[/FPcr...]"""
    mode, words, ea_text = ea
    return _line([0xF200 | mode, 0x8000 | regs << 10, *words], text or "%s.l %s,%s" % ("fmove" if regs in (1, 2, 4) else "fmovem", ea_text, _control_names(regs)))


def fmove_ctrl_out(regs, ea, text=None):
    mode, words, ea_text = ea
    return _line([0xF200 | mode, 0xA000 | regs << 10, *words], text or "%s.l %s,%s" % ("fmove" if regs in (1, 2, 4) else "fmovem", _control_names(regs), ea_text))


def _mask(registers, reversed_order):
    """The register-list byte: FP0 is bit 7 for control and post-increment addressing and bit 0 for pre-decrement addressing."""
    return sum(1 << (r if reversed_order else 7 - r) for r in registers)


def fmovem_in(registers, ea, text=None):
    """FMOVEM.X <ea>,<registers> (static list, post-increment or control addressing)."""
    mode, words, ea_text = ea
    return _line([0xF200 | mode, 0xD000 | _mask(registers, False), *words], text or "fmovem.x %s,{%s}" % (ea_text, ",".join("fp%d" % r for r in registers)))


def fmovem_out(registers, ea, predecrement=False, text=None):
    """FMOVEM.X <registers>,<ea>: -(An) (static list) stores the highest register first and reverses the mask; (An), (An)+ and the others use the normal mask."""
    mode, words, ea_text = ea
    ext = (0xE000 if predecrement else 0xF000) | _mask(registers, predecrement)
    return _line([0xF200 | mode, ext, *words], text or "fmovem.x {%s},%s" % (",".join("fp%d" % r for r in registers), ea_text))


def fmovem_dynamic_out(data_register, ea, predecrement=False, text=None):
    """FMOVEM.X D<n>,<ea>: the list is the low byte of the data register (bit order as in the static forms)."""
    mode, words, ea_text = ea
    return _line([0xF200 | mode, (0xE800 if predecrement else 0xF800) | data_register << 4, *words], text or "fmovem.x d%d,%s" % (data_register, ea_text))


def fmovem_dynamic_in(data_register, ea, text=None):
    mode, words, ea_text = ea
    return _line([0xF200 | mode, 0xD800 | data_register << 4, *words], text or "fmovem.x %s,d%d" % (ea_text, data_register))


def fbcc(cc, skip=0, text=None):
    """FBcc.W that jumps over the `skip` bytes that follow it (the displacement counts from its own word, two bytes before the next instruction)."""
    return _line([0xF280 | cc, (skip + 2) & 0xFFFF], text or "fb%s.w +%d" % (CONDITIONS[cc], skip))


def fscc(cc, ea, text=None):
    mode, words, ea_text = ea
    return _line([0xF240 | mode, cc, *words], text or "fs%s %s" % (CONDITIONS[cc], ea_text))


def fdbcc(cc, data_register, skip=0, text=None):
    """FDBcc that, when the loop goes on, jumps over the `skip` bytes that follow it (a negative skip goes back: the displacement counts from its own word)."""
    return _line([0xF248 | data_register, cc, (skip + 2) & 0xFFFF], text or "fdb%s d%d,%+d" % (CONDITIONS[cc], data_register, skip))


def ftrapcc(cc, operand=None, text=None):
    """FTRAPcc: operand None (no operand), or ("w", value) / ("l", value)."""
    if operand is None:
        return _line([0xF27C, cc], text or "ftrap%s" % CONDITIONS[cc])
    kind, value = operand
    if kind == "w":
        return _line([0xF27A, cc, value], text or "ftrap%s.w #%d" % (CONDITIONS[cc], value))
    return _line([0xF27B, cc, value >> 16, value], text or "ftrap%s.l #%d" % (CONDITIONS[cc], value))


def fnop():
    return _line([0xF280, 0x0000], "fnop")


def fsave(ea, text=None):
    mode, words, ea_text = ea
    return _line([0xF300 | mode, *words], text or "fsave %s" % ea_text)


def frestore(ea, text=None):
    mode, words, ea_text = ea
    return _line([0xF340 | mode, *words], text or "frestore %s" % ea_text)


def extended_words(sign, exp, man):
    """The six words of an extended-precision immediate or table entry: sign and exponent, a zero word, the 64 bits of significand."""
    return [sign << 15 | exp, 0, man >> 48 & 0xFFFF, man >> 32 & 0xFFFF, man >> 16 & 0xFFFF, man & 0xFFFF]


def extended_long(sign, exp, man):
    """The three longwords of the same, for `.long` lines of a table."""
    return "    .long $%08X,$%08X,$%08X" % (sign << 31 | exp << 16, man >> 32, man & 0xFFFFFFFF)
