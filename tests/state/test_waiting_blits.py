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


# Original MIT micro-ROM. The list repeatedly writes BLTCON0 then COPJMP1.
# A VBI starts a large D-only blit while the CPU runs an ordinary RAM-free loop.
# This exposes nested Copper waits without changing CPU timing or source policy.
COPPER_CHAIN = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #handler,$6C.L
    MOVE.W #2,$DFF02E.L
    MOVE.L #$0401FFFE,$2000.L
    MOVE.L #$00400100,$2004.L
    MOVE.L #$00880000,$2008.L
    MOVE.L #$2000,$DFF080.L
    MOVE.W #$0100,$DFF040.L
    MOVE.W #0,$DFF042.L
    MOVE.L #$10000,$DFF054.L
    MOVE.W #0,$DFF066.L
    MOVE.W #$82C0,$DFF096.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$FFC0,$DFF058.L
    ADDQ.L #1,D1
    MOVE.W #$0020,$DFF09C.L
    RTE
"""


# Same original MIT program, with an ordinary off/on pair before COPJMP1.
# The off write lets pending waits return; the following on write is not an
# unbounded-wait counterexample merely because the blitter event can be rearmed.
COPPER_DMA_PULSE = COPPER_CHAIN.replace(
    "    MOVE.L #$00880000,$2008.L",
    "    MOVE.L #$00960040,$2008.L\n"
    "    MOVE.L #$00968040,$200C.L\n"
    "    MOVE.L #$00880000,$2010.L",
)


class WaitingBlitsReproducer(ProbeCase):
    ROMS = {"large-blit": (LARGE_BLIT, None, None),
            "copper-chain": (COPPER_CHAIN, None, None),
            "copper-dma-pulse": (COPPER_DMA_PULSE, None, None)}
    MACHINE = "a1200"

    def audio_counts(self, raw_mode=None, scenario="large-blit", units=5):
        command = [str(self.exe), str(self.roms[scenario]), "--machine", self.MACHINE,
                   "--cpu-compatibility", "normal", "--units", str(units), "--every", "1"]
        if raw_mode is not None:
            command += ["--poke-at", "1", f"waiting-blits:{raw_mode}"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, run.returncode, run.stderr[-2000:])
        counts = {int(m[1]): int(m[3]) for line in run.stdout.splitlines()
                  if (m := PCM.match(line))}
        self.assertEqual(set(range(1, units + 1)), set(counts))
        return counts

    def test_production_default_returns_one_pal_unit(self):
        default = self.audio_counts()
        self.assertEqual(default, self.audio_counts(0))
        # PAL at 48 kHz is about 960 frames; allow reset/long-field variation.
        self.assertTrue(all(940 < n < 1000 for n in default.values()), default)

    def test_copper_loop_raw_wait_swallows_multiple_units(self):
        # Regression reproducer, not support for raw waiting_blits. ADR0015
        # explains why the nested Copper path needs a causal decision first.
        control = self.audio_counts(0, "copper-chain", 3)
        raw = self.audio_counts(3, "copper-chain", 3)
        self.assertTrue(all(940 < n < 1000 for n in control.values()), control)
        self.assertEqual(control[1], raw[1])
        self.assertTrue(all(n > 1700 for unit, n in raw.items() if unit > 1), raw)

    def test_unsupported_raw_wait_reproduces_multiple_units_in_one_call(self):
        raw = self.audio_counts(3)
        self.assertTrue(all(n > 1700 for unit, n in raw.items() if unit > 1), raw)

    def test_ordinary_copper_dma_pulse_returns_one_pal_unit(self):
        # This specific sequence is a control for ADR0015's DMA hypothesis,
        # not general support for raw waiting_blits or a bound on nesting.
        raw = self.audio_counts(3, "copper-dma-pulse", 3)
        self.assertTrue(all(940 < n < 1000 for n in raw.values()), raw)


if __name__ == "__main__":
    unittest.main()
