"""AV01 picture half: what the delivered video shows for hardware programs, PAL and NTSC.

The expected pictures come from the programs (tests/scenarios/a500/display.py): which colour the Copper puts on
which line, which pixels the bitplane sets, where the display window is. They are not captures of the engine.
Every picture is also part of the STEP01 comparison (N calls against continuous advance).
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from scenario_schema import load_json, validate_scenario  # noqa: E402

from tests.scenarios.a500.display import Display, HEIGHT, WIDTH, from_rgb, predict, rgb, rom_source
from tests.step.test_step import BUILD, FIXTURES, TOOLS, builder, configure_trace_tree, frames_of, run_probe

BASELINES = Path(__file__).resolve().parents[1] / "scenarios/a500/display-baselines.json"
BARS = ((64, 0x00F), (128, 0xF00), (192, 0x0FF))
FIRST = 76   # first column of the display window

CASES = {
    "lores-bars": Display("lores-bars", mode="lores", bg=0x008, bars=BARS),
    "hires-bars": Display("hires-bars", mode="hires", bg=0x008, bars=BARS),
    "hires-lace-fields": Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True),
    "flip-mode": Display("flip-mode", mode="lores", flip="mode", bg=0x008, bars=BARS),
    "flip-lace": Display("flip-lace", mode="hires", flip="lace", bg=0x008, bars=BARS),
}
STANDARDS = ("pal", "ntsc")


def load_dump(path):
    data = Path(path).read_bytes()
    width, height, parity, flags = struct.unpack("<4I", data[:16])
    pixels = struct.unpack("<%dI" % (width * height), data[16:16 + 4 * width * height])
    return {"width": width, "height": height, "parity": parity, "flags": flags,
            "rows": [pixels[r * width:(r + 1) * width] for r in range(height)]}


def mismatches(picture, expected, limit=3):
    found = []
    for r, (row, want) in enumerate(zip(picture["rows"], expected)):
        for c, (got, exp) in enumerate(zip(row, want)):
            if exp is not None and got != exp:
                found.append("row %d col %d: %06x, expected %06x" % (r, c, got, exp))
                if len(found) >= limit:
                    return found
    return found


def picture_hash(picture):
    digest = hashlib.sha256()
    for row in picture["rows"]:
        digest.update(struct.pack("<%dI" % len(row), *row))
    return digest.hexdigest()


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-display-")
        cls.roms = {}
        for name, display in CASES.items():
            path = Path(cls.tmp.name) / (name + ".rom")
            path.write_bytes(builder.build(rom_source(display)))
            cls.roms[name] = path
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, name, standard, units):
        key = (name, standard, units)
        if key not in self.cache:
            dump = Path(self.tmp.name) / f"{name}-{standard}-{units}"
            dump.mkdir()
            code, out = run_probe(self.exe, self.roms[name], units, "frames", 1, timeout=300, extra=[standard],
                                  env={"P6_FRAME_DUMP": str(dump)})
            self.assertEqual(code, 0, out[-500:])
            pictures = {n: load_dump(dump / f"frame-{n:03d}.bin") for n in range(1, units + 1)
                        if (dump / f"frame-{n:03d}.bin").exists()}
            self.cache[key] = (frames_of(out), pictures, out)
        return self.cache[key]

    def test_the_program_appears_on_the_screen(self):
        for name in ("lores-bars", "hires-bars"):
            for standard in STANDARDS:
                rows, pictures, _ = self.run_case(name, standard, 6)
                expected = predict(CASES[name], standard)
                for unit in range(2, 7):
                    with self.subTest(display=name, standard=standard, unit=unit):
                        picture = pictures[unit]
                        self.assertEqual((picture["width"], picture["height"]), (WIDTH, HEIGHT[standard]))
                        self.assertEqual(mismatches(picture, expected), [])

    def test_it_is_not_a_black_screen(self):
        # The picture holds the four colours of the program: background, three bars and the foreground.
        _, pictures, _ = self.run_case("lores-bars", "pal", 4)
        colours = {p for row in pictures[4]["rows"] for p in row}
        self.assertTrue({rgb(0x008), rgb(0x00F), rgb(0xF00), rgb(0x0FF), rgb(0x0F0)} <= colours)

    def test_interlaced_fields_are_woven_and_announced_by_their_parity(self):
        for standard in STANDARDS:
            rows, pictures, _ = self.run_case("hires-lace-fields", standard, 14)
            display = CASES["hires-lace-fields"]
            height = HEIGHT[standard]
            steady = range(9, 15)
            parities = [rows[n - 1][16] for n in steady]
            with self.subTest(standard=standard, what="parity alternates"):
                self.assertEqual(set(parities), {1, 2})     # EVEN = rows 0,2,4.. and ODD = rows 1,3,5..
                self.assertTrue(all(a != b for a, b in zip(parities, parities[1:])), parities)
            for n in steady:
                with self.subTest(standard=standard, unit=n):
                    picture, previous = pictures[n], pictures[n - 1]
                    # Each field shows the foreground colour its own VERTB handler programmed; the two differ.
                    fg = {p: picture["rows"][200 + p][FIRST] for p in (0, 1)}
                    self.assertNotEqual(fg[0], fg[1])
                    models = {p: predict(display, standard, fg=from_rgb(fg[p])) for p in (0, 1)}
                    expected = [models[r % 2][r] for r in range(height)]
                    self.assertEqual(mismatches(picture, expected), [])
                    # The rows this unit rewrote are the ones of the parity it announces.
                    changed = {r % 2 for r in range(height) if picture["rows"][r][FIRST] != previous["rows"][r][FIRST]}
                    self.assertEqual(changed, {0} if rows[n - 1][16] == 1 else {1})

    def test_flipping_mode_every_unit_changes_the_picture_and_never_the_buffer(self):
        for standard in STANDARDS:
            rows, pictures, _ = self.run_case("flip-mode", standard, 10)
            display = CASES["flip-mode"]
            models = {m: predict(display, standard, mode=m) for m in ("lores", "hires")}
            seen = []
            for n in range(3, 11):
                with self.subTest(standard=standard, unit=n):
                    picture = pictures[n]
                    self.assertEqual((picture["width"], picture["height"]), (WIDTH, HEIGHT[standard]))
                    matching = [m for m, model in models.items() if not mismatches(picture, model)]
                    self.assertEqual(len(matching), 1, "the picture matches no display mode")
                    seen.append(matching[0])
            self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])), seen)

    def test_a_guest_that_toggles_interlace_every_unit_never_changes_the_buffer(self):
        # The renderer re-measures the frame every unit and adopts other sizes (PAL 576 rows; NTSC 760 columns,
        # 486 rows). The buffer stays, everything is zero outside the active raster, and a raster that does not fit
        # the buffer is refused without stopping the machine.
        for standard in STANDARDS:
            rows, pictures, out = self.run_case("flip-lace", standard, 16)
            height = 576 if standard == "pal" else 486
            with self.subTest(standard=standard):
                self.assertEqual(len(rows), 16)
                self.assertEqual({(r[5], r[6], r[7]) for r in rows}, {(760, height, 7296)})
                self.assertTrue(all(r[18] == "zero" for r in rows))
                delivered = [r for r in rows if r[2]]
                self.assertTrue(delivered)
                self.assertTrue(all(r[10] <= 760 and r[11] <= height for r in delivered))
                self.assertTrue(all(r[2] == 0 and r[17] & 2 for r in rows if not r[2]))
                ticks = [r[1] for r in rows]
                self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))

    def test_every_picture_is_part_of_the_step01_comparison(self):
        def compare(job):
            name, standard, units = job
            outs = []
            for mode in ("steps", "continuous"):
                code, out = run_probe(self.exe, self.roms[name], units, mode, 1, timeout=300, extra=[standard])
                self.assertEqual(code, 0, f"{name} {standard} {mode} x{units}: {out[-300:]}")
                outs.append(out)
            return job, outs
        jobs = [(name, "pal", units) for name in CASES for units in (1, 2, 3, 7)]
        jobs += [(name, "ntsc", 7) for name in ("lores-bars", "hires-bars", "flip-mode")]
        with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as pool:
            for job, (steps, continuous) in pool.map(compare, jobs):
                with self.subTest(job=job):
                    self.assertEqual(steps, continuous)

    def test_pictures_are_repeatable_and_match_the_recorded_baselines(self):
        baselines = json.loads(BASELINES.read_text(encoding="utf-8"))
        self.assertEqual(set(baselines["pictures"]), {f"{n}-{s}" for n in ("lores-bars", "hires-bars") for s in STANDARDS})
        for name in ("lores-bars", "hires-bars"):
            for standard in STANDARDS:
                _, pictures, out = self.run_case(name, standard, 6)
                dump = Path(self.tmp.name) / f"again-{name}-{standard}"
                dump.mkdir()
                code, again = run_probe(self.exe, self.roms[name], 6, "frames", 1, timeout=300, extra=[standard],
                                        env={"P6_FRAME_DUMP": str(dump)})
                self.assertEqual(code, 0)
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(out, again)                       # two processes, identical report
                    digest = picture_hash(pictures[6])
                    self.assertEqual(digest, picture_hash(load_dump(dump / "frame-006.bin")))
                    self.assertEqual(digest, baselines["pictures"][f"{name}-{standard}"])

    def test_scenario_files_are_valid_and_name_the_rom_they_run(self):
        for path in sorted((Path(BASELINES).parent).glob("display-*.json")):
            if path == BASELINES:
                continue
            with self.subTest(scenario=path.name):
                scenario = validate_scenario(load_json(path))
                name, standard = scenario["scenario_id"].removeprefix("display-").rsplit("-", 1)
                rom = builder.build(rom_source(CASES[name]))
                self.assertEqual(scenario["firmware"], [hashlib.sha256(rom).hexdigest()])
                self.assertEqual(scenario["config"]["video_standard"], standard.upper())
                self.assertEqual((scenario["render"], scenario["program"], scenario["media"]), (True, None, []))
        self.assertEqual(len(list(Path(BASELINES).parent.glob("display-*.json"))) - 1, len(CASES) * len(STANDARDS))

if __name__ == "__main__":
    unittest.main()
