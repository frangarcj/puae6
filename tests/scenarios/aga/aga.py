"""AGA micro-ROMs (A1200 preset) for the AV01 scenarios, and the picture each one has to produce.

Every expectation comes from the program and from the AGA hardware documentation, never from a capture of the engine. The
geometry of the canonical raster is the one of tests/scenarios/a500/display.py and tests/scenarios/ecs/ecs.py (ADR 0006):

    row r shows Amiga line  first_line + r // 2       first_line = 26 (PAL), 21 (NTSC)
    column c shows lores position 91 + c / 2           DIWSTRT hstart $81 = column 76, DIWSTOP hstop $1C1 = column 716

What the AGA chipset adds and these programs exercise:

  Planes     BPLCON0 BPU = bits 14..12 plus BPU3 = bit 4 (8 planes: bits 14..12 clear, bit 4 set), so 256 colour registers.
             BPL7PT/BPL8PT are $0F8/$0FC. The pixel value is the bit of plane 1 (LSB) .. plane 8 (MSB).
  Palette    24 bits per register: COLORxx holds the high nibble of every component when BPLCON3 LOCT (bit 9) is 0 and the low
             nibble when it is 1; BPLCON3 bits 15..13 select the bank of 32 registers (colour number = 32 * bank + xx). A write
             with LOCT = 0 also loads the low nibble with the same value (an OCS program that writes $RGB gets $RRGGBB: the
             upstream rule of COLOR_WRITE, custom.cpp, needed for $FFF to be white), so a 24-bit colour is written high first, low
             second. XRGB8888 is the 24 bits as they are.
  HAM8       BPLCON0 HAM (bit 11) with 8 planes. The two LSB planes are the control bits: 00 = take colour register (value >> 2)
             (64 registers), 01 = replace the six MSBs of blue with the six MSBs of the value, 10 = red, 11 = green; the two
             LSBs of the component keep their previous value. The hold colour at the start of a line is COLOR00.
  FMODE      $DFF1FC bits 1..0 = bitplane fetch width (0: 16 bits, 1: 32 bits, 3: 64 bits), bits 3..2 = sprite width (same
             coding). A fetch unit of U colour clocks carries 16 * U / 8 lores pixels per plane (U = 8, 16, 32 for 16, 32, 64 bits;
             a hires unit is U = 8, 8, 16 clocks and carries 4 * U pixels). DDFSTRT and DDFSTOP are rounded down to a multiple of
             U (the low bits are ignored), units are fetched from the rounded start to the rounded stop included, and the
             first fetched pixel is displayed one unit after the rounded start. With DDFSTRT $38 (lores, every FMODE) and
             $3C, $38, $30 (hires, FMODE 0, 1, 3) the first pixel is therefore at colour clock $40, so the same program shows
             the same picture at every FMODE once DDFSTOP is moved to fetch the same 320 or 640 pixels:
             DDFSTOP = DDFSTRT + (units - 1) * U. The modulo is minus the bytes fetched per plane and line. A DDFSTRT that is
             not inside the unit that ends at $40 is NOT described by this rule (test_aga.MeasuredTests pins what the engine does).
  BPLCON4    bits 15..8 BPLAM: XOR mask of the bitplane pixel value before it selects the colour register (the border, COLOR00,
             is not XORed); bits 7..4 ESPRM and 3..0 OSPRM: the even / odd sprites take their colours from registers
             16 * nibble + 4 * pair + value.
  Sprites    SPRxPOS/SPRxCTL give VSTART, VSTOP and HSTART (lores pixels, $81 = the first pixel of the window); SPRxDATA/DATB are
             16, 32 or 64 bits wide (FMODE bits 3..2). In memory SPRxPOS and SPRxCTL are one DMA unit each of width / 8 bytes (the
             control word is the first 16 bits of its unit, the rest is ignored), then one line is a DATA unit followed by a
             DATB unit, and two zero units end the sprite. A sprite pixel is
             value = 2 * DATB bit + DATA bit, 0 is transparent, and SPRES = 0 makes it one lores pixel wide.
"""
from dataclasses import dataclass

from tests.scenarios.a500.display import FIRST_COLUMN, FIRST_LINE, HEIGHT, MARGIN, WIDTH, WRAP_COLUMN

BPLCON0_BASE = 0x0200                                # colour burst
DIWSTRT, DIWSTOP = 0x2C81, 0x2CC1                    # lines 44 .. 299, lores pixels 0 .. 319 of the window (DIWHIGH never written)
DIW_LINES = (44, 300)
DIW_COLUMNS = (FIRST_COLUMN, FIRST_COLUMN + 640)
PIXELS = {"lores": 320, "hires": 640}
# DDFSTRT that is a multiple of the fetch unit (hires) or lies inside the unit that ends at colour clock $40 (lores $38), so that the
# first pixel is at clock $40 in every FMODE. Other values are only measured (test_aga.DdfstrtMeasuredTests).
DDFSTRT = {("lores", 0): 0x38, ("lores", 1): 0x38, ("lores", 3): 0x38, ("hires", 0): 0x3C, ("hires", 1): 0x38, ("hires", 3): 0x30}
PLANE_AT, PLANE_STEP = 0x20000, 0x100                # plane p at PLANE_AT + p * PLANE_STEP (aligned to 8 bytes: FMODE 3)
COPPER_AT = 0x2000
NULL_SPRITE = 0x30000
SPRITE_AT, SPRITE_STEP = 0x31000, 0x400
BPLCON3_BASE = 0x0C00
LOCT = 0x0200
SPRITE_FMODE_SHIFT = 2
# MEASURED, not deduced: a sprite whose HSTART equals the window's DIWSTRT hstart appears one lores pixel (two columns) to the right of
# the first bitplane pixel, in every sprite width. (The Amiga Hardware Reference Manual's examples place the left edge of a standard
# window at sprite coordinate $80 for DIWSTRT $81, which is the same one-pixel offset; the test pins the measured column.)
SPRITE_DELAY = 2


def rgb24(r, g, b):
    return r << 16 | g << 8 | b


def unit_clocks(mode, fmode):
    """U: colour clocks of one fetch unit (FMODE 0, 1 or 3)."""
    bits = {0: 16, 1: 32, 3: 64}[fmode]
    return 8 * bits // 16 if mode == "lores" else max(8, 4 * bits // 16)


def unit_pixels(mode, fmode):
    return unit_clocks(mode, fmode) * (2 if mode == "lores" else 4)


def unit_bytes(mode, fmode):
    return unit_pixels(mode, fmode) // 8


def fetch_units(mode, fmode, ddfstrt, ddfstop):
    """(rounded start, units fetched) of a program, following the rule of the module docstring."""
    u = unit_clocks(mode, fmode)
    first, last = ddfstrt & ~(u - 1), ddfstop & ~(u - 1)
    return first, (last - first) // u + 1


def coherent_ddfstop(mode, fmode, pixels=None, start=None):
    """DDFSTOP that fetches `pixels` pixels (default: the whole window) from DDFSTRT."""
    units = (pixels or PIXELS[mode]) // unit_pixels(mode, fmode)
    return (DDFSTRT[mode, fmode] if start is None else start) + (units - 1) * unit_clocks(mode, fmode)


def lcg(seed):
    state = seed
    while True:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        yield state >> 8


# --- sprites ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sprite:
    number: int
    hstart: int                  # 9 bits, $81 = pixel 0 of the window
    vstart: int
    height: int = 16
    seed: int = 1

    def lines(self, width):
        """((DATA, DATB), ...) of `width` bits per line: pseudo-random but with every one of the 4 values in every line."""
        source = lcg(self.seed * 7919 + width)

        def draw():
            return sum(next(source) << 16 * i for i in range(width // 16 + 1)) & (1 << width) - 1
        return tuple((draw() | 1, draw() | 2) for _ in range(self.height))

    def words(self, width):
        """The memory image of the sprite as 16-bit words (control unit, lines, terminating unit)."""
        unit = width // 16                       # 16-bit words per DMA unit
        vstop = self.vstart + self.height
        assert vstop < 256 and self.vstart > 0
        pos = (self.vstart & 0xFF) << 8 | (self.hstart >> 1) & 0xFF
        ctl = (vstop & 0xFF) << 8 | (self.hstart & 1)
        words = [pos] + [0] * (unit - 1) + [ctl] + [0] * (unit - 1)       # POS and CTL: the first 16 bits of one unit each
        for data, datb in self.lines(width):
            for value in (data, datb):
                words += [value >> shift & 0xFFFF for shift in range(width - 16, -1, -16)]
        return words + [0] * (2 * unit)

    def pixel(self, width, line, k):
        """Value 0..3 of pixel k (0 = leftmost) of the sprite on `line`, or None outside it."""
        row = line - self.vstart
        if not 0 <= row < self.height or not 0 <= k < width:
            return None
        data, datb = self.lines(width)[row]
        bit = width - 1 - k
        return (datb >> bit & 1) << 1 | (data >> bit & 1)


# --- the display -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AgaDisplay:
    name: str
    mode: str = "lores"                  # lores | hires
    planes: int = 8
    fmode: int = 0                       # bitplane fetch: 0 | 1 | 3
    sprite_fmode: int = 0                # sprite width: 0 (16) | 1 (32) | 3 (64)
    ham: bool = False
    xor: int = 0                         # BPLCON4 BPLAM
    sprite_base: int = 0x11              # BPLCON4 ESPRM << 4 | OSPRM
    indices: tuple = ()                  # the value of every pixel of the row (PIXELS[mode] of them)
    palette: tuple = ()                  # ((register, rgb24, style), ...); style: full | twelve | lofirst
    bars: tuple = ()                     # ((first line, rgb24 of COLOR00), ...) written by the Copper with LOCT every frame
    sprites: tuple = ()
    ddfstrt: object = None               # None: DDFSTRT[mode, fmode]
    ddfstop: object = None               # None: coherent_ddfstop
    fetch_pixels: object = None          # pixels the program asks the DDF registers to fetch (None: the window)

    def ddf(self):
        start = self.ddfstrt if self.ddfstrt is not None else DDFSTRT[self.mode, self.fmode]
        stop = self.ddfstop if self.ddfstop is not None else coherent_ddfstop(self.mode, self.fmode, self.fetch_pixels, start)
        return start, stop

    def fetched_pixels(self):
        """Pixels per line the DDF registers make the chipset fetch (always a whole number of units)."""
        _, units = fetch_units(self.mode, self.fmode, *self.ddf())
        return units * unit_pixels(self.mode, self.fmode)

    def row_bytes(self):
        return self.fetched_pixels() // 8

    def sprite_width(self):
        return {0: 16, 1: 32, 3: 64}[self.sprite_fmode]

    def palette_map(self):
        return {reg: rgb for reg, rgb, _ in self.palette}

    def registers(self):
        """The colour register every pixel of a non-HAM row selects."""
        return [code ^ self.xor for code in self.indices]

    def colors(self):
        """rgb24 of every pixel of the row as the chipset shows it, from the indices (HAM8: the hold-and-modify chain)."""
        table = self.palette_map()
        out = []
        if self.ham:
            hold = table[0]
            for code in self.indices:
                ctl, data = code & 3, code & 0xFC
                if ctl == 0:
                    hold = table[code >> 2]
                elif ctl == 1:
                    hold = hold & 0xFFFF03 | data
                elif ctl == 2:
                    hold = hold & 0x03FFFF | data << 16
                else:
                    hold = hold & 0xFF03FF | data << 8
                out.append(hold)
            return out
        return [table[code ^ self.xor] for code in self.indices]

    def background(self, line):
        color = self.palette_map()[0]
        for first, value in self.bars:
            if line >= first:
                color = value
        return color

    def origin(self):
        """Column of the first displayed pixel."""
        first, _ = fetch_units(self.mode, self.fmode, *self.ddf())
        return int(4 * (first + unit_clocks(self.mode, self.fmode) - 45))


def plane_rows(d):
    """The bytes of every plane (plane 1 first): row_bytes() bytes, bit 7 of byte 0 is the first pixel."""
    rows = []
    for p in range(d.planes):
        bits = "".join("1" if code >> p & 1 else "0" for code in d.indices)
        rows.append(bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)))
    return rows


def rom_source(d):
    """Assembly of the program; an AgaDisplay is fully described by its fields."""
    a = ["; Original PUAE6 micro-ROM, licensed under MIT; see LICENSE.",
         "; Generated by tests/scenarios/aga/aga.py for the AGA display %s." % d.name,
         "entry:", "    MOVE.W #$2700,SR", "    LEA $400.W,A7",
         "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L"]
    # Bitplane data.
    for p, row in enumerate(plane_rows(d)):
        base = PLANE_AT + p * PLANE_STEP
        for i in range(0, len(row), 4):
            a.append("    MOVE.L #$%08X,$%X.L" % (int.from_bytes(row[i:i + 4], "big"), base + i))
    # Palette: per bank, the stray low nibbles first (LOCT), the high nibbles, then the low nibbles of the full 24-bit entries.
    banks = sorted({reg // 32 for reg, _, _ in d.palette})
    for bank in banks:
        entries = [(reg, rgb, style) for reg, rgb, style in d.palette if reg // 32 == bank]
        base = BPLCON3_BASE | bank << 13

        def nibbles(rgb, shift):
            return ((rgb >> 16 + shift & 15) << 8) | ((rgb >> 8 + shift & 15) << 4) | (rgb >> shift & 15)
        stray = [(reg, rgb) for reg, rgb, style in entries if style == "lofirst"]
        if stray:
            a.append("    MOVE.W #$%04X,$DFF106.L" % (base | LOCT))
            a += ["    MOVE.W #$%04X,$%X.L" % (nibbles(rgb, 0) ^ 0x777, 0xDFF180 + 2 * (reg % 32)) for reg, rgb in stray]
        a.append("    MOVE.W #$%04X,$DFF106.L" % base)
        a += ["    MOVE.W #$%04X,$%X.L" % (nibbles(rgb, 4), 0xDFF180 + 2 * (reg % 32)) for reg, rgb, _ in entries]
        full = [(reg, rgb) for reg, rgb, style in entries if style == "full"]
        if full:
            a.append("    MOVE.W #$%04X,$DFF106.L" % (base | LOCT))
            a += ["    MOVE.W #$%04X,$%X.L" % (nibbles(rgb, 0), 0xDFF180 + 2 * (reg % 32)) for reg, rgb in full]
    a += ["    MOVE.W #$%04X,$DFF106.L" % BPLCON3_BASE, "    MOVE.W #$0024,$DFF104.L", "    MOVE.W #$0000,$DFF102.L",
          "    MOVE.W #$%04X,$DFF10C.L" % (d.xor << 8 | d.sprite_base),
          "    MOVE.W #$%04X,$DFF1FC.L" % (d.fmode | d.sprite_fmode << SPRITE_FMODE_SHIFT)]
    a += ["    MOVE.W #$%04X,$DFF08E.L" % DIWSTRT, "    MOVE.W #$%04X,$DFF090.L" % DIWSTOP]
    start, stop = d.ddf()
    modulo = (-d.row_bytes()) & 0xFFFF
    a += ["    MOVE.L #$%04X%04X,$DFF092.L" % (start, stop), "    MOVE.L #$%04X%04X,$DFF108.L" % (modulo, modulo)]
    for p in range(d.planes):
        a.append("    MOVE.L #$%X,$%X.L" % (PLANE_AT + p * PLANE_STEP, 0xDFF0E0 + 4 * p))
    bplcon0 = (0x8000 if d.mode == "hires" else 0) | (d.planes & 7) << 12 | (0x10 if d.planes == 8 else 0) \
        | (0x800 if d.ham else 0) | BPLCON0_BASE
    a.append("    MOVE.W #$%04X,$DFF100.L" % bplcon0)
    # Sprites: the null sprite, then the data of every sprite of the display.
    width = d.sprite_width()
    if d.sprites:
        a += ["    MOVE.L #0,$%X.L" % (NULL_SPRITE + 4 * i) for i in range(4)]
        for sprite in d.sprites:
            base = SPRITE_AT + sprite.number * SPRITE_STEP
            words = sprite.words(width)
            for i in range(0, len(words), 2):
                a.append("    MOVE.L #$%04X%04X,$%X.L" % (words[i], words[i + 1], base + 2 * i))
    # Copper list: the sprite pointers and COLOR00 (24 bits) at the top of the frame, then one WAIT + 4 MOVEs per bar.
    cop = []
    if d.sprites:
        pointers = {s.number: SPRITE_AT + s.number * SPRITE_STEP for s in d.sprites}
        for n in range(8):
            address = pointers.get(n, NULL_SPRITE)
            cop += [(0x120 + 4 * n, address >> 16), (0x122 + 4 * n, address & 0xFFFF)]

    def color00(rgb):
        hi = (rgb >> 20 & 15) << 8 | (rgb >> 12 & 15) << 4 | rgb >> 4 & 15
        lo = (rgb >> 16 & 15) << 8 | (rgb >> 8 & 15) << 4 | rgb & 15
        return [(0x106, BPLCON3_BASE), (0x180, hi), (0x106, BPLCON3_BASE | LOCT), (0x180, lo)]
    cop += color00(d.background(0))
    for line, rgb in d.bars:
        assert 0 < line < 256, "bars stay below line 256 so that the WAIT needs no vertical wrap"
        cop += [((line << 8) | 1, 0xFFFE)] + color00(rgb)
    cop.append((0xFFFF, 0xFFFE))
    for i, (first, second) in enumerate(cop):
        a.append("    MOVE.L #$%04X%04X,$%X.L" % (first, second, COPPER_AT + 4 * i))
    dmacon = 0x8380 | (0x20 if d.sprites else 0)
    a += ["    MOVE.L #$%X,$DFF080.L" % COPPER_AT, "    MOVE.W #$%04X,$DFF096.L" % dmacon,
          "loop:", "    ADDQ.L #1,D0", "    BRA.S loop"]
    return "\n".join(a) + "\n"


def predict(d, standard):
    """Expected XRGB8888 rows (None where the model does not describe the cell) of a settled unit of the display."""
    colors = d.colors()
    fetched = d.fetched_pixels()
    origin = d.origin()
    height = HEIGHT[standard]
    width = d.sprite_width()
    picture = []
    for r in range(height):
        line = FIRST_LINE[standard] + r // 2
        cells = []
        for c in range(WIDTH):
            if c < MARGIN or c >= WIDTH - MARGIN or r >= height - MARGIN or (standard == "ntsc" and c >= WRAP_COLUMN) \
                    or (d.bars and c >= WRAP_COLUMN):
                cells.append(None)      # the blanking margins, and the cells where a Copper colour change is half done
                continue
            value = d.background(line + 1 if c >= WRAP_COLUMN else line)
            if DIW_LINES[0] <= line < DIW_LINES[1] and DIW_COLUMNS[0] <= c < DIW_COLUMNS[1]:
                pixel = (c - origin) // 2 if d.mode == "lores" else c - origin
                if c >= origin:
                    register = d.registers()[pixel] if pixel < fetched else d.xor
                    value = d.background(line + 1 if c >= WRAP_COLUMN else line) if register == 0 else \
                        (colors[pixel] if pixel < fetched else d.palette_map()[register])
                for sprite in d.sprites:
                    k = (c - DIW_COLUMNS[0] - SPRITE_DELAY) // 2 - (sprite.hstart - 0x81)
                    code = sprite.pixel(width, line, k)
                    if code:
                        value = d.palette_map()[(d.sprite_base >> 4 if sprite.number % 2 == 0 else d.sprite_base & 15) * 16 + code]
                        break
            cells.append(value)
        picture.append(cells)
    return picture


# --- the catalogue ---------------------------------------------------------------------------------------------------

def ramp_palette(styles=None):
    """256 distinct 24-bit colours whose low nibbles differ from the high ones: red is the index."""
    return tuple((i, rgb24(i, (i * 37 + 11) & 255, (i * 91 + 7) & 255), "full") for i in range(256))


def twelve_bit_palette():
    """The ramp where a third of the registers is written like OCS (LOCT = 0 only), a third low-nibble first, a third in full."""
    out = []
    for i, rgb, _ in ramp_palette():
        replicated = rgb24(*(((c >> 4) * 17) for c in (rgb >> 16 & 255, rgb >> 8 & 255, rgb & 255)))
        if i % 3 == 0:
            out.append((i, replicated, "twelve"))
        elif i % 3 == 1:
            out.append((i, replicated, "lofirst"))
        else:
            out.append((i, rgb, "full"))
    return tuple(out)


def ramp_indices(count):
    return tuple(x & 255 for x in range(count))


def hires_indices(count):
    """4-bit values that never repeat with a short period (a shifted picture is never mistaken for the right one); pixel 0 is not 0."""
    source = lcg(77)
    return (5,) + tuple(next(source) & 15 for _ in range(count - 1))


def ham_indices(count):
    """Hold-and-modify codes: random, but the first two pixels modify the hold colour (COLOR00) before any register is taken."""
    source = lcg(2024)
    codes = [(next(source) & 0xFC) | 1, (next(source) & 0xFC) | 2]
    codes += [next(source) & 255 for _ in range(count - 2)]
    return tuple(codes)


def hires_palette():
    return tuple((i, rgb24(i * 17, (i * 53 + 3) & 255, (255 - i * 11) & 255), "full") for i in range(16))


def ham_palette():
    return tuple((i, rgb24((i * 4 + 1) & 255, (i * 29 + 5) & 255, (i * 61 + 9) & 255), "full") for i in range(64))


BAR_COLORS = ((64, rgb24(0x12, 0x34, 0x56)), (128, rgb24(0xF1, 0x02, 0x83)), (192, rgb24(0x0E, 0xDC, 0xB9)))
SPRITE_PALETTE = ((0, rgb24(0x10, 0x22, 0x33), "full"), (1, rgb24(0xF0, 0xE1, 0xD2), "full")) + tuple(
    (base + v, rgb24(0x20 + 30 * v + (base >> 4), 0xF0 - 40 * v - (base >> 5), 0x31 + 17 * v + (base >> 6)), "full")
    for base in (0x40, 0x90) for v in (1, 2, 3))      # sprite colours: even sprites at 64+, odd sprites at 144+ (BPLCON4 $49)
SPRITE_BACKGROUND = tuple(1 if (x // 8) % 2 == 0 else 0 for x in range(PIXELS["lores"]))      # stripes of 8 pixels, one plane


def sprite_display(name, fmode):
    return AgaDisplay(name, planes=1, indices=SPRITE_BACKGROUND, palette=SPRITE_PALETTE, sprite_fmode=fmode, sprite_base=0x49,
                      sprites=(Sprite(0, 0x81 + 20, 60, seed=1), Sprite(1, 0x81 + 120, 90, seed=2)))


DISPLAYS = {d.name: d for d in (
    AgaDisplay("planes8", indices=ramp_indices(320), palette=ramp_palette()),
    AgaDisplay("planes8-fmode1", fmode=1, indices=ramp_indices(320), palette=ramp_palette()),
    AgaDisplay("planes8-fmode3", fmode=3, indices=ramp_indices(320), palette=ramp_palette()),
    AgaDisplay("planes8-xor", xor=0xA5, indices=ramp_indices(320), palette=ramp_palette()),
    AgaDisplay("palette12", indices=ramp_indices(320), palette=twelve_bit_palette()),
    AgaDisplay("ham8", ham=True, indices=ham_indices(320), palette=ham_palette()),
    AgaDisplay("ham8-fmode3", ham=True, fmode=3, indices=ham_indices(320), palette=ham_palette()),
    AgaDisplay("hires4", mode="hires", planes=4, indices=hires_indices(640), palette=hires_palette()),
    AgaDisplay("hires4-fmode1", mode="hires", planes=4, fmode=1, indices=hires_indices(640), palette=hires_palette()),
    AgaDisplay("hires4-fmode3", mode="hires", planes=4, fmode=3, indices=hires_indices(640), palette=hires_palette()),
    # DDFSTOP that fetches only the first 3 units of 64 pixels: the rest of the window shows register 0 (nothing fetched).
    AgaDisplay("planes8-short", fmode=3, indices=ramp_indices(320), palette=ramp_palette(), fetch_pixels=192),
    AgaDisplay("bars24", planes=1, indices=SPRITE_BACKGROUND, bars=BAR_COLORS,
               palette=((0, rgb24(0x08, 0x0A, 0x0C), "full"), (1, rgb24(0xF3, 0xE5, 0xD7), "full"))),
    sprite_display("sprite16", 0), sprite_display("sprite32", 1), sprite_display("sprite64", 3),
)}


# --- the scenario files (tests/scenarios/aga/aga-*.json) -------------------------------------------------------------

SCENARIO_UNITS = 6


def scenario_programs():
    """{scenario id: (assembly of the program, units, video standard)} for every program of this module in PAL and NTSC."""
    return {"aga-%s-%s" % (name, standard): (rom_source(display), SCENARIO_UNITS, standard)
            for standard in ("pal", "ntsc") for name, display in DISPLAYS.items()}


def scenario_document(scenario_id, rom_sha256, units, standard):
    """The strict-v1 scenario (tools/scenario_schema.py) of an AGA program on the A1200 preset of the deterministic-a500 profile."""
    idle = {"type": "none", "buttons": 0, "dx": 0, "dy": 0}
    return {
        "schema_version": 1, "scenario_id": scenario_id, "profile": "deterministic-a500",
        "config": {"machine": "A1200", "video_standard": standard.upper(), "audio_rate_hz": 48000, "random_seed": 1,
                   "rtc_epoch_seconds": 946684800, "chip_ram_bytes": 2097152, "slow_ram_bytes": 0},
        "firmware": [rom_sha256], "media": [], "program": None, "units": units,
        "inputs": [{"unit": 0, "keys": "0" * 32, "ports": [dict(idle), dict(idle)]}],
        "events": [], "checkpoints": list(range(1, units + 1)), "probes": [], "render": True, "audio": True,
    }
