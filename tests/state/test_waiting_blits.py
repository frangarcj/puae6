"""T77b reproducer: synchronous upstream MMIO waits can swallow video boundaries.

This documents why waiting_blits stays disabled in production. It does not certify
that enabling it is supported; a resumable instruction implementation is still needed.
"""
import subprocess
import unittest

from tests.state.harness import PCM, ProbeCase


LARGE_BLIT = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.W #$8240,$DFF096.L
loop:
    MOVE.W #$0100,$DFF040.L
    MOVE.W #0,$DFF042.L
    MOVE.L #$2000,$DFF054.L
    MOVE.W #0,$DFF066.L
    MOVE.W #$FFC0,$DFF058.L
    MOVE.W #$0100,$DFF040.L
    BRA.S loop
"""


class WaitingBlitsReproducer(ProbeCase):
    ROMS = {"large-blit": (LARGE_BLIT, None, None)}
    MACHINE = "a1200"

    def audio_counts(self, raw_mode=None):
        command = [str(self.exe), str(self.roms["large-blit"]), "--machine", self.MACHINE,
                   "--cpu-compatibility", "normal", "--units", "5", "--every", "1"]
        if raw_mode is not None:
            command += ["--poke-at", "1", f"waiting-blits:{raw_mode}"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, run.returncode, run.stderr[-2000:])
        counts = {int(m[1]): int(m[3]) for line in run.stdout.splitlines()
                  if (m := PCM.match(line))}
        self.assertEqual(set(range(1, 6)), set(counts))
        return counts

    def test_production_default_returns_one_pal_unit(self):
        default = self.audio_counts()
        self.assertEqual(default, self.audio_counts(0))
        # PAL at 48 kHz is about 960 frames; allow reset/long-field variation.
        self.assertTrue(all(940 < n < 1000 for n in default.values()), default)

    def test_unsupported_raw_wait_reproduces_multiple_units_in_one_call(self):
        raw = self.audio_counts(3)
        self.assertTrue(all(n > 1700 for unit, n in raw.items() if unit > 1), raw)


if __name__ == "__main__":
    unittest.main()
