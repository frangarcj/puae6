"""T31d (IN01, LR01, DET05): the RetroPad mapper, turbo fire and the mouse by stick or D-Pad are input like any other, and travel with the state.

adapters/libretro/mapper.h turns what the frontend reports in one retro_run (and a few bits of memory) into the p6_input packet of p6_run_frame. The minimal
frontend of tests/libretro-host scripts buttons, sticks and keys by position on the emulated timeline, and the core's debug log shows the packet each run handed
to the bridge (ports=... keys=...), so every test here reads the contract's own words: which joystick bits, which mouse counts and buttons, which Amiga keys.
  * the C++ of the mapper has its own checks on synthetic controls (tests/libretro-ui/mapper_probe.cpp: defaults, targets, edges, turbo, the stick arithmetic,
    the soft reset, and the input memory through the adapter section);
  * here the whole core: options in, packets out, and the memory through save states, a state file, rewind and run-ahead (which must reach the state, picture and
    packets of the plain run), with the arithmetic of the stick mouse and the speed of the mouse written again in Python from the option texts.
Nothing here can be a time: the turbo is a count of emulated units, and a static scan of the adapter sources says the adapter reads no clock and no entropy.
Run: python3 -m unittest discover -s tests/libretro-ui -p "test_mapper.py"
"""
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parents[1] / "libretro-host"
sys.path.insert(0, str(HOST))
import test_input as ti  # noqa: E402
import test_libretro as lr  # noqa: E402

ROOT = lr.ROOT
RETROK = ti.RETROK
BUTTON_1, BUTTON_2, BUTTON_3, BUTTON_UP, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT = 1, 2, 4, 8, 16, 32, 64
MOUSE, JOYSTICK = 2, 1
# The bits of a RetroPad mask (RETRO_DEVICE_ID_JOYPAD_*).
PAD = {"B": 1 << 0, "Y": 1 << 1, "SELECT": 1 << 2, "START": 1 << 3, "UP": 1 << 4, "DOWN": 1 << 5, "LEFT": 1 << 6, "RIGHT": 1 << 7, "A": 1 << 8, "X": 1 << 9,
       "L": 1 << 10, "R": 1 << 11, "L2": 1 << 12, "R2": 1 << 13, "L3": 1 << 14, "R3": 1 << 15}
CTRL, LEFT_AMIGA, RIGHT_AMIGA = 0x63, 0x66, 0x67
STATE_NATIVE = re.compile(r"(?m)^state native (\S+) hash=(\w+)$")


def key_bit(code):
    return 1 << code


def stick_milli(axis, x, y, deadzone_percent, speed_tenths):
    """Thousandths of a count one axis of a stick moves the mouse in a unit (the option text: the whole stick past the deadzone, 10 counts at the edge at 100%)."""
    deadzone = deadzone_percent * 32768 // 100
    magnitude = math.isqrt(x * x + y * y)
    if magnitude <= deadzone or axis == 0:
        return 0
    past = min(magnitude, 32768) - deadzone
    product = axis * past * 10 * 100 * speed_tenths
    divisor = (32768 - deadzone) * magnitude
    return int(product / divisor) if product >= 0 else -int(-product / divisor)       # C++ integer division truncates toward zero


def carried(milli_per_unit):
    """Whole counts per unit for a list of thousandths, carrying what does not make a count; a unit of 0 forgets the carry."""
    carry, counts = 0, []
    for milli in milli_per_unit:
        if milli == 0:
            carry = 0
            counts.append(0)
            continue
        total = carry + milli
        whole = int(total / 1000)
        carry = total - whole * 1000
        counts.append(whole)
    return counts


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MapperProbeTests(unittest.TestCase):
    """The C++ of mapper.h and the input memory of state_frame.h on synthetic controls, compiled with ASan and UBSan next to no engine."""

    def test_the_mapper_holds_its_own_checks(self):
        with tempfile.TemporaryDirectory(prefix="p6-mapper-") as directory:
            exe = Path(directory) / "mapper_probe"
            command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                       "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "tests/libretro-ui/mapper_probe.cpp"), "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mapper probe ok", result.stdout)

    def test_the_schemas_mapper_targets_are_the_keys_of_the_wrapper_and_the_hotkeys_are_libretro_key_codes(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import generate_options
        schema = generate_options.load_schema()
        targets = {v["value"]: v["abi"] for v in schema["value_sets"]["mapper_target"]}
        wrapper = ti.bizhawk_keys()
        keys = {abi - 0x100: name for name, abi in targets.items() if abi >= 0x100}
        self.assertEqual(set(keys), set(wrapper) - {0x62})                          # every key of the Amiga but the Caps Lock, which latches
        for code, name in keys.items():
            self.assertEqual(name, "key_" + re.sub(r"[^a-z0-9]+", "_", wrapper[code].lower()).strip("_"))
        retro = {m[1]: int(m[2]) for m in re.finditer(r"^\s+RETROK_(\w+)\s*=\s*(\d+),", (ROOT / "references/libretro-api/include/libretro.h").read_text(), re.M)}
        for entry in schema["value_sets"]["hotkey_key"]:
            if entry["value"] != "---":
                self.assertEqual(entry["abi"], retro[{"f11": "F11", "f12": "F12", "pause": "PAUSE", "print": "PRINT", "scrolllock": "SCROLLOCK", "numlock": "NUMLOCK",
                                                      "menu": "MENU", "rctrl": "RCTRL", "rshift": "RSHIFT"}[entry["value"]]], entry["value"])
        self.assertEqual(targets["---"], 0)

    def test_the_adapter_reads_no_clock_and_no_entropy(self):
        banned = re.compile(r"\b(chrono|clock_gettime|gettimeofday|time\s*\(|clock\s*\(|usleep|nanosleep|sleep\s*\(|rand\s*\(|srand|random_device|mt19937|"
                            r"get_time_usec|std::thread|pthread|getenv)\b")
        for path in sorted((ROOT / "adapters/libretro").glob("*")):
            if path.suffix not in (".h", ".cpp") or path.name == "options_generated.h":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("//")[0]
                self.assertIsNone(banned.search(code), f"{path.name}:{number}: {line.strip()}")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MapperTests(lr.LibretroHarness):
    def packets(self, *arguments, runs=10, rom="boot", options=()):
        flags = []
        for option in options:
            flags += ["--set", option]
        out = self.frontend(rom, "--runs", runs, *flags, *arguments).stdout
        return out, ti.packets(out)

    @staticmethod
    def pad_at(position, *names, port=0):
        mask = 0
        for name in names:
            mask |= PAD[name]
        return ["--joy-at", f"{position}:{port}:{mask:x}"]

    # --- the default functions and the mapping ---------------------------------------------------------------------------------------------------

    def test_the_default_functions_are_the_joystick_the_pad_always_was(self):
        arguments = self.pad_at(2, "B") + self.pad_at(3, "A") + self.pad_at(4, "X") + self.pad_at(5, "UP", "LEFT") + self.pad_at(6, "DOWN", "RIGHT") + \
            self.pad_at(7, "Y", "SELECT", "START", "L", "R", "L2", "R2", "L3", "R3") + self.pad_at(8)
        _, found = self.packets(*arguments, runs=8)
        self.assertEqual([p[5] for p in found], [0, BUTTON_1, BUTTON_2, BUTTON_3, BUTTON_UP | BUTTON_LEFT, BUTTON_DOWN | BUTTON_RIGHT, 0, 0])
        self.assertTrue(all(p[8] == 0 and p[:4] == (MOUSE, 0, 0, 0) for p in found))

    def test_a_button_can_be_mapped_to_a_joystick_function_a_mouse_function_a_key_or_nothing(self):
        cases = [
            ("puae6_mapper_x=key_space", ("X",), lambda p: p[5] == 0 and p[8] == key_bit(0x40)),
            ("puae6_mapper_y=joy_fire3", ("Y",), lambda p: p[5] == BUTTON_3),
            ("puae6_mapper_b=none", ("B",), lambda p: True),
            ("puae6_mapper_l=mouse_left", ("L",), lambda p: p[1] == BUTTON_1 and p[5] == 0),
            ("puae6_mapper_r=mouse_middle", ("R",), lambda p: p[1] == BUTTON_3),
            ("puae6_mapper_l2=joy_up", ("L2",), lambda p: p[5] == BUTTON_UP),
            ("puae6_mapper_up=key_a", ("UP",), lambda p: p[5] == 0 and p[8] == key_bit(0x20)),
            ("puae6_mapper_a=joy_fire", ("A",), lambda p: p[5] == BUTTON_1),
            ("puae6_mapper_start=mouse_move_right", ("START",), lambda p: p[2] == 6 and p[3] == 0),
            ("puae6_mapper_select=mouse_move_up", ("SELECT",), lambda p: p[3] == -6),
            ("puae6_mapper_r2=key_f10", ("R2",), lambda p: p[8] == key_bit(0x59)),
            ("puae6_mapper_l3=key_help", ("L3",), lambda p: p[8] == key_bit(0x5F)),
            ("puae6_mapper_r3=key_keypad_enter", ("R3",), lambda p: p[8] == key_bit(0x43)),
        ]
        for option, buttons, expected in cases:
            with self.subTest(option=option):
                _, found = self.packets(*self.pad_at(2, *buttons), runs=3, options=(option,))
                if option.endswith("=none"):          # silenced: nothing at all comes of the press, which does give BUTTON_1 by default
                    self.assertEqual({p[5] for p in found} | {p[1] for p in found} | {p[8] for p in found}, {0}, found)
                    _, default = self.packets(*self.pad_at(2, *buttons), runs=3)
                    self.assertEqual(default[1][5], BUTTON_1)
                else:
                    self.assertEqual([expected(p) for p in found], [False, True, True], found)
                self.assertTrue(all(p[4] == JOYSTICK for p in found))

    def test_a_stick_direction_has_its_own_mapping_and_the_rest_of_the_stick_keeps_the_joystick(self):
        _, found = self.packets("--analog", "0:-30000,-30000", runs=3, options=("puae6_mapper_ll=key_left",))
        self.assertEqual({(p[5], p[8]) for p in found}, {(BUTTON_UP, key_bit(0x4F))})             # left became a key, up is still the joystick's
        _, right = self.packets("--analog-right", "0:30000,0", runs=3, options=("puae6_mapper_rr=joy_fire",))
        self.assertEqual({p[5] for p in right}, {BUTTON_1})
        _, nothing = self.packets("--analog-right", "0:30000,0", runs=3)                            # the right stick did nothing, and still does
        self.assertEqual({p[5] for p in nothing}, {0})

    def test_the_face_button_options_deal_the_fire_buttons_again(self):
        table = {"disabled": (BUTTON_1, BUTTON_2, 0), "jump": (BUTTON_1, BUTTON_UP, 0), "rotate": (BUTTON_2, 0, BUTTON_1), "rotate_jump": (BUTTON_UP, 0, BUTTON_1)}
        for value, (b, a, y) in table.items():
            with self.subTest(face_buttons=value):
                arguments = self.pad_at(1, "B") + self.pad_at(2, "A") + self.pad_at(3, "Y") + self.pad_at(4, "X")
                _, found = self.packets(*arguments, runs=4, options=(f"puae6_face_buttons={value}",))
                self.assertEqual([p[5] for p in found], [b, a, y, BUTTON_3])

    def test_the_soft_reset_is_the_ctrl_amiga_amiga_chord_for_a_fixed_number_of_units(self):
        f12 = RETROK["F12"]
        _, found = self.packets("--key-at", f"3:{f12}:1", "--key-at", f"4:{f12}:0", "--key-at", f"20:{f12}:1", "--key-at", f"21:{f12}:0", runs=100,
                                options=("puae6_hotkey_reset=f12",))
        chord = key_bit(CTRL) | key_bit(LEFT_AMIGA) | key_bit(RIGHT_AMIGA)
        held = [i + 1 for i, p in enumerate(found) if p[8]]
        self.assertTrue(all(found[i - 1][8] == chord for i in held))
        self.assertEqual(held, list(range(3, 43)))                      # pressed in run 3: exactly 40 units; the press at run 20 meant nothing while it held
        _, again = self.packets("--key-at", f"3:{f12}:1", "--key-at", f"4:{f12}:0", "--key-at", f"50:{f12}:1", "--key-at", f"51:{f12}:0", runs=100,
                                options=("puae6_hotkey_reset=f12",))
        self.assertEqual([i + 1 for i, p in enumerate(again) if p[8]], list(range(3, 43)) + list(range(50, 90)))

    # --- hotkeys: a key that runs an action and is not sent to the Amiga ---------------------------------------------------------------------------

    def test_a_hotkey_is_not_sent_to_the_amiga_and_an_unmapped_key_is(self):
        rctrl = RETROK["RCTRL"]
        _, plain = self.packets("--key-at", f"2:{rctrl}:1", runs=3)
        self.assertEqual([p[8] for p in plain], [0, key_bit(CTRL), key_bit(CTRL)])
        _, hotkey = self.packets("--key-at", f"2:{rctrl}:1", runs=3, options=("puae6_hotkey_mouse_toggle=rctrl",))
        self.assertEqual([p[8] for p in hotkey], [0, 0, 0])
        _, left = self.packets("--key-at", f"2:{RETROK['LCTRL']}:1", runs=3, options=("puae6_hotkey_mouse_toggle=rctrl",))
        self.assertEqual([p[8] for p in left], [0, key_bit(CTRL), key_bit(CTRL)])             # the left Ctrl is still a Ctrl

    # --- the mouse by D-Pad -----------------------------------------------------------------------------------------------------------------------

    def test_in_dpad_mouse_mode_the_pad_moves_the_mouse_and_the_joystick_gets_nothing_from_it(self):
        arguments = self.pad_at(2, "RIGHT", "DOWN", "B") + self.pad_at(3, "LEFT", "A") + self.pad_at(4, "UP", "X") + self.pad_at(5)
        _, found = self.packets(*arguments, runs=5, options=("puae6_joyport=mouse",))
        self.assertEqual([p[:4] for p in found], [(MOUSE, 0, 0, 0), (MOUSE, BUTTON_1, 6, 6), (MOUSE, BUTTON_2, -6, 0), (MOUSE, BUTTON_3, 0, -6), (MOUSE, 0, 0, 0)])
        self.assertTrue(all(p[4:8] == (JOYSTICK, 0, 0, 0) for p in found))
        _, fast = self.packets(*self.pad_at(2, "RIGHT"), runs=3, options=("puae6_joyport=mouse", "puae6_dpad_mouse_speed=18"))
        self.assertEqual([p[2] for p in fast], [0, 18, 18])
        _, with_mouse = self.packets(*self.pad_at(2, "RIGHT"), "--mouse-at", "2:5,-2,1", runs=3, options=("puae6_joyport=mouse",))
        self.assertEqual(with_mouse[1][:4], (MOUSE, BUTTON_1, 11, -2))                          # the frontend's mouse and the pad together
        _, only = self.packets(*self.pad_at(2, "RIGHT", "B"), "--device", "0:257", runs=3, options=("puae6_joyport=mouse",))
        self.assertEqual(only[1][4:8], (JOYSTICK, BUTTON_1 | BUTTON_RIGHT, 0, 0))               # Joystick only has no mouse port to give: the pad stays a joystick

    def test_a_hotkey_or_a_button_switches_the_pad_between_joystick_and_mouse_while_it_runs(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0", "--key-at", f"7:{f11}:1", "--key-at", f"8:{f11}:0"]
        _, found = self.packets(*keys, *self.pad_at(1, "RIGHT"), runs=9, options=("puae6_hotkey_mouse_toggle=f11",))
        self.assertEqual([(p[2], p[5]) for p in found], [(0, BUTTON_RIGHT)] * 2 + [(6, 0)] * 4 + [(0, BUTTON_RIGHT)] * 3)
        _, mapped = self.packets(*self.pad_at(1, "RIGHT"), "--joy-at", f"3:0:{PAD['RIGHT'] | PAD['SELECT']:x}", "--joy-at", f"4:0:{PAD['RIGHT']:x}", runs=6,
                                 options=("puae6_mapper_select=toggle_mouse",))
        self.assertEqual([(p[2], p[5]) for p in mapped], [(0, BUTTON_RIGHT)] * 2 + [(6, 0)] * 4)

    # --- turbo fire -------------------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def turbo_pattern(held_runs, pulse, runs):
        """BUTTON_1 per run for a button held in the runs of `held_runs`, which restarts its phase every time it is released."""
        phase, pattern = 0, []
        for n in range(1, runs + 1):
            if n in held_runs:
                pattern.append((phase % pulse) < pulse // 2)
                phase = (phase + 1) % 120
            else:
                pattern.append(False)
                phase = 0
        return pattern

    def test_turbo_fire_presses_and_releases_the_button_by_the_count_of_units(self):
        held = set(range(2, 27)) | set(range(30, 41))
        arguments = self.pad_at(2, "B") + self.pad_at(27) + self.pad_at(30, "B") + self.pad_at(41)
        for pulse in (2, 4, 6, 8, 10, 12):
            with self.subTest(pulse=pulse):
                _, found = self.packets(*arguments, runs=44, options=("puae6_turbo_fire=enabled", f"puae6_turbo_pulse={pulse}"))
                self.assertEqual([bool(p[5] & BUTTON_1) for p in found], self.turbo_pattern(held, pulse, 44))
        _, off = self.packets(*arguments, runs=44)
        self.assertEqual([bool(p[5] & BUTTON_1) for p in off], [n in held for n in range(1, 45)])      # off by default: the button is plain
        # Another button as the turbo one: B stays pressed, A auto-fires (and is what it is mapped to).
        _, other = self.packets(*self.pad_at(2, "B", "A"), runs=10, options=("puae6_turbo_fire=enabled", "puae6_turbo_fire_button=A", "puae6_turbo_pulse=4"))
        self.assertEqual([p[5] for p in other][1:], [BUTTON_1 | BUTTON_2, BUTTON_1 | BUTTON_2, BUTTON_1, BUTTON_1] * 2 + [BUTTON_1 | BUTTON_2])

    def test_the_turbo_hotkey_inverts_the_option(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"5:{f11}:1", "--key-at", f"6:{f11}:0"]
        _, found = self.packets(*keys, *self.pad_at(2, "B"), runs=14, options=("puae6_hotkey_turbo_toggle=f11", "puae6_turbo_pulse=4"))
        # Plain until the key is pressed in run 5, then the phase counts from the unit the button has been held (run 2 on): it is turbo from then on.
        self.assertEqual([bool(p[5] & BUTTON_1) for p in found], [False, True, True, True, True, True, False, False, True, True, False, False, True, True])
        _, on = self.packets(*keys, *self.pad_at(2, "B"), runs=10, options=("puae6_hotkey_turbo_toggle=f11", "puae6_turbo_fire=enabled", "puae6_turbo_pulse=4"))
        self.assertEqual([bool(p[5] & BUTTON_1) for p in on][:4], [False, True, True, False])           # on from the start, off after the key

    # --- the mouse by analog stick and the Mouse Speed ------------------------------------------------------------------------------------------------

    def test_the_analog_stick_moves_the_mouse_with_the_arithmetic_of_the_option_text(self):
        for stick, flag, mode in ((0, "--analog", "left"), (1, "--analog-right", "right")):
            for x, y, deadzone, speed in ((32767, 0, "20", "1.0"), (8000, 0, "20", "1.0"), (-9000, 5000, "10", "0.5"), (20000, -20000, "30", "3.0"),
                                          (5000, 0, "20", "1.0"), (12000, 12000, "0", "0.1")):
                with self.subTest(stick=mode, x=x, y=y, deadzone=deadzone, speed=speed):
                    options = (f"puae6_analog_mouse={mode}", f"puae6_analog_mouse_deadzone={deadzone}",
                               f"puae6_analog_mouse_speed_{'left' if stick == 0 else 'right'}={speed}")
                    _, found = self.packets(flag, f"0:{x},{y}", runs=24, options=options)
                    tenths = int(float(speed) * 10)
                    mx, my = stick_milli(x, x, y, int(deadzone), tenths), stick_milli(y, x, y, int(deadzone), tenths)
                    self.assertEqual([p[2] for p in found], carried([mx] * 24))
                    self.assertEqual([p[3] for p in found], carried([my] * 24))
                    self.assertTrue(all(p[:2] == (MOUSE, 0) and p[4] == JOYSTICK for p in found))

    def test_a_stick_that_moves_the_mouse_gives_no_joystick_directions_and_the_other_stick_is_unaffected(self):
        _, found = self.packets("--analog", "0:30000,-30000", "--analog-right", "0:-30000,0", runs=4, options=("puae6_analog_mouse=left",))
        self.assertEqual({p[5] for p in found}, {0})                                       # the left stick is the mouse's; the right stick never gave directions
        self.assertTrue(all(p[2] > 0 and p[3] < 0 for p in found[1:]))
        _, both = self.packets("--analog", "0:30000,0", "--analog-right", "0:0,30000", runs=4, options=("puae6_analog_mouse=both",))
        self.assertTrue(all(p[2] > 0 and p[3] > 0 for p in both[1:]))
        _, mapped = self.packets("--analog", "0:30000,0", runs=4, options=("puae6_analog_mouse=left", "puae6_mapper_lr=joy_fire"))
        self.assertEqual({(p[2], p[5]) for p in mapped}, {(0, BUTTON_1)})                    # a mapped direction wins: that stick is not a mouse
        _, joystick_only = self.packets("--analog", "0:30000,0", "--device", "0:257", runs=4, options=("puae6_analog_mouse=left",))
        self.assertEqual({(p[4], p[5]) for p in joystick_only}, {(JOYSTICK, BUTTON_RIGHT)})   # no mouse port: the stick stays a joystick direction

    def test_a_stick_at_rest_forgets_the_part_of_a_count_it_carried(self):
        steps = ["--stick-at", "1:0:0:8000,0", "--stick-at", "4:0:0:0,0", "--stick-at", "6:0:0:8000,0", "--stick-at", "7:0:0:0,0"]
        _, found = self.packets(*steps, runs=8, options=("puae6_analog_mouse=left",))
        milli = [8000, 8000, 8000, 0, 0, 8000, 0, 0]
        self.assertEqual([p[2] for p in found], carried([stick_milli(m, m, 0, 20, 10) for m in milli]))
        self.assertEqual(carried([551, 551, 551, 0, 0, 551, 0, 0]), [0, 1, 0, 0, 0, 0, 0, 0])      # the carry of 102 is dropped at rest: the run-6 unit starts over

    def test_the_mouse_speed_scales_the_frontends_mouse_and_keeps_the_fractions(self):
        _, half = self.packets("--mouse", "3,-3,0", runs=8, options=("puae6_mouse_speed=50",))
        self.assertEqual([p[2] for p in half], [1, 2, 1, 2, 1, 2, 1, 2])
        self.assertEqual([p[3] for p in half], [-1, -2, -1, -2, -1, -2, -1, -2])
        _, double = self.packets("--mouse", "4,0,3", runs=3, options=("puae6_mouse_speed=200",))
        self.assertEqual([p[:3] for p in double], [(MOUSE, 3, 8)] * 3)
        _, tenth = self.packets("--mouse", "7,0,0", runs=6, options=("puae6_mouse_speed=10",))
        self.assertEqual(sum(p[2] for p in tenth), 4)                                           # 6 * 7 * 10% = 4.2 counts: 4 delivered, 0.2 carried
        _, plain = self.packets("--mouse", "3,-3,0", runs=4, options=("puae6_mouse_speed=100",))
        self.assertEqual([p[2] for p in plain], [3] * 4)

    # --- the memory travels with the state ------------------------------------------------------------------------------------------------------

    OPTIONS = ("puae6_turbo_fire=enabled", "puae6_turbo_pulse=6", "puae6_analog_mouse=both", "puae6_analog_mouse_speed_left=0.3", "puae6_mouse_speed=70",
               "puae6_hotkey_mouse_toggle=f11", "puae6_hotkey_reset=f12", "puae6_mapper_start=key_space")
    SCRIPT = ["--joy-at", f"2:0:{PAD['B']:x}", "--joy-at", f"26:0:0", "--stick-at", "3:0:0:5000,-3000", "--stick-at", "5:0:1:-9000,100", "--stick-at", "16:0:0:0,0",
              "--stick-at", "17:0:0:7000,7000",
              "--mouse-at", "4:3,2,1", "--mouse-at", "5:3,2,1", "--mouse-at", "6:3,2,1", "--mouse-at", "7:3,2,1", "--mouse-at", "8:0,0,0",
              "--key-at", f"9:{RETROK['F11']}:1", "--key-at", f"10:{RETROK['F11']}:0", "--key-at", f"12:{RETROK['F12']}:1", "--key-at", f"13:{RETROK['F12']}:0"]

    def view(self, run):
        return (run["unit"], run["ticks"], run["audio"], run["ahash"], run["hash"], run["px"])

    def test_a_load_replays_the_same_packets_because_the_turbo_the_carries_the_mode_and_the_soft_reset_come_back_with_the_state(self):
        out, _ = self.packets(*self.SCRIPT, "--do", "11:save:a", "--do", "30:save:before", "--do", "30:load:a", "--do", "30:save:again", runs=40, options=self.OPTIONS)
        seen = ti.by_unit(out)
        for unit in range(12, 22):                                                           # the load at 30 went back to unit 11: units 12..21 ran twice
            self.assertEqual(len(seen[unit]), 2, f"unit {unit}")
            self.assertEqual(seen[unit][0], seen[unit][1], f"unit {unit}: the replay after the load sent another packet")
        saves = {m[1]: (m[5], m[6]) for m in ti.SAVE.finditer(out)}
        self.assertEqual(saves["again"], saves["a"])
        self.assertNotEqual(saves["before"][1], saves["a"][1])                               # the section does carry the memory: the future changed it
        native = dict(STATE_NATIVE.findall(out))
        self.assertEqual(native["again"], native["a"])
        # The soft reset chord was in progress at the save (pressed in run 12, saved after run 11: it comes after) and the mode toggled at run 9 was in it.
        packets = ti.packets(out)
        self.assertTrue(any(p[8] & key_bit(CTRL) for p in packets))

    def test_the_memory_is_in_the_state_file_and_runahead_and_rewind_converge_with_the_plain_run(self):
        control = self.frontend("boot", "--runs", 30, *self.SCRIPT, *[a for o in self.OPTIONS for a in ("--set", o)], "--do", "30:save:z").stdout
        final = STATE_NATIVE.search(control)[2]
        for ahead in (("--ahead-every", "1"), ("--ahead-every", "4"), ("--ahead", "8:6")):
            with self.subTest(ahead=ahead):
                out = self.frontend("boot", "--runs", 30, *self.SCRIPT, *[a for o in self.OPTIONS for a in ("--set", o)], *ahead, "--do", "30:save:z").stdout
                self.assertEqual([self.view(r) for r in self.runs(out)], [self.view(r) for r in self.runs(control)])
                self.assertEqual(STATE_NATIVE.search(out)[2], final)
                self.assertEqual({u: set(p) for u, p in ti.by_unit(out).items() if u <= 30}, {u: set(p) for u, p in ti.by_unit(control).items()})        # a unit is always given the same packet
                self.assertGreater(len(re.findall(r"(?m)^spec ", out)), 3)
        path = self.work() / "x.state"
        flags = [a for o in self.OPTIONS for a in ("--set", o)]
        first = self.frontend("boot", "--runs", 30, *self.SCRIPT, *flags, "--do", f"11:savefile:{path}").stdout
        second = self.frontend("boot", "--runs", 19, *self.SCRIPT, *flags, "--do", f"0:loadfile:{path}:pos=11").stdout
        self.assertEqual(ti.packets(second), ti.packets(first)[11:30])                      # the turbo phase, the carries and the held inputs were in the file

    def test_the_toggles_of_the_mapper_are_part_of_the_state(self):
        f11 = RETROK["F11"]
        keys = ["--key-at", f"3:{f11}:1", "--key-at", f"4:{f11}:0"]
        options = ("puae6_hotkey_mouse_toggle=f11",)
        arguments = [*keys, *self.pad_at(1, "RIGHT"), "--do", "6:save:on", "--do", "6:save:later", "--do", "9:device:0:1"]
        flags = [a for o in options for a in ("--set", o)]
        out = self.frontend("boot", "--runs", 12, *arguments, *flags, "--do", "10:load:on").stdout
        found = ti.packets(out)
        self.assertEqual([p[2] for p in found[:6]], [0, 0, 6, 6, 6, 6])                       # the mouse mode started in run 3
        self.assertEqual(found[10][:4], found[6][:4])                                          # after the load at 10 the mode is the saved one

    # --- the presentation toggles ---------------------------------------------------------------------------------------------------------------

    def test_the_crop_and_aspect_toggles_change_what_is_shown_and_not_the_machine(self):
        from tests.scenarios.a500 import display
        shown = display.Display("ui", mode="hires", bg=0x000, fg=0x0F0, bars=((60, 0x00F), (120, 0x0F0), (180, 0xF00), (240, 0x880)))
        rom = Path(self.tmp.name) / "ui.rom"
        rom.write_bytes(lr.builder.build(display.rom_source(shown)))
        self.roms["ui"] = rom
        f11, f12 = RETROK["F11"], RETROK["F12"]
        keys = ["--key-at", f"4:{f11}:1", "--key-at", f"5:{f11}:0", "--key-at", f"8:{f11}:1", "--key-at", f"9:{f11}:0",
                "--key-at", f"6:{f12}:1", "--key-at", f"7:{f12}:0", "--key-at", f"10:{f12}:1", "--key-at", f"11:{f12}:0"]
        flags = ["--set", "puae6_hotkey_crop_toggle=f11", "--set", "puae6_hotkey_aspect_toggle=f12", "--set", "puae6_video_crop_delay=disabled"]
        control = self.frontend("ui", "--runs", 12, "--do", "12:save:z").stdout
        out = self.frontend("ui", "--runs", 12, *keys, *flags, "--do", "12:save:z").stdout
        sizes = [r["size"] for r in self.runs(out)]
        self.assertEqual(sizes[:3], [(756, 574)] * 3)
        self.assertLess(sizes[3][0], 756)                                                       # crop on from run 4 (Automatic: the drawn area)
        self.assertEqual(sizes[7], (756, 574))                                                  # and off again from run 8
        aspects = [float(m[1]) for m in re.finditer(r"(?m)^geometry \d+x\d+ aspect=([\d.]+)", out)]
        self.assertGreaterEqual(len(aspects), 4)
        self.assertEqual([(r["unit"], r["ticks"], r["ahash"]) for r in self.runs(out)], [(r["unit"], r["ticks"], r["ahash"]) for r in self.runs(control)])
        self.assertEqual(STATE_NATIVE.search(out)[2], STATE_NATIVE.search(control)[2])


if __name__ == "__main__":
    unittest.main()
