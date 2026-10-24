"""AV01 for the resolution and line mode options (T72): every raster the configuration can ask for is delivered inside the buffer announced at load.

The options are the engine's own gfx_resolution and gfx_vresolution (docs/adr/0006-video-output.md, "Resolucion y modo de linea"), so the picture is
what the renderer draws, not a scaling of the default one. The expected pictures still come from the programs: the model of tests/scenarios/a500/display.py
describes the default raster (hires columns, two rows per line) from the program alone, and the other rasters are the same program seen at another
resolution, which is a change of coordinates and nothing else (a column is a quarter of a colour clock at hires, half of that at lores, twice at
super-hires; a row is one line, or half a line when the lines are doubled). The default stays what it was: the same bytes as before the options, which
the recorded baselines of tests/scenarios/a500/display-baselines.json and tests/scenarios/ecs/ecs-baselines.json (run by their own tests) keep.
Set P6_RECORD_BASELINES=1 to rewrite tests/step/resolution-baselines.json.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.scenarios.a500.display import WIDTH, predict, rom_source
from tests.scenarios.ecs import ecs
from tests.step.test_display import CASES, load_dump, mismatches, picture_hash
from tests.step.test_step import FIXTURES, ROOT, TOOLS, builder, configure_trace_tree, frames_of, make_rom, run_probe

BASELINES = ROOT / "tests/step/resolution-baselines.json"
STANDARDS = ("pal", "ntsc")
RESOLUTIONS = ("lores", "hires", "superhires")
LINE_MODES = ("single", "double")
COMBINATIONS = [(r, l) for r in RESOLUTIONS for l in LINE_MODES]
PICTURES = ("lores-bars", "hires-bars")
FIRST = 76                 # first column of the display window at hires
RESOLUTION_STEP = {"lores": 0, "hires": 1, "superhires": 2}
FLAG_NO_VIDEO = 2


# The probe names of the eight machines and whether the Agnus of the preset is OCS (bridge/config.cpp kMachines; the A1000 has the 8361, the A2000 OG the OCS one of the A500).
MACHINES = ("a500", "a500og", "a500plus", "a600", "a1000", "a1200", "a2000og", "a2000")
OCS_AGNUS = {"a500", "a500og", "a1000", "a2000og"}


def buffer_size(standard, machine, resolution, lines):
    """The buffer an image announces (docs/adr/0006-video-output.md): 380 << resolution columns; the long PAL height, or the NTSC one on an OCS Agnus (it cannot leave its standard), halved by single lines."""
    ntsc_short = standard == "ntsc" and machine in OCS_AGNUS
    height = 486 if ntsc_short else 576
    return 380 << RESOLUTION_STEP[resolution], height // (1 if lines == "double" else 2)


def nominal_raster(standard, resolution, lines):
    """The raster of a normal unit: 189 colour clocks of four hires columns, and 287 / 242 lines of one row or two."""
    return 189 << (RESOLUTION_STEP[resolution] + 1), (287 if standard == "pal" else 242) << (1 if lines == "double" else 0)


def pixel_aspect(standard, resolution, lines):
    """Width : height of one pixel, reduced: 26:25 (PAL) and 43:50 (NTSC) for a hires pixel on a double line; lores is twice as wide, super-hires half, single lines twice as tall."""
    from math import gcd
    num, den = (26, 25) if standard == "pal" else (43, 50)
    num *= 2 if resolution == "lores" else 1
    den *= 2 if resolution == "superhires" else 1
    den *= 2 if lines == "single" else 1
    return num // gcd(num, den), den // gcd(num, den)


def scaled_model(display, standard, resolution, lines, **kwargs):
    """The expected rows of the program at a resolution: the default model read at the coordinates of the other raster.

    Output column x is the column of the default (hires) raster that holds the same instant of the beam: 2x at lores, x at hires, x / 2 at super-hires.
    Output row y is the row of the default (double-line) raster that holds the same line: 2y with single lines, y with double. Cells the default model leaves
    undescribed stay undescribed."""
    base = predict(display, standard, **kwargs)
    width, height = nominal_raster(standard, resolution, lines)
    step = RESOLUTION_STEP[resolution]
    rows = []
    for y in range(height):
        source = base[y * (2 if lines == "single" else 1)]
        rows.append([source[x * 2 >> step] if (x * 2 >> step) < WIDTH else None for x in range(width)])
    return rows


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ResolutionCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-resolution-")
        cls.roms = {}
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rom(self, key, source=None):
        if key not in self.roms:
            path = Path(self.tmp.name) / (key + ".rom")
            path.write_bytes(builder.build(source))
            self.roms[key] = path
        return self.roms[key]

    def run_options(self, key, source, standard, units, resolution=None, lines=None, machine="a500", mode="frames", env=None):
        """(report rows, pictures by unit, raw output) of a program run with the given options (None: the profile's, not named at all)."""
        cache_key = (key, standard, units, resolution, lines, machine, mode)
        if cache_key not in self.cache:
            directory = Path(self.tmp.name) / ("-".join(str(part) for part in cache_key))
            directory.mkdir()
            extra = [standard]
            if resolution or lines or machine != "a500":
                extra += [machine]
            if resolution or lines:
                extra += [resolution or "hires"]
            if lines:
                extra += [lines]
            code, out = run_probe(self.exe, self.rom(key, source), units, mode, 1, timeout=600, extra=extra,
                                  env={"P6_FRAME_DUMP": str(directory), "P6_UNIT_LIMIT_LINES": "6000", **(env or {})})
            self.assertEqual(code, 0, out[-600:])
            pictures = {n: load_dump(directory / f"frame-{n:03d}.bin") for n in range(1, units + 1) if (directory / f"frame-{n:03d}.bin").exists()}
            self.cache[cache_key] = (frames_of(out), pictures, out)
        return self.cache[cache_key]


class RasterTests(ResolutionCase):
    def test_every_option_announces_and_delivers_its_buffer(self):
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for standard in STANDARDS:
            for resolution, lines in COMBINATIONS:
                rows, _, out = self.run_options("vsync", source, standard, 4, resolution, lines)
                width, height = buffer_size(standard, "a500", resolution, lines)
                active = nominal_raster(standard, resolution, lines)
                aspect = pixel_aspect(standard, resolution, lines)
                with self.subTest(standard=standard, resolution=resolution, lines=lines):
                    self.assertEqual(len(rows), 4)
                    self.assertIn(f"geometry {width}x{height} active={active[0]}x{active[1]} aspect={aspect[0]}/{aspect[1]}\n", out)
                    for row in rows:
                        (_, _, video, nbytes, fmt, w, h, stride, ax, ay, aw, ah, an, ad, cn, cd, parity, flags, outside, *_) = row
                        self.assertEqual((video, fmt, w, h, stride, nbytes), (1, 1, width, height, 7296, 7296 * height))
                        self.assertEqual((ax, ay, aw, ah, an, ad), (0, 0, *active, *aspect))
                        self.assertEqual((parity, flags & ~1, outside), (0, 0, "zero"))

    def test_naming_the_profile_raster_changes_nothing(self):
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for standard in STANDARDS:
            _, _, plain = self.run_options("vsync", source, standard, 6)
            _, _, named = self.run_options("vsync", source, standard, 6, "hires", "double")
            with self.subTest(standard=standard):
                self.assertEqual(named.replace(next(l for l in named.splitlines() if l.startswith("geometry")) + "\n", ""),
                                 plain.replace(next(l for l in plain.splitlines() if l.startswith("geometry")) + "\n", ""))
                self.assertIn("size=760x%d " % (576 if standard == "pal" else 486), plain)

    def test_the_other_machines_announce_the_same_scale(self):
        # An ECS or AGA Agnus reserves the PAL height in both standards (it can be programmed into the other one), and single lines halve it.
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for machine in ("a600", "a1200"):
            for standard in STANDARDS:
                for resolution, lines in (("lores", "single"), ("superhires", "double"), ("superhires", "single")):
                    rows, _, _ = self.run_options("vsync", source, standard, 4, resolution, lines, machine)
                    width, height = buffer_size(standard, machine, resolution, lines)
                    with self.subTest(machine=machine, standard=standard, resolution=resolution, lines=lines):
                        self.assertEqual({(r[5], r[6], r[10], r[11], r[18]) for r in rows}, {(width, height, *nominal_raster(standard, resolution, lines), "zero")})


    def test_every_machine_announces_and_delivers_the_height_of_its_agnus(self):
        # T72a: the height follows the Agnus of the preset, not the machine number: an OCS Agnus (A500, A500 OG, A1000, A2000 OG) keeps 486 rows in NTSC, an ECS or AGA one (A500+, A600,
        # A1200, A2000) 576. p6_query_video (the "geometry" line the probe prints before loading anything) and every frame of the run carry the same buffer, in every option.
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for machine in MACHINES:
            for standard in STANDARDS:
                for resolution, lines in COMBINATIONS:
                    rows, _, out = self.run_options("vsync", source, standard, 3, resolution, lines, machine)
                    width, height = buffer_size(standard, machine, resolution, lines)
                    with self.subTest(machine=machine, standard=standard, resolution=resolution, lines=lines):
                        self.assertEqual(len(rows), 3)
                        self.assertEqual(height, (486 if standard == "ntsc" and machine in OCS_AGNUS else 576) // (1 if lines == "double" else 2))
                        self.assertIn(f"geometry {width}x{height} ", out)
                        self.assertEqual({(r[5], r[6]) for r in rows}, {(width, height)})


class PictureTests(ResolutionCase):
    def picture(self, name, standard, resolution, lines, unit=6):
        _, pictures, _ = self.run_options(name, rom_source(CASES[name]), standard, unit, resolution, lines)
        return pictures[unit]

    def test_the_program_appears_at_every_resolution_and_line_mode(self):
        for name in PICTURES:
            for standard in STANDARDS:
                for resolution, lines in COMBINATIONS:
                    picture = self.picture(name, standard, resolution, lines)
                    expected = scaled_model(CASES[name], standard, resolution, lines)
                    with self.subTest(display=name, standard=standard, resolution=resolution, lines=lines):
                        self.assertEqual((picture["width"], picture["height"]), nominal_raster(standard, resolution, lines))
                        self.assertEqual(mismatches(picture, expected), [])

    def test_the_default_picture_is_the_model_of_the_default_raster(self):
        for name in PICTURES:
            for standard in STANDARDS:
                self.assertEqual(scaled_model(CASES[name], standard, "hires", "double"), predict(CASES[name], standard))
                self.assertEqual(picture_hash(self.picture(name, standard, None, None)), picture_hash(self.picture(name, standard, "hires", "double")))

    def test_pictures_match_the_recorded_baselines(self):
        recorded = {}
        for name in PICTURES:
            for standard in STANDARDS:
                for resolution, lines in COMBINATIONS:
                    recorded[f"{name}-{standard}-{resolution}-{lines}"] = picture_hash(self.picture(name, standard, resolution, lines))
        if os.environ.get("P6_RECORD_BASELINES"):
            BASELINES.write_text(json.dumps({"pictures": recorded}, indent=2) + "\n", encoding="utf-8")
        baselines = json.loads(BASELINES.read_text(encoding="utf-8"))
        self.assertEqual(set(baselines["pictures"]), set(recorded))
        for key, digest in recorded.items():
            with self.subTest(picture=key):
                self.assertEqual(digest, baselines["pictures"][key])
        self.assertEqual(len(set(recorded.values())), len(recorded), "two rasters of one program are the same picture")
        # The profile's raster, named or not, is the picture the golden baselines of the A500 scenarios have always had.
        golden = json.loads((ROOT / "tests/scenarios/a500/display-baselines.json").read_text(encoding="utf-8"))["pictures"]
        for name in PICTURES:
            for standard in STANDARDS:
                self.assertEqual(recorded[f"{name}-{standard}-hires-double"], golden[f"{name}-{standard}"])

    def test_repeating_a_run_gives_the_same_report(self):
        name = "hires-bars"
        for resolution, lines in (("lores", "single"), ("superhires", "double")):
            first = self.run_options(name, rom_source(CASES[name]), "pal", 6, resolution, lines)[2]
            code, again = run_probe(self.exe, self.roms[name], 6, "frames", 1, timeout=600,
                                    extra=["pal", "a500", resolution, lines], env={"P6_UNIT_LIMIT_LINES": "6000"})
            self.assertEqual(code, 0)
            with self.subTest(resolution=resolution, lines=lines):
                self.assertEqual(first, again)


class InterlaceTests(ResolutionCase):
    def run_lace(self, standard, resolution, lines, units=14):
        return self.run_options("hires-lace-fields", rom_source(CASES["hires-lace-fields"]), standard, units, resolution, lines)

    def test_double_lines_weave_the_fields_at_every_resolution(self):
        for standard in STANDARDS:
            for resolution in RESOLUTIONS:
                rows, pictures, _ = self.run_lace(standard, resolution, "double")
                width, height = nominal_raster(standard, resolution, "double")
                step = RESOLUTION_STEP[resolution]
                column = (FIRST << step) // 2                    # the first column of the window, at the same instant of the beam
                parities = [rows[n - 1][16] for n in range(9, 15)]
                with self.subTest(standard=standard, resolution=resolution):
                    self.assertEqual(set(parities), {1, 2})
                    self.assertTrue(all(a != b for a, b in zip(parities, parities[1:])), parities)
                    for n in range(9, 15):
                        picture, previous = pictures[n], pictures[n - 1]
                        self.assertEqual((picture["width"], picture["height"]), (width, height))
                        fg = {p: picture["rows"][200 + p][column] for p in (0, 1)}
                        self.assertNotEqual(fg[0], fg[1])
                        # The rows this unit rewrote are the ones of the parity it announces.
                        changed = {r % 2 for r in range(height) if picture["rows"][r][column] != previous["rows"][r][column]}
                        self.assertEqual(changed, {0} if rows[n - 1][16] == 1 else {1})

    def test_single_lines_show_each_field_as_a_picture_of_its_own(self):
        for standard in STANDARDS:
            for resolution in RESOLUTIONS:
                rows, pictures, _ = self.run_lace(standard, resolution, "single")
                width, height = buffer_size(standard, "a500", resolution, "single")
                step = RESOLUTION_STEP[resolution]
                column = (FIRST << step) // 2
                with self.subTest(standard=standard, resolution=resolution):
                    self.assertEqual({r[16] for r in rows[8:]}, {0})                 # nothing to weave: the buffer is progressive
                    self.assertEqual({(r[5], r[6], r[18]) for r in rows}, {(width, height, "zero")})
                    colours = []
                    for n in range(9, 15):
                        picture = pictures[n]
                        self.assertLessEqual(picture["width"], width)
                        self.assertLessEqual(picture["height"], height)
                        self.assertGreaterEqual(picture["height"], nominal_raster(standard, resolution, "single")[1] - 1)
                        # Every row of the window shows the colour this unit's handler programmed: one foreground in the whole picture, a new one in the next unit.
                        window = {picture["rows"][r][column] for r in range(100, 200)}
                        self.assertEqual(len(window), 1, window)
                        colours.append(window.pop())
                    self.assertTrue(all(a != b for a, b in zip(colours, colours[1:])), colours)
                    self.assertEqual(len(set(colours)), len(colours))                # the handler programs a new colour in every unit

    def test_a_guest_that_toggles_interlace_never_overflows_any_buffer(self):
        source = rom_source(CASES["flip-lace"])
        for standard in STANDARDS:
            for resolution, lines in COMBINATIONS:
                rows, pictures, _ = self.run_options("flip-lace", source, standard, 16, resolution, lines)
                width, height = buffer_size(standard, "a500", resolution, lines)
                with self.subTest(standard=standard, resolution=resolution, lines=lines):
                    self.assertEqual(len(rows), 16)
                    self.assertEqual({(r[5], r[6], r[7]) for r in rows}, {(width, height, 7296)})
                    self.assertTrue(all(r[18] == "zero" for r in rows))
                    delivered = [r for r in rows if r[2]]
                    self.assertTrue(delivered)
                    self.assertTrue(all(r[10] <= width and r[11] <= height for r in delivered))
                    self.assertTrue(all(r[17] & FLAG_NO_VIDEO for r in rows if not r[2]))
                    ticks = [r[1] for r in rows]
                    self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))


class BeamTests(ResolutionCase):
    """A programmed beam on an ECS machine is drawn or refused, never written outside the buffer of the option."""

    BEAMS = ("to-ntsc", "to-pal", "toggle", "productivity", "productivity-shres", "var-tallest", "var-wide")

    def test_programmed_beams_fit_or_are_refused_in_every_raster(self):
        jobs = [(name, standard, resolution, lines) for name in self.BEAMS for standard in STANDARDS
                for resolution, lines in (("lores", "single"), ("superhires", "double"))]

        def run(job):
            name, standard, resolution, lines = job
            return job, self.run_options("beam-" + name, ecs.rom_source(ecs.beam_display_for(name, standard)), standard, 10, resolution, lines, "a600")
        with ThreadPoolExecutor(max_workers=max(1, min(2, (os.cpu_count() or 2) - 2))) as pool:
            for (name, standard, resolution, lines), (rows, _, _) in pool.map(run, jobs):
                width, height = buffer_size(standard, "a600", resolution, lines)
                with self.subTest(beam=name, standard=standard, resolution=resolution, lines=lines):
                    self.assertEqual(len(rows), 10)
                    self.assertEqual({(r[5], r[6], r[7]) for r in rows}, {(width, height, 7296)})          # announced at load, constant
                    self.assertTrue(all(r[18] == "zero" for r in rows))
                    delivered = [r for r in rows if r[2]]
                    self.assertTrue(all(r[10] <= width and r[11] <= height for r in delivered))
                    self.assertTrue(all(r[17] & FLAG_NO_VIDEO for r in rows if not r[2]))

    def test_the_aga_machine_draws_in_every_raster(self):
        source = (FIXTURES / "vsync.s").read_text(encoding="utf-8")
        for resolution, lines in COMBINATIONS:
            rows, _, _ = self.run_options("vsync", source, "pal", 6, resolution, lines, "a1200")
            with self.subTest(resolution=resolution, lines=lines):
                self.assertEqual({r[2] for r in rows[2:]}, {1})
                self.assertEqual({(r[10], r[11]) for r in rows[2:]}, {nominal_raster("pal", resolution, lines)})


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class Step01Tests(ResolutionCase):
    def test_n_calls_equal_continuous_advance_at_every_raster(self):
        jobs = [(name, standard, resolution, lines) for name in ("hires-bars", "hires-lace-fields") for standard in ("pal",)
                for resolution, lines in COMBINATIONS]
        roms = {name: self.rom(name, rom_source(CASES[name])) for name in ("hires-bars", "hires-lace-fields")}

        def compare(job):
            name, standard, resolution, lines = job
            outs = []
            for mode in ("steps", "continuous"):
                code, out = run_probe(self.exe, roms[name], 5, mode, 1, timeout=300, extra=[standard, "a500", resolution, lines])
                self.assertEqual(code, 0, f"{job} {mode}: {out[-300:]}")
                outs.append(out)
            return job, outs
        with ThreadPoolExecutor(max_workers=max(1, min(2, (os.cpu_count() or 2) - 2))) as pool:
            for job, (steps, continuous) in pool.map(compare, jobs):
                with self.subTest(job=job):
                    self.assertEqual(steps, continuous)

    def test_the_raster_is_part_of_the_machine(self):
        # The same program at two rasters is two machines: the state digest of the last unit differs, and so does the picture.
        digests = set()
        for resolution, lines in COMBINATIONS:
            _, _, out = self.run_options("hires-bars", rom_source(CASES["hires-bars"]), "pal", 4, resolution, lines, mode="steps")
            digests.add(next(l for l in out.splitlines() if l.startswith("final digest=")))
        self.assertEqual(len(digests), len(COMBINATIONS))


if __name__ == "__main__":
    unittest.main()
