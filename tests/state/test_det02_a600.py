"""T23b: DET02 on the A600 profile (ECS, 1 MiB Chip, no Slow RAM, no clock chip): the whole table of tests/state/test_det02.py on `--machine a600`, the scenarios that live in the upper
half of Chip RAM, and the owner's Kickstart 2.05 (opt-in).

The machinery, the modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here: a scenario that diverges only on the A600 has to be a reproducer
and an attribution, not a looser digest (DIVERGENCES below, empty). The table is run whole (it takes about as long as the A500's, four minutes, so it is split in two classes that the
parallel runner puts on two workers: the scenarios with a disk, whose horizons are the longest, and the rest). "busy-high" is the A600's own: the same program as "busy" with every
pointer above 512 KiB (where an A500 has Slow RAM and an A600 has Chip), so that the DMA channels, the blitter and the copper work on Chip RAM that the A500 profile does not have.

Opt-in with the owner's firmware (it is read in memory from the zip and written to a temporary directory outside the repository that goes away at the end; no hash, no byte and no copy of
it is kept anywhere):
    P6_KICKSTART_A600_ZIP  zip with the Kickstart 2.05 .rom (A600, 512 KiB)
    P6_WB205_ZIP           zip with the Workbench 2.05 boot disk (.adf); it is mounted in DF0 at boot in the "workbench" scenarios, which are the ones that touch the disk the most
"""
import os
import unittest

from tests.state import test_det02 as d
from tests.state import test_state_input as input_tests
from tests.state import test_state_rtc as rtc_tests
from tests.state import test_state_upstream as upstream
from tests.state.harness import TRACE, USER
from tests.step.test_kickstart_a600 import member

# BUSY with every pointer above 512 KiB: bitplanes, sprite, audio and copper list at $C0000 and up, the blitter between $90000 and $D0000, the disk DMA at $D0000.
HIGH = {
    "#$AAAA5555,$4000.L": "#$AAAA5555,$C4000.L", "#$12345678,$5000.L": "#$12345678,$C5000.L", "#$4000,$DFF0E0.L": "#$C4000,$DFF0E0.L", "#$5000,$DFF0E4.L": "#$C5000,$DFF0E4.L",
    "#$AAAAAAAA,$6000.L": "#$AAAAAAAA,$C6000.L", "#$0F0F0F0F,$6004.L": "#$0F0F0F0F,$C6004.L", "#$00000000,$6008.L": "#$00000000,$C6008.L", "#$6000,$DFF120.L": "#$C6000,$DFF120.L",
    "#$7000,$DFF0A0.L": "#$C7000,$DFF0A0.L", "#$7100,$DFF0B0.L": "#$C7100,$DFF0B0.L", "#$7000,$7000.L": "#$C7000,$C7000.L",
    "#$2000,$DFF080.L": "#$C2000,$DFF080.L", "#$01800123,$2000.L": "#$01800123,$C2000.L", "#$FFFFFFFE,$2004.L": "#$FFFFFFFE,$C2004.L",
    "#$10000,$DFF050.L": "#$90000,$DFF050.L", "#$40000,$DFF054.L": "#$B0000,$DFF054.L", "#$8000,$DFF020.L": "#$D0000,$DFF020.L",
}

# The whole table of the A500 (257 tests) plus the scenarios of the upper half of Chip RAM.
ALL = {
    **d.SCENARIOS,
    "busy-high": ("busy-high", [], (2, 5), d.CORE),
    "busy-high-disk": ("busy-high", "disk", (8,), (d.SAME_BEAM, d.NEW)),
    "blit-high+bitplanes": ("blt-bpl-high", [], (2,), d.CHANNELS),
}
SCENARIOS = {name: scenario for name, scenario in ALL.items() if not isinstance(scenario[1], str)}
DISK_SCENARIOS = {name: scenario for name, scenario in ALL.items() if isinstance(scenario[1], str)}

# Where DET02 diverges on the A600 and not on the A500 (the causes are those of tests/state/test_det02.py): none.
DIVERGENCES = {}

ROMS = {
    **d.Det02Tests.ROMS,
    "busy-high": (upstream.BUSY, None, HIGH),
    "blt-bpl-high": (upstream.BUSY, None, {**HIGH, "#$83FF,$DFF096.L": "#$8340,$DFF096.L"}),
}


class Det02A600Tests(d.Det02Case):
    MACHINE = "a600"
    ROMS = ROMS


class Det02A600DiskTests(d.Det02Case):
    MACHINE = "a600"
    ROMS = ROMS


d.populate(Det02A600Tests, SCENARIOS, DIVERGENCES)
d.populate(Det02A600DiskTests, DISK_SCENARIOS, DIVERGENCES)

KICKSTART_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_ZIP = os.environ.get("P6_WB205_ZIP")

# The Kickstart 2.05 at the boundaries the A500 table uses for the 1.3 (60: the CIAs untouched; 107: the keyboard handshake in flight; 140: steady, with the LED lit and the TOD counting)
# with the mouse and keys of KICKSTART_INPUT, and with the Workbench disk in DF0 from boot (state of the floppy controller in the middle of the load of the system: motor, seek, DMA
# in flight, the disk-ready interrupts) at boundaries inside the boot, with the Workbench shell not up before unit ~1550.
KICKSTART_SCENARIOS = {
    "kickstart": ("kickstart", input_tests.KICKSTART_INPUT, (60,), (d.SAME, d.SAME_BEAM, d.NEW, d.NEW_BEAM)),
    "kickstart-handshake": ("kickstart", input_tests.KICKSTART_INPUT, (107,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", input_tests.KICKSTART_INPUT, (140,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    # T15l: every unit in which the 2.05 switches the display on (80-125), with no medium and no input. The programmed beam changes (the monitor blanks the picture for two units: the
    # no-signal countdown) and BPLCON0 ERSY stops the beam counters for a few units (syncs_stopped); a state taken inside used to give another picture, or another line, after the load.
    "kickstart-window": ("kickstart", [], tuple(range(80, 126)), (d.SAME_BEAM, d.NEW)),
    # ... and after it, up to the first 200 units: 194 is inside a blit of the screen whose D write sits in a pipeline slot (the pointer increment is in the slot, not in a register).
    "kickstart-late": ("kickstart", [], (130, 160, 190, 193, 194, 195, 200), (d.SAME_BEAM, d.NEW)),
    "kickstart-insert-disk": ("kickstart", [], (700,), (d.SAME_BEAM, d.NEW)),       # no disk: the animation of the disk entering the drive, long after the boot
}
WORKBENCH_SCENARIOS = {
    "workbench-boot": ("kickstart", "workbench", (200, 300, 400, 700), (d.SAME_BEAM, d.NEW)),
    # T15l: the same window as "kickstart-window" with the disk in DF0 (the floppy controller selecting the drive while the display is switched on).
    "workbench-window": ("kickstart", "workbench", (95, 105, 112, 115, 118, 120, 122), (d.SAME_BEAM, d.NEW)),
}


class Kickstart205Case(d.Det02Case):
    """The A600 with the owner's Kickstart 2.05 (and, in the subclass, the Workbench 2.05 disk)."""

    MACHINE = "a600"
    workbench = None

    @classmethod
    def firmware(cls):
        rom = cls.dir / "kickstart-2.05.rom"
        rom.write_bytes(member(KICKSTART_ZIP, ".rom", 524288))
        if WORKBENCH_ZIP:
            cls.workbench = cls.dir / "workbench-2.05.adf"
            cls.workbench.write_bytes(member(WORKBENCH_ZIP, ".adf", 901120))
        return rom

    def arguments(self, extra):
        if extra == "workbench":
            return ["--adf", str(self.workbench), "--event-at", "0", "0", "0", "1"]
        return super().arguments(extra)


@unittest.skipUnless(KICKSTART_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP to a zip with an authorized Kickstart 2.05")
class Det02Kickstart205Tests(Kickstart205Case):
    pass


@unittest.skipUnless(KICKSTART_ZIP and WORKBENCH_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to run the Workbench 2.05 boot")
class Det02Workbench205Tests(Kickstart205Case):
    pass


# The Workbench in DF0 at boundaries inside the load of the system. The states taken here used to diverge (T23b found it, T15k fixed it): the floppy controller keeps the last value
# written to CIA-B port B (the drives selected, the step line, the direction, the side) in file statics that no body of upstream's carries, and the first write after a load was judged
# against the ones of the machine that loaded it: a head step lost or made up, shifter bits drawn or not, so the generator of the first unit after the load was not the original's
# (patch 0105, the select word of DSKX). The same 2.05 image on the A500 and the 1.3 with a game disk on the A500 (tests/state/test_det02_kickstart.py) were the same case.
WORKBENCH_DIVERGENCES = {}


@unittest.skipUnless(KICKSTART_ZIP and WORKBENCH_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to run the Workbench 2.05 boot on the A500")
class Det02Workbench205A500Tests(Kickstart205Case):
    MACHINE = "a500"


d.populate(Det02Kickstart205Tests, KICKSTART_SCENARIOS, {})
d.populate(Det02Workbench205Tests, WORKBENCH_SCENARIOS, WORKBENCH_DIVERGENCES)
d.populate(Det02Workbench205A500Tests, WORKBENCH_SCENARIOS, WORKBENCH_DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
