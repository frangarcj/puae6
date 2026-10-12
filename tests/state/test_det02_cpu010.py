"""T52: DET02 on a machine with a 68010 (p6_config.cpu_model, the A500 with the cycle-exact 68010 of upstream): a state taken in the middle of a program comes back to the machine of the original.

The 68010 block of the CPU chunk (bridge/state_cpu.cpp visit_010: the vector base register, SFC, DFC and the loop mode, 10 bytes after the body of the 68000) is compared the way every other chunk
is: the digest of the 68010 (bridge/state_probe.cpp cpu()) holds those fields, RAM and the PCM are compared unit by unit, in the same process and in a new one. The programs are the ones in
which a 68010 is in a place a 68000 never is at a unit boundary: inside a DBRA loop that runs in loop mode (the loop table of patch 0311: without the mode in the state, the first pass after the
load pays the cycles of a pass that enters the loop and the units after it are not the original's), with the vector table moved by VBR and the function code registers set while user mode and
supervisor mode alternate through traps and privilege violations, and in the exception that an address error is on a 68010 (format 8, 29 words on the stack). The programs the A500 already has
(DMA of the disk, the blitter, the copper, audio, the CIAs) run again with the 68010, whose cycle-exact bus is the 68000's. The machinery, the modes and what a divergence table is are in
tests/state/test_det02.py; nothing is relaxed here.
"""
import unittest

from tests.state import test_det02 as d
from tests.state import test_state_upstream as upstream

PROLOGUE = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #$4000,D0
    .word $4E7B,$0801            ; movec d0, vbr
"""

# A DBRA loop over one instruction that reads ROM (nothing delays it): the 68010 runs it in loop mode, so at nearly every boundary regs.loop_mode is set. The vertical blank interrupt of
# every unit takes the CPU out of loop mode and the loop is entered again, which is what the first pass after a return pays for. The handler logs how far the loop had got.
LOOP = "; PUAE6 test program (T52): a one-instruction DBRA loop on a 68010 (loop mode), interrupted every unit.\n" + PROLOGUE + """    MOVE.L #h,$406C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
main:
    MOVEA.L #$F80000,A2
    MOVE.L #30000,D0
lm:
    .word $4A5A                  ; tst.w (a2)+
    .word $51C8,$FFFC            ; ~ dbra d0, lm
    ADDQ.L #1,D4
    BRA.W main
h:
    MOVE.W #$0020,$DFF09C.L
    MOVE.L D0,$700.L
    MOVE.L D4,$704.L
    ADDQ.L #1,D3
    MOVE.L D3,$708.L
    RTE
"""

# The same loop with an odd register in it: ADD.W D1,(A3) is a read-modify-write on Chip RAM, so the loop mode meets the bus of the chipset (DMA slots taken, the CPU waiting).
LOOP_CHIP = LOOP.replace("""    MOVEA.L #$F80000,A2
    MOVE.L #30000,D0
lm:
    .word $4A5A                  ; tst.w (a2)+
""", """    MOVEA.L #$3000,A3
    MOVE.L #30000,D0
lm:
    .word $D353                  ; add.w d1, (a3)
""").replace("ADDQ.L #1,D4", "ADDQ.L #1,D4\n    ADDQ.L #3,D1")

# The vector base moved to $4000, SFC and DFC set once (nothing in the program writes them again, so only the state can put them back), MOVES through them, supervisor and user mode alternating: TRAP #0 (supervisor, stays), the privileged MOVE from SR in user mode
# (the privilege violation handler skips it), TRAP #1 (the handler stacks supervisor mode and returns there). The vertical blank handler logs the registers the 68010 added.
VBR = "; PUAE6 test program (T52): VBR, SFC, DFC, MOVES, user mode and traps on a 68010.\n" + PROLOGUE + """    MOVE.L #h,$406C.L
    MOVE.L #t0,$4080.L
    MOVE.L #t1,$4084.L
    MOVE.L #pv,$4020.L
    MOVEA.L #$3000,A1
    MOVE.L #$0A00,D0
    .word $4E7B,$0800            ; movec d0, usp
    MOVEQ #5,D0
    .word $4E7B,$0000            ; movec d0, sfc
    MOVEQ #2,D0
    .word $4E7B,$0001            ; movec d0, dfc
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
main:
    ADDQ.L #1,D5
    .word $0E91,$5800            ; moves.l d5, (a1)
    .word $0E91,$6000            ; moves.l (a1), d6
    TRAP #0
    MOVE.W #$0000,SR             ; user mode
    .word $40C7                  ; move.w sr, d7: privilege violation
    ADDQ.L #1,D2
    TRAP #1                      ; supervisor mode again
    BRA.W main
t0:
    ADDQ.L #1,D1
    RTE
t1:
    .word $3EBC,$2000            ; move.w #$2000, (a7)
    RTE
pv:
    .word $54AF,$0002            ; addq.l #2, 2(a7)
    RTE
h:
    MOVE.W #$0020,$DFF09C.L
    .word $4E7A,$3000            ; movec sfc, d3
    .word $4E7A,$4001            ; movec dfc, d4
    MOVE.L D3,$700.L
    MOVE.L D4,$704.L
    MOVE.L D5,$708.L
    MOVE.L D6,$70C.L
    MOVE.L D2,$710.L
    RTE
"""

# JMP to an odd address: the address error of a 68010 stacks the 29 words of format 8 and the RTE of the handler pops them. At a boundary the frame can be on the stack (the interrupt handler
# runs on top of it) and the program counts both in registers the handler logs.
ADDRESS_ERROR = "; PUAE6 test program (T52): the address error of a 68010, format 8.\n" + PROLOGUE + """    MOVE.L #h,$406C.L
    MOVE.L #ha,$400C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
    MOVEA.L #$3001,A2
main:
    ADDQ.L #1,D5
    .word $4ED2                  ; jmp (a2): odd address
    ADDQ.L #1,D7                 ; never executed
resume:
    BRA.W main
ha:
    ADDQ.L #1,D6
    .word $2F7C                  ; ~ move.l #resume, 2(a7)
    .long resume
    .word $0002
    RTE
h:
    MOVE.W #$0020,$DFF09C.L
    MOVE.L D5,$700.L
    MOVE.L D6,$704.L
    MOVE.L D7,$708.L
    RTE
"""

ROMS = {**d.Det02Tests.ROMS, "c10-loop": (LOOP, None, None), "c10-loop-chip": (LOOP_CHIP, None, None), "c10-vbr": (VBR, None, None), "c10-address": (ADDRESS_ERROR, None, None)}

SCENARIOS = {
    "c10-loop": ("c10-loop", [], (1, 2, 3, 5), d.CORE),
    "c10-loop-chip": ("c10-loop-chip", [], (2, 3), d.CORE),
    "c10-vbr": ("c10-vbr", [], (1, 2, 4), d.CORE),
    "c10-address": ("c10-address", [], (1, 3), d.CORE),
    # the programs of the A500 with the cycle-exact 68010 in it
    "vsync": ("vsync", [], (3,), d.CORE),
    "copper_irq": ("copper_irq", [], (2,), d.CORE),
    "tone": ("tone", [], (3,), d.CORE),
    "busy": ("busy", [], (2, 5), d.CORE),
    "busy-disk": ("busy", "disk", (8,), d.CORE),
    "stop": ("stop", [], (3,), d.SHAPES),
    "cias": ("cias", [], (8,), d.SHAPES),
}

# Where DET02 diverges on a 68010 and not on a 68000: none.
DIVERGENCES = {}

CPU = "68010"


class Cpu010Case(d.Det02Case):
    ROMS = ROMS

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        extra = [] if "--cpu" in arguments else ["--cpu", CPU]
        return super().probe_on(machine, rom, *arguments, *extra)


class Det02Cpu010Tests(Cpu010Case):
    """The programs above, with a 68010 in the A500."""


d.populate(Det02Cpu010Tests, SCENARIOS, DIVERGENCES)


class Det02Cpu010A600Tests(Cpu010Case):
    """The A600 takes a 68010 as well (the same socket): its ECS chipset and 1 MiB of Chip RAM against the 68010's bus."""

    MACHINE = "a600"


d.populate(Det02Cpu010A600Tests, {"c10-loop-chip": SCENARIOS["c10-loop-chip"], "c10-vbr": SCENARIOS["c10-vbr"], "vsync": SCENARIOS["vsync"]}, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
