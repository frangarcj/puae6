"""AV01 for the AGA chipset of the A1200 preset: 8 bitplanes, the 24-bit palette, HAM8, FMODE, wide sprites and BPLCON4.

Same method as tests/step/test_display.py and tests/step/test_ecs.py: micro-ROMs (tests/scenarios/aga/aga.py) whose picture is
deduced from the program and from the AGA hardware documentation, never from a capture of the engine, run through the STEP01
probe on the A1200 machine. The hash baselines (tests/scenarios/aga/aga-baselines.json) only fix repeatability, recorded after
the picture matched its prediction on every described cell.

Two kinds of statement are kept apart on purpose:

  * deduced   the program writes it, the hardware documentation says what it does (planes, palette nibbles, HAM8 chain, FMODE
              fetch units with coherent DDF registers, BPLCON4 XOR and sprite banks, sprite widths and memory layout).
  * measured  what the engine does where the documentation (as available here) does not say: the column of a sprite, the
              rounding of an unaligned DDFSTRT. Pinned as MEASURED tests so that a change of the engine is a visible change.
"""
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.scenarios.a500.display import HEIGHT, WIDTH
from tests.scenarios.aga import aga
from tests.step.test_display import mismatches, picture_hash
from tests.step.test_ecs import EcsCase
from tests.step.test_step import ROOT, TOOLS, builder, configure_trace_tree, run_probe

BASELINES = ROOT / "tests/scenarios/aga/aga-baselines.json"
STANDARDS = ("pal", "ntsc")
UNITS = aga.SCENARIO_UNITS
MACHINE = "a1200"
BUFFER = (760, 576)                                    # the PAL height in both standards on every machine with BEAMCON0 (ADR 0006)
FIRST_COLUMN = 76
FRAME_ENV = {"P6_UNIT_LIMIT_LINES": "6000"}


class ModelTests(unittest.TestCase):
    """The model itself, against values worked out by hand."""

    def test_ham8_chain_follows_the_control_bits(self):
        # Register 0 is the hold colour at the start of the line; the two LSBs are the control bits (00 = register code >> 2,
        # 01 = blue, 10 = red, 11 = green); a modification replaces the six MSBs of one component and keeps its two LSBs.
        table = ((0, 0x112233, "full"), (5, 0xABCDEF, "full"))
        codes = (0b11111101,      # control 01, data 111111: blue = 111111 + the two LSBs of $33 (11) = $FF
                 0b10101010,      # control 10, data 101010: red = 101010 + the two LSBs of $11 (01) = $A9
                 0b01010111,      # control 11, data 010101: green = 010101 + the two LSBs of $22 (10) = $56
                 5 << 2,          # control 00: register 5
                 0b00000001)      # control 01, data 000000: blue = 000000 + the two LSBs of $EF (11) = $03
        d = aga.AgaDisplay("hand", ham=True, palette=table, indices=codes)
        self.assertEqual(d.colors(), [0x1122FF, 0xA922FF, 0xA956FF, 0xABCDEF, 0xABCD03])

    def test_ham8_register_codes_select_among_64_registers(self):
        table = ((0, 0x000000, "full"), (5, 0xABCDEF, "full"), (63, 0x010203, "full"))
        d = aga.AgaDisplay("hand", ham=True, palette=table, indices=(5 << 2, 63 << 2, 0b10 | 1 << 7))
        self.assertEqual(d.colors(), [0xABCDEF, 0x010203, 0x010203 & 0x03FFFF | 0x80 << 16])

    def test_a_palette_entry_is_written_high_nibble_first(self):
        d = aga.AgaDisplay("hand", planes=1, indices=(1,) * 320, palette=((0, 0x000000, "full"), (1, 0x1A2B3C, "full")))
        source = aga.rom_source(d)
        high = source.index("MOVE.W #$0123,$DFF182.L")                # high nibbles of $1A2B3C: R=1 G=2 B=3
        low = source.index("MOVE.W #$0ABC,$DFF182.L")                 # low nibbles: R=A G=B B=C
        self.assertLess(high, low)
        self.assertIn("MOVE.W #$0E00,$DFF106.L", source[high:low])     # BPLCON3 bank 0 with LOCT set between the two writes

    def test_a_twelve_bit_write_replicates_the_nibble(self):
        for i, rgb, style in aga.twelve_bit_palette():
            if style != "full":
                self.assertEqual(rgb & 0x0F0F0F, (rgb >> 4) & 0x0F0F0F, i)

    def test_fetch_geometry_gives_the_same_pixels_and_origin_in_every_fmode(self):
        for mode in ("lores", "hires"):
            shapes = set()
            for fmode in (0, 1, 3):
                d = aga.AgaDisplay("x", mode=mode, planes=4, fmode=fmode, indices=(1,) * aga.PIXELS[mode], palette=((0, 0, "full"), (1, 1, "full")))
                shapes.add((d.fetched_pixels(), d.origin(), d.row_bytes()))
            self.assertEqual(shapes, {(aga.PIXELS[mode], FIRST_COLUMN, aga.PIXELS[mode] // 8)}, mode)

    def test_the_documented_ddf_values_come_out_of_the_formula(self):
        # FMODE 0/1/3 lores: the classic $D0, $C8, $B8 with DDFSTRT $38; hires: $D4 with $3C (FMODE 0).
        self.assertEqual([aga.coherent_ddfstop("lores", fm) for fm in (0, 1, 3)], [0xD0, 0xC8, 0xB8])
        self.assertEqual(aga.coherent_ddfstop("hires", 0), 0xD4)

    def test_wide_sprite_memory_image(self):
        sprite = aga.Sprite(0, 0x95, 60, height=2)
        for width, unit in ((16, 1), (32, 2), (64, 4)):            # unit: 16-bit words of one DMA unit
            words = sprite.words(width)
            self.assertEqual(len(words), 2 * unit + 2 * 2 * unit + 2 * unit)      # POS, CTL, 2 lines of DATA + DATB, two end units
            self.assertEqual(words[0], 60 << 8 | 0x95 >> 1)                       # SPRxPOS: VSTART, HSTART H8..H1
            self.assertEqual(words[unit], 62 << 8 | 1)                            # SPRxCTL: VSTOP, H0
            self.assertEqual(words[1:unit], [0] * (unit - 1))                     # the rest of the POS unit
            self.assertEqual(words[unit + 1:2 * unit], [0] * (unit - 1))
            self.assertEqual(words[-2 * unit:], [0] * (2 * unit))                 # the terminating units are zero
            data, datb = sprite.lines(width)[0]
            self.assertEqual(int.from_bytes(b"".join(w.to_bytes(2, "big") for w in words[2 * unit:3 * unit]), "big"), data)
            self.assertEqual(int.from_bytes(b"".join(w.to_bytes(2, "big") for w in words[3 * unit:4 * unit]), "big"), datb)
            self.assertTrue(0 <= data < 1 << width and 0 <= datb < 1 << width)

    def test_every_program_assembles(self):
        for name, display in aga.DISPLAYS.items():
            with self.subTest(display=name):
                builder.build(aga.rom_source(display))

    def test_each_picture_describes_what_the_catalogue_says(self):
        planes8 = aga.predict(aga.DISPLAYS["planes8"], "pal")
        colours = {c for row in planes8[60:480] for c in row[FIRST_COLUMN:716] if c is not None}
        self.assertEqual(len(colours), 256)                                       # every one of the 256 registers is on the screen
        self.assertTrue(any(c & 0x0F0F0F != (c >> 4) & 0x0F0F0F for c in colours))   # a genuine 24-bit colour
        ham = aga.predict(aga.DISPLAYS["ham8"], "pal")
        self.assertGreater(len({c for row in ham[60:480] for c in row[FIRST_COLUMN:716]}), 200)
        self.assertNotEqual(aga.predict(aga.DISPLAYS["planes8-xor"], "pal"), planes8)


class AgaCase(EcsCase):
    """EcsCase with the A1200 machine and the AGA programs."""

    def picture(self, name, standard, units=UNITS, **options):
        return self.run_rom(name, aga.rom_source(aga.DISPLAYS[name]), standard, MACHINE, units, **options)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class PictureTests(AgaCase):
    def test_the_picture_is_the_one_the_program_describes(self):
        for name, display in aga.DISPLAYS.items():
            for standard in STANDARDS:
                _, pictures, _, _ = self.picture(name, standard)
                expected = aga.predict(display, standard)
                for unit in range(2, UNITS + 1):
                    with self.subTest(display=name, standard=standard, unit=unit):
                        picture = pictures[unit]
                        self.assertEqual((picture["width"], picture["height"]), (WIDTH, HEIGHT[standard]))
                        self.assertEqual(mismatches(picture, expected), [])

    def test_the_256_registers_are_on_the_screen_with_their_24_bits(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.picture("planes8", standard)
            seen = {p for row in pictures[UNITS]["rows"][60:480] for p in row[FIRST_COLUMN:716]}
            with self.subTest(standard=standard):
                self.assertEqual(len(seen), 256)
                self.assertEqual(seen, {rgb for _, rgb, _ in aga.DISPLAYS["planes8"].palette})
                self.assertTrue(any(p & 0x0F0F0F != (p >> 4) & 0x0F0F0F for p in seen))    # not a 12-bit palette

    def test_twelve_bit_writes_and_stray_low_nibbles_end_up_as_the_documented_colours(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.picture("palette12", standard)
            _, reference, _, _ = self.picture("planes8", standard)
            with self.subTest(standard=standard):
                # The registers written in full are the same colours as in the plain palette, the others are replicated high nibbles.
                self.assertNotEqual(picture_hash(pictures[UNITS]), picture_hash(reference[UNITS]))
                seen = {p for row in pictures[UNITS]["rows"][60:480] for p in row[FIRST_COLUMN:716]}
                self.assertEqual(seen, {rgb for _, rgb, _ in aga.DISPLAYS["palette12"].palette})

    def test_bplcon4_xor_selects_the_other_register(self):
        for standard in STANDARDS:
            _, plain, _, _ = self.picture("planes8", standard)
            _, xored, _, _ = self.picture("planes8-xor", standard)
            with self.subTest(standard=standard):
                row = 100
                for pixel in range(256):             # pixel p has value p (the ramp): it shows what pixel p ^ $A5 shows without the mask
                    self.assertEqual(xored[UNITS]["rows"][row][FIRST_COLUMN + 2 * pixel],
                                     plain[UNITS]["rows"][row][FIRST_COLUMN + 2 * (pixel ^ 0xA5)])

    def test_fmode_changes_only_how_the_data_is_fetched(self):
        for standard in STANDARDS:
            for names in (("planes8", "planes8-fmode1", "planes8-fmode3"), ("hires4", "hires4-fmode1", "hires4-fmode3"),
                          ("ham8", "ham8-fmode3")):
                hashes = {n: picture_hash(self.picture(n, standard)[1][UNITS]) for n in names}
                with self.subTest(standard=standard, group=names):
                    self.assertEqual(len(set(hashes.values())), 1, hashes)

    def test_ddfstop_that_fetches_three_units_leaves_the_rest_of_the_window_on_register_zero(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.picture("planes8-short", standard)
            d = aga.DISPLAYS["planes8-short"]
            zero = d.palette_map()[0]
            row = pictures[UNITS]["rows"][100]
            with self.subTest(standard=standard):
                self.assertEqual(d.fetched_pixels(), 192)
                self.assertEqual(set(row[FIRST_COLUMN + 2 * 192:716]), {zero})
                self.assertGreater(len(set(row[FIRST_COLUMN:FIRST_COLUMN + 2 * 192])), 100)

    def test_ham8_holds_and_modifies_the_colour_of_the_previous_pixel(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.picture("ham8", standard)
            row = pictures[UNITS]["rows"][100]
            with self.subTest(standard=standard):
                self.assertGreater(len(set(row[FIRST_COLUMN:716])), 200)        # more colours than the 64 registers
                self.assertTrue(set(row[FIRST_COLUMN:716]) - {rgb for _, rgb, _ in aga.DISPLAYS["ham8"].palette})
                first = aga.DISPLAYS["ham8"].colors()[0]
                self.assertEqual(row[FIRST_COLUMN], first)                      # the first pixel modified COLOR00

    def test_sprites_are_in_front_and_use_the_banks_of_bplcon4(self):
        for name in ("sprite16", "sprite32", "sprite64"):
            for standard in STANDARDS:
                _, pictures, _, _ = self.picture(name, standard)
                d = aga.DISPLAYS[name]
                seen = {p for row in pictures[UNITS]["rows"] for p in row}
                with self.subTest(display=name, standard=standard):
                    for base in (0x40, 0x90):                                    # ESPRM 4 for sprite 0, OSPRM 9 for sprite 1
                        for value in (1, 2, 3):
                            self.assertIn(d.palette_map()[base + value], seen)

    def test_pictures_are_repeatable_and_match_the_recorded_baselines(self):
        baselines = json.loads(BASELINES.read_text(encoding="utf-8"))
        self.assertEqual(set(baselines["pictures"]), {f"{n}-{s}" for n in aga.DISPLAYS for s in STANDARDS})
        for name in aga.DISPLAYS:
            for standard in STANDARDS:
                _, pictures, _, out = self.picture(name, standard)
                _, again_pictures, _, again_out = self.run_rom(name + "-again", aga.rom_source(aga.DISPLAYS[name]), standard, MACHINE, UNITS)
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(out, again_out)
                    digest = picture_hash(pictures[UNITS])
                    self.assertEqual(digest, picture_hash(again_pictures[UNITS]))
                    self.assertEqual(digest, baselines["pictures"][f"{name}-{standard}"])

    def test_n_calls_equal_continuous_advance_on_the_a1200(self):
        names = ("planes8", "planes8-fmode3", "ham8-fmode3", "hires4-fmode3", "sprite64", "bars24")
        roms = {}
        for name in names:
            roms[name] = Path(self.tmp.name) / f"step01-{name}.rom"
            roms[name].write_bytes(builder.build(aga.rom_source(aga.DISPLAYS[name])))

        def compare(job):
            name, standard, units = job
            outs = []
            for mode in ("steps", "continuous"):
                code, out = run_probe(self.exe, roms[name], units, mode, 1, timeout=600, extra=[standard, MACHINE], env=FRAME_ENV)
                self.assertEqual(code, 0, f"{name} {standard} {mode} x{units}: {out[-300:]}")
                outs.append(out)
            return job, outs
        jobs = [(name, standard, units) for name in names for standard in STANDARDS for units in (3, 7)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for job, (steps, continuous) in pool.map(compare, jobs):
                with self.subTest(job=job):
                    self.assertEqual(steps, continuous)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class SafetyTests(AgaCase):
    """SAFE01 for the AGA modes: the contract of ADR 0006, decision 2, in every unit of every program."""

    def test_every_unit_of_every_aga_program_is_zero_outside_active_in_both_standards(self):
        delivered = 0
        for name in aga.DISPLAYS:
            for standard in STANDARDS:
                rows, _, _, _ = self.picture(name, standard)
                self.assertEqual(len(rows), UNITS)
                for row in rows:
                    (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, *_rest) = row
                    with self.subTest(program=name, standard=standard, unit=row[0]):
                        self.assertEqual((w, h, stride), (*BUFFER, 7296))
                        self.assertEqual(row[18], "zero")                          # every byte outside `active` is zero
                        if video:
                            self.assertEqual(nbytes, stride * h)
                            self.assertEqual((ax, ay, aw, ah), (0, 0, 756, HEIGHT[standard]))
                        delivered += bool(video)
        self.assertEqual(delivered, UNITS * len(aga.DISPLAYS) * len(STANDARDS))     # no AGA mode refuses the picture

    def test_the_whole_buffer_is_zero_outside_the_raster_in_the_widest_modes(self):
        # The probe summarizes "outside active" in one word; here the dump of the buffer itself, stride padding included.
        import struct
        for name in ("planes8-fmode3", "ham8-fmode3", "hires4-fmode3", "sprite64"):
            for standard in STANDARDS:
                rows, _, _, _ = self.picture(name, standard, buffer=True)
                directory = Path(self.tmp.name) / "-".join(map(str, (name, standard, MACHINE, UNITS, "frames", False, True)))
                for unit in range(1, UNITS + 1):
                    with self.subTest(display=name, standard=standard, unit=unit):
                        active = rows[unit - 1]
                        data = (directory / f"buffer-{unit:03d}.bin").read_bytes()
                        width, height, stride, _ = struct.unpack("<4I", data[:16])
                        pixels = struct.unpack("<%dI" % (height * stride // 4), data[16:16 + height * stride])
                        columns = stride // 4
                        stray = [(y, x) for y in range(height) for x in range(columns) if pixels[y * columns + x]
                                 and not (y < active[11] and x < active[10])]
                        self.assertEqual(stray, [])


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class MeasuredTests(AgaCase):
    """MEASURED, not hardware statements: pinned so that a change of the engine is a visible change."""

    def first_column(self, picture, display, row=100):
        zero = display.palette_map()[0]
        return next(c for c in range(FIRST_COLUMN, 716) if picture["rows"][row][c] != zero)

    def test_a_sprite_appears_one_lores_pixel_after_the_bitplane_pixel_of_the_same_coordinate(self):
        # The model places sprite pixel k at column 76 + 2 * (HSTART - $81) + 2 * k + SPRITE_DELAY; the pictures of all three widths
        # match it with SPRITE_DELAY = 2 (see PictureTests), and with 0 they do not.
        self.assertEqual(aga.SPRITE_DELAY, 2)
        d = aga.DISPLAYS["sprite16"]
        _, pictures, _, _ = self.picture("sprite16", "pal")
        for name_delay in (0, 4):
            with self.subTest(delay=name_delay):
                original = aga.SPRITE_DELAY
                aga.SPRITE_DELAY = name_delay
                try:
                    self.assertNotEqual(mismatches(pictures[UNITS], aga.predict(d, "pal")), [])
                finally:
                    aga.SPRITE_DELAY = original

    # (mode, FMODE, DDFSTRT, first column of the picture), PAL. Where the program is not on an aligned value the engine starts the
    # fetch at the next fetch unit: a DDFSTRT of $3C shows lores pixels at the clock of $48 (FMODE 0), $50 (FMODE 1) or $60 (FMODE 3)
    # instead of $40, and hires FMODE 1/3 at $48/$50. (The model of aga.py only describes DDFSTRT values the engine does not move.)
    DDFSTRT_ROUNDING = (("lores", 0, 0x3C, 108), ("lores", 1, 0x3C, 140), ("lores", 3, 0x3C, 204),
                        ("lores", 0, 0x40, 108), ("lores", 1, 0x40, 140), ("lores", 3, 0x40, 204),
                        ("hires", 1, 0x3C, 108), ("hires", 3, 0x3C, 140), ("hires", 0, 0x40, 92),
                        ("hires", 1, 0x40, 108), ("hires", 3, 0x40, 140),
                        ("lores", 1, 0x30, 76), ("lores", 3, 0x28, 76), ("hires", 1, 0x34, 76), ("hires", 3, 0x34, 76))

    def test_an_unaligned_ddfstrt_starts_the_picture_at_the_next_fetch_unit(self):
        for mode, fmode, ddfstrt, column in self.DDFSTRT_ROUNDING:
            base = aga.DISPLAYS["planes8" if mode == "lores" else "hires4"]
            display = dataclasses.replace(base, name=f"ddf-{mode}-{fmode}-{ddfstrt:x}", fmode=fmode, ddfstrt=ddfstrt)
            if mode == "lores":
                display = dataclasses.replace(display, indices=tuple((x + 1) & 255 for x in range(320)))      # pixel 0 is not register 0
            _, pictures, _, _ = self.run_rom(display.name, aga.rom_source(display), "pal", MACHINE, 4)
            with self.subTest(mode=mode, fmode=fmode, ddfstrt=hex(ddfstrt)):
                self.assertEqual(self.first_column(pictures[4], display), column)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class MetadataTests(AgaCase):
    def test_geometry_aspect_and_cadence_are_those_of_the_ecs_for_each_standard(self):
        expected = {"pal": (576, 574, (26, 25), (3546895, 70824)), "ntsc": (576, 484, (43, 50), (715909, 11921))}
        for standard, (height, active_h, aspect, cadence) in expected.items():
            rows, _, _, _ = self.picture("planes8-fmode3", standard)
            for row in rows:
                with self.subTest(standard=standard, unit=row[0]):
                    (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, an, ad, cn, cd, parity, flags, outside, *_) = row
                    self.assertEqual((video, fmt, w, h, stride), (1, 1, 760, height, 7296))
                    self.assertEqual((ax, ay, aw, ah, an, ad, cn, cd), (0, 0, 756, active_h, *aspect, *cadence))
                    self.assertEqual((parity, flags & ~1, outside), (0, 0, "zero"))


@unittest.skipUnless(TOOLS and os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with ASan/UBSan")
class AgaGuestSanitizerTests(unittest.TestCase):
    """Every AGA program under ASan and UBSan, PAL and NTSC: no write outside the buffer, no undefined behaviour."""

    def test_the_aga_programs_are_clean(self):
        exe = configure_trace_tree(ROOT / "build/step-trace-san", True)
        with tempfile.TemporaryDirectory(prefix="p6-aga-san-") as tmp:
            for name, display in aga.DISPLAYS.items():
                rom = Path(tmp) / (name + ".rom")
                rom.write_bytes(builder.build(aga.rom_source(display)))
                for standard in STANDARDS:
                    with self.subTest(guest=name, standard=standard):
                        code, out = run_probe(exe, rom, 12, "frames", 1, timeout=900, extra=[standard, MACHINE], env=FRAME_ENV)
                        self.assertEqual(code, 0, out[-600:])
                        self.assertNotIn("runtime error", out)
                        self.assertNotIn("AddressSanitizer", out)


if __name__ == "__main__":
    unittest.main()
