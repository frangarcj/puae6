"""T72 (LR01): the resolution and line mode options through the libretro core, run by the minimal frontend.

puae6_video_resolution (lores, hires, superhires) and puae6_video_line_mode (single, double) are restart options: they are p6_config.video_resolution and video_line_mode, the engine's own
gfx_resolution and gfx_vresolution (docs/adr/0006-video-output.md). What is decided here:
  * the default is the profile's raster: a session that names hires and double lines is the session that names nothing, and every other raster is the machine the headless runner builds with
    --resolution and --line-mode (the same ticks, audio and picture of every unit, the picture of the right size);
  * the geometry announced at the load is the raster of the options: the nominal active raster as the base, the buffer every frame reports as the maximum, the pixel aspect of the raster;
  * the crop levels and the position follow the raster (a level is a size in lores pixels and lines), and the forced pixel aspects scale with it;
  * a state is loaded by the same raster and refused by any other, and the rewind and the run-ahead converge with each raster;
  * a change made while the machine runs is reported and applies at the next load; a value of no schema fails the load naming the key.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_resolution.py"
"""
from math import gcd
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

from tests.scenarios.a500.display import rom_source  # noqa: E402
from tests.step.test_display import CASES  # noqa: E402

RESOLUTION = "puae6_video_resolution"
LINE_MODE = "puae6_video_line_mode"
RESOLUTIONS = ("lores", "hires", "superhires")
LINE_MODES = ("single", "double")
COMBINATIONS = [(r, l) for r in RESOLUTIONS for l in LINE_MODES]
STEP = {"lores": 0, "hires": 1, "superhires": 2}
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
AV = re.compile(r"^av base=(\d+)x(\d+) max=(\d+)x(\d+) aspect=([\d.]+) fps=([\d.]+) rate=(\d+) region=(\d+)$", re.M)
UNITS = 12


def options(resolution=None, lines=None):
    out = []
    if resolution:
        out += ["--set", f"{RESOLUTION}={resolution}"]
    if lines:
        out += ["--set", f"{LINE_MODE}={lines}"]
    return out


def flags(resolution, lines):
    return ["--resolution", resolution, "--line-mode", lines]


def nominal(standard, resolution, lines):
    return 189 << (STEP[resolution] + 1), (287 if standard == "pal" else 242) << (1 if lines == "double" else 0)


def buffer(standard, resolution, lines):
    return 380 << STEP[resolution], (576 if standard == "pal" else 486) >> (0 if lines == "double" else 1)


def aspect(standard, resolution, lines):
    num, den = (26, 25) if standard == "pal" else (43, 50)
    num *= 2 if resolution == "lores" else 1
    den *= 2 if resolution == "superhires" else 1
    den *= 2 if lines == "single" else 1
    return num // gcd(num, den), den // gcd(num, den)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class ResolutionTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "bars.rom"
        rom.write_bytes(lr.builder.build(rom_source(CASES["hires-bars"])))
        cls.roms["bars"] = rom

    def play(self, *opts, runs=UNITS, rom="bars", extra=()):
        return self.frontend(rom, "--runs", runs, *opts, *extra).stdout

    def compare_with_headless(self, out, rom, units, *flags):
        """Every unit of a session of the core has the ticks, audio and picture that p6_headless gives the same ROM with the same machine."""
        headless = self.headless_units(rom, units, *flags)
        ticks = {int(m[1]): int(m[2]) for m in lr.TICKS.finditer(out)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), sorted(headless))
        for n, expected in headless.items():
            self.assertEqual((ticks[n], runs[n]["audio"]), (expected["ticks"], expected["audio"]), f"unit {n}")
            if expected["video"]:
                self.assertEqual((runs[n]["size"], runs[n]["hash"]), (expected["size"], expected["hash"]), f"unit {n}")
            else:
                self.assertTrue(runs[n]["dupe"], f"unit {n}")
        return runs

    def av(self, out):
        found = AV.search(out)
        self.assertIsNotNone(found, out[-500:])
        return found

    # --- the default is the profile's raster, and the headless runner builds every other -----------------------------------------------------------

    def test_naming_the_profile_raster_is_naming_nothing(self):
        plain = self.runs(self.play())
        named = self.runs(self.play(*options("hires", "double")))
        self.assertEqual([(r["hash"], r["ahash"], r["ticks"], r["size"]) for r in named], [(r["hash"], r["ahash"], r["ticks"], r["size"]) for r in plain])
        self.assertEqual({r["size"] for r in plain[3:]}, {(756, 574)})

    def test_every_raster_is_the_machine_the_headless_runner_builds(self):
        for resolution, lines in COMBINATIONS:
            with self.subTest(resolution=resolution, lines=lines):
                out = self.play(*options(resolution, lines))
                runs = self.compare_with_headless(out, "bars", UNITS, *flags(resolution, lines))
                self.assertEqual({r["size"] for n, r in runs.items() if n > 3}, {nominal("pal", resolution, lines)})

    def test_the_same_in_ntsc(self):
        for resolution, lines in (("lores", "single"), ("superhires", "double")):
            with self.subTest(resolution=resolution, lines=lines):
                out = self.play(*options(resolution, lines), "--set", "puae6_video_standard=NTSC")
                runs = self.compare_with_headless(out, "bars", UNITS, *flags(resolution, lines), "--ntsc")
                self.assertEqual({r["size"] for n, r in runs.items() if n > 3}, {nominal("ntsc", resolution, lines)})

    # --- what is announced at the load -------------------------------------------------------------------------------------------------------------

    def test_the_geometry_announced_at_the_load_is_the_raster_of_the_options(self):
        for standard in ("pal", "ntsc"):
            for resolution, lines in COMBINATIONS:
                with self.subTest(standard=standard, resolution=resolution, lines=lines):
                    out = self.play(*options(resolution, lines), "--set", f"puae6_video_standard={standard.upper()}", runs=3)
                    av = self.av(out)
                    width, height = nominal(standard, resolution, lines)
                    num, den = aspect(standard, resolution, lines)
                    self.assertEqual((int(av[1]), int(av[2]), int(av[3]), int(av[4])), (width, height, *buffer(standard, resolution, lines)))
                    self.assertAlmostEqual(float(av[5]), width * num / (height * den), places=3)
                    self.assertTrue(all(r["size"][0] <= int(av[3]) and r["size"][1] <= int(av[4]) for r in self.runs(out)), "a picture larger than the announced maximum")

    def test_the_profile_geometry_is_what_it_always_was(self):
        av = self.av(self.play(runs=3))
        self.assertEqual((av[1], av[2], av[3], av[4]), ("756", "574", "760", "576"))

    # --- the presentation follows the raster -------------------------------------------------------------------------------------------------------

    def test_a_crop_level_is_a_size_in_lores_pixels_and_lines_whatever_the_raster(self):
        # Maximum is 320 x 200 lores pixels and lines: 640 x 400 at hires on double lines, and the raster's own multiples elsewhere.
        for resolution, lines in COMBINATIONS:
            with self.subTest(resolution=resolution, lines=lines):
                out = self.play(*options(resolution, lines), "--set", "puae6_video_crop=maximum", runs=6)
                hx, hy = 1 << STEP[resolution], 2 if lines == "double" else 1
                self.assertEqual({r["size"] for r in self.runs(out)[3:]}, {(320 * hx, 200 * hy)})

    def test_a_forced_pixel_aspect_is_scaled_with_the_raster(self):
        for resolution, lines in (("lores", "double"), ("superhires", "single")):
            with self.subTest(resolution=resolution, lines=lines):
                plain = self.av(self.play(*options(resolution, lines), runs=3))
                forced = self.av(self.play(*options(resolution, lines), "--set", "puae6_video_aspect=NTSC", runs=3))
                width, height = int(plain[1]), int(plain[2])
                num, den = aspect("ntsc", resolution, lines)
                self.assertAlmostEqual(float(forced[5]), width * num / (height * den), places=3)

    # --- states, rewind and run-ahead --------------------------------------------------------------------------------------------------------------

    def state_file(self, *opts, at=6):
        path = self.work() / "x.state"
        self.frontend("bars", "--runs", at + 2, *opts, "--do", f"{at}:savefile:{path}")
        return path

    def load_ok(self, path, *opts):
        out = self.frontend("bars", "--runs", 3, *opts, "--do", f"0:loadfile:{path}:pos=6").stdout
        return LOAD.search(out)[3] == "1"

    def test_a_state_loads_at_the_same_raster_and_is_refused_at_any_other(self):
        rasters = [("hires", "double"), ("lores", "single"), ("superhires", "double"), ("hires", "single")]
        for saved in rasters:
            path = self.state_file(*options(*saved))
            for loaded in rasters:
                with self.subTest(saved=saved, loaded=loaded):
                    self.assertEqual(self.load_ok(path, *options(*loaded)), saved == loaded)

    def test_a_state_of_the_profile_raster_loads_in_the_session_that_names_it(self):
        path = self.state_file()
        self.assertTrue(self.load_ok(path, *options("hires", "double")))
        self.assertTrue(self.load_ok(self.state_file(*options("hires", "double")), *options()))

    def config_hash_of(self, path):
        data = path.read_bytes()
        start = data.index(b"P6NATIVE")
        return data[start + 176:start + 208]

    def test_the_config_hash_is_the_series_one_at_the_profile_raster_and_carries_the_options_in_bits_28_to_30(self):
        from tests.state import envelope as e
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(self.config_hash_of(self.state_file(at=3)), base)
        self.assertEqual(self.config_hash_of(self.state_file(*options("hires", "double"), at=3)), base)
        for resolution, lines, bits in (("lores", None, 1), ("superhires", None, 2), (None, "single", 4), ("lores", "single", 5), ("superhires", "single", 6)):
            self.assertEqual(self.config_hash_of(self.state_file(*options(resolution, lines), at=3)), e.config_hash(1, 1, 48000, bits << 28, 1, 946684800), (resolution, lines))

    def test_a_load_replays_each_unit_at_every_raster_and_the_runahead_leaves_the_run_as_a_plain_one_has_it(self):
        for resolution, lines in (("lores", "single"), ("superhires", "double")):
            with self.subTest(resolution=resolution, lines=lines):
                out = self.play(*options(resolution, lines), runs=30, extra=("--do", "10:save:a", "--do", "20:load:a"))
                seen = {}
                for run in self.runs(out):
                    seen.setdefault(run["unit"], []).append((run["ticks"], run["audio"], run["ahash"], run["hash"]))
                replayed = [views for views in seen.values() if len(views) > 1]
                self.assertGreaterEqual(len(replayed), 6)
                for views in replayed:
                    self.assertEqual(len(set(views)), 1, views)
                plain = self.runs(self.play(*options(resolution, lines), runs=16))
                ahead = self.runs(self.play(*options(resolution, lines), runs=16, extra=("--ahead-every", 2)))
                self.assertEqual([(r["audio"], r["ahash"], r["hash"]) for r in ahead], [(r["audio"], r["ahash"], r["hash"]) for r in plain])

    # --- refusals and changes while the machine runs ----------------------------------------------------------------------------------------------

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def test_a_value_of_no_schema_fails_the_load_naming_the_key(self):
        for key, value, allowed in ((RESOLUTION, "ultra", "lores, hires, superhires"), (LINE_MODE, "interlaced", "single, double")):
            with self.subTest(key=key):
                out = self.frontend("bars", "--set", f"{key}={value}", expect=3).stdout
                self.assertIn(f'the option {key} has the value "{value}", which this core does not support (allowed: {allowed})', out)
                self.assertNotIn("run 1 ", out)

    def test_a_change_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("bars", lr.KICKSTART_NAMES[0])
        base = self.frontend("bars", "--runs", 12, system=system).stdout
        for key, value, running in ((RESOLUTION, "lores", "hires"), (LINE_MODE, "single", "double")):
            changed = self.frontend("bars", "--runs", 12, "--set-at", f"5:{key}={value}", system=system).stdout
            self.assertEqual(self.messages(changed), [f"PUAE6: {key} is now {value} but the machine runs with {running}: reload the content to apply it"])
            self.assertEqual([(r["audio"], r["ahash"], r["hash"], r["size"]) for r in self.runs(changed)], [(r["audio"], r["ahash"], r["hash"], r["size"]) for r in self.runs(base)])


if __name__ == "__main__":
    unittest.main()
