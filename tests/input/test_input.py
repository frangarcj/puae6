"""IN01 for keyboard, mouse and joystick: what the guest reads, against what the hardware documentation says.

The micro-ROM tests/fixtures/microrom/input_all.s logs JOY0DAT/JOY1DAT/POT0DAT/POT1DAT/POTGOR and CIA-A PRA at
every vertical blank and every byte of the CIA keyboard interface. The expected values below are derived from the
Amiga hardware reference (quadrature counters, pull-ups, the keyboard's inverted and rotated code), not from the
engine's output, and the input goes in through the real p6_run_frame. The same scripts also run in the STEP01
comparison (N calls against continuous advance, input applied at the boundary) and in two processes.
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tests.step.test_step import BUILD, TOOLS, builder, configure_trace_tree, run_probe

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/microrom"

# port types and button bits of bridge/include/p6.h
NONE, JOYSTICK, MOUSE = 0, 1, 2
B1, B2, B3, UP, DOWN, LEFT, RIGHT = 1, 2, 4, 8, 16, 32, 64
CTRL, LAMI, RAMI, ESC, KEY_Q, KEY_W, CAPS = 0x63, 0x66, 0x67, 0x45, 0x10, 0x11, 0x62


def key_words(codes):
    words = [0, 0, 0, 0]
    for code in codes:
        words[code // 32] |= 1 << (code % 32)
    return words


def line(keys=(), p0=(NONE, 0, 0, 0), p1=(NONE, 0, 0, 0)):
    k = key_words(keys)
    return "%08x %08x %08x %08x %d %d %d %d %d %d %d %d" % (*k, *p0, *p1)


def sdr(code, down):
    """The byte in the CIA serial data register: the key code and an up flag, rotated left one bit, inverted."""
    return ~((code << 1) | (0 if down else 1)) & 0xFF


class Run:
    def __init__(self, exe, rom, script, units, mode="steps", standard="pal", seed=1):
        with tempfile.TemporaryDirectory(prefix="p6-input-") as directory:
            path = Path(directory) / "script.txt"
            path.write_text("\n".join(script) + "\n")
            code, out = run_probe(exe, rom, units, mode, seed, timeout=600, extra=[standard],
                                  env={"P6_INPUT_SCRIPT": str(path), "P6_DUMP_LOG": "1"})
        assert code == 0, out[-600:]
        self.text = out
        self.logged = {}
        for l in out.splitlines():
            m = re.match(r"logu (\d+):(.*)$", l)
            if m:
                self.logged[int(m[1])] = [int(w, 16) for w in m[2].split()]
        self.digests = re.findall(r"digest=([0-9a-f]{16})", out)

    def unit_words(self, unit):
        """The words the guest logged during 1-based `unit`."""
        return self.logged[unit]

    def records(self, unit):
        words, out = self.unit_words(unit), []
        i = 0
        while i < len(words):
            if words[i] == 0xAA55:
                out.append(("vb", words[i + 1:i + 7]))
                i += 7
            elif words[i] == 0xBB00:
                out.append(("kbd", words[i + 1:i + 3]))
                i += 3
            else:
                raise AssertionError("unknown log word %04x in unit %d" % (words[i], unit))
        return out

    def keyboard(self, first, last):
        """(unit, ICR, SDR) of every keyboard record from unit `first` to `last`."""
        return [(u, w[0], w[1]) for u in range(first, last + 1) for kind, w in self.records(u) if kind == "kbd"]

    def sdrs(self, first, last):
        return [b for _, _, b in self.keyboard(first, last)]


def combo_script(units=14):
    idle = line()
    script = {
        4: line(p0=(MOUSE, 0, 5, -3), p1=(JOYSTICK, RIGHT, 0, 0)),
        5: line(p0=(MOUSE, 0, 120, 0), p1=(JOYSTICK, RIGHT | UP, 0, 0)),
        6: line(p0=(MOUSE, B1, -7, 0), p1=(JOYSTICK, LEFT, 0, 0)),
        7: line(p0=(MOUSE, B2, 0, 0), p1=(JOYSTICK, DOWN | LEFT, 0, 0)),
        8: line(p0=(MOUSE, B3, 0, 0), p1=(JOYSTICK, B1, 0, 0)),
        9: line(p0=(MOUSE, B1 | B2 | B3, 0, 0), p1=(JOYSTICK, B2, 0, 0)),
        10: line(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, B3 | UP, 0, 0)),
        11: line(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, DOWN, 0, 0)),
    }
    return [script.get(u, idle if u < 4 else line(p0=(MOUSE, 0, 0, 0), p1=(JOYSTICK, 0, 0, 0))) for u in range(1, units + 1)]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-input-rom-")
        cls.rom = Path(cls.tmp.name) / "input_all.rom"
        cls.rom.write_bytes(builder.build((FIXTURES / "input_all.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def execute(self, script, units, **kwargs):
        return Run(self.exe, self.rom, script, units, **kwargs)

    def test_mouse_and_joystick_registers_follow_the_documented_encoding(self):
        # unit: (JOY0DAT, JOY1DAT & 0x0303, POTGOR, PRA); counters start at 0; Y counts down for "up" (negative dy).
        expected = {2: (0x0000, 0x0000, 0x5500, 0xE2), 3: (0x0000, 0x0000, 0x5500, 0xE2),
                    4: (0xFD05, 0x0003, 0x5500, 0xE2), 5: (0xFD7D, 0x0103, 0x5500, 0xE2),
                    6: (0xFD76, 0x0300, 0x5500, 0xA2), 7: (0xFD76, 0x0301, 0x5100, 0xE2),
                    8: (0xFD76, 0x0000, 0x5400, 0x62), 9: (0xFD76, 0x0000, 0x1000, 0xA2),
                    10: (0xFD76, 0x0100, 0x4500, 0xE2), 11: (0xFD76, 0x0001, 0x5500, 0xE2)}
        for standard in ("pal", "ntsc"):
            run = self.execute(combo_script(), 14, standard=standard)
            for unit, (joy0, joy1, potgor, pra) in expected.items():
                with self.subTest(standard=standard, unit=unit):
                    vb = [w for kind, w in run.records(unit) if kind == "vb"]
                    self.assertEqual(len(vb), 1)
                    self.assertEqual((vb[0][0], vb[0][1] & 0x0303, vb[0][4], vb[0][5] & 0xE2), (joy0, joy1, potgor, pra & 0xE2))

    def test_keys_reach_the_guest_as_the_documented_codes_and_are_released(self):
        # Power-up stream $00, $FD, $FE reaches the guest in unit 1; then ESC held 30..39, Q and W tapped at 50.
        script = [line(keys=[ESC] if 30 <= u <= 39 else [KEY_Q, KEY_W] if u == 50 else []) for u in range(1, 61)]
        run = self.execute(script, 60)
        self.assertEqual(run.keyboard(1, 1), [(1, 0x88, 0x00), (1, 0x88, 0x04), (1, 0x88, 0x02)])
        self.assertEqual(run.sdrs(2, 29), [])
        self.assertEqual(run.sdrs(30, 39), [sdr(ESC, True)])           # 0x75: pressed once, not repeated while held
        self.assertEqual(run.sdrs(40, 49), [sdr(ESC, False)])          # 0x74
        self.assertEqual(run.sdrs(50, 50), [sdr(KEY_Q, True), sdr(KEY_W, True)])   # in ascending order
        self.assertEqual(run.sdrs(51, 60), [sdr(KEY_Q, False), sdr(KEY_W, False)])
        self.assertEqual([sdr(ESC, True), sdr(ESC, False), sdr(KEY_Q, True), sdr(KEY_W, True)], [0x75, 0x74, 0xDF, 0xDD])

    def test_caps_lock_bit_is_the_latch(self):
        script = [line(keys=[CAPS] if 10 <= u < 20 else []) for u in range(1, 31)]
        run = self.execute(script, 30)
        self.assertEqual(run.sdrs(10, 19), [sdr(CAPS, True)])          # latched: one make, nothing on repeat
        self.assertEqual(run.sdrs(20, 30), [sdr(CAPS, False)])

    def test_a_flood_of_keys_ends_with_every_key_up_in_the_guest(self):
        # 100 keys toggle every unit for 10 units: more than the keyboard ring can hold, and the guest takes at most
        # one code per 16 lines. Edges that do not fit are sent later; none may be lost.
        codes = [c for c in range(0x68) if c not in (CTRL, LAMI, RAMI, 0x66, 0x67)][:100]
        script = [line(keys=codes if 1 <= u <= 10 and u % 2 else []) for u in range(1, 241)]
        run = self.execute(script, 240)
        balance = {}
        for _, _, byte in run.keyboard(1, 240)[3:]:          # after the power-up stream $00, $FD, $FE
            raw = ~byte & 0xFF
            code, up = raw >> 1, raw & 1
            balance.setdefault(code, []).append(not up)
        self.assertEqual(set(balance), set(codes))
        for code, edges in balance.items():
            self.assertEqual(edges, [True, False] * (len(edges) // 2), hex(code))   # alternating, ends released
            self.assertGreaterEqual(len(edges), 2, hex(code))                       # every key got through at least once

    def test_ctrl_amiga_amiga_resets_and_held_keys_are_pressed_again_after_the_boot(self):
        def keys(u):
            held = []
            if u >= 20: held.append(CTRL)
            if u >= 21: held.append(LAMI)
            if 22 <= u <= 24: held.append(RAMI)
            if u >= 30: held = [k for k in held if k not in (CTRL, LAMI)]
            return held
        script = [line(keys=keys(u)) for u in range(1, 41)]
        run = self.execute(script, 40)
        stream = run.sdrs(1, 40)
        first_powerup = stream[:3]
        self.assertEqual(first_powerup, [0x00, 0x04, 0x02])
        # After the chord ends the machine boots again: the power-up stream comes a second time ...
        again = [i for i in range(3, len(stream) - 2) if stream[i:i + 3] == [0x00, 0x04, 0x02]]
        self.assertEqual(len(again), 1, stream)
        after = stream[again[0] + 3:]
        # ... and then every key the host still holds is pressed once, and released when the host releases it.
        self.assertEqual(after, [sdr(CTRL, True), sdr(LAMI, True), sdr(CTRL, False), sdr(LAMI, False)])

    def test_two_processes_and_n_calls_against_continuous_advance_agree(self):
        script = combo_script(12)
        for standard in ("pal",):
            a = self.execute(script, 12, standard=standard)
            b = self.execute(script, 12, standard=standard)
            self.assertEqual(a.text, b.text)
            for units in (3, 7, 12):
                steps = self.execute(script, units, mode="steps", standard=standard)
                continuous = self.execute(script, units, mode="continuous", standard=standard)
                self.assertEqual(steps.text, continuous.text, f"{units} units")
        keys = [line(keys=[ESC] if u in (3, 4) else [KEY_Q] if u == 6 else []) for u in range(1, 9)]
        self.assertEqual(self.execute(keys, 8, mode="steps").text, self.execute(keys, 8, mode="continuous").text)


if __name__ == "__main__":
    unittest.main()
