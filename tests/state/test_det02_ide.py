"""T56: DET02 with a hard file on the IDE port of Gayle: a state taken with the controller busy (a command in progress, a sector half transferred, BSY or DRQ set, the interrupt of the drive
pending or on its way to INTREQ, the registers of Gayle) and with writes in the overlay (a new 4 KiB chunk every pass), loaded back, runs on as the original did, subsystem by subsystem.
The machinery, the modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here.

"ideloop" is the micro-ROM of tests/hdf/ide_rom.py (loop_program): at the level 2 autovector an INT2 handler acknowledges the drive and Gayle, and the main loop writes two blocks (a new chunk
of the overlay every pass), reads them back and reads block 0 of the base, for ever; a pass lasts a few video units, so the boundaries fall inside a command at most times. The "ram" digest
of the probe carries the section of the state (p6_state::visit_hdf: Gayle, the four IDE slots with their sector buffers, and the overlay), and the buffers of the guest are in Chip RAM, so a
divergence of the controller shows both ways.

Opt-in with the owner's Kickstarts (environment only, read in place; no copy, no hash, no byte of it kept anywhere): see tests/state/test_det02_hdf.py for the variables; the IDE scenarios of the
real systems are in tests/step/test_kickstart_ide.py.
"""
import os
from pathlib import Path
import unittest

from tests.hdf import hdf_image, ide_rom
from tests.state import test_det02 as d

# name -> (rom, extra, units N at which the state is taken, modes). The program starts in the first unit. The boundaries fall inside a command at most times (a sector half read or written,
# DRQ set); unit 14 is one where the drive is BSY with its interrupt one line away (irq_delay 1), and units 5 and 36 ones where the interrupt of the drive is pending (irq set).
SCENARIOS = {
    "ide-loop": ("ideloop", "ide", (2, 3, 4, 5, 8, 14, 36), d.CORE),
    "ide-loop-late": ("ideloop", "ide", (13, 21, 36), d.SHAPES),
    # a guest that pokes at the registers of Gayle at random (ide_rom.chaos_program): the state of a drive in whatever condition the random commands left it
    "ide-chaos-1": ("chaos1", "ide", (2, 3, 4, 6, 9, 14, 20), d.CORE),
    "ide-chaos-2": ("chaos2", "ide", (3, 5, 8, 13, 21), d.CORE),
    "ide-chaos-3": ("chaos3", "ide", (2, 4, 7, 11, 18), d.CORE),
}
# Where DET02 diverges with a hard file on the IDE: none.
DIVERGENCES = {}


class IdeCase(d.Det02Case):
    ROMS = {"ideloop": (ide_rom.loop_program(), None, None), "chaos1": (ide_rom.chaos_program(1), None, None), "chaos2": (ide_rom.chaos_program(0x1234ABCD), None, None),
            "chaos3": (ide_rom.chaos_program(0xDEADBEEF), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf = cls.dir / "d.hdf"
        cls.hdf.write_bytes(hdf_image.rdb_image(cylinders=64))

    def arguments(self, extra):
        if extra == "ide":
            return ["--hdf", str(self.hdf), "--hdf-controller", "ide"]
        if extra == "ide-fast":
            # a Zorro II Fast RAM board as well: the autoconfig chain has two cards, the board and the IDE port of Gayle
            return ["--hdf", str(self.hdf), "--hdf-controller", "ide", "--fast", "1024"]
        return super().arguments(extra)


class Det02IdeA600Tests(IdeCase):
    MACHINE = "a600"


class Det02IdeA1200Tests(IdeCase):
    MACHINE = "a1200"


SCENARIOS["ide-fast"] = ("ideloop", "ide-fast", (3, 5, 14, 36), (d.SAME_BEAM, d.NEW))
d.populate(Det02IdeA600Tests, SCENARIOS, DIVERGENCES)
d.populate(Det02IdeA1200Tests, {"ide-loop": ("ideloop", "ide", (3, 5, 14), (d.SAME_BEAM, d.NEW))}, DIVERGENCES)


# --- the owner's Kickstarts and Workbench 2.05 (T56) --------------------------------------------------------------------------------------------------------------
# The ADF of Workbench 2.05 (read in memory from the owner's zip, written to a temporary directory outside the repository, never copied, hashed or kept) is a valid plain partition (see
# tests/hdf/test_workbench_hdf.py): given as a hard file on the IDE, the A600 (Kickstart 2.05) and the A1200 (Kickstart 3.1) boot from DH0. States are taken at boundaries inside the scan of
# the drive (IDENTIFY, INITIALIZE DRIVE PARAMETERS, the reads of the RDB), the mount and the load of the system by the file system, and, with the volume not closed (the file system validates it
# and writes the root block and the bitmap with WRITE SECTORS), just before, at and after a real write through the controller.
KICKSTART_205 = os.environ.get("P6_KICKSTART_A600_ZIP")
KICKSTART_31 = os.environ.get("P6_KICKSTART_A1200_ROM")
WORKBENCH_205 = os.environ.get("P6_WB205_ZIP")
ANSWER_THE_PROMPT = ["--key", "600", "0x0A", "down", "--key", "604", "0x0A", "up", "--key", "620", "0x44", "down", "--key", "624", "0x44", "up"]


class WorkbenchIdeCase(d.Det02Case):
    @classmethod
    def disk(cls):
        from tests.step.test_kickstart_a600 import member
        from tests.hdf.test_workbench_hdf import unclean
        cls.workbench = cls.dir / "workbench-2.05.hdf"
        cls.workbench.write_bytes(member(WORKBENCH_205, ".adf", 901120))
        cls.unclean = cls.dir / "workbench-2.05-unclean.hdf"
        cls.unclean.write_bytes(unclean(cls.workbench.read_bytes()))

    def arguments(self, extra):
        if extra == "workbench-ide":
            return ["--hdf", str(self.workbench), "--hdf-controller", "ide"]
        if extra == "workbench-ide-unclean":
            return ["--hdf", str(self.unclean), "--hdf-controller", "ide"]
        if extra == "workbench-ide-keys":
            return ["--hdf", str(self.workbench), "--hdf-controller", "ide", *ANSWER_THE_PROMPT]
        return super().arguments(extra)


@unittest.skipUnless(KICKSTART_205 and WORKBENCH_205, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to boot the Workbench 2.05 from the IDE of the A600")
class Det02IdeWorkbench205Tests(WorkbenchIdeCase):
    MACHINE = "a600"

    @classmethod
    def firmware(cls):
        from tests.step.test_kickstart_a600 import member
        rom = cls.dir / "kickstart-2.05.rom"
        rom.write_bytes(member(KICKSTART_205, ".rom", 524288))
        cls.disk()
        return rom


@unittest.skipUnless(KICKSTART_31 and Path(KICKSTART_31).is_file() and WORKBENCH_205, "BLOCKED: set P6_KICKSTART_A1200_ROM and P6_WB205_ZIP to boot the Workbench 2.05 from the IDE of the A1200")
class Det02IdeWorkbench31Tests(WorkbenchIdeCase):
    MACHINE = "a1200"

    @classmethod
    def firmware(cls):
        cls.disk()
        return Path(KICKSTART_31)


# The A600 (Kickstart 2.05) writes the root block and the bitmap between the units 200 and 205 and shows the Workbench from unit ~540; the A1200 (3.1) writes between 100 and 125.
WORKBENCH_A600 = {
    "workbench-ide-boot": ("kickstart", "workbench-ide", (30, 60, 120, 180, 260, 350, 440, 505, 560), (d.SAME_BEAM, d.NEW)),
    "workbench-ide-unclean": ("kickstart", "workbench-ide-unclean", (150, 200, 203, 205, 300), (d.SAME_BEAM, d.NEW)),
    "workbench-ide-screen": ("kickstart", "workbench-ide-keys", (630, 680, 740), (d.SAME_BEAM, d.NEW)),
}
WORKBENCH_A1200 = {
    "workbench-ide-boot": ("kickstart", "workbench-ide", (30, 80, 120, 200, 260, 320), (d.SAME_BEAM, d.NEW)),
    "workbench-ide-unclean": ("kickstart", "workbench-ide-unclean", (100, 110, 120, 125, 200), (d.SAME_BEAM, d.NEW)),
}
d.populate(Det02IdeWorkbench205Tests, WORKBENCH_A600, DIVERGENCES)
d.populate(Det02IdeWorkbench31Tests, WORKBENCH_A1200, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
