"""T54: DET02 on the A1200 with a 68882 (SoftFloat): a state taken in the middle of a program that computes with the FPU comes back to the machine of the original.

The FPU block of the CPU chunk (bridge/state_cpu.cpp visit_fpu: FP0-FP7 as 80-bit patterns, FPCR, FPSR, FPIAR, the FPU state, the exception posted for the next
instruction with the words of its mid-instruction frame, and the FSAVE data of the last exception) is compared the way every other chunk is: the digest of the A1200 with an
FPU (bridge/state_probe.cpp cpu()) holds those fields, RAM and the PCM are compared unit by unit, in the same process and in a new one. The programs are the ones in which
the FPU is in a different place at the boundary: arithmetic on a changing register file, every rounding mode and precision, an exception that waits for the next FPU
instruction while a thousand integer instructions pass, FSAVE and FRESTORE with stale exception data, FMOVEM and the control registers. The machinery, the modes and what a
divergence table is are in tests/state/test_det02.py; nothing is relaxed here.

Opt-in with the owner's Kickstart 3.1 (environment only, read in place; no copy, no hash and no byte of it is kept anywhere):
    P6_KICKSTART_A1200_ROM  the Kickstart 3.1 r40.68 .rom (A1200): the machine boots with a 68882 and the FPU is detected by exec
"""
from fractions import Fraction
from pathlib import Path
import os
import unittest

from tests.state import test_det02 as d
from tests.host import fpu_asm as f
from tests.host import fpu_ref as r

PROLOGUE = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
"""

FPU = "68882"


def constant(value, comment):
    ext, _ = r.round_value(Fraction(value), 0)       # 1.0001 and 1/3 are not exact: rounded to nearest, which is what the program then sees
    return f.extended_long(ext.sign, ext.exp, ext.man) + "     ; " + comment


# FP0..FP7 at the start: 1.5, 0.5, 1.0001 (inexact in binary), 3, 0.75, 1/3, 2, 0
CONSTANTS = [(Fraction(3, 2), "1.5"), (Fraction(1, 2), "0.5"), (Fraction(10001, 10000), "1.0001"), (Fraction(3), "3"), (Fraction(3, 4), "0.75"),
             (Fraction(1, 3), "1/3"), (Fraction(2), "2"), (Fraction(0), "0")]


def table():
    return "consts:\n" + "\n".join(constant(v, c) for v, c in CONSTANTS) + "\n"


def start(trap=False, fpcr=0):
    lines = [PROLOGUE, "    MOVEA.L #consts,A1\n"]
    lines += [f.cpgen_mem("fmove", f.EXTENDED, f.postinc(1), n) + "\n" for n in range(8)]
    lines.append("    MOVEA.L #$20000,A3          ; the log\n    MOVEA.L #$30000,A4          ; the FSAVE buffer\n")
    if trap:
        lines.append("    MOVE.L #trap,$C8.L          ; vector 50: FPCP divide by zero\n    MOVE.L #trap,$CC.L\n")
        lines.append(f.fmove_ctrl_in(f.FPCR, f.imm_long(0x0400)) + "\n")
    if fpcr:
        lines.append(f.fmove_ctrl_in(f.FPCR, f.imm_long(fpcr)) + "\n")
    lines.append("    MOVE.L #h,$6C.L\n    MOVE.W #$C020,$DFF09A.L\n    MOVE.W #$2000,SR\nloop:\n")
    return "".join(lines)


def handler():
    """The vertical-blank handler: logs what the FPU holds without changing it. FSAVE writes the frame and FRESTORE puts the same state back."""
    return "h:\n    MOVE.W #$0020,$DFF09C.L\n" + "\n".join([
        f.fmove_ctrl_out(f.FPSR, f.postinc(3)),
        f.fmove_ctrl_out(f.FPIAR, f.postinc(3)),
        f.fmove_out(f.EXTENDED, 0, f.postinc(3)),
        f.fsave(f.predec(7)),
        "    .word $26D7                 ; MOVE.L (A7),(A3)+: the first longword of the frame says whether the FPU is idle",
        "    .word $26EF,$0004           ; MOVE.L 4(A7),(A3)+: the command and condition word of an idle 68882 frame",
        "    .word $26EF,$0028           ; MOVE.L 40(A7),(A3)+: the exceptional operand of the frame, three longwords",
        "    .word $26EF,$002C           ; MOVE.L 44(A7),(A3)+",
        "    .word $26EF,$0030           ; MOVE.L 48(A7),(A3)+",
        f.frestore(f.postinc(7)),
        "    RTE",
    ]) + "\n"


# FP0 is the accumulator, FP1 0.5, FP2 1.0001, FP3 a square root over two, FP4 its sine, FP5 an arc tangent, FP6 two and FP7 a logarithm; the values stay finite and positive
def integer_handler():
    """The vertical-blank handler of the trap program: no FPU instruction in it, so that an exception posted by the program is still pending at the boundary (an FPU instruction of the
    handler would take it, and the handler runs before the boundary of its unit)."""
    return "h:\n    MOVE.W #$0020,$DFF09C.L\n    ADDQ.L #1,D3\n    RTE\n"


ARITH_BODY = "\n".join([
    f.cpgen_reg("fmul", 2, 0),           # fp0 *= 1.0001
    f.cpgen_reg("fadd", 1, 0),           # += 0.5
    f.cpgen_reg("fsqrt", 0, 3),
    f.cpgen_reg("fdiv", 6, 3),           # fp3 = sqrt(fp0) / 2
    f.cpgen_reg("fsin", 3, 4),
    f.cpgen_reg("fatan", 4, 5),
    f.cpgen_reg("fadd", 5, 0),
    f.cpgen_reg("fabs", 0, 0),
    f.cpgen_reg("flogn", 0, 7),
    f.cpgen_reg("fabs", 7, 7),
    f.cpgen_reg("fcmp", 1, 7),
    f.fbcc(f.CONDITIONS.index("ge"), 4),                # over the FNEG, 4 bytes
    f.cpgen_reg("fneg", 5, 5),
    f.cpgen_reg("fint", 3, 3),
    f.cpgen_reg("fsub", 3, 0),
    f.cpgen_reg("fabs", 0, 0),
]) + "\n    BRA.W loop\n"

# every rounding mode and precision in turn, with inexact operations in each
MODES_BODY = "".join(
    f.fmove_ctrl_in(f.FPCR, f.imm_long(prec << 6 | mode << 4)) + "\n"
    + "\n".join([f.cpgen_reg("fdiv", 2, 0), f.cpgen_reg("fadd", 5, 0), f.cpgen_reg("fmul", 5, 3), f.cpgen_reg("fsub", 1, 3), f.cpgen_reg("fsqrt", 3, 4),
                 f.cpgen_reg("fadd", 4, 0)]) + "\n"
    for prec in (0, 1, 2) for mode in range(4)) + "    BRA.W loop\n"

# a division by zero posts its exception when the divide is done and the next FPU instruction takes it: a loop of about a unit's length is in between, so most boundaries fall in the window
TRAP_BODY = "\n".join([
    f.cpgen_reg("fmove", 5, 2),
    f.cpgen_reg("fdiv", 7, 2),            # FP7 is zero: DZ with the trap enabled
]) + "\n    MOVE.L #20000,D2\nwait:\n    ADDQ.L #1,D0\n    .word $51CA,$FFFC           ; DBF D2,wait\n" + f.fnop() + "\n" + "    ADDQ.L #1,D1\n" * 300 + "    BRA.W loop\n"
TRAP_HANDLER = "trap:\n" + "\n".join([
    "    .word $26EF,$0004           ; MOVE.L 4(A7),(A3)+: the low half of the PC and the format and vector word of the frame the exception pushed",
    f.fsave(f.predec(7)),
    "    .word $26D7                 ; MOVE.L (A7),(A3)+: the frame id",
    "    .word $26EF,$0004           ; MOVE.L 4(A7),(A3)+: the command and condition word",
    "    .word $26EF,$0028           ; MOVE.L 40(A7),(A3)+: the exceptional operand: the dividend",
    "    .word $26EF,$002C           ; MOVE.L 44(A7),(A3)+",
    "    .word $26EF,$0030           ; MOVE.L 48(A7),(A3)+",
    f.fmove_ctrl_out(f.FPSR, f.postinc(3)),
    f.fmove_ctrl_out(f.FPIAR, f.postinc(3)),
    f.frestore(f.postinc(7)),
    f.fmove_ctrl_in(f.FPSR, f.imm_long(0)),
    "    ADDQ.L #1,D5",
    "    RTE",
]) + "\n"
# FMOVE.L #0,FPSR clears the status the trap left, as a real handler does before returning

FRAMES_BODY = "\n".join([
    f.fmovem_out([0, 1, 2], f.predec(7), True),
    f.fmovem_in([4, 5, 6], f.postinc(7)),
    f.fmove_ctrl_out(f.FPCR | f.FPSR | f.FPIAR, f.predec(7)),
    f.cpgen_reg("fmul", 2, 4),
    f.fmove_ctrl_in(f.FPCR | f.FPSR | f.FPIAR, f.postinc(7)),
    f.fsave(f.ind(4)),
    f.cpgen_reg("fadd", 4, 5),
    f.frestore(f.ind(4)),
    f.fmove_out(f.DOUBLE, 5, f.postinc(3)),
    f.fmove_out(f.SINGLE, 4, f.postinc(3)),
    f.fmove_out(f.LONG, 0, f.postinc(3)),
    f.fmove_out(f.WORD, 1, f.postinc(3)),
]) + "\n    BRA.W loop\n"


def program(body, trap=False, extra="", fpcr=0):
    return "; PUAE6 test program (T54): the FPU in the middle of a computation.\n" + start(trap, fpcr) + body + (integer_handler() if trap else handler()) + extra + table()


ROMS = {
    "fpu-arith": (program(ARITH_BODY), None, None),
    "fpu-modes": (program(MODES_BODY), None, None),
    "fpu-rounding": (program(ARITH_BODY, fpcr=0xA0), None, None),         # precision double, rounding toward minus infinity, set once: a loaded machine has to get it from the state
    "fpu-trap": (program(TRAP_BODY, True, TRAP_HANDLER), None, None),
    "fpu-frames": (program(FRAMES_BODY), None, None),
}

SCENARIOS = {
    "fpu-arith": ("fpu-arith", [], (2, 3, 5), d.CORE),
    "fpu-modes": ("fpu-modes", [], (2, 4), d.SHAPES),
    "fpu-rounding": ("fpu-rounding", [], (2, 3), d.CORE),
    "fpu-trap": ("fpu-trap", [], (2, 3, 4, 5), d.CORE),
    "fpu-frames": ("fpu-frames", [], (3,), d.SHAPES),
}


class FpuCase(d.Det02Case):
    MACHINE = "a1200"
    ROMS = ROMS

    def arguments(self, extra):
        return [*super().arguments(extra), "--fpu", FPU]


class Det02FpuTests(FpuCase):
    """The programs above, with a 68882."""


d.populate(Det02FpuTests, SCENARIOS, {})

KICKSTART_ROM = os.environ.get("P6_KICKSTART_A1200_ROM")


@unittest.skipUnless(KICKSTART_ROM and Path(KICKSTART_ROM).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized Kickstart 3.1 (A1200)")
class Det02FpuKickstartTests(FpuCase):
    """The owner's Kickstart 3.1 booting with a 68882: exec finds the coprocessor and the state is taken while it does."""

    ROMS = {}

    @classmethod
    def firmware(cls):
        return Path(KICKSTART_ROM)


d.populate(Det02FpuKickstartTests, {"kickstart-fpu": ("kickstart", [], (60, 140), (d.SAME_BEAM, d.RESET_BEAM, d.NEW))}, {})


if __name__ == "__main__":
    unittest.main()
