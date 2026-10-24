"""T50: DET02 on the A500 OG, A500+, A1000, A2000 OG and A2000 (micro-ROMs, no Kickstart), and the owner's Kickstart 1.3 on the three machines that run it (opt-in).

The machinery, the modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here: a scenario that diverges on a machine and not on the A500 has to be a
reproducer and an attribution, not a looser digest (DIVERGENCES below, empty). One class per machine, so that the parallel runner puts them on different workers. Each runs the programs
that exercise what the machine adds to the A500: the CIA TOD (counting the vertical sync or, on the A1000 and the A2000s, the mains: the program of tests/scenarios/machines/machines.py that
times the ticks), the clock, the unmapped space, the 1 MiB of Chip RAM (the A500+ and the A2000: "busy-high", the A600's program with every pointer above 512 KiB), the ECS Agnus, the A1000 chips.

Opt-in with the owner's firmware (a path in an environment variable, read-only, nothing copied, hashed or recorded):
    P6_KICKSTART_ROM       the Kickstart 1.3 (A500, A1000, A2000: the same 256 KiB image), as in tests/state/test_det02_kickstart.py
    P6_GAME_ADF            (optional) the game disk of the "kickstart-disk" scenarios, as there
"""
import os
import unittest

from tests.scenarios.machines import machines as M
from tests.state import test_det02 as d
from tests.state import test_state_input as input_tests
from tests.state.test_det02_a600 import HIGH
from tests.state import test_state_upstream as upstream

CORE_ONLY = (d.SAME_BEAM, d.NEW)

BASE = {
    "vsync": ("vsync", [], (3,), d.CORE),
    "copper_irq": ("copper_irq", [], (2,), d.CORE),
    "tone": ("tone", [], (3,), d.CORE),
    "busy": ("busy", [], (2, 5), d.CORE),
    "busy-disk": ("busy", "disk", (8,), d.CORE),
    "stop": ("stop", [], (3,), d.SHAPES),
    "clock": ("clock", [], (4,), d.SHAPES),
    "cias": ("cias", [], (8,), d.SHAPES),
    "long_frame": ("long_frame", [], (1, 12), d.CORE),
    "interlaced": ("interlaced", [], (4, 9), d.CORE),
    # The TOD program polls the CIA-A TOD for 24 ticks and logs the E-clock timer: states inside the wait, in PAL and in NTSC.
    "tod": ("tod", [], (4, 9), d.CORE),
    "tod-ntsc": ("tod", ["--ntsc"], (6,), d.CORE),
}
HIGH_RAM = {
    "busy-high": ("busy-high", [], (2, 5), d.CORE),
    "busy-high-disk": ("busy-high", "disk", (8,), (d.SAME_BEAM, d.NEW)),
}

# Where DET02 diverges on these machines and not on the A500 (the causes are those of tests/state/test_det02.py): none.
DIVERGENCES = {}

ROMS = {
    **d.Det02Tests.ROMS,
    "busy-high": (upstream.BUSY, None, HIGH),
    "tod": (M.tod_source(), None, None),
}


class MachineCase(d.Det02Case):
    ROMS = ROMS
    SCENARIOS = BASE


def make(machine, scenarios):
    cls = type(f"Det02{machine.upper()}Tests", (MachineCase,), {"MACHINE": machine})
    d.populate(cls, scenarios, DIVERGENCES)
    return cls


Det02A500OGTests = make("a500og", BASE)
Det02A500PlusTests = make("a500plus", {**BASE, **HIGH_RAM})
Det02A1000Tests = make("a1000", BASE)
Det02A2000OGTests = make("a2000og", BASE)
Det02A2000Tests = make("a2000", {**BASE, **HIGH_RAM})

KICKSTART = os.environ.get("P6_KICKSTART_ROM")
KICKSTART_SCENARIOS = {
    "kickstart": ("kickstart", input_tests.KICKSTART_INPUT, (60,), (d.SAME, d.SAME_BEAM, d.NEW, d.NEW_BEAM)),
    "kickstart-handshake": ("kickstart", input_tests.KICKSTART_INPUT, (107,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", input_tests.KICKSTART_INPUT, (140,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    # the system up to the first screen: the CIA TOD of the mains machines counts through it, the clock of the A2000 is read by the Kickstart
    "kickstart-late": ("kickstart", [], (200, 400), (d.SAME_BEAM, d.NEW)),
}


GAME = os.environ.get("P6_GAME_ADF")
KICKSTART_DISK_SCENARIOS = {
    # The Kickstart loads the disk mounted in DF0 at boot: motor, seeks, DMA in flight and the controller's own latches at boundaries inside the load.
    "kickstart-disk": ("kickstart", "game", (150, 300, 500), (d.SAME_BEAM, d.NEW)),
}


def kickstart_case(machine):
    @unittest.skipUnless(KICKSTART, "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3")
    class Case(d.Det02Case):
        MACHINE = machine

    @unittest.skipUnless(KICKSTART and GAME, "BLOCKED: set P6_KICKSTART_ROM and P6_GAME_ADF to an authorized Kickstart 1.3 and a game disk")
    class DiskCase(d.Det02Case):
        MACHINE = machine

        def arguments(self, extra):
            if extra == "game":
                return ["--adf", GAME, "--event-at", "0", "0", "0", "1"]
            return super().arguments(extra)

    d.populate(Case, KICKSTART_SCENARIOS, DIVERGENCES)
    d.populate(DiskCase, KICKSTART_DISK_SCENARIOS, DIVERGENCES)
    Case.__name__ = Case.__qualname__ = f"Det02Kickstart13{machine.upper()}Tests"
    DiskCase.__name__ = DiskCase.__qualname__ = f"Det02Kickstart13Disk{machine.upper()}Tests"
    return Case, DiskCase


Det02Kickstart13A500OGTests, Det02Kickstart13DiskA500OGTests = kickstart_case("a500og")
Det02Kickstart13A1000Tests, Det02Kickstart13DiskA1000Tests = kickstart_case("a1000")
Det02Kickstart13A2000OGTests, Det02Kickstart13DiskA2000OGTests = kickstart_case("a2000og")


if __name__ == "__main__":
    unittest.main()
