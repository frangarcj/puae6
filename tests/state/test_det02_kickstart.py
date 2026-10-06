"""T15i3: DET02 on the owner's Kickstart 1.3, at three boundaries (opt-in: P6_KICKSTART_ROM names the image; it is never copied, hashed or committed).

The boundaries are the ones the closure of the devices uses: unit 60 (the CIAs untouched), 107 (the keyboard handshake in flight) and 140 (the steady state with the LED lit and the
TOD counting), with the mouse and keys of KICKSTART_INPUT. The machinery, the modes and what a divergence table is are in tests/state/test_det02.py.
"""
import os
import unittest

from tests.state import test_det02 as d
from tests.state import test_state_input as input_tests

SCENARIOS = {
    "kickstart": ("kickstart", input_tests.KICKSTART_INPUT, (60,), (d.SAME, d.SAME_BEAM, d.NEW, d.NEW_BEAM)),
    "kickstart-handshake": ("kickstart", input_tests.KICKSTART_INPUT, (107,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", input_tests.KICKSTART_INPUT, (140,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
}

# Where DET02 diverges on the Kickstart (the causes are those of tests/state/test_det02.py).
DIVERGENCES = {
    "kickstart@60/same-image": d.row("beam", 0, "chipset.beam"),
    "kickstart@60/same-image-beam-set": d.row("audio-phase", 1, "bridge pcm"),
    "kickstart@60/new-process": d.row("beam", 0, "chipset.beam"),
    "kickstart@60/new-process-beam-set": d.row("frame-phase", 1, "cpu scheduler rtc cia input video bridge chipset.beam chipset.counters pcm"),
    "kickstart-handshake@107/same-image-beam-set": d.row("kickstart-mixed", 1, "video bridge pcm"),
    "kickstart-handshake@107/reset-image-beam-set": d.row("kickstart-mixed", 1, "cpu scheduler ram rtc video bridge chipset.beam chipset.clxdat"),
    "kickstart-handshake@107/new-process": d.row("beam", 0, "chipset.beam"),
    "kickstart-steady@140/same-image-beam-set": d.row("kickstart-mixed", 1, "ram bridge pcm"),
    "kickstart-steady@140/reset-image-beam-set": d.row("kickstart-mixed", 1, "ram video bridge pcm"),
    "kickstart-steady@140/new-process": d.row("beam", 0, "chipset.beam"),
}


@unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
class Det02KickstartTests(d.Det02Case):
    pass


d.populate(Det02KickstartTests, SCENARIOS, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
