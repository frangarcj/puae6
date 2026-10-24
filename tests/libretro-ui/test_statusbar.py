"""T31b (LR01, DET05): the statusbar, the messages and the LED interface are outputs drawn from the machine, never inputs to it.

adapters/libretro/statusbar.h draws a bar on the copy of the picture the frontend gets, from the drive light of the unit just run (P6_FRAME_DRIVE_LIGHT), the packet
the adapter itself made for that unit and the input memory (the bar's flag and the message of the last action, which travel with the state). The same drive light
goes to the frontend's LED interface when a LED changes. The tests ask two things, as tests/libretro-ui/test_present.py does:
  * what does the frontend see? Every picture is dumped and compared with the native raster p6_headless draws: outside the bar it is that raster, pixel for pixel;
    the LED box inside it is the color of the LED, which the floppy guest of tests/scenarios/disk turns on;
  * what does the machine see? Nothing: units, cadence, audio and the machine's part of a saved state are those of a run with the bar off, and a rewound or
    run-ahead timeline shows the same pictures (the message in progress is in the state).
The C++ of the drawing has its own checks in tests/libretro-ui/overlay_probe.cpp. Run: python3 -m unittest discover -s tests/libretro-ui -p "test_statusbar.py"
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parents[1] / "libretro-host"
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402
import test_present as tp  # noqa: E402

from tests.scenarios.a500 import display  # noqa: E402

ROOT = lr.ROOT
RETROK = {m[1]: int(m[2]) for m in re.finditer(r"^\s+RETROK_(\w+)\s*=\s*(\d+),", (ROOT / "references/libretro-api/include/libretro.h").read_text(), re.M)}
STATE_NATIVE = re.compile(r"(?m)^state native (\S+) hash=(\w+)$")
LED = re.compile(r"(?m)^led (\d+) (\d+)=(\d)( \(speculative\))?$")
LED_ON, LED_OFF = 0x40F040, 0x103810            # statusbar.h
BAR = 22                                         # rows of the bar at scale 2 (a picture 756 wide)
spec = importlib.util.spec_from_file_location("disk_fixture", ROOT / "tests/scenarios/disk/disk.py")
disk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disk)


def rows(picture, first, last):
    """Rows [first, last) of a (width, height, bytes per pixel, bytes) picture."""
    width, height, bpp, pixels = picture
    return pixels[first * width * bpp:last * width * bpp]


def pixel(picture, x, y):
    width, _, bpp, pixels = picture
    return int.from_bytes(pixels[(y * width + x) * bpp:(y * width + x + 1) * bpp], "little")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class OverlayProbeTests(unittest.TestCase):
    """The C++ of overlay.h and statusbar.h on synthetic pictures, compiled with ASan and UBSan next to no engine."""

    def test_the_drawing_holds_its_own_checks(self):
        with tempfile.TemporaryDirectory(prefix="p6-overlay-") as directory:
            exe = Path(directory) / "overlay_probe"
            command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                       "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "tests/libretro-ui/overlay_probe.cpp"), "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("overlay probe ok", result.stdout)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class StatusbarTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        screen = display.Display("ui", mode="hires", bg=0x000, fg=0x0F0, bars=((60, 0x00F), (120, 0x0F0), (180, 0xF00), (240, 0x880)))
        rom = Path(cls.tmp.name) / "ui.rom"
        rom.write_bytes(lr.builder.build(display.rom_source(screen)))
        cls.roms["ui"] = rom
        floppy = Path(cls.tmp.name) / "floppy.rom"
        floppy.write_bytes(lr.builder.build(disk.rom_source()))
        cls.roms["floppy"] = floppy
        adf = Path(cls.tmp.name) / "a.adf"
        adf.write_bytes(disk.make_adf(0x41))
        cls.adf = adf
        cls.native = {}

    def native_raster(self, units=12):
        if units not in self.native:
            directory = self.work()
            self.headless_text("ui", units, "--dump", str(directory), "--dump-from", str(units))
            self.native[units] = tp.read_frame(directory / f"frame-{units:03d}.bin")
        return self.native[units]

    def shown(self, *options, runs=12, rom="ui", flags=(), **kwargs):
        directory = self.work()
        arguments = ["--runs", runs, "--dump-pictures", directory, "--dump-from", runs, *flags]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend(rom, *arguments, **kwargs).stdout
        return out, {int(re.search(r"picture-(\d+)", p.name)[1]): tp.read_picture(p) for p in directory.glob("picture-*.bin")}

    # --- what the frontend sees ----------------------------------------------------------------------------------------------------------------

    def test_by_default_there_is_no_bar_and_no_led_call(self):
        out, pictures = self.shown()
        self.assertEqual(pictures[12][3], self.native_raster()[2])
        self.assertNotRegex(out, r"(?m)^led ")

    def test_the_bar_covers_the_rows_of_its_place_and_leaves_the_rest_of_the_raster_as_it_was(self):
        native = (756, 574, 4, self.native_raster()[2])
        for mode, bar_rows in (("bottom", (574 - BAR, 574)), ("top", (0, BAR)), ("bottom_basic", (574 - BAR, 574)), ("top_minimal", (0, BAR))):
            with self.subTest(mode=mode):
                _, pictures = self.shown("puae6_statusbar_startup=enabled", f"puae6_statusbar={mode}")
                shown = pictures[12]
                self.assertEqual(shown[:3], (756, 574, 4))
                outside = [(0, bar_rows[0]), (bar_rows[1], 574)]
                for first, last in outside:
                    self.assertEqual(rows(shown, first, last), rows(native, first, last), f"rows {first}..{last}")
                self.assertNotEqual(rows(shown, *bar_rows), rows(native, *bar_rows))
                # The bar is dark: its background lets a fifth of the picture through, so no pixel of it is brighter than the text.
                self.assertTrue(all(pixel(shown, x, bar_rows[0] + 1) != pixel(native, x, bar_rows[0] + 1) or x < 3 for x in range(100, 110)))

    def test_the_bar_of_a_cropped_picture_is_drawn_over_the_cropped_picture(self):
        native = (756, 574, 4, self.native_raster()[2])
        _, bare = self.shown("puae6_video_crop=medium")
        _, with_bar = self.shown("puae6_video_crop=medium", "puae6_statusbar_startup=enabled")
        w, h = bare[12][:2]
        self.assertEqual((w, h), (640, 480))
        self.assertNotEqual(rows(with_bar[12], h - BAR, h), rows(bare[12], h - BAR, h))
        self.assertEqual(rows(with_bar[12], 0, h - BAR), rows(bare[12], 0, h - BAR))
        del native

    def test_the_bar_is_in_the_16_bit_picture_too(self):
        _, plain = self.shown("puae6_video_color_depth=16bit")
        _, bar = self.shown("puae6_video_color_depth=16bit", "puae6_statusbar_startup=enabled")
        self.assertEqual(bar[12][2], 2)
        self.assertEqual(rows(bar[12], 0, 574 - BAR), rows(plain[12], 0, 574 - BAR))
        self.assertNotEqual(rows(bar[12], 574 - BAR, 574), rows(plain[12], 574 - BAR, 574))

    def test_the_hotkey_and_a_mapped_button_show_and_hide_the_bar(self):
        f12, f11 = RETROK["F12"], RETROK["F11"]
        native = self.native_raster()
        keys = ["--key-at", f"3:{f12}:1", "--key-at", f"4:{f12}:0", "--key-at", f"8:{f12}:1", "--key-at", f"9:{f12}:0"]
        sizes = {}
        for run in (2, 5, 10):
            _, pictures = self.shown(runs=run, flags=keys)
            sizes[run] = pictures[run][3] == native[2]
        self.assertEqual(sizes, {2: True, 5: False, 10: True})                           # F12 is the default hotkey: up in run 3, down in run 8
        # A button of the pad mapped to the toggle, and another key as the hotkey.
        _, mapped = self.shown("puae6_mapper_start=toggle_statusbar", runs=6, flags=["--joy-at", "3:0:8", "--joy-at", "4:0:0"])
        self.assertNotEqual(mapped[6][3], native[2])
        _, elsewhere = self.shown("puae6_hotkey_statusbar=f11", runs=6, flags=["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"])
        self.assertNotEqual(elsewhere[6][3], native[2])
        _, unmapped = self.shown("puae6_hotkey_statusbar=---", runs=6, flags=["--key-at", f"3:{f12}:1", "--key-at", f"4:{f12}:0"])
        self.assertEqual(unmapped[6][3], native[2])

    def test_a_message_shows_in_the_bar_and_goes_after_150_units(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"]
        options = ("puae6_statusbar_startup=enabled", "puae6_hotkey_turbo_toggle=f11")
        _, quiet = self.shown(*options, runs=12)
        _, said = self.shown(*options, runs=12, flags=keys)
        self.assertNotEqual(rows(said[12], 574 - BAR, 574), rows(quiet[12], 574 - BAR, 574))        # TURBO ON, and the flag TURBO
        self.assertEqual(rows(said[12], 0, 574 - BAR), rows(quiet[12], 0, 574 - BAR))
        # Within the bar the message is on the right: the left part differs only by the TURBO flag, and the right part has the text.
        right = lambda picture: b"".join(picture[3][(y * 756 + 600) * 4:(y * 756 + 756) * 4] for y in range(574 - BAR, 574))
        self.assertNotEqual(right(said[12]), right(quiet[12]))
        _, later = self.shown(*options, runs=160, flags=keys)
        _, later_quiet = self.shown(*options, runs=160)
        self.assertEqual(right(later[160]), right(later_quiet[160]))                         # the message went (the TURBO flag stays: it is the mode)
        self.assertNotEqual(rows(later[160], 574 - BAR, 574), rows(later_quiet[160], 574 - BAR, 574))

    def test_with_the_bar_hidden_the_messages_option_shows_the_message_alone(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"]
        native = self.native_raster()
        _, off = self.shown("puae6_hotkey_turbo_toggle=f11", runs=12, flags=keys)
        self.assertEqual(off[12][3], native[2])                                          # by default a hidden bar says nothing
        _, strip = self.shown("puae6_hotkey_turbo_toggle=f11", "puae6_statusbar_messages=enabled", runs=12, flags=keys)
        self.assertNotEqual(strip[12][3], native[2])
        self.assertEqual(rows(strip[12], 0, 574 - BAR), rows((756, 574, 4, native[2]), 0, 574 - BAR))
        _, over = self.shown("puae6_hotkey_turbo_toggle=f11", "puae6_statusbar_messages=enabled", runs=160, flags=keys)
        self.assertEqual(over[160][3], native[2])

    # --- the LED --------------------------------------------------------------------------------------------------------------------------------

    def floppy_session(self, *flags, runs=60, options=()):
        arguments = ["--runs", runs, *flags]
        for option in options:
            arguments += ["--set", option]
        return self.frontend("floppy", *arguments, content=self.adf).stdout

    def test_the_led_interface_is_set_when_the_drive_light_changes_and_only_then(self):
        out = self.floppy_session()
        calls = [(int(m[1]), int(m[2]), int(m[3])) for m in LED.finditer(out)]
        self.assertTrue(calls, "the floppy guest switches its motor on: the LED must light")
        leds = {led for _, led, _ in calls}
        self.assertEqual(leds, {1, 3})                                                   # "floppy drives" and DF0, in PUAE5's order
        for led in leds:
            states = [state for _, l, state in calls if l == led]
            self.assertEqual(states[0], 1)
            self.assertTrue(all(a != b for a, b in zip(states, states[1:])), states)      # never the same state twice in a row
        self.assertEqual(calls[-2:][0][2], 0)                                            # and they go out when the content closes
        self.assertEqual({state for _, _, state in calls[-2:]}, {0})
        silent = self.frontend("boot", "--runs", 20).stdout
        self.assertNotRegex(silent, r"(?m)^led ")                                        # no disk, no light, no call

    def test_a_frontend_without_the_led_interface_runs_the_same(self):
        with_led = self.floppy_session(runs=30)
        without = self.floppy_session("--no-led", runs=30)
        self.assertNotRegex(without, r"(?m)^led ")
        self.assertEqual([(r["unit"], r["ticks"], r["ahash"], r["hash"]) for r in self.runs(without)],
                         [(r["unit"], r["ticks"], r["ahash"], r["hash"]) for r in self.runs(with_led)])

    def test_the_led_of_the_bar_follows_the_drive_light(self):
        first = int(LED.search(self.floppy_session())[1])
        boxes = {}
        for rom, run in (("boot", 12), ("floppy", 2), ("floppy", first + 5), ("floppy", first + 20)):
            directory = self.work()
            arguments = ["--runs", run, "--dump-pictures", directory, "--dump-from", run, "--set", "puae6_statusbar_startup=enabled"]
            self.frontend(rom, *arguments, **({"content": self.adf} if rom == "floppy" else {}))
            picture = tp.read_picture(directory / f"picture-{run:03d}.bin")
            boxes[(rom, run)] = pixel(picture, 8 + 34 + 4 + 8, picture[1] - BAR + 4 + 7)
        self.assertEqual(boxes[("boot", 12)], LED_OFF)                                    # no disk, no motor
        self.assertEqual(boxes[("floppy", first + 5)], LED_ON)
        self.assertEqual(boxes[("floppy", first + 20)], LED_ON)
        self.assertEqual(boxes[("floppy", 2)], LED_ON if first <= 2 else LED_OFF)       # and the bar says what the interface says: lit from the run it was set in

    # --- what the machine sees: nothing --------------------------------------------------------------------------------------------------------

    def machine(self, *options, flags=(), runs=14):
        arguments = ["--runs", runs, "--do", "10:save:a", *flags]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend("ui", *arguments).stdout
        found = self.runs(out)
        return ([(r["unit"], r["ticks"], r["audio"], r["ahash"]) for r in found], STATE_NATIVE.search(out)[2], re.search(r"(?m)^cycle \d+ end .* audio_hash=(\w+)$", out)[1],
                re.search(r"(?m)^state size=(\d+)$", out)[1])

    def test_no_statusbar_option_changes_the_units_the_audio_or_the_state_of_the_machine(self):
        control = self.machine()
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"]
        for options in (("puae6_statusbar_startup=enabled",), ("puae6_statusbar_startup=enabled", "puae6_statusbar=top_minimal"),
                        ("puae6_statusbar_startup=enabled", "puae6_statusbar=bottom_basic", "puae6_video_crop=auto"), ("puae6_statusbar_messages=enabled",)):
            with self.subTest(options=options):
                shown = self.machine(*options, "puae6_hotkey_turbo_toggle=f11", flags=keys)
                self.assertEqual(shown[:3], control[:3])
        self.assertEqual(self.machine("puae6_statusbar_startup=enabled", flags=["--no-dupe"])[3], self.machine(flags=["--no-dupe"])[3])      # the size of a state is the same

    def test_rewind_and_runahead_show_the_same_bar_because_the_flag_and_the_message_are_in_the_state(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0", "--key-at", f"9:{RETROK['F12']}:1", "--key-at", f"10:{RETROK['F12']}:0"]
        options = ["--set", "puae6_hotkey_turbo_toggle=f11", "--set", "puae6_statusbar_startup=enabled", "--set", "puae6_statusbar_messages=enabled"]
        control = self.frontend("ui", "--runs", 24, *keys, *options, "--do", "24:save:z").stdout
        view = lambda out: [(r["unit"], r["hash"], r["px"]) for r in self.runs(out)]
        for ahead in (("--ahead-every", "1"), ("--ahead-every", "3"), ("--ahead", "6:5")):
            with self.subTest(ahead=ahead):
                out = self.frontend("ui", "--runs", 24, *keys, *options, *ahead, "--do", "24:save:z").stdout
                self.assertEqual(view(out), view(control))                               # the pictures of the visible runs, bar and message included
                self.assertEqual(STATE_NATIVE.search(out)[2], STATE_NATIVE.search(control)[2])
        # A load brings back the bar as it was: the unit seen twice shows the same picture both times.
        out = self.frontend("ui", "--runs", 24, *keys, *options, "--do", "7:save:a", "--do", "16:load:a").stdout
        seen = {}
        for r in self.runs(out):
            seen.setdefault(r["unit"], set()).add(r["hash"])
        self.assertTrue(all(len(hashes) == 1 for unit, hashes in seen.items() if unit <= 16), seen)
        self.assertTrue(any(unit > 8 for unit in seen))


if __name__ == "__main__":
    unittest.main()
