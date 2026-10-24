"""AV01 for the ECS chipset of the A600 preset: identification, 1 MiB of Chip RAM, SuperHires, DIWHIGH, BEAMCON0 and productivity.

Same method as tests/step/test_display.py: micro-ROMs (tests/scenarios/ecs/ecs.py) whose picture or log is deduced from the program
and from the ECS hardware documentation, never from a capture of the engine, run through the STEP01 probe on the A600 machine. The
hash baselines (tests/scenarios/ecs/ecs-baselines.json) only fix repeatability, recorded after the picture matched its prediction.

Two kinds of statement are kept apart on purpose:

  * deduced   the program writes it, the hardware documentation says what it does (identity, RAM, the window, the SHRES colours).
  * measured  what the bridge reports for a programmed beam (BEAMCON0, HTOTAL, VTOTAL) is checked as safety (never a byte outside
              the delivered buffer, a refused picture is announced) and, where the bridge is known to be wrong about the standard it
              is running, the current behaviour is pinned as a KNOWN LIMIT so that its correction is a visible change. (None is left: T23f
              announces cadence and aspect of the beam the unit really ran and T23g keeps everything outside `active` at zero.)
"""
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from tests.scenarios.a500.display import Display, HEIGHT, WIDTH, predict as a500_predict, rgb
from tests.scenarios.ecs import ecs
from tests.step.test_display import load_dump, mismatches, picture_hash
from tests.step.test_step import FIXTURES, ROOT, TOOLS, builder, configure_trace_tree, frames_of, make_rom, run_probe

BASELINES = ROOT / "tests/scenarios/ecs/ecs-baselines.json"
STANDARDS = ("pal", "ntsc")
BUFFER = {"pal": (760, 576), "ntsc": (760, 576)}          # an ECS machine reserves the PAL height in both configurations (ADR 0006)
NOMINAL_CADENCE = {"pal": (3546895, 70824), "ntsc": (715909, 11921)}
CHIPSET_CLOCK = {"pal": 3546895, "ntsc": 3579545}          # the clock of the configuration, whatever beam the guest programs
CLOCK_TICKS_PER_CCK = 512
FLAG_NO_VIDEO = 2
LOG_LINE = re.compile(r"^logu (\d+):((?: [0-9a-f]{4})*)$")


def log_words(out):
    """All the 16-bit words the program logged, per unit, in order."""
    units = {}
    for line in out.splitlines():
        match = LOG_LINE.match(line)
        if match:
            units[int(match[1])] = [int(w, 16) for w in match[2].split()]
    return units


class EcsCase(unittest.TestCase):
    """A probe tree (shared per class) and a runner for generated ROMs."""

    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-ecs-")
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_rom(self, key, source, standard, machine, units, mode="frames", log=False, buffer=False):
        """(report rows, pictures by unit, log words by unit, raw output) of `source` run for `units` units."""
        cache_key = (key, standard, machine, units, mode, log, buffer)
        if cache_key not in self.cache:
            directory = Path(self.tmp.name) / ("-".join(map(str, cache_key)))
            directory.mkdir()
            rom = directory / "program.rom"
            rom.write_bytes(builder.build(source))
            env = {"P6_FRAME_DUMP": str(directory), "P6_UNIT_LIMIT_LINES": "6000"}
            if log:
                env["P6_DUMP_LOG"] = "1"
            if buffer:
                env["P6_BUFFER_DUMP"] = str(directory)
            code, out = run_probe(self.exe, rom, units, mode, 1, timeout=600, extra=[standard, machine], env=env)
            self.assertEqual(code, 0, out[-600:])
            pictures = {n: load_dump(directory / f"frame-{n:03d}.bin") for n in range(1, units + 1)
                        if (directory / f"frame-{n:03d}.bin").exists()}
            self.cache[cache_key] = (frames_of(out), pictures, log_words(out), out)
        return self.cache[cache_key]


class ModelTests(unittest.TestCase):
    """The ECS model agrees with the A500 one where the chipsets are the same, and DIWHIGH changes the window it describes."""

    def test_the_ecs_model_is_the_a500_model_for_the_ocs_modes(self):
        for standard in STANDARDS:
            for mode in ("lores", "hires"):
                with self.subTest(standard=standard, mode=mode):
                    reference = a500_predict(Display("x", mode=mode, bg=0x008, bars=ecs.BARS), standard)
                    self.assertEqual(ecs.predict(ecs.EcsDisplay("x", mode=mode, bars=ecs.BARS), standard), reference)

    def test_diwhigh_moves_the_window_the_model_describes(self):
        for name in ("diwhigh-v", "diwhigh-stop", "diwhigh-start"):
            ecs_window = ecs.DISPLAYS[name].window()
            ocs_window = ecs.EcsDisplay(name, ecs=False, **{k: getattr(ecs.DISPLAYS[name], k)
                                                            for k in ("diwstrt", "diwstop", "diwhigh")}).window()
            self.assertNotEqual(ecs_window, ocs_window, name)
        self.assertEqual(ecs.DISPLAYS["diwhigh-v"].window(), (44, 0x190, 76, 716))              # lines 44.. to the end of the frame
        self.assertEqual(ecs.EcsDisplay("o", diwstop=0x90C1, diwhigh=0x2100, ecs=False).window()[:2], (44, 0x90))   # OCS: stop line 144
        self.assertEqual(ecs.DISPLAYS["diwhigh-stop"].window(), (44, 300, 76, 76 + (0xC1 - 129) * 2))               # 64 lores pixels
        self.assertEqual(ecs.DISPLAYS["diwhigh-start"].window(), (44, 300, 76 + (0x13F - 129) * 2, 716))         # 130 lores pixels

    def test_a_shres_row_is_the_hires_row_with_every_bit_twice(self):
        row = ecs.DISPLAYS["shres"].stored_row()
        visible = ecs.DISPLAYS["shres"].visible_row()
        self.assertEqual(len(row), 2 * len(visible))
        bits = "".join(format(w, "032b") for w in row)
        want = "".join(format(w, "032b") for w in visible)
        self.assertEqual(bits, "".join(b + b for b in want))

    def test_every_beam_program_has_an_expected_outcome_and_assembles(self):
        self.assertEqual(set(ecs.BEAM), set(ecs.BEAM_OUTCOME))
        for name in ecs.BEAM:
            for standard in STANDARDS:
                builder.build(ecs.rom_source(ecs.beam_display_for(name, standard)))


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class IdentificationTests(EcsCase):
    def test_the_program_reads_the_ecs_agnus_and_denise_ids(self):
        for standard in STANDARDS:
            _, pictures, units, _ = self.run_rom("ident", ecs.ident_source(), standard, "a600", 6, log=True)
            words = [w for unit in sorted(units) for w in units[unit]]
            pairs = list(zip(words[0::2], words[1::2]))
            with self.subTest(standard=standard):
                self.assertGreaterEqual(len(pairs), 6)       # the power-on read and one per VERTB
                for vposr, deniseid in pairs:
                    self.assertEqual(vposr & 0x7000, 0x2000 | (0x1000 if standard == "ntsc" else 0), hex(vposr))
                    self.assertEqual(deniseid, 0xFFFC)
                # The same value is on the screen: COLOR00 = DENISEID & $FFF = $FFC over the display.
                inside = {p for row in pictures[6]["rows"][60:480] for p in row[100:700]}
                self.assertEqual(inside, {rgb(0xFFC)})

    def test_the_ocs_a500_is_not_identified_as_ecs(self):
        for standard in STANDARDS:
            _, pictures, units, _ = self.run_rom("ident", ecs.ident_source(), standard, "a500", 6, log=True)
            words = [w for unit in sorted(units) for w in units[unit]]
            pairs = list(zip(words[0::2], words[1::2]))
            with self.subTest(standard=standard):
                self.assertGreaterEqual(len(pairs), 6)
                for vposr, deniseid in pairs:
                    self.assertEqual(vposr & 0x7000, 0x1000 if standard == "ntsc" else 0, hex(vposr))   # OCS family, NTSC bit
                    self.assertNotEqual(deniseid, 0xFFFC)       # no Denise ID register: the bus value comes back
                self.assertNotEqual({p for row in pictures[6]["rows"][60:480] for p in row[100:700]}, {rgb(0xFFC)})


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class PictureTests(EcsCase):
    def test_the_picture_is_the_one_the_program_describes(self):
        for name, display in ecs.DISPLAYS.items():
            for standard in STANDARDS:
                _, pictures, _, _ = self.run_rom(name, ecs.rom_source(display), standard, "a600", 6)
                expected = ecs.predict(display, standard)
                for unit in range(2, 7):
                    with self.subTest(display=name, standard=standard, unit=unit):
                        picture = pictures[unit]
                        self.assertEqual((picture["width"], picture["height"]), (WIDTH, HEIGHT[standard]))
                        self.assertEqual(mismatches(picture, expected), [])

    def test_the_program_writes_and_reads_one_mebibyte_of_chip_ram(self):
        for standard in STANDARDS:
            _, pictures, units, _ = self.run_rom("chip1m", ecs.rom_source(ecs.DISPLAYS["chip1m"]), standard, "a600", 6, log=True)
            with self.subTest(standard=standard):
                self.assertEqual(units[1], ecs.chip_ram_expected())
                # Bitplane $C0000 and Copper list $90000 are both on the screen: all four colours are there.
                colours = {p for row in pictures[6]["rows"] for p in row}
                self.assertTrue({rgb(0x008), rgb(0x00F), rgb(0xF00), rgb(0x0FF), rgb(0x0F0)} <= colours)

    def test_diwhigh_matters_on_the_ecs_and_is_ignored_by_the_ocs(self):
        for name in ("diwhigh-v", "diwhigh-stop", "diwhigh-start"):
            for standard in STANDARDS:
                ecs_display = ecs.DISPLAYS[name]
                ocs_display = ecs.EcsDisplay(name, bars=ecs.BARS, ecs=False, diwstrt=ecs_display.diwstrt,
                                             diwstop=ecs_display.diwstop, diwhigh=ecs_display.diwhigh)
                _, ecs_pictures, _, _ = self.run_rom(name, ecs.rom_source(ecs_display), standard, "a600", 6)
                _, ocs_pictures, _, _ = self.run_rom(name, ecs.rom_source(ocs_display), standard, "a500", 6)
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(mismatches(ocs_pictures[6], ecs.predict(ocs_display, standard)), [])
                    self.assertNotEqual(picture_hash(ocs_pictures[6]), picture_hash(ecs_pictures[6]))

    def test_superhires_shows_the_two_colours_of_the_program_and_nothing_else_inside_the_window(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.run_rom("shres", ecs.rom_source(ecs.DISPLAYS["shres"]), standard, "a600", 6)
            vstart, vstop, left, right = ecs.DISPLAYS["shres"].window()
            first = {"pal": 26, "ntsc": 21}[standard]
            inside = {pictures[6]["rows"][r][c] for r in range(2 * (60 - first), 2 * (63 - first)) for c in range(left, right)}
            with self.subTest(standard=standard):
                self.assertEqual(inside, {rgb(0x00A), rgb(0x0F0)})

    def test_pictures_are_repeatable_and_match_the_recorded_baselines(self):
        baselines = json.loads(BASELINES.read_text(encoding="utf-8"))
        self.assertEqual(set(baselines["pictures"]), {f"{n}-{s}" for n in ecs.DISPLAYS for s in STANDARDS})
        for name, display in ecs.DISPLAYS.items():
            for standard in STANDARDS:
                _, pictures, _, out = self.run_rom(name, ecs.rom_source(display), standard, "a600", 6)
                again = self.run_rom(name + "-again", ecs.rom_source(display), standard, "a600", 6)
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(out, again[3])
                    digest = picture_hash(pictures[6])
                    self.assertEqual(digest, picture_hash(again[1][6]))
                    self.assertEqual(digest, baselines["pictures"][f"{name}-{standard}"])

    def test_n_calls_equal_continuous_advance_on_the_a600(self):
        programs = {"ident": ecs.ident_source()}
        programs.update({n: ecs.rom_source(ecs.DISPLAYS[n]) for n in ("chip1m", "shres", "diwhigh-v")})
        programs.update({"beam-" + n: ecs.rom_source(ecs.beam_display_for(n, "pal")) for n in ("to-ntsc", "to-pal", "toggle", "productivity")})
        roms = {}
        for name, source in programs.items():
            roms[name] = Path(self.tmp.name) / f"step01-{name}.rom"
            roms[name].write_bytes(builder.build(source))

        def compare(job):
            name, standard, units = job
            rom = roms[name]
            outs = []
            for mode in ("steps", "continuous"):
                code, out = run_probe(self.exe, rom, units, mode, 1, timeout=600, extra=[standard, "a600"],
                                      env={"P6_UNIT_LIMIT_LINES": "6000"})
                self.assertEqual(code, 0, f"{name} {standard} {mode} x{units}: {out[-300:]}")
                outs.append(out)
            return job, outs
        jobs = [(name, standard, units) for name in programs for standard in STANDARDS for units in (3, 7)]
        with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as pool:
            for job, (steps, continuous) in pool.map(compare, jobs):
                with self.subTest(job=job):
                    self.assertEqual(steps, continuous)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class MetadataTests(EcsCase):
    def test_geometry_aspect_and_cadence_are_those_of_the_a500_for_each_standard(self):
        # The A600 preset has the same canonical raster: 756 x 574 / 484 active, in a 760 x 576 buffer for both standards (the NTSC
        # configuration keeps the PAL height so that a programmed PAL beam still fits; the A500 NTSC buffer is 486 rows).
        expected = {"pal": (576, 574, (26, 25), (3546895, 70824)), "ntsc": (576, 484, (43, 50), (715909, 11921))}
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for standard, (height, active_h, aspect, cadence) in expected.items():
            rows, _, _, _ = self.run_rom("vsync", source, standard, "a600", 4)
            self.assertEqual(len(rows), 4)
            for row in rows:
                with self.subTest(standard=standard, unit=row[0]):
                    (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, an, ad, cn, cd, parity, flags, outside, *_) = row
                    self.assertEqual((video, fmt, w, h, stride), (1, 1, 760, height, 7296))
                    self.assertEqual((ax, ay, aw, ah, an, ad, cn, cd), (0, 0, 756, active_h, *aspect, *cadence))
                    self.assertEqual((parity, flags & ~1, outside), (0, 0, "zero"))


def steady(rows, first=4):
    return rows[first - 1:]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BeamTests(EcsCase):
    """BEAMCON0 and the programmable beam: nothing is ever written outside the buffer, and the outcome is the documented one."""

    UNITS = 10

    def beam(self, name, standard):
        return self.run_rom("beam-" + name, ecs.rom_source(ecs.beam_display_for(name, standard)), standard, "a600", self.UNITS)

    def test_no_mode_writes_outside_the_buffer_and_a_refused_picture_is_announced(self):
        for name in ecs.BEAM:
            for standard in STANDARDS:
                rows, pictures, _, _ = self.beam(name, standard)
                with self.subTest(program=name, standard=standard):
                    self.assertEqual(len(rows), self.UNITS)
                    for row in rows:
                        (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, *_rest) = row
                        flags, outside, audio = row[17], row[18], row[21]
                        self.assertEqual((w, h, stride), (*BUFFER[standard], 7296))       # the buffer never changes with the beam
                        self.assertEqual(outside, "zero")                                 # every byte outside `active` is zero
                        self.assertTrue(0 < audio <= 8192, audio)                         # the PCM of a long frame still fits the queue
                        if video:
                            self.assertEqual(nbytes, stride * h)
                            self.assertTrue(ax + aw <= w and ay + ah <= h, (ax, ay, aw, ah))
                        else:
                            self.assertEqual((nbytes, aw, ah, flags & FLAG_NO_VIDEO), (0, 0, 0, FLAG_NO_VIDEO))
                    ticks = [row[1] for row in rows]
                    self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))

    def test_the_outcome_of_each_programmed_mode_is_the_documented_one(self):
        # A picture is delivered only when the raster the programme asks for fits the buffer of the configured standard
        # (docs/adr/0006-video-output.md, decision 6); otherwise the unit advances and carries P6_FRAME_NO_VIDEO.
        for name, outcome in ecs.BEAM_OUTCOME.items():
            for standard, want in zip(STANDARDS, outcome or ()):
                rows, _, _, _ = self.beam(name, standard)
                with self.subTest(program=name, standard=standard):
                    got = {("video" if row[2] else "none") for row in steady(rows)}
                    self.assertEqual(got, {want})

    def test_a_mode_that_changes_nothing_looks_like_the_plain_display(self):
        for standard in STANDARDS:
            _, pictures, _, _ = self.beam("same", standard)
            plain = ecs.EcsDisplay("plain", bars=ecs.BARS)
            for unit in range(2, 7):
                with self.subTest(standard=standard, unit=unit):
                    self.assertEqual(mismatches(pictures[unit], ecs.predict(plain, standard)), [])

    def test_productivity_fits_the_buffer_of_both_standards(self):
        # MEASURED, not a hardware statement: doublescan draws one row per line, 504 of them, 616 columns wide. The buffer of an ECS
        # machine is 576 rows in both configurations, so the NTSC one no longer refuses it.
        for standard in STANDARDS:
            rows, pictures, _, _ = self.beam("productivity", standard)
            with self.subTest(standard=standard):
                self.assertEqual({(r[10], r[11]) for r in steady(rows)}, {(616, 504)})
                self.assertEqual({(p["width"], p["height"]) for n, p in pictures.items() if n >= 4}, {(616, 504)})

    def announced_unit_ticks(self, row, standard):
        """The ticks of one unit that the cadence of `row` promises: the configuration's clock over the announced rate."""
        return CLOCK_TICKS_PER_CCK * CHIPSET_CLOCK[standard] * row[15] / row[14]

    def test_pal_timing_programmed_on_an_ntsc_configuration_is_announced_with_the_pal_beam(self):
        # A guest that sets the PAL bit on an NTSC machine (a Kickstart 2.05 monitor driver can) runs 312-line units. They are
        # reported as such: PAL aspect, cadence of 312 lines of 227 clocks on the clock of the configuration, and the picture
        # (574 rows) is delivered, because the buffer of an ECS machine has the PAL height.
        rows, _, _, _ = self.beam("to-pal", "ntsc")
        late = steady(rows)
        self.assertEqual({(r[2], r[6], r[10], r[11], r[17] & FLAG_NO_VIDEO) for r in late}, {(1, 576, 756, 574, 0)})
        self.assertEqual({(r[12], r[13]) for r in late}, {(26, 25)})
        self.assertEqual({Fraction(r[14], r[15]) for r in late}, {Fraction(CHIPSET_CLOCK["ntsc"], 312 * 227)})
        ticks = [b[1] - a[1] for a, b in zip(rows, rows[1:])][3:]
        pal_frame = 312 * 227 * CLOCK_TICKS_PER_CCK
        self.assertTrue(all(abs(t - pal_frame) <= 2 * 227 * CLOCK_TICKS_PER_CCK for t in ticks), ticks)
        for r in late:
            self.assertEqual(self.announced_unit_ticks(r, "ntsc"), pal_frame)          # the rate is exactly that length

    def test_ntsc_timing_programmed_on_a_pal_configuration_is_announced_with_the_ntsc_beam(self):
        # The other direction: 262 lines of 227.5 clocks on the PAL clock, NTSC aspect, the 760 x 484 raster delivered.
        rows, pictures, _, _ = self.beam("to-ntsc", "pal")
        late = steady(rows)
        self.assertEqual({(r[2], r[6], r[10], r[11]) for r in late}, {(1, 576, 760, 484)})
        self.assertEqual({(r[12], r[13]) for r in late}, {(43, 50)})
        self.assertEqual({Fraction(r[14], r[15]) for r in late}, {Fraction(CHIPSET_CLOCK["pal"], 59605)})   # 262 lines of 227.5 clocks
        ticks = [b[1] - a[1] for a, b in zip(rows, rows[1:])][3:]
        ntsc_frame = 262 * 2275 * CLOCK_TICKS_PER_CCK // 10
        self.assertTrue(all(abs(t - ntsc_frame) <= 2 * 228 * CLOCK_TICKS_PER_CCK for t in ticks), ticks)
        for r in late:
            self.assertEqual(self.announced_unit_ticks(r, "pal"), ntsc_frame)
        self.assertEqual({(p["width"], p["height"]) for n, p in pictures.items() if n >= 4}, {(760, 484)})

    def test_a_programmed_beam_is_announced_with_its_own_lines_and_clocks(self):
        # VARBEAMEN with HTOTAL/VTOTAL: lines x clocks per unit on the clock of the configuration. The PAL bit decides the aspect and
        # whether a line is half a clock longer (NTSC timing): "var-less" is 260 lines of 227 clocks with the PAL bit; "productivity" is
        # 524 lines of 114 clocks without it (114.5 on average). The announced length is the real spacing of the units.
        cases = {"var-less": (Fraction(260 * 227), (26, 25)), "productivity": (Fraction(524 * 229, 2), (43, 50))}
        for name, (clocks, aspect) in cases.items():
            for standard in STANDARDS:
                rows, _, _, _ = self.beam(name, standard)
                late = steady(rows)
                with self.subTest(program=name, standard=standard):
                    self.assertEqual({Fraction(r[14], r[15]) for r in late}, {CHIPSET_CLOCK[standard] / clocks})
                    self.assertEqual({(r[12], r[13]) for r in late}, {aspect})
                    ticks = [b[1] - a[1] for a, b in zip(rows, rows[1:])][3:]
                    expected = clocks * CLOCK_TICKS_PER_CCK
                    self.assertTrue(all(abs(t - expected) <= 2 * 229 * CLOCK_TICKS_PER_CCK for t in ticks), (ticks, expected))

    def test_productivity_with_superhires_leaves_nothing_outside_active_in_the_transition_units(self):
        # T23c measured 30 pixels of row 1, columns 756..785, in units 1 and 2 of the NTSC configuration: the renderer drew the first
        # lines of the 150-clock SuperHires beam while it still held the 756-pixel width of the previous mode (T23g). The port now
        # zeroes the tail of every row at the end of the pass. The two units are still delivered with the old raster, and the buffer
        # itself (stride padding included, read from the dump rather than from the probe's one-word summary) is zero outside `active`.
        import struct
        name = "productivity-shres"
        for standard, active_h in (("ntsc", 484), ("pal", 574)):
            rows, _, _, _ = self.run_rom("beam-" + name, ecs.rom_source(ecs.beam_display_for(name, standard)), standard, "a600", 6, buffer=True)
            directory = Path(self.tmp.name) / "-".join(map(str, ("beam-" + name, standard, "a600", 6, "frames", False, True)))
            self.assertEqual([r[18] for r in rows], ["zero"] * 6)
            for unit in (1, 2):
                with self.subTest(standard=standard, unit=unit):
                    active = rows[unit - 1]
                    self.assertEqual((active[2], active[10], active[11]), (1, 756, active_h))
                    data = (directory / f"buffer-{unit:03d}.bin").read_bytes()
                    width, height, stride, _ = struct.unpack("<4I", data[:16])
                    pixels = struct.unpack("<%dI" % (height * stride // 4), data[16:16 + height * stride])
                    columns = stride // 4
                    stray = [(y, x) for y in range(height) for x in range(columns) if pixels[y * columns + x]
                             and not (y < active[11] and x < active[10])]
                    self.assertEqual(stray, [])

    def test_every_unit_of_every_ecs_program_is_zero_outside_active_in_both_standards(self):
        # The contract of ADR 0006 decision 2 for the A600: displays and programmed beams, PAL and NTSC, every unit that carries a
        # picture (a unit without one has no buffer to inspect).
        programs = {"display-" + n: d for n, d in ecs.DISPLAYS.items()}
        programs.update({"beam-" + n: None for n in ecs.BEAM})
        delivered = 0
        for name, display in programs.items():
            for standard in STANDARDS:
                source = ecs.rom_source(display or ecs.beam_display_for(name[5:], standard))
                rows, _, _, _ = self.run_rom(name, source, standard, "a600", 8)
                for row in rows:
                    with self.subTest(program=name, standard=standard, unit=row[0]):
                        self.assertEqual(row[18], "zero")
                        delivered += bool(row[2])
        self.assertGreater(delivered, 100)

    def test_toggling_the_standard_every_unit_keeps_the_machine_and_the_buffer_consistent(self):
        for standard in STANDARDS:
            rows, _, _, _ = self.beam("toggle", standard)
            with self.subTest(standard=standard):
                self.assertEqual(len(rows), self.UNITS)
                self.assertEqual({(r[5], r[6]) for r in rows}, {BUFFER[standard]})
                self.assertTrue(all(r[18] == "zero" for r in rows))


@unittest.skipUnless(TOOLS and os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with ASan/UBSan")
class EcsGuestSanitizerTests(unittest.TestCase):
    """Every ECS program and every programmed beam under ASan and UBSan: no write outside the buffer, no undefined behaviour."""

    def test_the_ecs_programs_are_clean(self):
        exe = configure_trace_tree(ROOT / "build/step-trace-san", True)
        programs = {"ident": ecs.ident_source()}
        programs.update({"display-" + n: ecs.rom_source(d) for n, d in ecs.DISPLAYS.items()})
        programs.update({"beam-" + n: ecs.rom_source(ecs.beam_display_for(n, "pal")) for n in ecs.BEAM})
        with tempfile.TemporaryDirectory(prefix="p6-ecs-san-") as tmp:
            for name, source in programs.items():
                rom = Path(tmp) / (name + ".rom")
                rom.write_bytes(builder.build(source))
                for standard in STANDARDS:
                    with self.subTest(guest=name, standard=standard):
                        code, out = run_probe(exe, rom, 12, "frames", 1, timeout=900, extra=[standard, "a600"],
                                              env={"P6_UNIT_LIMIT_LINES": "6000"})
                        self.assertEqual(code, 0, out[-600:])
                        self.assertNotIn("runtime error", out)
                        self.assertNotIn("AddressSanitizer", out)


if __name__ == "__main__":
    unittest.main()
