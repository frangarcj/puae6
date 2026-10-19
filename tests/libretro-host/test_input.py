"""T17c (LR01, IN01): the keyboard, the mouse and the port devices of the libretro adapter, run by the minimal frontend.

tests/libretro-host/host.cpp plays a frontend that presses and releases keys (RETROK_* numbers), moves a mouse, holds an analog stick and changes the device of
a port, all by position on the emulated timeline, and the core's debug log shows the p6_input packet each run handed to p6_run_frame ("ports=... keys=...").
The packet is compared with the contract (adapters/libretro/input_map.h says how the host maps, docs/02-contracts.md how the bridge reads it), the Amiga key
set with the one the BizHawk wrapper names (integration/bizhawk/core/Puae6/Controllers.cs), and the machine with the p6_state_probe, which drives the same
packets by unit: an observer micro-ROM shows what the guest read in the colour of the screen, and the libretro run must paint the same pictures, unit for unit,
as the probe does with the same keys, mouse and joystick. The input memory (the Caps Lock latch, mouse movement that did not fit a unit) travels with the state,
so a load, a state file and runahead converge with the plain run. Run: python3 -m unittest discover -s tests/libretro-host -p "test_input.py"
"""
from pathlib import Path
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

ROOT = lr.ROOT
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
RETROK = {m[1]: int(m[2]) for m in re.finditer(r"^\s+RETROK_(\w+)\s*=\s*(\d+),", (ROOT / "references/libretro-api/include/libretro.h").read_text(), re.M)}
RETRO_JOYPAD, RETRO_MOUSE, RETRO_KEYBOARD, RETRO_NONE = 1, 2, 3, 0
JOYSTICK, MOUSE = 1, 2
JOYSTICK_ONLY = 257                                  # RETRO_DEVICE_SUBCLASS(RETRO_DEVICE_JOYPAD, 0): player 1's pad without the mouse
IDLE = (MOUSE, 0, 0, 0, JOYSTICK, 0, 0, 0)           # the default packet with nothing pressed: an idle mouse and an idle pad
BUTTON_1, BUTTON_2, BUTTON_3, BUTTON_UP, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT = 1, 2, 4, 8, 16, 32, 64
CAPS_LOCK = 0x62

# A guest that makes its input visible: every pass it acknowledges the keyboard and paints COLOR00 with JOY0DAT ^ (JOY1DAT rotated) ^ POTGOR ^ the fire
# buttons ^ the last keyboard byte (the SDR, which changes with every make and break), so a key, a mouse count or a button changes the picture. The
# assembler of the micro-ROMs has no EOR or ROL, so those are .word opcodes (the comments say which).
OBSERVER = """; PUAE6 test program: the colour of the screen is what the guest reads from its input devices.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and the power LED are outputs, the fire buttons inputs
    MOVE.B #2,$BFE001.L         ; OVL off
loop:
    MOVE.B #$40,$BFEE01.L       ; keyboard handshake (CRA SPMODE output and back), every pass
    MOVE.B #$00,$BFEE01.L
    MOVEQ #0,D4
    MOVE.B $BFEC01.L,D4         ; SDR: the last keyboard byte
    .word $3439
    .long $DFF00A               ; MOVE.W JOY0DAT,D2
    .word $3C39
    .long $DFF00C               ; MOVE.W JOY1DAT,D6
    .word $EF5E                 ; ROL.W #7,D6
    .word $BD42                 ; EOR.W D6,D2
    .word $3C39
    .long $DFF016               ; MOVE.W POTGOR,D6
    .word $BD42                 ; EOR.W D6,D2
    MOVEQ #0,D6
    MOVE.B $BFE001.L,D6         ; PRA: the fire buttons
    .word $BD42                 ; EOR.W D6,D2
    .word $E15C                 ; ROL.W #8,D4
    .word $B942                 ; EOR.W D4,D2
    MOVE.W D2,$DFF180.L        ; COLOR00
    BRA.S loop
"""

# The Amiga keyboard by the names of the BizHawk wrapper, and the host keys (RETROK_*) that press each key. Letters, digits and F1..F10 are added below.
KEYS_BY_NAME = {
    "Grave": ["BACKQUOTE"], "Minus": ["MINUS"], "Equals": ["EQUALS"], "Backslash": ["BACKSLASH"],
    "Left Bracket": ["LEFTBRACKET"], "Right Bracket": ["RIGHTBRACKET"], "Semicolon": ["SEMICOLON"], "Quote": ["QUOTE"], "Hash": ["HASH"],
    "Less Than": ["LESS", "OEM_102"], "Comma": ["COMMA"], "Period": ["PERIOD"], "Slash": ["SLASH"],
    "Space": ["SPACE"], "Backspace": ["BACKSPACE"], "Tab": ["TAB"], "Return": ["RETURN"], "Escape": ["ESCAPE"], "Delete": ["DELETE"],
    "Up": ["UP"], "Down": ["DOWN"], "Right": ["RIGHT"], "Left": ["LEFT"],
    "Keypad Period": ["KP_PERIOD"], "Keypad Enter": ["KP_ENTER"], "Keypad Minus": ["KP_MINUS"], "Keypad Plus": ["KP_PLUS"],
    "Keypad Slash": ["KP_DIVIDE"], "Keypad Star": ["KP_MULTIPLY"], "Keypad Left Paren": ["HOME"], "Keypad Right Paren": ["END"],
    "Help": ["HELP", "INSERT"],
    "Left Shift": ["LSHIFT"], "Right Shift": ["RSHIFT"], "Control": ["LCTRL", "RCTRL"], "Left Alt": ["LALT"], "Right Alt": ["RALT"],
    "Left Amiga": ["LMETA", "LSUPER", "PAGEUP"], "Right Amiga": ["RMETA", "RSUPER", "PAGEDOWN"],
}
KEYS_BY_NAME.update({f"Keypad {n}": [f"KP{n}"] for n in range(10)})
KEYS_BY_NAME.update({letter: [letter.lower()] for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
KEYS_BY_NAME.update({str(n): [str(n)] for n in range(10)})
KEYS_BY_NAME.update({f"F{n}": [f"F{n}"] for n in range(1, 11)})


def bizhawk_keys():
    """code -> name of every key Controllers.cs gives the wrapper (BuildKeyNames): the explicit Set calls and its loops."""
    text = (ROOT / "integration/bizhawk/core/Puae6/Controllers.cs").read_text(encoding="utf-8")
    names = {int(m[1], 16): m[2] for m in re.finditer(r'Set\((0x[0-9A-Fa-f]+), "([^"]+)"\)', text)}
    digits = re.search(r"string\[\] digits = \[([^\]]*)\]", text)[1]
    for i, digit in enumerate(re.findall(r'"(\w)"', digits)):
        names[0x01 + i] = digit
    for row, base in ((1, 0x10), (2, 0x20), (3, 0x31)):
        for i, letter in enumerate(re.search(rf'string row{row} = "([A-Z]+)"', text)[1]):
            names[base + i] = letter
    for i in range(10):
        names[0x50 + i] = f"F{i + 1}"
    return names


def key_bit(code):
    return 1 << code


def packets(out):
    """One (type0, buttons0, dx0, dy0, type1, buttons1, dx1, dy1, keys) per run, in order; keys is the 128-bit bitmap as an integer."""
    found = []
    for m in lr.TICKS.finditer(out):
        a, b = m[3].split(",")
        found.append((*(int(x, 16) if i == 1 else int(x) for i, x in enumerate(a.split(":"))),
                      *(int(x, 16) if i == 1 else int(x) for i, x in enumerate(b.split(":"))), int(m[4], 16)))
    return found


def by_unit(out):
    """unit -> every packet the core got for it (a replay after a load sees a unit again)."""
    seen = {}
    for m in lr.TICKS.finditer(out):
        seen.setdefault(int(m[1]), []).append((m[3], m[4]))
    return seen


def classes(values):
    """The partition a sequence makes, as the first-occurrence number of each value: two sequences with the same partition show the same pictures where
    the other does, whatever their hash functions are."""
    seen = {}
    return [seen.setdefault(value, len(seen)) for value in values]


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class InputTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        observer = Path(cls.tmp.name) / "keys.rom"
        observer.write_bytes(lr.builder.build(OBSERVER))
        cls.roms["keys"] = observer

    def run_input(self, *options, runs=10, rom="boot", **kwargs):
        out = self.frontend(rom, "--runs", runs, *options, **kwargs).stdout
        return out, packets(out)

    # --- the controllers the core offers ----------------------------------------------------------------------------------------------------

    def test_the_core_offers_a_joystick_a_mouse_on_port_0_a_keyboard_only_type_and_none(self):
        out = self.frontend("boot", "--runs", 2).stdout
        ports = {int(m[1]): dict(re.findall(r"(\S.*?)=(\d+)", m[2])) for m in re.finditer(r"(?m)^controller port (\d+):(.*)$", out)}
        self.assertEqual(ports[0], {"Joystick + Mouse (Amiga ports 2 and 1)": "1", "Joystick only (Amiga port 2)": "257", "Mouse only (Amiga port 1)": "2",
                                    "Keyboard only (port empty)": "3", "None": "0", "CD32 pad (Amiga port 2)": "513", "Lightgun (Amiga port 2)": "4", "Lightpen (Amiga port 2)": "6"})
        self.assertEqual(ports[1], {"Joystick (Amiga port 1; needs Player 1 on Joystick only)": "1", "Keyboard only (port empty)": "3", "None": "0",
                                  "CD32 pad (Amiga port 1; needs Player 1 without mouse)": "513", "Mouse (Amiga port 2; overrides Player 1 pad)": "2", "Lightgun (Amiga port 1)": "4", "Lightpen (Amiga port 1)": "6"})
        self.assertEqual(sorted(ports), [0, 1])
        self.assertRegex(out, r"(?m)^input descriptors count=\d+ ports=3$")                                  # port 0 and port 1 have names for their controls
        refused = self.frontend("boot", "--runs", 2, "--reject-optional").stdout          # a frontend with neither interface still gets the same core
        self.assertNotIn("controller port", refused)
        self.assertEqual(self.runs(refused)[1]["hash"], self.runs(out)[1]["hash"])

    def test_the_device_of_a_port_is_the_frontends_choice_and_a_type_the_port_was_not_offered_is_refused(self):
        _, default = self.run_input(runs=2)
        self.assertEqual([p[:8] for p in default], [IDLE, IDLE])                    # a frontend that never calls retro_set_controller_port_device: pad and mouse
        _, both = self.run_input("--device", "0:2", "--device", "1:0", runs=2)
        self.assertEqual(both[0][:8], (MOUSE, 0, 0, 0, 0, 0, 0, 0))
        out, port1 = self.run_input("--device", "0:257", "--device", "1:2", runs=2)
        self.assertEqual(port1[0][:8], (0, 0, 0, 0, MOUSE, 0, 0, 0))
        self.assertIn("player 1's pad is not sent: player 2's mouse holds Amiga port 2", out)
        out, bogus = self.run_input("--device", "0:77", "--device", "5:1", "--device", "1:257", runs=2)       # the joystick-only subclass is player 1's alone
        self.assertEqual(bogus[0][:8], IDLE)
        self.assertRegex(out, r"device 77 is not available in port 0")
        self.assertRegex(out, r"device 1 is not available in port 5")
        self.assertRegex(out, r"device 257 is not available in port 1")

    def test_none_and_keyboard_only_leave_the_port_empty_and_the_keyboard_still_works(self):
        for device in (RETRO_NONE, RETRO_KEYBOARD):
            with self.subTest(device=device):
                _, found = self.run_input("--device", f"0:{device}", "--device", f"1:{device}", "--joy", "0:ffff", "--joy", "1:ffff",
                                          "--key-at", f"2:{RETROK['a']}:1", runs=4)
                self.assertTrue(all(p[:8] == (0,) * 8 for p in found))                 # an empty port carries no buttons and no movement
                self.assertEqual([p[8] for p in found], [0, key_bit(0x20), key_bit(0x20), key_bit(0x20)])

    def test_a_device_change_takes_effect_at_the_next_run_and_leaves_no_movement_behind(self):
        _, found = self.run_input("--mouse", "3,0,1", "--joy", "0:1", "--do", "4:device:0:257", "--do", "7:device:0:2", "--do", "9:device:0:1", runs=12)
        # Pad and mouse at once; player 1 on Joystick only (player 2's joystick takes the mouse port); the mouse alone; pad and mouse again.
        self.assertEqual([p[0] for p in found], [MOUSE] * 4 + [JOYSTICK] * 3 + [MOUSE] * 5)       # the call between run 4 and run 5 is seen by run 5
        self.assertEqual([p[4] for p in found], [JOYSTICK] * 7 + [0] * 2 + [JOYSTICK] * 3)
        self.assertEqual([p[2] for p in found], [3] * 4 + [0] * 3 + [3] * 5)                   # no movement left over from the run the mouse was away
        self.assertEqual([p[5] for p in found[:7]], [BUTTON_1] * 7)                            # player 1's B is the fire button of the joystick port throughout
        self.assertEqual([p[5] for p in found[7:9]], [0, 0])                                   # and the mouse alone has no pad

    # --- the keyboard ---------------------------------------------------------------------------------------------------------------------

    def test_the_amiga_keys_are_the_ones_the_bizhawk_wrapper_names(self):
        wrapper = bizhawk_keys()
        self.assertEqual(sorted(wrapper), [c for c in range(0x68) if c in wrapper])                 # no key at 0x68 or above
        self.assertGreater(len(wrapper), 90)
        mapped = {name for name in wrapper.values()} - {"Caps Lock"}
        self.assertEqual(mapped, set(KEYS_BY_NAME), "the test's table and the wrapper's key names differ")
        hosts = [k for keys in KEYS_BY_NAME.values() for k in keys]
        self.assertEqual(len(hosts), len(set(hosts)))                                              # a host key presses one Amiga key
        for key in hosts:
            self.assertIn(key, RETROK)

    def test_every_mapped_host_key_presses_exactly_its_amiga_key(self):
        wrapper = {name: code for code, name in bizhawk_keys().items()}
        script = [(RETROK[host], wrapper[name]) for name, hosts in sorted(KEYS_BY_NAME.items(), key=lambda item: wrapper[item[0]]) for host in hosts]
        arguments = []
        for i, (retro, _) in enumerate(script):                                                   # key i is down in run 2i+1 and up in run 2i+2
            arguments += ["--key-at", f"{2 * i + 1}:{retro}:1", "--key-at", f"{2 * i + 2}:{retro}:0"]
        _, found = self.run_input(*arguments, runs=2 * len(script))
        self.assertEqual(len(found), 2 * len(script))
        for i, (retro, code) in enumerate(script):
            self.assertEqual(found[2 * i][8], key_bit(code), f"RETROK {retro} should press Amiga key {code:#x}")
            self.assertEqual(found[2 * i + 1][8], 0, f"RETROK {retro} released")
            self.assertTrue(all(p[:8] == IDLE for p in found[2 * i:2 * i + 2]))                           # and nothing else moves
        self.assertEqual({code for _, code in script} | {CAPS_LOCK}, set(bizhawk_keys()))              # the whole keyboard is reachable

    def test_keys_held_together_set_their_bits_together_and_a_shared_amiga_key_stays_down_until_both_hosts_keys_are_up(self):
        a, space, lshift = RETROK["a"], RETROK["SPACE"], RETROK["LSHIFT"]
        lctrl, rctrl = RETROK["LCTRL"], RETROK["RCTRL"]
        _, found = self.run_input("--key-at", f"2:{a}:1", "--key-at", f"3:{space}:1", "--key-at", f"3:{lshift}:1", "--key-at", f"4:{a}:0",
                                  "--key-at", f"5:{lctrl}:1", "--key-at", f"5:{rctrl}:1", "--key-at", f"6:{lctrl}:0", "--key-at", f"7:{rctrl}:0", runs=8)
        self.assertEqual([p[8] for p in found],
                         [0, key_bit(0x20), key_bit(0x20) | key_bit(0x40) | key_bit(0x60), key_bit(0x40) | key_bit(0x60),
                          key_bit(0x40) | key_bit(0x60) | key_bit(0x63), key_bit(0x40) | key_bit(0x60) | key_bit(0x63), key_bit(0x40) | key_bit(0x60), key_bit(0x40) | key_bit(0x60)])

    def test_caps_lock_latches_every_press_toggles_it_and_holding_it_does_nothing_more(self):
        caps = RETROK["CAPSLOCK"]
        # Press in run 3 (held until run 6), again in run 9: on from run 3, off from run 9. Holding it down for four runs toggled it once.
        _, found = self.run_input("--key-at", f"3:{caps}:1", "--key-at", f"7:{caps}:0", "--key-at", f"9:{caps}:1", "--key-at", f"10:{caps}:0", runs=12)
        self.assertEqual([bool(p[8] >> CAPS_LOCK & 1) for p in found], [False, False] + [True] * 6 + [False] * 4)
        self.assertEqual([p[8] & ~key_bit(CAPS_LOCK) for p in found], [0] * 12)
        # A key held from the first run counts as a press.
        _, held = self.run_input("--key-at", f"1:{caps}:1", runs=4)
        self.assertEqual([bool(p[8] >> CAPS_LOCK & 1) for p in held], [True] * 4)

    def test_codes_the_amiga_keyboard_does_not_have_are_ignored(self):
        unknown = [RETROK[n] for n in ("F11", "F12", "F13", "PAUSE", "PRINT", "NUMLOCK", "SCROLLOCK", "MENU", "CLEAR", "KP_EQUALS", "COMPOSE", "EURO", "COLON",
                                        "GREATER", "TILDE")] + [9999, 65535, 3, 400]
        arguments = ["--set", "puae6_hotkey_statusbar=---"]       # F12 is the statusbar's hotkey by default (T31b): it draws a bar, it does not reach the Amiga
        for code in unknown:
            arguments += ["--key-at", f"2:{code}:1"]
        out, found = self.run_input(*arguments, runs=6)
        self.assertTrue(all(p[8] == 0 for p in found))
        plain = self.runs(self.frontend("boot", "--runs", 6).stdout)
        self.assertEqual([(r["hash"], r["ahash"], r["ticks"]) for r in self.runs(out)], [(r["hash"], r["ahash"], r["ticks"]) for r in plain])

    def test_the_keyboard_is_read_from_the_poll_of_the_run_and_never_by_a_callback(self):
        out = self.frontend("boot", "--runs", 3, "--key-at", f"1:{RETROK['a']}:1").stdout
        self.assertNotIn("12", re.search(r"environment=([\d,]+)", out)[1].split(","), "SET_KEYBOARD_CALLBACK is the async way and is not used")
        self.assertTrue(all(r["polls"] == 1 for r in self.runs(out)))

    # --- the mouse ------------------------------------------------------------------------------------------------------------------------

    def test_the_mouse_deltas_and_buttons_reach_port_0_as_the_contract_reads_them(self):
        _, found = self.run_input("--mouse-at", "2:5,-3,1", "--mouse-at", "3:-7,9,6", "--mouse-at", "4:0,0,4", "--mouse-at", "5:120,-120,0", runs=6)
        self.assertEqual([p[:4] for p in found], [(MOUSE, 0, 0, 0), (MOUSE, BUTTON_1, 5, -3), (MOUSE, BUTTON_2 | BUTTON_3, -7, 9), (MOUSE, BUTTON_3, 0, 0),
                                                 (MOUSE, 0, 120, -120), (MOUSE, 0, 0, 0)])
        self.assertTrue(all(p[4:8] == (JOYSTICK, 0, 0, 0) for p in found))                          # the pad of player 1 is in the joystick port all along
        _, alone = self.run_input("--mouse-at", "2:5,-3,1", "--device", "0:2", runs=3)              # "Mouse only": the same mouse, and nothing in the joystick port
        self.assertEqual([p[:8] for p in alone], [(MOUSE, 0, 0, 0, 0, 0, 0, 0), (MOUSE, BUTTON_1, 5, -3, 0, 0, 0, 0), (MOUSE, BUTTON_1, 0, 0, 0, 0, 0, 0)])

    def test_movement_beyond_120_counts_a_unit_is_not_lost_but_comes_in_the_next_runs_up_to_a_bound(self):
        _, found = self.run_input("--mouse-at", "2:300,-130,0", runs=7)
        self.assertEqual([(p[2], p[3]) for p in found], [(0, 0), (120, -120), (120, -10), (60, 0), (0, 0), (0, 0), (0, 0)])
        _, burst = self.run_input("--mouse-at", "2:30000,0,0", runs=14)                              # a huge burst is cut: 8 units of backlog besides this one
        self.assertEqual(sum(p[2] for p in burst), 9 * 120)
        self.assertEqual([p[2] for p in burst], [0] + [120] * 9 + [0] * 4)

    # --- the joystick -----------------------------------------------------------------------------------------------------------------------

    def test_the_joystick_buttons_the_dpad_and_the_left_stick_make_the_same_packet(self):
        only = ("--device", "0:257")                                                                 # player 2's pad takes the mouse port, so both pads show
        _, pad = self.run_input("--joy", "0:391", "--joy", "1:40", *only, runs=2)
        # Player 1 (libretro port 0) is the joystick port (ports[1]), player 2 the mouse port (ports[0]).
        self.assertEqual(pad[0][:8], (JOYSTICK, BUTTON_LEFT, 0, 0, JOYSTICK, BUTTON_1 | BUTTON_2 | BUTTON_3 | BUTTON_UP | BUTTON_RIGHT, 0, 0))
        _, stick = self.run_input("--analog", "0:30000,-30000", "--analog", "1:-30000,30000", *only, runs=2)
        self.assertEqual((stick[0][5], stick[0][1]), (BUTTON_UP | BUTTON_RIGHT, BUTTON_LEFT | BUTTON_DOWN))
        _, small = self.run_input("--analog", "0:15000,-15000", "--analog", "1:-20000,20000", *only, runs=2)          # at or under the threshold nothing moves
        self.assertEqual((small[0][1], small[0][5]), (0, 0))
        _, both = self.run_input("--analog", "0:30000,0", "--joy", "0:10", runs=2)
        self.assertEqual(both[0][5], BUTTON_RIGHT | BUTTON_UP)                                        # pad and stick together, with the default device too

    def test_the_default_device_gives_player_1_the_pad_and_the_mouse_at_once_and_player_2_the_mouse_port_only_when_player_1_lets_go_of_it(self):
        pads = ("--joy", "0:11", "--joy", "1:40")                          # player 1: B and UP; player 2: LEFT
        mouse = ("--mouse", "4,-2,3")
        both = (MOUSE, BUTTON_1 | BUTTON_2, 4, -2, JOYSTICK, BUTTON_1 | BUTTON_UP, 0, 0)
        # The default (nothing was ever set): the pad and the mouse together; player 2's pad has no port and the load says so.
        out, default = self.run_input(*pads, *mouse, runs=3)
        self.assertEqual(default[0][:8], both)
        self.assertRegex(out, r"(?m)^log 1 \[PUAE6\] player 2's joystick is not sent: the mouse holds its Amiga port \(set player 1 to Joystick only to free it\)$")
        # The same with every type of player 2 that is not a joystick: the rule only removes what had no port.
        for player_2 in (RETRO_NONE, RETRO_KEYBOARD):
            _, alone = self.run_input(*pads, *mouse, "--device", f"1:{player_2}", runs=3)
            self.assertEqual(alone[0][:8], both)
        # Mouse only: the mouse port is the mouse's, the joystick port is empty, player 2's pad still has no port.
        _, mouse_only = self.run_input(*pads, *mouse, "--device", "0:2", runs=3)
        self.assertEqual(mouse_only[0][:8], (MOUSE, BUTTON_1 | BUTTON_2, 4, -2, 0, 0, 0, 0))
        # Joystick only: the mouse is not read at all, and player 2's pad drives the mouse port.
        out, only = self.run_input(*pads, *mouse, "--device", "0:257", runs=3)
        self.assertEqual(only[0][:8], (JOYSTICK, BUTTON_LEFT, 0, 0, JOYSTICK, BUTTON_1 | BUTTON_UP, 0, 0))
        self.assertNotIn("player 2's joystick is not sent", out)
        # Player 1 empty: player 2 alone in the mouse port.
        for player_1 in (RETRO_NONE, RETRO_KEYBOARD):
            _, second = self.run_input(*pads, *mouse, "--device", f"0:{player_1}", runs=3)
            self.assertEqual(second[0][:8], (JOYSTICK, BUTTON_LEFT, 0, 0, 0, 0, 0, 0))
        # Changing player 1 between runs moves the rule at the next run, and says so when it makes the pad lose its port.
        out, changing = self.run_input(*pads, *mouse, "--device", "0:257", "--do", "1:device:0:1", "--do", "2:device:0:257", "--do", "3:device:0:2", runs=5)
        self.assertEqual([p[:8] for p in changing],
                         [only[0][:8], both, only[0][:8], (MOUSE, BUTTON_1 | BUTTON_2, 4, -2, 0, 0, 0, 0), (MOUSE, BUTTON_1 | BUTTON_2, 4, -2, 0, 0, 0, 0)])
        self.assertEqual(len(re.findall(r"player 2's joystick is not sent", out)), 2)             # the two calls that made it so; the load found Joystick only

    def test_keys_mouse_and_joystick_paint_the_same_pictures_as_the_state_probe_does_with_the_same_packets(self):
        units = 32
        # Runs 1-20: the default device, the pad of player 1 in the joystick port (probe ports[1]) and the mouse in the mouse port (ports[0]) in the same runs.
        # From run 21 on player 1 is "Joystick only" and both players are joysticks: player 1 in the joystick port, player 2 in the mouse port.
        retro = ["--key-at", f"6:{RETROK['a']}:1", "--key-at", f"10:{RETROK['a']}:0", "--key-at", f"14:{RETROK['SPACE']}:1",
                 "--key-at", f"18:{RETROK['LSHIFT']}:1", "--key-at", f"26:{RETROK['SPACE']}:0",
                 "--mouse-at", "16:5,-3,1", "--mouse-at", "17:0,0,1", "--mouse-at", "19:0,0,0", "--do", "20:device:0:257",
                 "--joy-at", "12:0:1", "--joy-at", "16:0:10", "--joy-at", "18:0:0",
                 "--joy-at", "21:0:1", "--joy-at", "23:0:10", "--joy-at", "25:0:0", "--joy-at", "27:1:1", "--joy-at", "29:1:0"]
        probe = ["--port", "0:2:0:0:0", "--port", "1:1:0:0:0", "--key", "6", "0x20", "down", "--key", "10", "0x20", "up", "--key", "14", "0x40", "down",
                 "--key", "18", "0x60", "down", "--key", "26", "0x40", "up",
                 "--port-at", "12", "1:1:1:0:0", "--port-at", "16", "1:1:8:0:0", "--port-at", "18", "1:1:0:0:0",
                 "--port-at", "16", "0:2:1:5:-3", "--port-at", "17", "0:2:1:0:0", "--port-at", "19", "0:2:0:0:0",
                 "--port-at", "21", "0:1:0:0:0", "--port-at", "21", "1:1:1:0:0", "--port-at", "23", "1:1:8:0:0", "--port-at", "25", "1:1:0:0:0",
                 "--port-at", "27", "0:1:1:0:0", "--port-at", "29", "0:1:0:0:0"]
        out = self.frontend("keys", "--runs", units, *retro).stdout
        reference = subprocess.run([str(self.probe), str(self.roms["keys"]), "--units", str(units), "--every", "1", *probe], capture_output=True, text=True,
                                   timeout=600)
        self.assertEqual(reference.returncode, 0, reference.stderr[-400:])
        pictures = {int(m[1]): m[2] for m in re.finditer(r"(?m)^digest (\d+) video (\w+) ", reference.stdout)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), list(range(1, units + 1)))
        core_pictures = [runs[n]["hash"] for n in range(1, units + 1)]
        self.assertEqual(classes(core_pictures), classes([pictures[n] for n in range(1, units + 1)]))
        self.assertGreaterEqual(len(set(core_pictures)), 8)                                           # the observer did show the input: not one flat picture
        plain = self.runs(self.frontend("keys", "--runs", units).stdout)
        self.assertLess(sum(1 for a, b in zip(plain, plain[1:]) if a["hash"] != b["hash"]), 3)       # and without input nothing changes after the start

    # --- the input memory travels with the state ------------------------------------------------------------------------------------------

    CAPS = RETROK["CAPSLOCK"]
    SCRIPT = ("--key-at", f"3:{RETROK['a']}:1", "--key-at", f"9:{RETROK['a']}:0", "--key-at", f"5:{RETROK['CAPSLOCK']}:1", "--key-at", f"6:{RETROK['CAPSLOCK']}:0",
              "--key-at", f"11:{RETROK['CAPSLOCK']}:1", "--key-at", f"12:{RETROK['CAPSLOCK']}:0", "--key-at", f"8:{RETROK['SPACE']}:1",
              "--mouse-at", "3:500,-200,1", "--mouse-at", "4:0,0,1", "--mouse-at", "10:0,0,0",
              "--joy-at", "7:0:11", "--joy-at", "15:0:0")      # the default device: the pad of player 1 moves in the same runs as the mouse
    # The same with two joysticks instead of the mouse: both pads move, and the keys and the Caps Lock latch go with them.
    PADS = ("--key-at", f"3:{RETROK['a']}:1", "--key-at", f"9:{RETROK['a']}:0", "--key-at", f"5:{RETROK['CAPSLOCK']}:1", "--key-at", f"6:{RETROK['CAPSLOCK']}:0",
            "--joy-at", "4:0:11", "--joy-at", "8:0:0", "--joy-at", "6:1:40", "--joy-at", "12:1:0", "--analog", "1:0,0", "--device", "0:257")

    @staticmethod
    def view(run):
        return (run["unit"], run["ticks"], run["audio"], run["ahash"], run["hash"], run["px"])

    def test_a_load_replays_the_same_packets_because_the_latch_and_the_owed_movement_come_back_with_the_state(self):
        control = self.frontend("keys", "--runs", 14, *self.SCRIPT).stdout
        out = self.frontend("keys", "--runs", 22, *self.SCRIPT, "--do", "5:save:a", "--do", "14:save:before", "--do", "14:load:a", "--do", "14:save:again").stdout
        seen = by_unit(out)
        for unit in range(6, 14):
            self.assertEqual(len(seen[unit]), 2, f"unit {unit}")
            self.assertEqual(seen[unit][0], seen[unit][1], f"unit {unit}: the replay after the load sent another packet")
        sent = packets(out)
        self.assertEqual(sent[14:22], sent[5:13])
        self.assertTrue(any(p[5] and p[2] for p in sent[:14]))                                         # the pad and the mouse moved in the same runs
        self.assertEqual((sent[5][2], sent[6][2]), (120, 20))                                           # the owed movement of the saved moment, not of the abandoned future
        self.assertEqual([self.view(r) for r in self.runs(out)][:14], [self.view(r) for r in self.runs(control)])
        saves = {m[1]: (m[5], m[6]) for m in SAVE.finditer(out)}
        self.assertEqual(saves["again"], saves["a"])                                                   # the loaded machine and the adapter section are the saved ones
        self.assertNotEqual(saves["before"][1], saves["a"][1])                                         # and the section does carry the memory: the future changed it

    def test_runahead_and_a_state_file_converge_with_the_plain_run_with_keys_and_mouse_in_the_speculative_frames(self):
        control = self.frontend("keys", "--runs", 20, *self.SCRIPT).stdout
        final = re.search(r"(?m)^state final ok=1 size=\d+ hash=(\w+)$", control)[1]
        for ahead in (("--ahead-every", "1"), ("--ahead-every", "3"), ("--ahead", "4:5"), ("--ahead", "5:4")):
            with self.subTest(ahead=ahead):
                out = self.frontend("keys", "--runs", 20, *self.SCRIPT, *ahead).stdout
                self.assertEqual([self.view(r) for r in self.runs(out)], [self.view(r) for r in self.runs(control)])
                self.assertEqual(re.search(r"(?m)^state final ok=1 size=\d+ hash=(\w+)$", out)[1], final)
                self.assertGreater(len(re.findall(r"(?m)^spec ", out)), 3)
        path = self.work() / "x.state"
        first = self.frontend("keys", "--runs", 20, *self.SCRIPT, "--do", f"5:savefile:{path}", system=self.system("keys")).stdout
        second = self.frontend("keys", "--runs", 15, *self.SCRIPT, "--do", f"0:loadfile:{path}:pos=5", system=self.system("keys")).stdout
        self.assertEqual(packets(second), packets(first)[5:20])                                      # the latch and the owed movement were in the file
        self.assertEqual([self.view(r) for r in self.runs(second)][:15], [self.view(r) for r in self.runs(first)][5:20])

    def test_runahead_and_a_load_converge_with_two_joysticks_and_the_keyboard(self):
        control = self.frontend("keys", "--runs", 16, *self.PADS).stdout
        final = re.search(r"(?m)^state final ok=1 size=\d+ hash=(\w+)$", control)[1]
        self.assertTrue(any(p[1] for p in packets(control)) and any(p[5] for p in packets(control)))             # both pads did move
        out = self.frontend("keys", "--runs", 16, *self.PADS, "--ahead-every", "2").stdout
        self.assertEqual([self.view(r) for r in self.runs(out)], [self.view(r) for r in self.runs(control)])
        self.assertEqual(re.search(r"(?m)^state final ok=1 size=\d+ hash=(\w+)$", out)[1], final)
        replay = self.frontend("keys", "--runs", 20, *self.PADS, "--do", "4:save:a", "--do", "12:load:a").stdout
        seen = by_unit(replay)
        for unit in range(5, 13):
            self.assertEqual(len(set(seen[unit])), 1, f"unit {unit}")

    def test_a_new_load_starts_with_no_latch_and_no_owed_movement(self):
        out = self.frontend("boot", "--runs", 6, "--cycles", 2, "--key-at", f"3:{self.CAPS}:1", "--key-at", f"4:{self.CAPS}:0", "--mouse-at", "2:400,0,0").stdout
        found = packets(out)
        self.assertEqual(len(found), 12)
        self.assertEqual(found[:6], found[6:])                                                       # the second cycle repeats the first, nothing left over
        self.assertEqual([p[2] for p in found[:6]], [0, 120, 120, 120, 40, 0])


if __name__ == "__main__":
    unittest.main()
