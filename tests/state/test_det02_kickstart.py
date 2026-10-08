"""T15i3, T15f2, the closure of T15f and T15g: DET02 on the owner's Kickstart 1.3, at three boundaries (opt-in: P6_KICKSTART_ROM names the image; it is never copied, hashed or committed).
T15k: and with a game disk in DF0 (P6_GAME_ADF names the .adf, also opt-in and never copied or hashed) at boundaries inside the load of the disk.

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
DIVERGENCES = {}


# The Kickstart loads the disk mounted in DF0 at boot: motor, seeks, DMA in flight and the controller's own latches at boundaries in the middle of it.
DISK_SCENARIOS = {
    "kickstart-disk": ("kickstart", "game", (150, 200, 300, 500), (d.SAME_BEAM, d.NEW)),
}


@unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
class Det02KickstartTests(d.Det02Case):
    pass


@unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM") and os.environ.get("P6_GAME_ADF"), "BLOCKED: set P6_KICKSTART_ROM and P6_GAME_ADF to an authorized Kickstart image and a disk")
class Det02KickstartDiskTests(d.Det02Case):
    def arguments(self, extra):
        if extra == "game":
            return ["--adf", os.environ["P6_GAME_ADF"], "--event-at", "0", "0", "0", "1"]
        return super().arguments(extra)


d.populate(Det02KickstartTests, SCENARIOS, DIVERGENCES)
d.populate(Det02KickstartDiskTests, DISK_SCENARIOS, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
