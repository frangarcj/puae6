"""Micro-ROM programs that keep Denise's register ring full at a unit boundary on OCS and ECS (T24c finding, T32b).

Agnus hands every write to a register Denise cares about (BPLCON0..4, the colours, DIWSTRT/DIWSTOP, DIWHIGH...) to the ring `rga_denise` together with the colour clock it
happened in, and the line is drawn from the ring when it ends. The native state carries that ring only on AGA (chunk DRNG); on the A500 and A600 ADR 0009 takes it for idle at a
unit boundary, and a unit ends in the vertical blank (vpos 4), where nothing is displayed. These programs test the assumption instead of reading it off the design: they rewrite
BPLCON0, BPLCON1, BPLCON2 and the colours without stopping, from the CPU (every instruction of the loop is a write) or from a Copper list whose WAITs cluster around lines 2 to 6, so that
the registers are being written when the boundary falls, with bitplane DMA and sprite DMA on so the ring also holds fetched data. The ECS variants add the registers only an ECS Denise has
(BPLCON3 and DIWHIGH). Whether the boundary state is complete is decided by DET02, not here (tests/reference/test_corpus.py).

All the programs are original, licensed under MIT like the micro-ROMs in tests/fixtures/microrom.
"""

PROLOGUE = """; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.
; {title}
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #$AAAA5555,$4000.L   ; bitplane data: four different words per plane
    MOVE.L #$0F0F33CC,$5000.L
    MOVE.L #$33CC00FF,$6000.L
    MOVE.L #$00FFF00F,$7000.L
    MOVE.L #$AAAAAAAA,$8000.L   ; SPR0 list: position and control, one line of data, end
    MOVE.L #$0F0F0F0F,$8004.L
    MOVE.L #$00000000,$8008.L
    MOVE.W #$4200,$DFF100.L     ; BPLCON0: 4 planes, colour
    MOVE.W #$0038,$DFF092.L     ; DDFSTRT
    MOVE.W #$00D0,$DFF094.L     ; DDFSTOP
    MOVE.W #$2C81,$DFF08E.L     ; DIWSTRT
    MOVE.W #$2CC1,$DFF090.L     ; DIWSTOP
    MOVE.L #$4000,$DFF0E0.L     ; BPL1PT .. BPL4PT
    MOVE.L #$5000,$DFF0E4.L
    MOVE.L #$6000,$DFF0E8.L
    MOVE.L #$7000,$DFF0EC.L
    MOVE.L #$8000,$DFF120.L     ; SPR0PT
    MOVE.W #$0FFF,$DFF1A2.L     ; a sprite colour
"""

# Writes of the loop of the CPU variant, in the order the program makes them: registers with the colour clocks they take in a row, hires and lores BPLCON0, scroll, priorities, colours.
CPU_WRITES = (
    (0x100, 0x4200), (0x180, 0x0F00), (0x102, 0x0011), (0x182, 0x00F0), (0x104, 0x0024), (0x184, 0x000F), (0x100, 0xC200), (0x186, 0x0FF0),
    (0x102, 0x0000), (0x188, 0x0F0F), (0x104, 0x0000), (0x18A, 0x00FF), (0x100, 0x4200), (0x18C, 0x0123), (0x102, 0x0022), (0x18E, 0x0456),
)
# Registers an ECS Denise adds to the loop: BPLCON3 (border blank and sprite resolution bits) and DIWHIGH.
ECS_WRITES = ((0x106, 0x0020), (0x1E4, 0x2100), (0x106, 0x0000), (0x1E4, 0x2000))


def cpu_ring(ecs=False):
    """The CPU rewrites BPLCON0/1/2 and the colours in a tight loop: the last write before the boundary is always a few colour clocks old."""
    writes = list(CPU_WRITES)
    if ecs:
        writes[4:4] = ECS_WRITES[:2]
        writes[10:10] = ECS_WRITES[2:]
    body = "".join(f"    MOVE.W #${value:04X},$DFF{reg:03X}.L\n" for reg, value in writes)
    title = "PUAE6 test program: the CPU rewrites BPLCON0/1/2 and the colours without stopping%s; bitplane and sprite DMA on." % (" (and BPLCON3/DIWHIGH, ECS)" if ecs else "")
    return PROLOGUE.format(title=title) + "    MOVE.W #$83A0,$DFF096.L     ; DMACON: SET, master, bitplanes and sprites\nloop:\n" + body + "    BRA.W loop\n"


def wait(vpos, hpos):
    return ((vpos & 0xFF) << 8 | (hpos & 0xFE) | 1) << 16 | 0xFFFE


def move(reg, value):
    return reg << 16 | value


def copper_list(ecs=False):
    """A list that writes BPLCON0/1/2 and colours at several positions of lines 2 to 6 (around the boundary at vpos 4) and once per line of the picture."""
    cl = []
    for vpos in (2, 3, 4, 5, 6):
        for hpos in (0x08, 0x38, 0x70, 0xA0, 0xD0):
            cl += [wait(vpos, hpos), move(0x100, 0x4200 if (vpos + hpos) & 1 else 0xC200), move(0x180, (vpos << 8 | hpos) & 0x0FFF), move(0x102, hpos >> 2 & 0xFF),
                   move(0x184, (hpos << 4 | vpos) & 0x0FFF)]
            if ecs:
                cl += [move(0x106, 0x0020 if hpos & 0x10 else 0x0000)]
    for vpos in range(0x30, 0x50):
        cl += [wait(vpos, 0x20), move(0x182, (vpos * 37) & 0x0FFF), move(0x104, vpos & 0x3F)]
    cl += [0xFFFFFFFE]
    return cl


def copper_ring(ecs=False):
    """The Copper rewrites the same registers at positions around lines 2 to 6 of every frame; the CPU only counts."""
    title = "PUAE6 test program: a Copper list rewrites BPLCON0/1/2 and the colours around the vertical blank%s; bitplane and sprite DMA on." % (" (and BPLCON3, ECS)" if ecs else "")
    out = PROLOGUE.format(title=title)
    for i, word in enumerate(copper_list(ecs)):
        out += f"    MOVE.L #${word:08X},${0x2000 + 4 * i:X}.L\n"
    out += "    MOVE.L #$2000,$DFF080.L     ; COP1LC\n    MOVE.W #$83E0,$DFF096.L     ; DMACON: SET, master, bitplanes, sprites and Copper\nloop:\n    ADDQ.L #1,D0\n    BRA.S loop\n"
    return out


def both_ring(ecs=False):
    """The Copper list of copper_ring while the CPU loop of cpu_ring writes the colours it does not touch: writes from both masters in the same lines."""
    out = copper_ring(ecs)
    loop = "loop:\n    ADDQ.L #1,D0\n    BRA.S loop\n"
    body = "".join(f"    MOVE.W #${value:04X},$DFF{reg:03X}.L\n" for reg, value in ((0x186, 0x0F0F), (0x188, 0x00FF), (0x18A, 0x0FF0), (0x18C, 0x0555)))
    return out.replace(loop, "loop:\n" + body + "    BRA.W loop\n")
