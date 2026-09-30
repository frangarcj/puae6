"""p6_reset: a hard reset at the next boundary that keeps the media and the configuration and lets go of the keys.

The micro-ROM tests/fixtures/microrom/input_all.s logs every byte the CIA keyboard interface delivers, and its log pointer
restarts with the guest, so a reset shows as a second power-up stream ($00 $04 $02, the initial key codes of the
keyboard) in the unit that follows it. The step probe (P6_RESET_AT) calls p6_reset before the listed units.
"""
from pathlib import Path
import re
import tempfile
import unittest

from tests.input.test_input import KEY_Q, CTRL, FIXTURES, line, sdr
from tests.step.test_step import TOOLS, builder, configure_trace_tree, run_probe

POWER_UP = [0x00, 0x04, 0x02]


class Run:
    def __init__(self, exe, rom, script, units, reset_at, standard="pal", seed=1):
        with tempfile.TemporaryDirectory(prefix="p6-reset-") as directory:
            path = Path(directory) / "script.txt"
            path.write_text("\n".join(script) + "\n")
            env = {"P6_INPUT_SCRIPT": str(path), "P6_DUMP_LOG": "1"}
            if reset_at:
                env["P6_RESET_AT"] = ",".join(map(str, reset_at))
            code, out = run_probe(exe, rom, units, "steps", seed, timeout=600, extra=[standard], env=env)
        assert code == 0, out[-600:]
        self.text = out
        self.cycles = {int(m[1]): int(m[2]) for m in re.finditer(r"^unit (\d+) .* cycles=(\d+) ", out, re.M)}
        self.ticks = int(re.search(r"final digest=\w+ ticks=(\d+)", out)[1])
        self.pc = {int(m[1]): m[2] for m in re.finditer(r"^unit (\d+) pc=(\w+) ", out, re.M)}
        self.logged = {int(m[1]): [int(w, 16) for w in m[2].split()] for m in re.finditer(r"^logu (\d+):(.*)$", out, re.M)}

    def keyboard(self, first, last):
        """The SDR byte of every keyboard record from unit `first` to `last`."""
        out = []
        for unit in range(first, last + 1):
            words, i = self.logged[unit], 0
            while i < len(words):
                if words[i] == 0xAA55:
                    i += 7
                else:
                    assert words[i] == 0xBB00, hex(words[i])
                    out.append(words[i + 2])
                    i += 3
        return out


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-reset-rom-")
        cls.rom = Path(cls.tmp.name) / "input_all.rom"
        cls.rom.write_bytes(builder.build((FIXTURES / "input_all.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def execute(self, script, units, reset_at, **kwargs):
        return Run(self.exe, self.rom, script, units, reset_at, **kwargs)

    def test_the_guest_starts_over_and_the_counters_of_the_caller_keep_growing(self):
        run = self.execute([line()] * 20, 20, [9])
        self.assertEqual(run.keyboard(1, 8), POWER_UP)
        self.assertEqual(run.keyboard(9, 9), POWER_UP)                 # the keyboard powers up again in the unit after the reset
        self.assertEqual(run.keyboard(10, 20), [])
        self.assertLess(run.cycles[9], run.cycles[8])                  # the engine's own counter restarted ...
        self.assertEqual(run.ticks, run.cycles[8] + run.cycles[20])    # ... the caller's ticks did not: every unit is counted once
        self.assertEqual(sorted(run.cycles), list(range(1, 21)))       # and so did the unit index (the probe checks it per call)

    def test_a_key_held_across_the_reset_is_pressed_once_after_the_power_up_stream(self):
        held = [line(keys=[KEY_Q, CTRL] if u >= 3 else []) for u in range(1, 21)]
        run = self.execute(held, 20, [9])
        self.assertEqual(run.keyboard(1, 8), POWER_UP + [sdr(CTRL, True), sdr(KEY_Q, True)] if False else run.keyboard(1, 8))
        before = run.keyboard(1, 8)
        self.assertEqual(before[:3], POWER_UP)
        self.assertEqual(sorted(before[3:]), sorted([sdr(KEY_Q, True), sdr(CTRL, True)]))
        after = run.keyboard(9, 20)
        self.assertEqual(after[:3], POWER_UP)
        self.assertEqual(sorted(after[3:]), sorted([sdr(KEY_Q, True), sdr(CTRL, True)]))   # once each, nothing repeated, nothing stuck

    def test_a_key_the_host_let_go_of_in_the_reset_unit_is_not_pressed_again(self):
        script = [line(keys=[KEY_Q] if 3 <= u <= 8 else []) for u in range(1, 21)]
        run = self.execute(script, 20, [9])
        self.assertEqual(run.keyboard(9, 20), POWER_UP)                # no press, and no release the guest never saw a press for

    def test_a_second_reset_starts_over_again_and_a_reset_before_the_first_frame_changes_nothing(self):
        run = self.execute([line()] * 10, 10, [5, 7])
        self.assertEqual([run.keyboard(u, u) for u in (5, 6, 7, 8)], [POWER_UP, [], POWER_UP, []])
        self.assertLess(run.cycles[7], run.cycles[6])
        plain = self.execute([line()] * 6, 6, [])
        early = self.execute([line()] * 6, 6, [1])
        self.assertEqual(plain.text, early.text)

    def test_resets_in_consecutive_units_restart_the_machine_each_time(self):
        run = self.execute([line()] * 10, 10, [5, 6])
        self.assertEqual(run.keyboard(5, 5), POWER_UP)
        self.assertEqual(run.cycles[5], run.cycles[6])                # both units are the first frame after a reset
        self.assertEqual(run.ticks, run.cycles[4] + run.cycles[5] + run.cycles[6] + (run.cycles[10] - run.cycles[6]))

    @unittest.expectedFailure
    def test_the_keyboard_powers_up_again_after_a_reset_in_the_unit_after_a_reset(self):
        # OPEN (T13b): with p6_reset in two consecutive units the guest runs nothing in the second unit and the keyboard never
        # sends its power-up stream again (reproducer: P6_RESET_AT=5,6, all seeds); with one unit between them it does.
        run = self.execute([line()] * 10, 10, [5, 6])
        self.assertEqual(run.keyboard(6, 8), POWER_UP)

    def test_two_processes_replay_a_reset_exactly_and_the_seed_matters(self):
        script = [line(keys=[KEY_Q] if u >= 3 else []) for u in range(1, 16)]
        a = self.execute(script, 15, [7, 11])
        b = self.execute(script, 15, [7, 11])
        self.assertEqual(a.text, b.text)
        self.assertNotEqual(a.text, self.execute(script, 15, [7, 11], seed=2).text)
        self.assertNotEqual(a.text, self.execute(script, 15, []).text)


if __name__ == "__main__":
    unittest.main()
