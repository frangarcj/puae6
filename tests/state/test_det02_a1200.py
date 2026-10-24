"""T24c: DET02 on the A1200 (68EC020 cycle exact, AGA, 2 MiB Chip, no clock chip): the 68000 programs of tests/state/test_det02.py that make sense on a 020, the scenarios of the
68020 itself (instruction cache and CACR, VBR, the master stack and T0, the exception frames, 020 instructions and unaligned long accesses to Chip RAM) and the scenarios of AGA
(eight bitplanes and the 24-bit palette, FMODE written in a loop so that its delayed event is in flight at the boundary, 64-bit sprites, a Copper that changes palette banks mid
frame, HAM8), then the owner's Kickstart 3.1 and an AGA demo (opt-in).

The machinery, the modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here: a scenario that diverges only on the A1200 has to be a reproducer
and an attribution (DIVERGENCES below), not a looser digest. The digest of the A1200 carries what P6CP's 020 block carries (see bridge/state_probe.cpp cpu()) and the AGA palette
(chip group "aga", compared here and not on the other machines).

Opt-in with the owner's firmware (environment only, read in place; no copy, no hash and no byte of it is kept anywhere):
    P6_KICKSTART_A1200_ROM  the Kickstart 3.1 r40.68 .rom (A1200, 512 KiB)
    P6_AGA_ADF              an AGA demo disk image, mounted in DF0 at boot
"""
from pathlib import Path
import os
import unittest

from tests.state import test_det02 as d
from tests.state import test_state_upstream as upstream
from tests.state.harness import ProbeCase, TRACE, USER

PROLOGUE = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
"""

# -- 68020 -----------------------------------------------------------------------------------------------------------------------------------------------------------------------

# The instruction cache: CACR enabled and cleared, then frozen and enabled again by two vertical-blank handlers that take turns, over a loop longer than the 256 bytes of the cache
# (so that lines are filled, hit and replaced), with the vectors in a table that VBR moved to $4000 and CAAR written.
CACHE020 = "; PUAE6 test program: the 68020 instruction cache under CACR, and the interrupt vectors behind VBR.\n" + PROLOGUE + """    MOVE.L #$4000,D0
    .word $4E7B,$0801           ; MOVEC D0,VBR
    MOVE.L #h_a,$406C.L
    MOVE.L #$00000009,D0
    .word $4E7B,$0002           ; MOVEC D0,CACR: enable and clear
    MOVE.L #$00001234,D0
    .word $4E7B,$0802           ; MOVEC D0,CAAR
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    MOVEA.L #$20000,A0
""" + "".join("    ADDQ.L #1,D0\n    MOVE.L D0,(A0)+\n" for _ in range(100)) + """    BRA.W loop
h_a:
    MOVE.W #$0020,$DFF09C.L
    MOVE.L #$00000003,D7
    .word $4E7B,$7002           ; MOVEC D7,CACR: enabled and frozen
    MOVE.L #h_b,$406C.L
    RTE
h_b:
    MOVE.W #$0020,$DFF09C.L
    MOVE.L #$0000000D,D7
    .word $4E7B,$7002           ; MOVEC D7,CACR: enabled, clear one entry (CAAR) and the whole cache
    MOVE.L #h_a,$406C.L
    RTE
"""

# The master stack (M) and the trace-on-change-of-flow bit (T0) with the vectors behind VBR: @SR@ is the status register the loop runs in. An interrupt is taken on the interrupt
# stack and clears M for its handler; the RTE puts it back. With T0 every taken branch takes a trace exception (format 2 frame).
MASTER = "; PUAE6 test program: the master stack and T0 of the 68020, with the interrupt vector table behind VBR and MSP at $5000.\n" + PROLOGUE + """    MOVE.L #$4000,D0
    .word $4E7B,$0801           ; MOVEC D0,VBR
    MOVE.L #h,$406C.L
    MOVE.L #trace,$4024.L
    MOVE.L #$5000,D0
    .word $4E7B,$0803           ; MOVEC D0,MSP
    MOVE.L #$00000007,D0
    .word $4E7B,$0000           ; MOVEC D0,SFC
    .word $4E7B,$0001           ; MOVEC D0,DFC
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #@SR@,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
h:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    RTE
trace:
    ADDQ.L #1,D2
    RTE
"""

# A loop of the exceptions with frames of format 0 (illegal, TRAP, line A and F, privilege from the user mode is not here) and 2 (zero divide), each handler skipping what raised it.
EXCEPTIONS = "; PUAE6 test program: exceptions of the 68020 and their frames.\n" + PROLOGUE + """    MOVE.L #$4000,D0
    .word $4E7B,$0801           ; MOVEC D0,VBR
    MOVE.L #skip,$4010.L        ; vector 4: illegal instruction
    MOVE.L #plain,$4014.L       ; vector 5: zero divide
    MOVE.L #skip,$4028.L        ; vector 10: line A
    MOVE.L #skip,$402C.L        ; vector 11: line F
    MOVE.L #plain,$4084.L       ; vector 33: TRAP #1
    MOVE.L #h,$406C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    .word $4AFC                 ; ILLEGAL
    TRAP #1
    .word $A000                 ; line A
    .word $F000                 ; line F (no FPU)
    MOVE.L #0,D1
    DIVU.W D1,D0
    ADDQ.L #1,D4
    BRA.W loop
skip:
    .word $54AF,$0002           ; ADDQ.L #2,2(A7): step over the instruction that raised it
plain:
    RTE
h:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D5
    RTE
"""

# 68020 instructions, unaligned accesses to Chip RAM above the first mebibyte and bitplane DMA taking slots from them, so that the cycle-exact accesses wait.
OPS020 = "; PUAE6 test program: 68020 instructions and unaligned long accesses to Chip RAM, with four bitplanes fetched.\n" + PROLOGUE + """    MOVE.W #$4200,$DFF100.L     ; BPLCON0: 4 planes
    MOVE.W #$0038,$DFF092.L
    MOVE.W #$00D0,$DFF094.L
    MOVE.W #$2C81,$DFF08E.L
    MOVE.W #$2CC1,$DFF090.L
    MOVE.L #$100000,$DFF0E0.L
    MOVE.L #$104000,$DFF0E4.L
    MOVE.L #$108000,$DFF0E8.L
    MOVE.L #$10C000,$DFF0EC.L
    MOVE.W #$8300,$DFF096.L
    MOVE.L #h,$6C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    MOVE.L #$12345,D0
    MOVE.L #$6789,D1
    .word $4C01,$0000           ; MULU.L D1,D0
    MOVE.L #$FFF0BDC0,D0
    MOVE.L #7,D1
    .word $4C41,$0800           ; DIVS.L D1,D0
    MOVE.L #$0ABCDEF1,D1
    .word $E9C1,$2108           ; BFEXTU D1{4:8},D2
    MOVE.L #$A5A5A5A5,D3
    .word $23C3,$0010,$0003     ; MOVE.L D3,$100003.L
    .word $2439,$0010,$0003     ; MOVE.L $100003.L,D2
    .word $33C3,$0010,$4005     ; MOVE.W D3,$104005.L
    .word $23C3,$001F,$FFF9     ; MOVE.L D3,$1FFFF9.L
    ADDQ.L #1,D4
    BRA.W loop
h:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D5
    RTE
"""

# A tracker-style loop: a channel plays for 1500 instructions (long enough to be in the middle of its sample), then the audio DMA is switched off, 50 instructions pass and it is switched on again. On a
# 68020 the audio code turns an off-on gap of fewer than 60 instructions into a DMA that waits (and a longer one into a restart of the channel: usehacks), comparing the count of instructions
# executed now with the count at which the DMA went off. The count is the CPU's and the instant is in AUDP, so a state that carried one and not the other judged the gap by the history of the
# machine that loaded it. The boundaries of the scenario (AUDIO_GAP_UNITS) are the ones in which the state falls inside the gap: tests/state/test_state_a1200.py checks it.
AUDIO_DMA_WAIT = "; PUAE6 test program: audio DMA switched off and on again 50 instructions later, in a loop.\n" + PROLOGUE + """    MOVE.L #$64646464,$20000.L
    MOVE.L #$64646464,$20004.L
    MOVE.L #$9C9C9C9C,$20008.L
    MOVE.L #$9C9C9C9C,$2000C.L
    MOVE.L #$20000,$DFF0A0.L
    MOVE.W #8,$DFF0A4.L
    MOVE.W #200,$DFF0A6.L
    MOVE.W #64,$DFF0A8.L
    MOVE.W #$8201,$DFF096.L
loop:
    MOVE.W #$0001,$DFF096.L     ; AUD0 DMA off
""" + "    ADDQ.L #1,D0\n" * 50 + """    MOVE.W #$8001,$DFF096.L     ; and on
""" + "    ADDQ.L #1,D1\n" * 1500 + """    BRA.W loop
"""
AUDIO_GAP_UNITS = (22, 53)       # the units in which the state falls inside the gap (the channel has dmaofftime_active set): tests/state/test_state_a1200.py checks it
AUDIO_OFF_UNITS = (4, 17)        # and the ones in which the channel is in state 0x13, a sample playing with its DMA switched off (the validation of AUDn used to refuse it)

# -- AGA -------------------------------------------------------------------------------------------------------------------------------------------------------------------------


def colour(bank, index):
    """An 8-bit-per-channel colour that is not one of the 4096 of the ECS (the low nibble of each channel is not the high one)."""
    n = bank * 32 + index
    return (n * 37 + 11) & 0xFF, (n * 91 + 53) & 0xFF, (n * 151 + 7) & 0xFF


def palette():
    """All 256 AGA colours: eight banks of 32, the high nibbles of each channel, then the low ones (BPLCON3 LOCT)."""
    lines = []
    for bank in range(8):
        for low in (0, 1):
            lines.append("    MOVE.W #$%04X,$DFF106.L" % ((bank << 13) | 0x0C00 | (0x200 if low else 0)))
            for index in range(32):
                r, g, b = colour(bank, index)
                nibbles = [x & 15 if low else x >> 4 for x in (r, g, b)]
                lines.append("    MOVE.W #$%03X,$DFF%03X.L" % (nibbles[0] << 8 | nibbles[1] << 4 | nibbles[2], 0x180 + 2 * index))
    lines.append("    MOVE.W #$0C00,$DFF106.L")
    return "\n".join(lines) + "\n"


def planes(count, base=0x100000, spacing=0x4000, longs=640):
    """Fill `count` bitplanes with a pattern that differs per plane and per line (A0 walks, D0 changes every eight longs)."""
    lines = []
    for p in range(count):
        lines.append("    MOVEA.L #$%X,A0" % (base + p * spacing))
        for chunk in range(longs // 8):
            lines.append("    MOVE.L #$%08X,D0" % ((p * 0x01010101 + chunk * 0x0F1D2B39 + 0x35A5C3E1) & 0xFFFFFFFF))
            lines.extend(["    MOVE.L D0,(A0)+"] * 8)
    return "\n".join(lines) + "\n"


def aga_display(*, fmode, bplcon0, planes_count=8, sprites=False, copper=False, loop="loop:\n    ADDQ.L #1,D0\n    BRA.S loop\n", ddf=(0x38, 0xA0), handler="colour"):
    """An AGA picture: the whole palette, `planes_count` bitplanes in Chip RAM above 1 MiB, optionally 64-bit sprites and a Copper list that changes palette bank mid frame, and a
    vertical-blank handler that writes a colour (`colour`: COLOR01 from D1) or does nothing (`none`)."""
    t = ["; PUAE6 test program: an AGA picture (generated by tests/state/test_det02_a1200.py).\n", PROLOGUE, palette(), planes(planes_count)]
    if sprites:
        t.append("    MOVE.L #$2C403C00,$110000.L\n    MOVE.L #$00000000,$110004.L\n")
        for line in range(8):
            t.append("    MOVE.L #$%08X,$%X.L\n    MOVE.L #$%08X,$%X.L\n" % (0xF0F0F0F0 ^ line * 0x01010101, 0x110008 + line * 16, 0x0FF00FF0 ^ line, 0x11000C + line * 16))
            t.append("    MOVE.L #$%08X,$%X.L\n    MOVE.L #$00000000,$%X.L\n" % (0xAAAA5555 ^ line, 0x110010 + line * 16, 0x110014 + line * 16))
        t.append("    MOVE.L #$110000,$DFF120.L\n    MOVE.W #$0F0F,$DFF1A2.L\n")
    if copper:
        t.append("    MOVE.L #$%08X,$120000.L\n" % 0x50BFFFFE)           # WAIT line $50
        t.append("    MOVE.L #$%08X,$120004.L\n" % 0x01062C00)           # BPLCON3: bank 1
        t.append("    MOVE.L #$01800F0F,$120008.L\n    MOVE.L #$%08X,$12000C.L\n" % 0x80BFFFFE)       # COLOR00, WAIT line $80
        t.append("    MOVE.L #$010660C0,$120010.L\n    MOVE.L #$018200F0,$120014.L\n    MOVE.L #$FFFFFFFE,$120018.L\n")
        t.append("    MOVE.L #$120000,$DFF080.L\n")
    t.append("    MOVE.W #$%04X,$DFF1FC.L\n" % fmode)
    t.append("    MOVE.W #$%04X,$DFF100.L\n" % bplcon0)
    t.append("    MOVE.W #$0011,$DFF10C.L\n    MOVE.W #$0024,$DFF104.L\n")
    t.append("    MOVE.W #$%04X,$DFF092.L\n    MOVE.W #$%04X,$DFF094.L\n" % ddf)
    t.append("    MOVE.W #$2C81,$DFF08E.L\n    MOVE.W #$2CC1,$DFF090.L\n    MOVE.W #$0000,$DFF108.L\n    MOVE.W #$0000,$DFF10A.L\n")
    for p in range(planes_count):
        t.append("    MOVE.L #$%X,$DFF%03X.L\n" % (0x100000 + p * 0x4000, 0xE0 + 4 * p))
    t.append("    MOVE.L #h,$6C.L\n    MOVE.W #$C020,$DFF09A.L\n")
    t.append("    MOVE.W #$%04X,$DFF096.L\n" % (0x8000 | 0x200 | 0x100 | (0x20 if sprites else 0) | (0x80 if copper else 0)))
    t.append("    MOVE.W #$2000,SR\n" + loop)
    t.append("h:\n    MOVE.W #$0020,$DFF09C.L\n    ADDQ.L #1,D1\n")
    for p in range(planes_count):          # the bitplane pointers walk through the picture and nothing reloads them: the handler does, every frame
        t.append("    MOVE.L #$%X,$DFF%03X.L\n" % (0x100000 + p * 0x4000, 0xE0 + 4 * p))
    if sprites:
        t.append("    MOVE.L #$110000,$DFF120.L\n")
    if handler == "colour":
        t.append("    .word $33C1,$00DF,$F182     ; MOVE.W D1,COLOR01\n")
    t.append("    RTE\n")
    return "".join(t)


FMODE_TOGGLE = "loop:\n    MOVE.W #$0003,$DFF1FC.L\n    MOVE.W #$000C,$DFF1FC.L\n    MOVE.W #$0000,$DFF1FC.L\n    BRA.S loop\n"
CHIP_RW = "loop:\n    ADDQ.L #1,D0\n    .word $23C0,$0013,$FFFD     ; MOVE.L D0,$13FFFD.L\n    .word $2439,$0013,$FFFD     ; MOVE.L $13FFFD.L,D2\n    BRA.S loop\n"

# BUSY with every pointer in the upper half of the 2 MiB of Chip RAM (the DMA channels, the blitter and the Copper on RAM that no other machine has).
HIGH = {
    "#$AAAA5555,$4000.L": "#$AAAA5555,$1C4000.L", "#$12345678,$5000.L": "#$12345678,$1C5000.L", "#$4000,$DFF0E0.L": "#$1C4000,$DFF0E0.L", "#$5000,$DFF0E4.L": "#$1C5000,$DFF0E4.L",
    "#$AAAAAAAA,$6000.L": "#$AAAAAAAA,$1C6000.L", "#$0F0F0F0F,$6004.L": "#$0F0F0F0F,$1C6004.L", "#$00000000,$6008.L": "#$00000000,$1C6008.L", "#$6000,$DFF120.L": "#$1C6000,$DFF120.L",
    "#$7000,$DFF0A0.L": "#$1C7000,$DFF0A0.L", "#$7100,$DFF0B0.L": "#$1C7100,$DFF0B0.L", "#$7000,$7000.L": "#$1C7000,$1C7000.L",
    "#$2000,$DFF080.L": "#$1C2000,$DFF080.L", "#$01800123,$2000.L": "#$01800123,$1C2000.L", "#$FFFFFFFE,$2004.L": "#$FFFFFFFE,$1C2004.L",
    "#$10000,$DFF050.L": "#$180000,$DFF050.L", "#$40000,$DFF054.L": "#$1A0000,$DFF054.L", "#$8000,$DFF020.L": "#$1D0000,$DFF020.L",
}

ROMS = {
    **{name: rom for name, rom in d.Det02Tests.ROMS.items() if name in ("vsync", "stop", "halt", "trace", "user", "long_ops", "copper_irq", "tone", "busy", "disk-dma", "disk-seek", "late-bpl")},
    "busy-high": (upstream.BUSY, None, HIGH),
    "cache020": (CACHE020, None, None),
    "master": (MASTER, {"SR": "$3000"}, None),
    "master-user-stack": (MASTER, {"SR": "$1000"}, None),
    "t0": (MASTER, {"SR": "$5000"}, None),
    "t0-master": (MASTER, {"SR": "$7000"}, None),
    "exceptions": (EXCEPTIONS, None, None),
    "audio-dma-wait": (AUDIO_DMA_WAIT, None, None),
    "ops020": (OPS020, None, None),
    "aga-8planes": (aga_display(fmode=0x0000, bplcon0=0x0210), None, None),
    "aga-fmode3": (aga_display(fmode=0x0003, bplcon0=0x0210), None, None),
    "aga-fmode-toggle": (aga_display(fmode=0x0000, bplcon0=0x0210, loop=FMODE_TOGGLE), None, None),
    "aga-sprites": (aga_display(fmode=0x000F, bplcon0=0x0210, sprites=True), None, None),
    "aga-copper-palette": (aga_display(fmode=0x0001, bplcon0=0x0210, copper=True), None, None),
    "aga-ham8": (aga_display(fmode=0x0003, bplcon0=0x0A10), None, None),
    "aga-chip-rw": (aga_display(fmode=0x0003, bplcon0=0x0210, loop=CHIP_RW), None, None),
    "aga-hires": (aga_display(fmode=0x0002, bplcon0=0x8210, planes_count=4, ddf=(0x3C, 0xA4)), None, None),
}

# name -> (rom, extra probe arguments, units N at which the state is taken, modes), as in tests/state/test_det02.py.
SCENARIOS = {
    "vsync": ("vsync", [], (3,), d.CORE),
    "stop": ("stop", [], (3,), d.SHAPES),
    "halt": ("halt", [], (2,), d.SHAPES),
    "trace": ("trace", [], (3,), d.SHAPES),
    "user": ("user", [], (3,), d.SHAPES),
    "long_ops": ("long_ops", [], (3, 4), d.SHAPES),
    "copper_irq": ("copper_irq", [], (2,), d.CORE),
    "tone": ("tone", [], (3,), d.CORE),
    "busy": ("busy", [], (2, 5), d.CORE),
    "busy-high": ("busy-high", [], (2, 5), d.CORE),
    "late-bitplanes": ("late-bpl", [], (8,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "cache020": ("cache020", [], (2, 3, 4), d.CORE),
    "master": ("master", [], (3,), d.CORE),
    "master-user-stack": ("master-user-stack", [], (3,), d.SHAPES),
    "t0": ("t0", [], (3,), d.CORE),
    "t0-master": ("t0-master", [], (3,), d.SHAPES),
    "exceptions": ("exceptions", [], (3, 4), d.CORE),
    "ops020": ("ops020", [], (3, 4), d.CORE),
    "audio-dma-wait": ("audio-dma-wait", [], AUDIO_GAP_UNITS + AUDIO_OFF_UNITS, (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "aga-8planes": ("aga-8planes", [], (3, 6), d.CORE),
    "aga-fmode3": ("aga-fmode3", [], (4,), d.CORE),
    "aga-fmode-toggle": ("aga-fmode-toggle", [], (4, 5, 6), d.CORE),
    "aga-sprites": ("aga-sprites", [], (4,), d.CORE),
    "aga-copper-palette": ("aga-copper-palette", [], (4,), d.CORE),
    "aga-ham8": ("aga-ham8", [], (4,), d.CORE),
    "aga-chip-rw": ("aga-chip-rw", [], (4,), d.CORE),
    "aga-hires": ("aga-hires", [], (4,), d.CORE),
    "disk-dma": ("disk-dma", "disk", (3, 12), d.SHAPES),
    "disk-seek": ("disk-seek", "disk", (5, 9), d.SHAPES),
}
PORTED = ("vsync", "stop", "halt", "trace", "user", "long_ops", "copper_irq", "tone", "busy", "busy-high", "late-bitplanes")
M68020 = ("cache020", "master", "master-user-stack", "t0", "t0-master", "exceptions", "ops020", "audio-dma-wait")
AGA = tuple(name for name in SCENARIOS if name.startswith("aga-"))
SCENARIOS_PORTED = {name: SCENARIOS[name] for name in PORTED}
SCENARIOS_020 = {name: SCENARIOS[name] for name in M68020}
SCENARIOS_AGA = {name: SCENARIOS[name] for name in AGA}
SCENARIOS_DISK = {name: s for name, s in SCENARIOS.items() if isinstance(s[1], str)}
assert set(SCENARIOS) == set(PORTED) | set(M68020) | set(AGA) | set(SCENARIOS_DISK), "a scenario is in no class"

# Where DET02 diverges on the A1200 and not on the others (the cause of each is attributed in tests/state/test_det02.py CAUSES or here): none.
DIVERGENCES = {}


class A1200Case(d.Det02Case):
    MACHINE = "a1200"
    ROMS = ROMS


class Det02A1200PortedTests(A1200Case):
    """The programs of the A500 table that run on a 68020."""


class Det02A1200CpuTests(A1200Case):
    """The 68020 itself."""


class Det02A1200AgaTests(A1200Case):
    """AGA."""


class Det02A1200DiskTests(A1200Case):
    """The floppy controller, with the 020 reading the disk."""


d.populate(Det02A1200PortedTests, SCENARIOS_PORTED, DIVERGENCES)
d.populate(Det02A1200CpuTests, SCENARIOS_020, DIVERGENCES)
d.populate(Det02A1200AgaTests, SCENARIOS_AGA, DIVERGENCES)
d.populate(Det02A1200DiskTests, SCENARIOS_DISK, DIVERGENCES)

KICKSTART_ROM = os.environ.get("P6_KICKSTART_A1200_ROM")
AGA_ADF = os.environ.get("P6_AGA_ADF")

# The Kickstart 3.1 (the power-on: the CIAs untouched, the keyboard handshake, the insert-disk screen, the drives probed) and, with the demo disk in DF0 from boot, boundaries inside
# the load of the disk (the floppy controller in the middle of a real load, AGA written by a real program).
KICKSTART_SCENARIOS = {
    "kickstart": ("kickstart", [], (60,), (d.SAME, d.SAME_BEAM, d.NEW, d.NEW_BEAM)),
    "kickstart-steady": ("kickstart", [], (140,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "kickstart-insert-disk": ("kickstart", [], (400,), (d.SAME_BEAM, d.NEW)),
}
DEMO_SCENARIOS = {
    # The head is on cylinder 40 and reading at 200, still reading and on 46 at 400 (a state in the middle of the load: motor, seek, DMA in flight), the demo shows its first AGA
    # picture (more than 32 colours of 24 bits) at 500, and 800 is the steady demo with the drive going again.
    "demo-boot": ("kickstart", "demo", (200, 400, 500, 800), (d.SAME_BEAM, d.NEW)),
}


class Kickstart31Case(d.Det02Case):
    """The A1200 with the owner's Kickstart 3.1 (and, in the subclass, the demo disk)."""

    MACHINE = "a1200"

    @classmethod
    def firmware(cls):
        return Path(KICKSTART_ROM)

    def arguments(self, extra):
        if extra == "demo":
            return ["--adf", AGA_ADF, "--event-at", "0", "0", "0", "1"]
        return super().arguments(extra)


@unittest.skipUnless(KICKSTART_ROM and Path(KICKSTART_ROM).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized Kickstart 3.1 (A1200)")
class Det02Kickstart31Tests(Kickstart31Case):
    pass


@unittest.skipUnless(KICKSTART_ROM and AGA_ADF and Path(KICKSTART_ROM).is_file() and Path(AGA_ADF).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM and P6_AGA_ADF to run the AGA demo")
class Det02AgaDemoTests(Kickstart31Case):
    pass


d.populate(Det02Kickstart31Tests, KICKSTART_SCENARIOS, {})
d.populate(Det02AgaDemoTests, DEMO_SCENARIOS, {})


if __name__ == "__main__":
    unittest.main()
