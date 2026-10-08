"""ECS micro-ROMs (A600 preset) for the AV01 scenarios, and the picture each one has to produce.

Every expectation comes from the program and from the ECS hardware documentation, never from a capture of the engine.
The geometry of the canonical raster is the one of tests/scenarios/a500/display.py (docs/adr/0006-video-output.md):

    row r shows Amiga line  first_line + r // 2       first_line = 26 (PAL), 21 (NTSC)
    column c shows lores position 91 + c / 2           data origin: see ORIGIN

What the ECS chipset adds and these programs exercise:

  VPOSR      bits 14..12: Agnus identity. The upstream table (custom.cpp VPOSR) reports the ECS family as $2 and sets bit 12
             when the machine is NTSC, so $2xxx PAL and $3xxx NTSC; the OCS Agnus of the A500 reports $0xxx and $1xxx.
  DENISEID   $DFF07C: the ECS Denise reads $FFFC; the OCS Denise has no such register (the read returns the bus value).
  Chip RAM   1 MiB: $80000-$FFFFF are real Chip RAM (no alias of the first 512 KiB); BPLxPTH/COPxLCH are five bits wide, so a
             bitplane at $C0000 and a Copper list at $90000 work.
  SHRES      BPLCON0 bit 6: 35 ns pixels, two per hires pixel, so a row holds 1280 bits = 160 bytes across the same display
             window. The ECS Denise forms the colour of a PAIR of successive pixels: register number (second << 2) | first,
             and shows two bits of every colour component on each pixel (the first pixel the high pair of bits of the nibble,
             the second the low pair). With one bitplane and equal pairs (0,0) and (1,1) the registers are 0 and 5, and a
             component nibble of 0, 5, $A or $F comes out unchanged on both pixels, so the programs use only those nibbles and
             the picture is the hires picture of one bit per pair whichever pixel of the pair the renderer keeps. The preset
             renders at hires (docs/adr/0006-video-output.md). The index rule is the one of upstream's get_shres_pix
             (drawing.cpp); the engine paints the border of an SHRES line black instead of COLOR00 in this setup, so the
             SHRES model leaves everything outside the display window undescribed.
  DIWHIGH    $DFF1E4: bits 2..0 = V10..V8 of the window start, bit 5 = H8 of the start, bits 10..8 = V10..V8 of the stop,
             bit 13 = H8 of the stop. Without DIWHIGH (OCS, or an ECS program that never wrote it) the stop line has bit 8 =
             NOT bit 7 and the stop column has H8 = 1.
  BEAMCON0   $DFF1DC: bit 5 PAL selects 625/312-line timing, clear 525/262-line timing; bit 7 VARBEAMEN makes HTOTAL ($1C0) and
             VTOTAL ($1C8) the length of the line and of the frame.
"""
from dataclasses import dataclass

from tests.scenarios.a500.display import Display, FIRST_COLUMN, FIRST_LINE, HEIGHT, MARGIN, WIDTH, WRAP_COLUMN, color_at_line, rgb

BPLCON0 = {"lores": 0x1200, "hires": 0x9200, "shres": 0x1240}
# Column of the first bitplane pixel: the data fetched at DDFSTRT is displayed one fetch word plus half a colour clock later; column 0 is
# colour clock 45.5 and a column is a quarter of a clock, so column = 4 * (DDFSTRT + word + 0.5 - 45.5) with a word of 8 (lores),
# 4 (hires) or 2 (SHRES) clocks: 76, 76 and 68 for DDFSTRT $38, $3C and $3C. MEASURED, not deduced: on NTSC the SHRES image of the odd
# lines sits half a colour clock (2 columns) further left (the engine's own comment calls its SHRES alignment a hack,
# drawing.cpp lts_unaligned_ecs_shres); lores and hires do not show it.
ORIGIN = {"lores": 76, "hires": 76, "shres": 68}
DDF = {"lores": 0x003800D0, "hires": 0x003C00D4, "shres": 0x003C00D4}
ROW_BYTES = {"lores": 40, "hires": 80, "shres": 160}
VPOSR, DENISEID, BEAMCON0, DIWHIGH, HTOTAL, VTOTAL = 0xDFF004, 0xDFF07C, 0xDFF1DC, 0xDFF1E4, 0xDFF1C0, 0xDFF1C8
BEAM_PAL, BEAM_VARBEAMEN = 0x0020, 0x0080
LOG = 0x1000                                 # the program appends 16-bit words here through (A0)+ (P6_DUMP_LOG shows them per unit)


def doubled(words):
    """Every bit of the 32-bit words twice (a SHRES row whose pairs of pixels are equal): two words per word."""
    out = []
    for word in words:
        for half in (word >> 16, word & 0xFFFF):
            value = 0
            for bit in range(15, -1, -1):
                value = (value << 2) | (0b11 if half >> bit & 1 else 0)
            out.append(value)
    return tuple(out)


@dataclass(frozen=True)
class EcsDisplay:
    """One bitplane whose single row is repeated on every line, a Copper list with colour bars, and optional extras."""
    name: str
    mode: str = "lores"                 # lores | hires | shres
    bg: int = 0x008
    fg: int = 0x0F0
    bars: tuple = ()                    # ((first line, COLOR00), ...) applied by the Copper every frame
    plane_at: int = 0x20000
    copper_at: int = 0x2000
    diwstrt: int = 0x2C81
    diwstop: int = 0x2CC1
    diwhigh: object = None              # None: never written
    registers: tuple = ()               # ((register address, 16-bit value), ...) written before the display DMA starts
    prelude: tuple = ()                 # assembly lines run first (after the stack and the CIAs)
    toggle: tuple = ()                  # ((register address, value A, value B)): the VERTB handler alternates them
    fg_register: int = 1                # colour register that holds `fg`: 1 for one plane; SHRES selects register 5 (see below)
    border: bool = True                 # False: the model does not describe the cells outside the display window
    ecs: bool = True                    # the model assumes the DIWHIGH of an ECS machine

    def visible_row(self):
        """The 32-bit words of the row as they appear on the screen (lores: 320 pixels, otherwise 640 hires pixels)."""
        return Display(self.name, mode="lores" if self.mode == "lores" else "hires").row()

    def stored_row(self):
        row = self.visible_row()
        return doubled(row) if self.mode == "shres" else row

    def window(self):
        """(first line, stop line, first column, stop column) of the display window, from DIWSTRT/DIWSTOP/DIWHIGH."""
        vstart, vstop = self.diwstrt >> 8, self.diwstop >> 8
        hstart, hstop = self.diwstrt & 0xFF, self.diwstop & 0xFF
        if self.diwhigh is not None and self.ecs:
            vstart |= (self.diwhigh & 7) << 8
            vstop |= ((self.diwhigh >> 8) & 7) << 8
            hstart |= ((self.diwhigh >> 5) & 1) << 8
            hstop |= ((self.diwhigh >> 13) & 1) << 8
        else:
            vstop |= 0 if vstop & 0x80 else 0x100
            hstop |= 0x100
        return vstart, vstop, FIRST_COLUMN + (hstart - 129) * 2, FIRST_COLUMN + (hstop - 129) * 2


def rom_source(d):
    """Assembly of the program; an EcsDisplay is fully described by its fields."""
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/scenarios/ecs/ecs.py for the ECS display %s." % d.name,
         "entry:", "    MOVE.W #$2700,SR", "    LEA $400.W,A7",
         "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L"]
    a += list(d.prelude)
    for i, word in enumerate(d.stored_row()):
        a.append("    MOVE.L #$%08X,$%X.L" % (word, d.plane_at + 4 * i))
    a += ["    MOVE.W #$%04X,$DFF180.L" % d.bg, "    MOVE.W #$%04X,$%X.L" % (d.fg, 0xDFF180 + 2 * d.fg_register)]
    a += ["    MOVE.W #$%04X,$DFF08E.L" % d.diwstrt, "    MOVE.W #$%04X,$DFF090.L" % d.diwstop]
    if d.diwhigh is not None:
        a.append("    MOVE.W #$%04X,$DFF1E4.L" % d.diwhigh)
    modulo = (-ROW_BYTES[d.mode]) & 0xFFFF
    a += ["    MOVE.L #$%08X,$DFF092.L" % DDF[d.mode], "    MOVE.L #$%04X%04X,$DFF108.L" % (modulo, modulo),
          "    MOVE.L #$%08X,$DFF0E0.L" % d.plane_at]
    a += ["    MOVE.W #$%04X,$%X.L" % (value, reg) for reg, value in d.registers]
    a.append("    MOVE.W #$%04X,$DFF100.L" % BPLCON0[d.mode])
    words = [(0x0180, d.bg)]
    for line, color in d.bars:
        assert 0 < line < 256, "bars stay below line 256 so that the WAIT needs no vertical wrap"
        words += [((line << 8) | 1, 0xFFFE), (0x0180, color)]
    words.append((0xFFFF, 0xFFFE))
    for i, (first, second) in enumerate(words):
        a.append("    MOVE.L #$%04X%04X,$%X.L" % (first, second, d.copper_at + 4 * i))
    a += ["    MOVE.L #$%X,$DFF080.L" % d.copper_at, "    MOVE.W #$8380,$DFF096.L"]   # COP1LC, DMACON: DMAEN | BPLEN | COPEN
    handler = []
    if d.toggle:
        a += ["    MOVE.L #$%04X,D2" % d.toggle[0][1], "    MOVE.L #$%04X,D3" % d.toggle[0][2]]
        handler += ["    EXG D2,D3", "    MOVE.W D2,$%X.L" % d.toggle[0][0]]
        assert len(d.toggle) == 1
    if handler:
        a += ["    MOVE.L #handler,$6C.L", "    MOVE.W #$C020,$DFF09A.L", "    MOVE.W #$2000,SR"]
    a += ["loop:", "    ADDQ.L #1,D0", "    BRA.S loop"]
    if handler:
        a += ["handler:", "    MOVE.W #$0020,$DFF09C.L"] + handler + ["    RTE"]
    return "\n".join(a) + "\n"


def predict(d, standard):
    """Expected XRGB8888 rows (None where the model does not describe the cell) of a settled unit of the display."""
    row = d.visible_row()
    height = HEIGHT[standard]
    vstart, vstop, left, right = d.window()
    picture = []
    for r in range(height):
        line = FIRST_LINE[standard] + r // 2
        cells = []
        for c in range(WIDTH):
            if c < MARGIN or c >= WIDTH - MARGIN or r >= height - MARGIN or (standard == "ntsc" and c >= WRAP_COLUMN):
                cells.append(None)
                continue
            inside = vstart <= line < vstop and left <= c < right and c >= FIRST_COLUMN
            if not d.border and not inside:
                cells.append(None)
                continue
            value = color_at_line(d, line + 1 if c >= WRAP_COLUMN else line)
            if inside:
                origin = ORIGIN[d.mode] - (2 if d.mode == "shres" and standard == "ntsc" and line % 2 else 0)
                pixel = (c - origin) // 2 if d.mode == "lores" else c - origin
                if 0 <= pixel < 32 * len(row) and row[pixel // 32] >> (31 - pixel % 32) & 1:
                    value = d.fg
            cells.append(rgb(value))
        picture.append(cells)
    return picture


# --- the identification program: VPOSR and DENISEID read by the program, logged and shown as the background colour ---------------

def ident_source():
    """Reads VPOSR and DENISEID at power-on and once per VERTB; logs both at LOG, puts DENISEID on COLOR00 and shows nothing else.

    The log holds pairs (VPOSR, DENISEID). The screen is a flat COLOR00 = DENISEID & $FFF (an ECS Denise: $FFC)."""
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/scenarios/ecs/ecs.py: chip identification.",
         "entry:", "    MOVE.W #$2700,SR", "    LEA $400.W,A7",
         "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L",
         "    LEA $%X.W,A0" % LOG,
         "    MOVE.W $%X.L,(A0)+" % VPOSR, "    MOVE.W $%X.L,(A0)+" % DENISEID,
         "    .word $3439", "    .long $%X" % DENISEID,       # MOVE.W DENISEID.L,D2
         "    MOVE.W D2,$DFF180.L",
         "    MOVE.W #$0200,$DFF100.L",                         # colour burst, no bitplane
         "    MOVE.L #handler,$6C.L", "    MOVE.W #$C020,$DFF09A.L", "    MOVE.W #$2000,SR",
         "loop:", "    ADDQ.L #1,D0", "    BRA.S loop",
         "handler:", "    MOVE.W #$0020,$DFF09C.L",
         "    MOVE.W $%X.L,(A0)+" % VPOSR, "    MOVE.W $%X.L,(A0)+" % DENISEID, "    RTE"]
    return "\n".join(a) + "\n"


# --- 1 MiB of Chip RAM: the program writes above 512 KiB, reads back, and shows a bitplane and a Copper list up there -----------

RAM_TEST = ((0x1100, 0xAAAA), (0x81100, 0x5555), (0xFFFFE, 0x1234), (0xC0FFE, 0x9ABC))
# Offsets 0x1100 and 0x81100 are the same modulo 512 KiB: an alias would make the second write replace the first.


def chip_ram_prelude():
    a = ["    LEA $%X.W,A0" % LOG]
    for address, value in RAM_TEST:
        a.append("    MOVE.W #$%04X,$%X.L" % (value, address))
    for address, _ in RAM_TEST + RAM_TEST[:1]:       # the first location once more at the end: later writes did not touch it
        a.append("    MOVE.W $%X.L,(A0)+" % address)
    return tuple(a)


def chip_ram_expected():
    return [value for _, value in RAM_TEST] + [RAM_TEST[0][1]]


# --- the catalogue ------------------------------------------------------------------------------------------------------------------

BARS = ((64, 0x00F), (128, 0xF00), (192, 0x0FF))
SHRES_BARS = BARS                 # nibbles 0 and $F only: unchanged by the SHRES split of every colour into two pixels

DISPLAYS = {
    # The OCS modes on the ECS chipset: the same pictures as on the A500.
    "lores-bars": EcsDisplay("lores-bars", bars=BARS),
    "hires-bars": EcsDisplay("hires-bars", mode="hires", bars=BARS),
    # 1 MiB of Chip RAM: bitplane at $C0000, Copper list at $90000, and the RAM test of chip_ram_prelude().
    "chip1m": EcsDisplay("chip1m", bars=BARS, plane_at=0xC0000, copper_at=0x90000, prelude=chip_ram_prelude()),
    # SuperHires: COLOR00 $00A (nibbles $A and 0) and COLOR05 $0F0 for the pair (1,1).
    "shres": EcsDisplay("shres", mode="shres", bg=0x00A, fg=0x0F0, fg_register=5, border=False, bars=SHRES_BARS),
    # DIWHIGH: vertical stop $190 (DIWSTOP $90C1 + V stop high 1, H8 stop kept); the window runs to the end of the frame.
    "diwhigh-v": EcsDisplay("diwhigh-v", bars=BARS, diwstop=0x90C1, diwhigh=0x2100),
    # DIWHIGH with H8 of the stop clear (and V stop high 1, so that the window keeps its 256 lines): DIWSTOP $C1 means column $C1, not $1C1.
    "diwhigh-stop": EcsDisplay("diwhigh-stop", bars=BARS, diwhigh=0x0100),
    # DIWHIGH with H8 of the start set (and the stop as above): DIWSTRT hstart $3F becomes $13F.
    "diwhigh-start": EcsDisplay("diwhigh-start", bars=BARS, diwstrt=0x2C3F, diwhigh=0x2120),
}

# BEAMCON0 programs. Each entry is the registers the program writes before the display DMA starts (and the toggle of the
# VERTB handler, if any). BEAMCON0 bits: PAL $20, VARBEAMEN $80. HTOTAL and VTOTAL hold the number of clocks / lines minus one.
BEAM = {
    "same": dict(),                                            # BEAMCON0 gets the config's own standard: nothing changes
    "to-ntsc": dict(registers=((BEAMCON0, 0x0000),)),           # PAL cleared: 525-line timing, whatever the config says
    "to-pal": dict(registers=((BEAMCON0, BEAM_PAL),)),         # PAL set: 625-line timing
    "toggle": dict(toggle=((BEAMCON0, BEAM_PAL, 0x0000),)),    # the VERTB handler flips between the two every unit
    "var-more": dict(registers=((HTOTAL, 226), (VTOTAL, 329), (BEAMCON0, BEAM_VARBEAMEN | BEAM_PAL))),     # 330 lines of 227 clocks
    "var-less": dict(registers=((HTOTAL, 226), (VTOTAL, 259), (BEAMCON0, BEAM_VARBEAMEN | BEAM_PAL))),     # 260 lines
    "var-tall": dict(registers=((HTOTAL, 226), (VTOTAL, 699), (BEAMCON0, BEAM_VARBEAMEN | BEAM_PAL))),     # 700 lines
    "var-tallest": dict(registers=((HTOTAL, 226), (VTOTAL, 2046), (BEAMCON0, BEAM_VARBEAMEN | BEAM_PAL))),  # 2047 lines
    "var-wide": dict(registers=((HTOTAL, 255), (VTOTAL, 311), (BEAMCON0, BEAM_VARBEAMEN | BEAM_PAL))),      # 256 clocks per line
    # Productivity (31 kHz): 114 clocks per line (HTOTAL <= 164) and 524 lines (VTOTAL >= 350) make upstream use its doublescan.
    "productivity": dict(registers=((HTOTAL, 113), (VTOTAL, 523), (BEAMCON0, BEAM_VARBEAMEN))),
    # Productivity timing with SuperHires (HTOTAL >= 140): upstream does not doublescan it; the frame is 524 lines long.
    "productivity-shres": dict(mode="shres", bg=0x00A, fg=0x0F0, fg_register=5, border=False,
                               registers=((HTOTAL, 150), (VTOTAL, 523), (BEAMCON0, BEAM_VARBEAMEN))),
}

# Does the picture of the programmed mode fit the delivered buffer (760 x 576 PAL, 760 x 486 NTSC)? From the program: the raster is
# two rows per line from the first drawn line to the end of the frame (a doublescan mode draws one row per line, 2 x 252 lines),
# and one column per quarter clock. 'video' = the unit carries a picture, 'none' = P6_FRAME_NO_VIDEO.
BEAM_OUTCOME = {
    #                      PAL config  NTSC config
    "same":               ("video", "video"),
    "to-ntsc":            ("video", "video"),    # 484 rows fit both buffers
    "to-pal":             ("video", "none"),     # 574 rows do not fit the 486 of an NTSC buffer
    "toggle":             None,                  # the length of the unit changes every unit: only the safety rules apply
    "var-more":           ("none", "none"),      # 660 rows
    "var-less":           ("video", "video"),    # 470 rows
    "var-tall":           ("none", "none"),
    "var-tallest":        ("none", "none"),
    "var-wide":           ("none", "none"),      # 256 clocks = 1024 columns > 760
    "productivity":       ("video", "none"),     # doublescan: 504 rows
    "productivity-shres": ("none", "none"),      # 1048 rows
}


def beam_display_for(name, standard):
    """The program for `name`; "same" writes the config's own standard to BEAMCON0 (a write that changes nothing)."""
    spec = dict(BEAM[name])
    if name == "same":
        spec["registers"] = ((BEAMCON0, BEAM_PAL if standard == "pal" else 0),)
    return EcsDisplay("beam-" + name, bars=BARS, **spec)
