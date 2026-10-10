"""T31c (LR01, DET05): crop, position, aspect, gamma and RGB565 of the libretro adapter are a presentation of a copy, and change nothing of the machine.

adapters/libretro/present.h turns the picture the engine drew into the one the frontend gets. The tests answer two questions:
  * what does the frontend see? The minimal frontend of tests/libretro-host dumps every picture it is shown (--dump-pictures) and p6_headless dumps the native
    raster of the same unit (--dump); the picture shown must be, pixel for pixel, the window / gamma / 565 of the native one, computed here again from the
    rules of the option text and not from the C++;
  * what does the machine see? Nothing: with every presentation option on, the units, the cadence, the audio and the machine's part of a saved state are the
    ones of a run with every option off (DET05: what is shown is not what is simulated).
The C++ helpers have their own checks in tests/libretro-ui/present_probe.cpp (the drawn area, the patience of the automatic window, every crop size).
Run: python3 -m unittest discover -s tests/libretro-ui -p "test_present.py"
"""
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parents[1] / "libretro-host"
sys.path.insert(0, str(HOST))
import test_libretro as lr  # noqa: E402

from tests.scenarios.a500 import display  # noqa: E402

ROOT = lr.ROOT
PAL = (756, 574, 26, 25)
NTSC = (756, 484, 43, 50)
LEVELS = {"minimum": (360, 270, 240), "smaller": (348, 264, 240), "small": (336, 256, 240), "medium": (320, 240, 240), "large": (320, 224, 224),
          "larger": (320, 216, 216), "maximum": (320, 200, 200)}
STATE_NATIVE = re.compile(r"^state native (\S+) hash=(\w+)$", re.M)


def read_frame(path):
    """headless frame-NNN.bin: four uint32 (width, height, parity, flags), then the rows as XRGB8888."""
    data = Path(path).read_bytes()
    width, height = struct.unpack_from("<II", data)
    return width, height, data[16:]


def read_picture(path):
    """host picture-RUN.bin: four uint32 (width, height, bytes per pixel, run), then the rows without padding."""
    data = Path(path).read_bytes()
    width, height, bpp, _ = struct.unpack_from("<IIII", data)
    assert len(data) == 16 + width * height * bpp, (len(data), width, height, bpp)
    return width, height, bpp, data[16:]


def window_of(raster, x, y, width, height):
    """The window of an XRGB8888 raster (width, height, bytes) with its top-left at (x, y), black where it falls outside."""
    rw, rh, pixels = raster
    rows = []
    for j in range(height):
        sy = y + j
        if not 0 <= sy < rh:
            rows.append(bytes(width * 4))
            continue
        left = max(0, -x)
        right = min(width, rw - x)
        if left >= right:
            rows.append(bytes(width * 4))
            continue
        rows.append(bytes(left * 4) + pixels[(sy * rw + x + left) * 4:(sy * rw + x + right) * 4] + bytes((width - right) * 4))
    return b"".join(rows)


def gamma_table(tenths):
    exponent = 1.0 / (1.0 + tenths / 10.0)
    return bytes(i if tenths == 0 else int(math.floor(255.0 * math.pow(i / 255.0, exponent) + 0.5)) for i in range(256))


def to_565(pixels):
    out = bytearray()
    for (value,) in struct.iter_unpack("<I", pixels):
        out += struct.pack("<H", ((value >> 8) & 0xF800) | ((value >> 5) & 0x07E0) | ((value >> 3) & 0x001F))
    return bytes(out)


def level_size(level, mode, geometry):
    """The window size crop 'level' in 'mode' (both, horizontal, vertical) gives on a raster, from the table in the option text."""
    width, height, _, _ = geometry
    lores_width, pal_lines, ntsc_lines = LEVELS[level]
    w, h = min(2 * lores_width, width), min(2 * (ntsc_lines if height < 500 else pal_lines), height)
    if mode == "vertical":
        w = width
    elif mode == "horizontal":
        h = height
    return w, h


def drawn_area(raster):
    """The bounding box of what differs from each row's border (its pixel at column 8), looking between the black margins."""
    width, height, pixels = raster
    left, right, top, bottom = width, 0, height, 0
    for y in range(height):
        row = struct.unpack_from(f"<{width}I", pixels, y * width * 4)
        background = row[8]
        inside = [x for x in range(8, width - 8) if row[x] != background]
        if inside:
            left, right, top, bottom = min(left, inside[0]), max(right, inside[-1]), min(top, y), y
    return None if bottom < top else (left, top, right - left + 1, bottom - top + 1)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class PresentProbeTests(unittest.TestCase):
    """The C++ of present.h on synthetic pictures, compiled with ASan and UBSan next to no engine."""

    def test_the_presentation_code_holds_its_own_checks(self):
        with tempfile.TemporaryDirectory(prefix="p6-present-") as directory:
            exe = Path(directory) / "present_probe"
            command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                       str(ROOT / "tests/libretro-ui/present_probe.cpp"), "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("present probe ok", result.stdout)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class PresentTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A screen with something to look at in both directions: a hires bitplane with a few colour bars of the border.
        cls.display = display.Display("ui", mode="hires", bg=0x000, fg=0x0F0, bars=((60, 0x00F), (120, 0x0F0), (180, 0xF00), (240, 0x880)))
        rom = Path(cls.tmp.name) / "ui.rom"
        rom.write_bytes(lr.builder.build(display.rom_source(cls.display)))
        cls.roms["ui"] = rom
        cls.native = {}

    def native_raster(self, units=12, *flags):
        """The raster p6_headless draws for the unit `units` of the display ROM: (width, height, XRGB8888 bytes)."""
        key = (units, flags)
        if key not in self.native:
            directory = self.work()
            self.headless_text("ui", units, "--dump", str(directory), "--dump-from", str(units), *flags)
            self.native[key] = read_frame(directory / f"frame-{units:03d}.bin")
        return self.native[key]

    def shown(self, *options, runs=12, rom="ui", flags=(), **kwargs):
        """(stdout, {run: (width, height, bytes per pixel, bytes)}) of a session of the minimal frontend with the core options given as KEY=VALUE (`flags`
        are options of the frontend itself). Only the last run is dumped."""
        directory = self.work()
        arguments = ["--runs", runs, "--dump-pictures", directory, "--dump-from", runs, *flags]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend(rom, *arguments, **kwargs).stdout
        pictures = {int(re.search(r"picture-(\d+)", path.name)[1]): read_picture(path) for path in directory.glob("picture-*.bin")}
        return out, pictures

    # --- what the frontend sees --------------------------------------------------------------------------------------------------------------

    def test_with_no_option_the_picture_is_the_native_raster_untouched(self):
        out, pictures = self.shown()
        width, height, bpp, pixels = pictures[12]
        self.assertEqual((width, height, bpp), (756, 574, 4))
        self.assertEqual(pixels, self.native_raster()[2])
        self.assertEqual(re.findall(r"(?m)^geometry (\d+x\d+)", out), ["756x574"])           # announced once
        self.assertNotIn("pixel-format RGB565", out)

    def test_every_crop_level_is_a_centered_window_of_the_native_raster(self):
        native = self.native_raster()
        for level in LEVELS:
            with self.subTest(level=level):
                out, pictures = self.shown(f"puae6_video_crop={level}")
                width, height, bpp, pixels = pictures[12]
                w, h = level_size(level, "both", PAL)
                self.assertEqual((width, height, bpp), (w, h, 4))
                self.assertEqual(pixels, window_of(native, (756 - w) // 2, (574 - h) // 2, w, h))
                self.assertEqual(re.findall(r"(?m)^geometry (\d+x\d+)", out), [f"{w}x{h}"])
                self.assertEqual(re.search(r"(?m)^av base=(\d+x\d+)", out)[1], f"{w}x{h}")      # and the load already said so
                self.assertEqual({p[:2] for p in pictures.values()}, {(w, h)})

    def test_the_crop_modes_cut_the_dimensions_they_say(self):
        native = self.native_raster()
        for mode in ("horizontal", "vertical", "both"):
            with self.subTest(mode=mode):
                _, pictures = self.shown("puae6_video_crop=medium", f"puae6_video_crop_mode={mode}")
                w, h = level_size("medium", mode, PAL)
                width, height, _, pixels = pictures[12]
                self.assertEqual((width, height), (w, h))
                self.assertEqual(pixels, window_of(native, (756 - w) // 2, (574 - h) // 2, w, h))
        _, pictures = self.shown("puae6_video_crop=medium", "puae6_video_crop_mode=4:3")
        self.assertEqual(pictures[12][:2], (640, 500))                                       # taller until the picture is no wider than 4:3 (see present_probe.cpp)

    def test_the_crop_heights_of_ntsc_are_the_ntsc_ones(self):
        directory = self.work()
        self.headless_text("ui", 12, "--ntsc", "--dump", str(directory), "--dump-from", "12")
        native = read_frame(directory / "frame-012.bin")
        self.assertEqual(native[:2], (756, 484))
        for level in ("minimum", "large"):
            with self.subTest(level=level):
                _, pictures = self.shown(f"puae6_video_crop={level}", "puae6_video_standard=NTSC")
                w, h = level_size(level, "both", NTSC)
                self.assertEqual(pictures[12][:2], (w, h))
                self.assertEqual(pictures[12][3], window_of(native, (756 - w) // 2, (484 - h) // 2, w, h))

    def test_a_position_moves_the_window_and_what_falls_outside_the_raster_is_black(self):
        native = self.native_raster()
        w, h = level_size("medium", "both", PAL)
        cases = [("10", "auto", 0, 20), ("auto", "-6", 12, (574 - h) // 2), ("auto", "8", -16, (574 - h) // 2), ("-4", "0", 0, -8), ("0", "0", 0, 0),
                 ("70", "-40", 80, 140)]
        for vertical, horizontal, x, y in cases:
            with self.subTest(vertical=vertical, horizontal=horizontal):
                _, pictures = self.shown("puae6_video_crop=medium", f"puae6_video_vertical_pos={vertical}", f"puae6_video_horizontal_pos={horizontal}")
                x = (756 - w) // 2 if horizontal == "auto" else x
                self.assertEqual(pictures[12][3], window_of(native, x, y, w, h))
        _, moved = self.shown("puae6_video_vertical_pos=6", "puae6_video_horizontal_pos=-4")        # no crop: the picture moves inside the same frame
        self.assertEqual(moved[12][:3], (756, 574, 4))
        self.assertEqual(moved[12][3], window_of(native, 8, 12, 756, 574))

    def test_the_automatic_crop_follows_what_the_guest_drew(self):
        native = self.native_raster()
        box = drawn_area(native)
        self.assertIsNotNone(box)
        x, y, w, h = box
        self.assertLess(w, 756)
        self.assertLess(h, 574)                                                              # the display has a border: there is something to crop
        w, h = max(w, 640), max(h, 400)
        cx, cy = x + box[2] // 2, y + box[3] // 2
        wx = max(0, min(cx - w // 2, 756 - w))
        wy = max(0, min(cy - h // 2, 574 - h))
        _, pictures = self.shown("puae6_video_crop=auto", "puae6_video_crop_delay=disabled")           # instant: the window of the area drawn in this very unit
        self.assertEqual(pictures[12][:2], (w, h))
        self.assertEqual(pictures[12][3], window_of(native, wx, wy, w, h))
        # The patient window takes the area of the first picture and moves to a new one only after it has been the same for a while (kCropDelayUnits, in
        # emulated units): once the guest has drawn the same screen for longer than that, it is the instant window.
        late = self.native_raster(70)
        self.assertEqual(drawn_area(late), box)
        _, pictures = self.shown("puae6_video_crop=auto", runs=70)
        self.assertEqual(pictures[70][:2], (w, h))
        self.assertEqual(pictures[70][3], window_of(late, wx, wy, w, h))

    def test_gamma_changes_the_colors_of_the_copy_and_nothing_else(self):
        native = self.native_raster()
        for value, tenths in (("0.5", 5), ("0.2", 2), ("-0.3", -3), ("-0.5", -5)):
            with self.subTest(gamma=value):
                _, pictures = self.shown(f"puae6_video_gamma={value}")
                self.assertEqual(pictures[12][:3], (756, 574, 4))
                self.assertEqual(pictures[12][3], native[2].translate(gamma_table(tenths)))
        _, same = self.shown("puae6_video_gamma=0")
        self.assertEqual(same[12][3], native[2])

    def test_sixteen_bit_color_hands_over_rgb565_when_the_frontend_takes_it(self):
        native = self.native_raster()
        out, pictures = self.shown("puae6_video_color_depth=16bit")
        self.assertIn("pixel-format RGB565", out)
        width, height, bpp, pixels = pictures[12]
        self.assertEqual((width, height, bpp), (756, 574, 2))
        self.assertEqual(pixels, to_565(native[2]))
        self.assertRegex(out, r"(?m)^run 12 video=756x574 pitch=1512 ")
        # With a crop and gamma: the window first, then the gamma, then the packing.
        _, both = self.shown("puae6_video_color_depth=16bit", "puae6_video_crop=large", "puae6_video_gamma=0.3")
        w, h = level_size("large", "both", PAL)
        window = window_of(native, (756 - w) // 2, (574 - h) // 2, w, h)
        self.assertEqual(both[12][3], to_565(window.translate(gamma_table(3))))
        # A frontend without RGB565: the core says so and stays in 24-bit.
        out, fallback = self.shown("puae6_video_color_depth=16bit", flags=("--no-rgb565",))
        self.assertRegex(out, r"(?m)^message PUAE6: the frontend does not accept 16-bit pixels")
        self.assertEqual(fallback[12][:3], (756, 574, 4))
        self.assertEqual(fallback[12][3], native[2])

    def test_the_aspect_options_announce_the_ratio_and_the_picture_does_not_change(self):
        native = self.native_raster()
        pal = 756 * 26 / (574 * 25)
        expected = {"pixel": pal, "4:3": 4 / 3, "PAL": pal, "NTSC": 756 * 43 / (574 * 50)}
        for value, ratio in expected.items():
            with self.subTest(aspect=value):
                out, pictures = self.shown(f"puae6_video_aspect={value}")
                self.assertAlmostEqual(float(re.search(r"(?m)^geometry \d+x\d+ aspect=([\d.]+)", out)[1]), ratio, places=5)
                self.assertAlmostEqual(float(re.search(r"(?m)^av base=\S+ max=\S+ aspect=([\d.]+)", out)[1]), ratio, places=5)
                self.assertEqual(pictures[12][3], native[2])
        out, _ = self.shown("puae6_video_aspect=PAL", "puae6_video_crop=medium")                  # the ratio of a cropped window follows its own size
        w, h = level_size("medium", "both", PAL)
        self.assertAlmostEqual(float(re.search(r"(?m)^geometry \d+x\d+ aspect=([\d.]+)", out)[1]), w * 26 / (h * 25), places=5)

    def test_a_presentation_option_changed_while_it_runs_applies_at_once_and_a_16_bit_one_waits_for_a_reload(self):
        native = self.native_raster()
        out = self.frontend("ui", "--runs", 12, "--dump-pictures", self.work(), "--set-at", "5:puae6_video_crop=medium").stdout
        self.assertEqual(re.findall(r"(?m)^geometry (\d+x\d+)", out), ["756x574", "640x480"])
        sizes = [r["size"] for r in self.runs(out)]
        self.assertEqual(sizes, [(756, 574)] * 4 + [(640, 480)] * 8)                         # the change made before run 5 is in the picture of run 5
        directory = self.work()
        out = self.frontend("ui", "--runs", 8, "--dump-pictures", directory, "--set-at", "3:puae6_video_color_depth=16bit").stdout
        self.assertRegex(out, r"puae6_video_color_depth is now 16bit but the machine runs with 24bit: reload the content to apply it")
        self.assertEqual({read_picture(p)[2] for p in directory.glob("picture-*.bin")}, {4})

    # --- what the machine sees: nothing -----------------------------------------------------------------------------------------------------------

    EVERYTHING = ("puae6_video_crop=medium", "puae6_video_crop_mode=4:3", "puae6_video_vertical_pos=10", "puae6_video_horizontal_pos=-6", "puae6_video_gamma=0.3",
                  "puae6_video_aspect=PAL", "puae6_video_color_depth=16bit")

    def machine(self, *options, extra=(), runs=14, rom="ui"):
        """What the machine did in a session: its units, cadence and audio, and its part of the state saved after run 10 (the adapter's part left out)."""
        arguments = ["--runs", runs, "--do", "10:save:a", *extra]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend(rom, *arguments).stdout
        found = self.runs(out)
        native = STATE_NATIVE.search(out)
        self.assertIsNotNone(native)
        return ([(r["unit"], r["ticks"], r["audio"], r["ahash"]) for r in found], native[2], re.search(r"(?m)^cycle \d+ end .* audio_hash=(\w+)$", out)[1],
                re.search(r"(?m)^state size=(\d+)$", out)[1], out)

    def test_no_presentation_option_changes_the_units_the_audio_or_the_state_of_the_machine(self):
        control = self.machine()
        for options in (self.EVERYTHING, ("puae6_video_crop=auto",), ("puae6_video_crop=maximum", "puae6_video_crop_mode=16:9"), ("puae6_video_gamma=-0.5",),
                        ("puae6_video_color_depth=16bit",), ("puae6_video_aspect=4:3", "puae6_video_vertical_pos=-20", "puae6_video_horizontal_pos=40")):
            with self.subTest(options=options):
                shown = self.machine(*options)
                self.assertEqual(shown[:3], control[:3])
        shown = self.machine(extra=("--set-at", "6:puae6_video_crop=large", "--set-at", "8:puae6_video_gamma=0.4", "--set-at", "11:puae6_video_crop=disabled"))
        self.assertEqual(shown[:3], control[:3])                                              # changed in the middle of a run: the same machine
        # The serialization size is the machine's and the adapter's, and neither grows with a presentation option (the host also checks it after every run).
        self.assertEqual(self.machine(*self.EVERYTHING, extra=("--no-dupe",))[3], self.machine(extra=("--no-dupe",))[3])

    def test_a_frontend_that_cannot_repeat_a_picture_saves_the_picture_in_its_format_and_a_state_loads_into_a_session_with_another_format(self):
        saved = self.work() / "p.state"
        # A 16-bit session saves a state whose section holds the cropped 16-bit picture; the frontend that loads it can be any other format.
        sixteen = self.frontend("ui", "--runs", 8, "--no-dupe", "--set", "puae6_video_color_depth=16bit", "--set", "puae6_video_crop=medium",
                                "--do", f"6:savefile:{saved}").stdout
        self.assertRegex(sixteen, r"state save \S+ ok=1")
        same = self.frontend("ui", "--runs", 4, "--no-dupe", "--set", "puae6_video_color_depth=16bit", "--set", "puae6_video_crop=medium",
                             "--do", f"0:loadfile:{saved}:pos=6").stdout
        self.assertRegex(same, r"state load \S+ pos=6 ok=1")
        other = self.frontend("ui", "--runs", 4, "--no-dupe", "--do", f"0:loadfile:{saved}:pos=6").stdout
        self.assertRegex(other, r"state load \S+ pos=6 ok=1")                                 # the machine is the same; the stale picture is dropped
        self.assertNotIn("VIOLATION", other)
        self.assertEqual(self.runs(other)[0]["size"], (756, 574))

    def test_rewind_and_runahead_converge_whatever_the_presentation(self):
        control = self.frontend("ui", "--runs", 16, *self.set_all(), "--do", "16:save:z").stdout
        final = re.search(r"(?m)^state native z hash=(\w+)$", control)[1]
        for ahead in (("--ahead-every", "2"), ("--ahead", "5:4")):
            with self.subTest(ahead=ahead):
                out = self.frontend("ui", "--runs", 16, *self.set_all(), *ahead, "--do", "16:save:z").stdout
                self.assertEqual(re.search(r"(?m)^state native z hash=(\w+)$", out)[1], final)
                self.assertEqual([(r["unit"], r["ticks"], r["hash"], r["ahash"]) for r in self.runs(out)],
                                 [(r["unit"], r["ticks"], r["hash"], r["ahash"]) for r in self.runs(control)])
                self.assertGreater(len(re.findall(r"(?m)^spec ", out)), 3)

    def set_all(self):
        arguments = []
        for option in self.EVERYTHING:
            arguments += ["--set", option]
        return arguments


if __name__ == "__main__":
    unittest.main()
