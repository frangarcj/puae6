"""T31a (IN01, LR01, DET05): the virtual keyboard is input like any other, and its drawing is not the machine's.

adapters/libretro/vkbd.h draws the Amiga keyboard over a copy of the picture and mapper.h runs its step: the keys a person presses with the RetroPad are bits of
the p6_input key bitmap, the same ones the host keyboard sets. The tests answer three questions:
  * what does the machine get? The packet of each run (the core's debug log, ports=... keys=...): the key under the cursor while B is down, latched modifiers, Caps
    Lock, Space on X, nothing for the joystick while the keyboard is open, and the physical keyboard unaffected. The layout is read from vkbd.h and the cursor moves
    are written again here from the option text, so the C++ is checked against a second statement of the rules;
  * what does the frontend see? The pictures are dumped: with the keyboard closed the picture is the native raster, untouched; open, the area around the keyboard is the
    native raster darkened by the Dimming option (computed here) and the machine's own part of the state, the units and the audio are those of a run without it;
  * what survives a state? Everything the keyboard remembers is in the adapter section: a load, a run-ahead branch and a state file bring back the same packets.
The C++ has its own checks in tests/libretro-ui/vkbd_probe.cpp. Run: python3 -m unittest discover -s tests/libretro-ui -p "test_vkbd.py"
"""
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
import test_input as ti  # noqa: E402
import test_libretro as lr  # noqa: E402
import test_present as tp  # noqa: E402

from tests.scenarios.a500 import display  # noqa: E402

ROOT = lr.ROOT
PAD = {"B": 1 << 0, "Y": 1 << 1, "SELECT": 1 << 2, "START": 1 << 3, "UP": 1 << 4, "DOWN": 1 << 5, "LEFT": 1 << 6, "RIGHT": 1 << 7, "A": 1 << 8, "X": 1 << 9}
BUTTON_1 = 1
STATE_NATIVE = re.compile(r"(?m)^state native (\S+) hash=(\w+)$")
RETROK = ti.RETROK
KEYS = [(m[1], int(m[2], 16), int(m[3]), int(m[4]), int(m[5])) for m in re.finditer(
    r'VK\("((?:[^"\\]|\\.)*)", (0x[0-9A-Fa-f]+), (\d), (\d+), (\d+)\)', (ROOT / "adapters/libretro/vkbd.h").read_text(encoding="utf-8"))]
KEYS = [(label, code, row, x, width) for label, code, row, x, width in KEYS]
CTRL_SELECT = PAD["SELECT"]


def key_bit(code):
    return 1 << code


def index_of(code):
    return next(i for i, k in enumerate(KEYS) if k[1] == code)


def move(cursor, direction):
    """The cursor after one press of UP/DOWN/LEFT/RIGHT, from the rules of the header: left and right walk the row and wrap; up and down go to the neighbouring row (wrapping)
    and land on the key whose middle is nearest to the middle of the key left (the first of them in layout order)."""
    _, _, row, x, width = KEYS[cursor]
    if direction in ("LEFT", "RIGHT"):
        step = 1 if direction == "RIGHT" else -1
        same = [(i, k) for i, k in enumerate(KEYS) if k[2] == row and i != cursor]
        ahead = [(i, (k[3] - x) * step) for i, k in same if (k[3] - x) * step > 0]
        if ahead:
            return min(ahead, key=lambda item: item[1])[0]
        return min(((i, (k[3] - x) * step) for i, k in same), key=lambda item: item[1])[0] if same else cursor
    target = (row + (1 if direction == "DOWN" else -1)) % 6
    middle = 2 * x + width
    return min(((i, abs(2 * k[3] + k[4] - middle)) for i, k in enumerate(KEYS) if k[2] == target), key=lambda item: item[1])[0]


def dim(channel, percent):
    """A channel after the dimming blend (black over it), as overlay.h computes it."""
    alpha = percent * 255 // 100
    return (channel * (255 - alpha) + 127) // 255


def dimmed(pixels, percent):
    """XRGB8888 bytes (little endian) with every color channel dimmed."""
    table = bytes(dim(i, percent) for i in range(256))
    return pixels.translate(table)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class VkbdProbeTests(unittest.TestCase):
    """The C++ of vkbd.h and its step on synthetic controls and pictures, compiled with ASan and UBSan next to no engine."""

    def test_the_virtual_keyboard_holds_its_own_checks(self):
        with tempfile.TemporaryDirectory(prefix="p6-vkbd-") as directory:
            exe = Path(directory) / "vkbd_probe"
            command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                       "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "tests/libretro-ui/vkbd_probe.cpp"), "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("vkbd probe ok", result.stdout)

    def test_the_layout_read_here_is_the_keyboard_of_the_wrapper(self):
        wrapper = ti.bizhawk_keys()
        self.assertEqual({k[1] for k in KEYS}, set(wrapper))                                # every key of the Amiga, Caps Lock included, once
        self.assertEqual(len(KEYS), len(wrapper))


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class VkbdTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        screen = display.Display("ui", mode="hires", bg=0x000, fg=0x0F0, bars=((60, 0x00F), (120, 0x0F0), (180, 0xF00), (240, 0x880)))
        rom = Path(cls.tmp.name) / "ui.rom"
        rom.write_bytes(lr.builder.build(display.rom_source(screen)))
        cls.roms["ui"] = rom
        cls.native = {}

    def native_raster(self, units=12):
        if units not in self.native:
            directory = self.work()
            self.headless_text("ui", units, "--dump", str(directory), "--dump-from", str(units))
            self.native[units] = tp.read_frame(directory / f"frame-{units:03d}.bin")
        return self.native[units]

    def packets(self, *arguments, runs=20, options=(), rom="boot"):
        flags = []
        for option in options:
            flags += ["--set", option]
        out = self.frontend(rom, "--runs", runs, *flags, *arguments).stdout
        return out, ti.packets(out)

    @staticmethod
    def pad(position, *names):
        mask = 0
        for name in names:
            mask |= PAD[name]
        return ["--joy-at", f"{position}:0:{mask:x}"]

    def shown(self, *options, runs=12, flags=()):
        directory = self.work()
        arguments = ["--runs", runs, "--dump-pictures", directory, "--dump-from", runs, *flags]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend("ui", *arguments).stdout
        return out, {int(re.search(r"picture-(\d+)", p.name)[1]): tp.read_picture(p) for p in directory.glob("picture-*.bin")}

    # --- the keys the pad presses ---------------------------------------------------------------------------------------------------------------

    @staticmethod
    def path(start, target):
        """The shortest list of D-Pad presses that takes the cursor from `start` to `target` by the rules of move()."""
        seen, queue = {start: []}, [start]
        for cursor in queue:
            for direction in ("RIGHT", "DOWN", "LEFT", "UP"):
                nxt = move(cursor, direction)
                if nxt not in seen:
                    seen[nxt] = seen[cursor] + [direction]
                    queue.append(nxt)
        return seen[target]

    def keyboard_script(self, steps):
        """(arguments, runs) for a session that opens the keyboard in run 2 and then does `steps`: ("go", code) walks the cursor to a key (one press per run pair),
        ("press", code, hold) walks to it and holds B for `hold` runs, ("pad", names..., hold) holds those buttons, ("wait", runs). Returns also the first run of each press."""
        arguments = self.pad(2, "SELECT") + self.pad(3)
        position, cursor, marks = 4, 0, []
        for step in steps:
            if step[0] in ("go", "press"):
                for direction in self.path(cursor, index_of(step[1])):
                    arguments += self.pad(position, direction) + self.pad(position + 1)
                    position += 2
                cursor = index_of(step[1])
            if step[0] == "press":
                arguments += self.pad(position, "B") + self.pad(position + step[2])
                marks.append(position)
                position += step[2] + 1
            elif step[0] == "pad":
                *names, hold = step[1:]
                arguments += self.pad(position, *names) + self.pad(position + hold)
                marks.append(position)
                position += hold + 1
            elif step[0] == "wait":
                position += step[1]
        return arguments, position, marks

    def test_select_opens_the_keyboard_and_b_presses_the_key_under_the_cursor_for_as_long_as_it_is_down(self):
        script = self.pad(2, "SELECT") + self.pad(3) + self.pad(4, "B") + self.pad(7)
        _, found = self.packets(*script, runs=9)
        first = KEYS[0][1]                                                                   # the cursor starts on the first key, ESC
        self.assertEqual(first, 0x45)
        self.assertEqual([p[8] for p in found], [0, 0, 0, key_bit(first), key_bit(first), key_bit(first), 0, 0, 0])
        # While it is open the pad is not the joystick: B was the fire button before and is again after it closes.
        self.assertTrue(all(p[5] == 0 for p in found[2:]))
        _, closed = self.packets(*self.pad(2, "SELECT"), *self.pad(3), *self.pad(4, "SELECT"), *self.pad(5), *self.pad(6, "B"), runs=8)
        self.assertEqual([p[5] for p in closed], [0] * 5 + [BUTTON_1] * 3)
        self.assertEqual([p[8] for p in closed], [0] * 8)

    def test_the_cursor_walks_the_layout_as_the_rules_say(self):
        moves = ["RIGHT", "RIGHT", "DOWN", "RIGHT", "DOWN", "DOWN", "LEFT", "UP", "UP", "UP", "UP", "LEFT", "LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "RIGHT", "UP", "UP", "LEFT"]
        cursor, expected, script, position = 0, [], self.pad(2, "SELECT") + self.pad(3), 4
        for direction in moves:
            cursor = move(cursor, direction)
            code = KEYS[cursor][1]
            script += self.pad(position, direction) + self.pad(position + 1)
            if code != 0x62 and code not in (0x60, 0x61, 0x63, 0x64, 0x65, 0x66, 0x67):         # a modifier or Caps Lock latches: it would stay in the later packets
                script += self.pad(position + 2, "B") + self.pad(position + 3)
                expected.append(key_bit(code))
            else:
                expected.append(0)
            position += 4
        _, found = self.packets(*script, runs=position)
        self.assertEqual([p[8] for p in found[5::4]], expected)                              # B is down in the third run of each group of four: the key the walk lands on
        self.assertGreater(sum(1 for key in expected if key), len(moves) // 2)

    def test_holding_a_direction_repeats_after_a_delay_and_then_steadily(self):
        # The press moves once; the repeat count then runs 2, 3, ... and moves when it is above 16 and a multiple of 4 past it (from the 20th unit on, every 4th).
        def moved(units):
            return 1 + sum(1 for repeat in range(2, units + 1) if repeat > 16 and (repeat - 16) % 4 == 0)
        for units in (1, 10, 16, 20, 24, 30):
            arguments = self.pad(2, "SELECT") + self.pad(3) + self.pad(4, "RIGHT") + self.pad(4 + units, "RIGHT", "B") + self.pad(6 + units)
            _, found = self.packets(*arguments, runs=6 + units)
            cursor = 0
            for _ in range(moved(units + 1)):                                              # B comes down in the unit after the `units`-th, when the cursor has moved again
                cursor = move(cursor, "RIGHT")
            pressed = [p[8] for p in found if p[8]]
            self.assertTrue(pressed, units)
            self.assertEqual(pressed[0], key_bit(KEYS[cursor][1]), f"after {units} units")

    def test_modifiers_latch_caps_lock_and_space_and_the_physical_keyboard_goes_on_working(self):
        arguments, runs, marks = self.keyboard_script([("press", 0x60, 1), ("press", 0x20, 1), ("press", 0x62, 1), ("pad", "Y", 1), ("pad", "X", 1),
                                                       ("press", 0x60, 1), ("press", 0x31, 1), ("wait", 2)])
        shift, a, caps, y, x, shift_off, z, _ = marks + [0]
        _, found = self.packets(*arguments, "--key-at", f"{runs - 1}:{RETROK['q']}:1", runs=runs)
        keys = {n + 1: p[8] for n, p in enumerate(found)}
        self.assertTrue(keys[shift + 1] & key_bit(0x60))                                      # Shift latched by the press, down after B was let go
        self.assertTrue(keys[a] & key_bit(0x20) and keys[a] & key_bit(0x60))                  # and still down for the A
        self.assertTrue(keys[caps + 1] & key_bit(0x62) and keys[y + 1] & key_bit(0x62) == 0 and keys[x] & key_bit(0x40))      # Caps Lock on, then Y flipped it, X is Space
        self.assertTrue(keys[z] & key_bit(0x31) and not keys[z] & key_bit(0x60))              # the second Shift press let go of Shift before Z
        self.assertTrue(keys[runs - 1] & key_bit(0x10))                                       # a physical Q reaches the Amiga while the keyboard is open
        self.assertTrue(all(p[5] == 0 for p in found[2:]))                                    # and the joystick gets nothing

    # --- closing, hotkeys and the mapper -----------------------------------------------------------------------------------------------------

    def test_closing_the_keyboard_lets_go_of_the_held_key_and_the_latched_modifiers(self):
        arguments, runs, marks = self.keyboard_script([("press", 0x60, 1), ("pad", "SELECT", 1), ("wait", 3)])
        _, found = self.packets(*arguments, runs=runs)
        keys = {n + 1: p[8] for n, p in enumerate(found)}
        self.assertTrue(keys[marks[0] + 1] & key_bit(0x60))                                           # latched ...
        self.assertEqual([keys[n] for n in range(marks[1], runs + 1)], [0] * (runs + 1 - marks[1]))   # ... and gone with the keyboard

    def test_a_hotkey_opens_it_and_select_can_be_remapped_or_unmapped(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"]
        _, hot = self.packets(*keys, *self.pad(6, "B"), *self.pad(7), runs=8, options=("puae6_hotkey_vkbd=f11",))
        self.assertEqual([p[8] != 0 for p in hot], [False] * 5 + [True, False, False])
        _, off = self.packets(*self.pad(2, "SELECT"), *self.pad(3), *self.pad(4, "B"), runs=6, options=("puae6_mapper_select=none",))
        self.assertEqual({p[8] for p in off}, {0})                                                    # Select does nothing, B stays the fire button
        self.assertEqual([p[5] for p in off][3:], [BUTTON_1] * 3)
        _, other = self.packets(*self.pad(2, "START"), *self.pad(3), *self.pad(4, "B"), runs=6, options=("puae6_mapper_select=none", "puae6_mapper_start=toggle_vkbd"))
        self.assertTrue(other[3][8] != 0)

    # --- what the frontend sees ---------------------------------------------------------------------------------------------------------------

    def test_closed_there_is_nothing_drawn_and_open_the_keyboard_is_over_a_dimmed_picture(self):
        native = self.native_raster()
        _, closed = self.shown()
        self.assertEqual(closed[12][3], native[2])
        flags = ["--joy-at", f"3:0:{PAD['SELECT']:x}", "--joy-at", "4:0:0"]
        _, opened = self.shown("puae6_vkbd_dimming=50%", flags=flags)
        width, height, _, pixels = opened[12]
        half = (756 - 8) // 46
        self.assertEqual(half, 16)
        kb_w, kb_h = half * 46, half * 2 * 6
        kb_x, kb_y = (756 - kb_w) // 2, 574 - kb_h - 4
        around = lambda pixels, x0, y0, x1, y1: b"".join(pixels[(y * 756 + x0) * 4:(y * 756 + x1) * 4] for y in range(y0, y1))
        # The rows above the keyboard and the margins at its sides are the native raster dimmed by 50%; the rows below are too.
        raster = native[2]
        self.assertEqual(around(pixels, 0, 0, 756, kb_y), dimmed(around(raster, 0, 0, 756, kb_y), 50))
        self.assertEqual(around(pixels, 0, kb_y, kb_x, kb_y + kb_h), dimmed(around(raster, 0, kb_y, kb_x, kb_y + kb_h), 50))
        self.assertEqual(around(pixels, kb_x + kb_w, kb_y, 756, kb_y + kb_h), dimmed(around(raster, kb_x + kb_w, kb_y, 756, kb_y + kb_h), 50))
        self.assertEqual(around(pixels, 0, kb_y + kb_h, 756, 574), dimmed(around(raster, 0, kb_y + kb_h, 756, 574), 50))
        self.assertNotEqual(around(pixels, kb_x, kb_y, kb_x + kb_w, kb_y + kb_h), around(raster, kb_x, kb_y, kb_x + kb_w, kb_y + kb_h))
        self.assertEqual((width, height), (756, 574))
        _, undimmed = self.shown("puae6_vkbd_dimming=0%", flags=flags)
        self.assertEqual(around(undimmed[12][3], 0, 0, 756, kb_y), around(native[2], 0, 0, 756, kb_y))                     # no dimming: the picture is untouched around it

    def test_the_theme_the_transparency_and_a_change_of_it_with_a_are_what_the_options_say(self):
        flags = ["--joy-at", f"3:0:{PAD['SELECT']:x}", "--joy-at", "4:0:0"]
        half = 16
        kb_x, kb_y = (756 - half * 46) // 2, 574 - half * 12 - 4
        k = KEYS[index_of(0x22)]                                                                    # D, in the middle of the home row
        probe = (kb_x + k[3] * half + 4, kb_y + k[2] * half * 2 + half * 2 - 5)
        face_of = lambda picture: int.from_bytes(picture[12][3][(probe[1] * 756 + probe[0]) * 4:(probe[1] * 756 + probe[0]) * 4 + 4], "little")
        palettes = {"beige": 0xD6CDB0, "cd32": 0x30386A, "light": 0xE4E4E4, "dark": 0x383838}
        for theme, face in palettes.items():
            with self.subTest(theme=theme):
                _, picture = self.shown(f"puae6_vkbd_theme={theme}", "puae6_vkbd_transparency=0%", flags=flags)
                self.assertEqual(face_of(picture), face)
        _, auto = self.shown("puae6_vkbd_theme=auto", "puae6_vkbd_transparency=0%", flags=flags)
        _, beige = self.shown("puae6_vkbd_theme=beige", "puae6_vkbd_transparency=0%", flags=flags)
        self.assertEqual(auto[12][3], beige[12][3])                                                   # the A500 is beige
        _, outline = self.shown("puae6_vkbd_theme=beige_outline", "puae6_vkbd_transparency=0%", flags=flags)
        self.assertNotEqual(outline[12][3], beige[12][3])                                             # the labels have an outline instead of a shadow
        # How see-through the keys are, and RetroPad A: it switches the transparency off, so the face is solid again.
        both = flags + ["--joy-at", f"6:0:{PAD['A']:x}", "--joy-at", "7:0:0"]
        _, see_through = self.shown("puae6_vkbd_transparency=75%", flags=flags)
        _, solid = self.shown("puae6_vkbd_transparency=75%", flags=both)
        self.assertEqual(face_of(solid), 0xD6CDB0)
        self.assertNotEqual(face_of(see_through), 0xD6CDB0)
        _, glass = self.shown("puae6_vkbd_transparency=100%", flags=flags)
        native = self.native_raster()[2]
        self.assertEqual(face_of(glass), int.from_bytes(native[(probe[1] * 756 + probe[0]) * 4:(probe[1] * 756 + probe[0]) * 4 + 4], "little"))      # all the way through: the picture itself

    def test_the_keyboard_sits_above_a_statusbar_at_the_bottom_and_leaves_the_machine_alone(self):
        flags = ["--joy-at", f"3:0:{PAD['SELECT']:x}", "--joy-at", "4:0:0"]
        _, bar_only = self.shown("puae6_statusbar_startup=enabled", "puae6_vkbd_dimming=0%", runs=12)
        _, both = self.shown("puae6_statusbar_startup=enabled", "puae6_vkbd_dimming=0%", runs=12, flags=flags)
        rows = lambda picture, a, b: picture[3][a * 756 * 4:b * 756 * 4]
        self.assertEqual(rows(both[12], 574 - 22, 574), rows(bar_only[12], 574 - 22, 574))           # the keyboard is not drawn over the bar
        self.assertNotEqual(rows(both[12], 574 - 22 - 4 - 192, 574 - 22 - 4), rows(bar_only[12], 574 - 22 - 4 - 192, 574 - 22 - 4))

    def machine(self, *options, flags=(), runs=14):
        arguments = ["--runs", runs, "--do", "10:save:a", *flags]
        for option in options:
            arguments += ["--set", option]
        out = self.frontend("ui", *arguments).stdout
        found = self.runs(out)
        return [(r["unit"], r["ticks"], r["audio"], r["ahash"]) for r in found], STATE_NATIVE.search(out)[2], re.search(r"(?m)^cycle \d+ end .* audio_hash=(\w+)$", out)[1]

    def test_opening_the_keyboard_changes_nothing_of_the_machine_until_a_key_is_pressed(self):
        control = self.machine()
        flags = ["--joy-at", f"3:0:{PAD['SELECT']:x}", "--joy-at", "4:0:0", "--joy-at", "6:0:1", "--joy-at", "7:0:0"]       # open, wander, a joystick button while open
        self.assertEqual(self.machine(flags=flags[:4])[:3], control[:3])
        for options in (("puae6_vkbd_theme=dark_outline", "puae6_vkbd_dimming=100%", "puae6_vkbd_transparency=100%"), ("puae6_video_crop=medium", "puae6_statusbar_startup=enabled")):
            self.assertEqual(self.machine(*options, flags=flags[:4])[:3], control[:3])
        # And a key press does reach it: the same session with B pressed on the keyboard is a different machine (the keys are input).
        pressed = self.machine(flags=flags[:4] + ["--joy-at", "6:0:1", "--joy-at", "7:0:0"])
        self.assertNotEqual(pressed[1], control[1])

    # --- the memory travels with the state ---------------------------------------------------------------------------------------------------

    SCRIPT = (["--joy-at", f"2:0:{PAD['SELECT']:x}", "--joy-at", "3:0:0", "--joy-at", f"4:0:{PAD['DOWN']:x}", "--joy-at", "5:0:0", "--joy-at", f"6:0:{PAD['DOWN']:x}",
               "--joy-at", "7:0:0", "--joy-at", f"8:0:{PAD['B']:x}", "--joy-at", "9:0:0", "--joy-at", f"10:0:{PAD['RIGHT']:x}", "--joy-at", f"30:0:{PAD['B'] | PAD['RIGHT']:x}",
               "--joy-at", f"40:0:{PAD['A'] | PAD['Y']:x}", "--joy-at", "41:0:0"])

    def view(self, run):
        return (run["unit"], run["ticks"], run["audio"], run["ahash"], run["hash"], run["px"])

    def test_a_load_replays_the_same_packets_and_pictures_because_the_cursor_the_held_key_and_the_latches_are_in_the_state(self):
        out = self.frontend("ui", "--runs", 50, *self.SCRIPT, "--do", "12:save:a", "--do", "36:save:before", "--do", "36:load:a", "--do", "36:save:again").stdout
        seen = ti.by_unit(out)
        for unit in range(13, 27):                                                         # the load at 36 went back to unit 12 and 14 more runs followed
            self.assertEqual(len(seen[unit]), 2, f"unit {unit}")
            self.assertEqual(seen[unit][0], seen[unit][1], f"unit {unit}")
        pictures = {}
        for r in self.runs(out):
            pictures.setdefault(r["unit"], set()).add(r["hash"])
        self.assertTrue(all(len(h) == 1 for u, h in pictures.items() if u <= 36), pictures)           # and the keyboard is drawn the same both times
        saves = {m[1]: (m[5], m[6]) for m in ti.SAVE.finditer(out)}
        self.assertEqual(saves["again"], saves["a"])
        self.assertNotEqual(saves["before"][1], saves["a"][1])

    def test_runahead_and_a_state_file_converge_with_the_plain_run(self):
        control = self.frontend("ui", "--runs", 50, *self.SCRIPT, "--do", "50:save:z").stdout
        final = STATE_NATIVE.search(control)[2]
        for ahead in (("--ahead-every", "1"), ("--ahead-every", "5"), ("--ahead", "9:7")):
            with self.subTest(ahead=ahead):
                out = self.frontend("ui", "--runs", 50, *self.SCRIPT, *ahead, "--do", "50:save:z").stdout
                self.assertEqual([self.view(r) for r in self.runs(out)], [self.view(r) for r in self.runs(control)])
                self.assertEqual(STATE_NATIVE.search(out)[2], final)
                self.assertGreater(len(re.findall(r"(?m)^spec ", out)), 3)
        path = self.work() / "x.state"
        first = self.frontend("ui", "--runs", 50, *self.SCRIPT, "--do", f"12:savefile:{path}").stdout
        second = self.frontend("ui", "--runs", 38, *self.SCRIPT, "--do", f"0:loadfile:{path}:pos=12").stdout
        self.assertEqual([self.view(r) for r in self.runs(second)], [self.view(r) for r in self.runs(first)][12:50])


if __name__ == "__main__":
    unittest.main()
