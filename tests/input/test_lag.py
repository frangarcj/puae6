"""IN01, lag: P6_FRAME_LAG follows what the emulated CPU reads, and nothing else.

tests/fixtures/microrom/input_rotate.s reads one input resource per VERTB (or none) and logs a tag for it. A unit is a
lag unit exactly when the guest read none of JOY0DAT, JOY1DAT, POT0DAT, POT1DAT, POTGOR, CIA-A PRA or the keyboard
byte in it; the expectation is computed from the guest's own log, not from the flag.
"""
import os
from pathlib import Path
import re
import tempfile
import unittest

from tests.input.test_input import FIXTURES, JOYSTICK, MOUSE, Run, TOOLS, builder, configure_trace_tree, line, ESC, KEY_Q
from tests.step.test_step import FRAME, frames_of, run_probe

NONE_TAG = 0xA4
LAG = 1


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class LagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-lag-")
        cls.rom = Path(cls.tmp.name) / "input_rotate.rom"
        cls.rom.write_bytes(builder.build((FIXTURES / "input_rotate.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frames(self, script, units, standard="pal", env=None):
        with tempfile.TemporaryDirectory(prefix="p6-lag-run-") as directory:
            path = Path(directory) / "script.txt"
            path.write_text("\n".join(script) + "\n")
            code, out = run_probe(self.exe, self.rom, units, "frames", 1, timeout=600, extra=[standard],
                                  env={"P6_INPUT_SCRIPT": str(path), "P6_DUMP_LOG": "1", **(env or {})})
        self.assertEqual(code, 0, out[-500:])
        logged = {int(m[1]): [int(w, 16) for w in m[2].split()] for m in re.finditer(r"^logu (\d+):(.*)$", out, re.M)}
        return frames_of(out), logged, out

    @staticmethod
    def expected_lag(words):
        """A unit is lag when the only tags it logged are 'nothing' and no keyboard record appeared."""
        tags, i = [], 0
        while i < len(words):
            if words[i] == 0xBB00:
                return False
            tags.append(words[i])
            i += 1 if words[i] == NONE_TAG else 2
        return all(t == NONE_TAG for t in tags)

    def test_the_flag_follows_the_guests_reads_in_pal_and_ntsc(self):
        keys = lambda u: [ESC] if u in (30, 31) else [KEY_Q] if u == 45 else []
        script = [line(keys=keys(u)) for u in range(1, 71)]
        for standard in ("pal", "ntsc"):
            rows, logged, _ = self.frames(script, 70, standard)
            lag = [bool(r[17] & LAG) for r in rows]
            want = [self.expected_lag(logged[u]) for u in range(1, 71)]
            with self.subTest(standard=standard):
                self.assertEqual(lag[1:], want[1:])                 # unit 1 also holds the boot
                self.assertGreater(sum(lag), 5)
                self.assertGreater(lag.count(False), 40)
                self.assertFalse(lag[29] or lag[44], "a unit that read the keyboard byte cannot be lag")

    def test_what_the_host_sends_does_not_make_a_unit_lag_or_not(self):
        quiet = [line() for _ in range(40)]
        busy = [line(p0=(MOUSE, 1, (u % 5) - 2, 3), p1=(JOYSTICK, 8 + (u % 4), 0, 0)) for u in range(1, 41)]
        quiet_rows, quiet_log, _ = self.frames(quiet, 40)
        busy_rows, busy_log, _ = self.frames(busy, 40)
        for u in range(2, 41):
            if self.expected_lag(busy_log[u]) and self.expected_lag(quiet_log[u]):
                self.assertTrue(busy_rows[u - 1][17] & LAG, u)     # moving the mouse is not a poll by the guest

    def test_the_poll_mask_is_write_only_for_the_machine(self):
        script = [line(p0=(MOUSE, 0, 1, 1)) for _ in range(20)]
        _, _, plain = self.frames(script, 20)
        _, _, noisy = self.frames(script, 20, env={"P6_POLL_NOISE": "1"})
        self.assertEqual(plain.splitlines()[-1], noisy.splitlines()[-1])       # final state digest and ticks
        digest = lambda text: re.findall(r"digest=([0-9a-f]{16})", text)
        self.assertEqual(digest(plain), digest(noisy))


if __name__ == "__main__":
    unittest.main()
