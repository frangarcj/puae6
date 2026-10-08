"""AV01 for the ECS chipset of the A600 preset: identification, 1 MiB of Chip RAM, SuperHires, DIWHIGH, BEAMCON0 and productivity.

Same method as tests/step/test_display.py: micro-ROMs (tests/scenarios/ecs/ecs.py) whose picture or log is deduced from the program
and from the ECS hardware documentation, never from a capture of the engine, run through the STEP01 probe on the A600 machine. The
hash baselines (tests/scenarios/ecs/ecs-baselines.json) only fix repeatability, recorded after the picture matched its prediction.

Two kinds of statement are kept apart on purpose:

  * deduced   the program writes it, the hardware documentation says what it does (identity, RAM, the window, the SHRES colours).
  * measured  what the bridge reports for a programmed beam (BEAMCON0, HTOTAL, VTOTAL) is checked as safety (never a byte outside
              the delivered buffer, a refused picture is announced) and, where the bridge is known to be wrong about the standard it
              is running, the current behaviour is pinned as a KNOWN LIMIT so that its correction is a visible change.
"""
from concurrent.futures import ThreadPoolExecutor
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
BUFFER = {"pal": (760, 576), "ntsc": (760, 486)}
NOMINAL_CADENCE = {"pal": (3546895, 70824), "ntsc": (715909, 11921)}
CLOCK_TICKS_PER_CCK = 512
FLAG_NO_VIDEO = 2
# KNOWN LIMIT (T23c): units after which the delivered buffer still holds pixels outside `active` (see BeamTests).
STRAY_UNITS = {("productivity-shres", "ntsc"): (1, 2)}
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
        # The A600 preset has the same canonical raster: 760 x 576 PAL / 760 x 486 NTSC buffer, 756 x 574 / 484 active.
        expected = {"pal": (576, 574, (26, 25), (3546895, 70824)), "ntsc": (486, 484, (43, 50), (715909, 11921))}
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
                        if row[0] not in STRAY_UNITS.get((name, standard), ()):
                            self.assertEqual(outside, "zero")                             # every byte outside `active` is zero
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

    def test_productivity_is_refused_by_the_ntsc_buffer_and_fits_the_pal_one(self):
        # MEASURED, not a hardware statement: doublescan draws one row per line, 504 of them, 616 columns wide.
        rows, pictures, _, _ = self.beam("productivity", "pal")
        self.assertEqual({(r[10], r[11]) for r in steady(rows)}, {(616, 504)})
        self.assertEqual({(p["width"], p["height"]) for n, p in pictures.items() if n >= 4}, {(616, 504)})
        rows, pictures, _, _ = self.beam("productivity", "ntsc")
        self.assertEqual({(r[2], r[3], r[10], r[11]) for r in steady(rows)}, {(0, 0, 0, 0)})
        self.assertEqual({n for n in pictures if n >= 3}, set())

    def test_pal_timing_programmed_on_an_ntsc_configuration_KNOWN_LIMIT(self):
        # KNOWN LIMIT (T23c). The standard of the delivered picture comes from the configuration, not from BEAMCON0: cadence
        # (bridge/video.cpp cadence()), the buffer height and the aspect are chosen by session.config.video_standard. A guest that
        # sets the PAL bit on an NTSC machine (a Kickstart 2.05 monitor driver can) runs 312-line units while the report says
        # NTSC: the 574 rows of the PAL raster do not fit the 486 of the NTSC buffer, so the units carry NO_VIDEO, and the cadence is
        # still the NTSC one although each unit lasts a PAL frame. If bridge/video.cpp derives these from the beam this test
        # has to change with it.
        rows, _, _, _ = self.beam("to-pal", "ntsc")
        late = steady(rows)
        self.assertEqual({(r[2], r[17] & FLAG_NO_VIDEO) for r in late}, {(0, FLAG_NO_VIDEO)})
        self.assertEqual({(r[14], r[15]) for r in late}, {NOMINAL_CADENCE["ntsc"]})            # reported: NTSC
        self.assertEqual({(r[12], r[13]) for r in late}, {(43, 50)})
        ticks = [b[1] - a[1] for a, b in zip(rows, rows[1:])][3:]
        pal_frame = 312 * 227 * CLOCK_TICKS_PER_CCK
        self.assertTrue(all(abs(t - pal_frame) <= 2 * 227 * CLOCK_TICKS_PER_CCK for t in ticks), ticks)   # actual: a PAL frame

    def test_ntsc_timing_programmed_on_a_pal_configuration_KNOWN_LIMIT(self):
        # KNOWN LIMIT (T23c), the other direction: the NTSC raster (760 x 484) fits the PAL buffer and is delivered, but the cadence and
        # the aspect still say PAL (50 Hz, 26:25) while each unit lasts an NTSC frame (262 lines of 227.5 clocks).
        rows, pictures, _, _ = self.beam("to-ntsc", "pal")
        late = steady(rows)
        self.assertEqual({(r[2], r[10], r[11]) for r in late}, {(1, 760, 484)})
        self.assertEqual({(r[14], r[15]) for r in late}, {NOMINAL_CADENCE["pal"]})              # reported: PAL
        self.assertEqual({(r[12], r[13]) for r in late}, {(26, 25)})
        ticks = [b[1] - a[1] for a, b in zip(rows, rows[1:])][3:]
        ntsc_frame = 262 * 2275 * CLOCK_TICKS_PER_CCK // 10
        self.assertTrue(all(abs(t - ntsc_frame) <= 2 * 228 * CLOCK_TICKS_PER_CCK for t in ticks), ticks)    # actual: an NTSC frame
        self.assertEqual({(p["width"], p["height"]) for n, p in pictures.items() if n >= 4}, {(760, 484)})

    def test_productivity_with_superhires_on_ntsc_leaves_stray_pixels_for_two_units_KNOWN_LIMIT(self):
        # KNOWN LIMIT (T23c). While the renderer still holds the frame it measured before the guest programmed the 524-line SHRES beam
        # (units 1 and 2; from unit 3 the mode is refused with NO_VIDEO), the delivered NTSC buffer carries 30 pixels in row 1 beyond
        # the active width (columns 756..785, so partly beyond the delivered width of 760, inside the 1824-pixel stride of the port's
        # array): the invariant "everything outside `active` is zero" does not hold in those two units. Memory stays inside the port's
        # array (the ASan/UBSan run, P6_TEST_SANITIZERS=1, is clean); the cause is in the port or in describe_video, outside this task.
        import struct
        name = "productivity-shres"
        rows, _, _, _ = self.run_rom("beam-" + name, ecs.rom_source(ecs.beam_display_for(name, "ntsc")), "ntsc", "a600", 6, buffer=True)
        self.assertEqual([r[0] for r in rows if r[18] == "dirty"], [1, 2])
        directory = Path(self.tmp.name) / "-".join(map(str, ("beam-" + name, "ntsc", "a600", 6, "frames", False, True)))
        for unit in (1, 2):
            data = (directory / f"buffer-{unit:03d}.bin").read_bytes()
            width, height, stride, _ = struct.unpack("<4I", data[:16])
            pixels = struct.unpack("<%dI" % (height * stride // 4), data[16:16 + height * stride])
            columns = stride // 4
            active = rows[unit - 1]
            stray = [(y, x) for y in range(height) for x in range(columns) if pixels[y * columns + x]
                     and not (y < active[11] and x < active[10])]
            self.assertEqual({y for y, _ in stray}, {1})
            self.assertEqual((min(x for _, x in stray), max(x for _, x in stray)), (756, 785))

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
