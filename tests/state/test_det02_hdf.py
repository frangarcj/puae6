"""T29c: DET02 with a hard file mounted: a state taken with the UAE controller busy (the UAEFS board and the trap area, the units of the file system and of uaehf.device, the EXTER queue
and the request line of its interrupt) and with writes in the overlay (a new 4 KiB chunk every unit), loaded back, runs on as the original did, subsystem by subsystem. The machinery, the
modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here.

"hdfloop" is the micro-ROM of tests/hdf/hdf_rom.py (loop_program): it boots the filesystem ROM as the T29a program does and then, at every vertical blank, writes two blocks across a chunk
boundary of the overlay, reads them back, reads block 0 of the base and takes one step of the EXTER interrupt (exter_int_helper), so that the interrupt is in progress, and the queue holds
replies, at most boundaries. The "ram" digest of the probe carries the section of the state (p6_state::visit_hdf: the controller and the overlay as the state writes them), and the guest's
own buffers are in Chip RAM, so a divergence of the controller shows both ways.

Opt-in with the owner's firmware (environment only, read in place; no copy, no hash, no byte of it kept anywhere): P6_KICKSTART_ROM (1.3, A500) and P6_KICKSTART_A1200_ROM (3.1, A1200) mount the
RDB partition of the same kind of image and boot from it, at boundaries inside the autoconfig, the mount and the validation of the volume (which writes into the overlay).
"""
import os
from pathlib import Path
import unittest

from tests.hdf import hdf_image, hdf_rom
from tests.state import test_det02 as d

# name -> (rom, extra, units N at which the state is taken, modes). The boot of the micro-ROM is done in the first unit: from unit 2 the handler has the interrupt in different steps.
SCENARIOS = {
    "hdf-loop": ("hdfloop", "hdf", (2, 3, 4, 5, 8), d.CORE),
    "hdf-loop-late": ("hdfloop", "hdf", (13,), d.SHAPES),
}
# Where DET02 diverges with a hard file (the causes are those of tests/state/test_det02.py): none.
DIVERGENCES = {}


class HdfCase(d.Det02Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf = cls.dir / "d.hdf"
        cls.hdf.write_bytes(hdf_image.rdb_image(cylinders=64))

    def arguments(self, extra):
        if extra == "hdf":
            return ["--hdf", str(self.hdf)]
        return super().arguments(extra)


class Det02HdfTests(HdfCase):
    ROMS = {"hdfloop": (hdf_rom.loop_program(), None, None)}


class Det02HdfA600Tests(HdfCase):
    MACHINE = "a600"
    ROMS = {"hdfloop": (hdf_rom.loop_program(), None, None)}


class Det02HdfA1200Tests(HdfCase):
    MACHINE = "a1200"
    ROMS = {"hdfloop": (hdf_rom.loop_program(), None, None)}


d.populate(Det02HdfTests, SCENARIOS, DIVERGENCES)
d.populate(Det02HdfA600Tests, {"hdf-loop": ("hdfloop", "hdf", (3, 5), (d.SAME_BEAM, d.NEW))}, DIVERGENCES)
d.populate(Det02HdfA1200Tests, {"hdf-loop": ("hdfloop", "hdf", (3, 5), (d.SAME_BEAM, d.NEW))}, DIVERGENCES)


# --- the owner's Kickstarts -------------------------------------------------------------------------------------------------------------------------------------
# A hard file with an RDB and an empty FFS volume (valid=False makes the file system validate it when it mounts it, which writes the bitmap and the root block into the overlay).
KICKSTART_13 = os.environ.get("P6_KICKSTART_ROM")
KICKSTART_31 = os.environ.get("P6_KICKSTART_A1200_ROM")
KICKSTART_SCENARIOS = {
    "kickstart-hdf": ("kickstart", "hdf", (80, 120, 200, 320), (d.SAME_BEAM, d.NEW)),
}


class KickstartHdfCase(HdfCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hdf.write_bytes(hdf_image.rdb_image(cylinders=64, valid=False))


@unittest.skipUnless(KICKSTART_13 and Path(KICKSTART_13).is_file(), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3")
class Det02HdfKickstart13Tests(KickstartHdfCase):
    pass


@unittest.skipUnless(KICKSTART_31 and Path(KICKSTART_31).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized Kickstart 3.1 (A1200)")
class Det02HdfKickstart31Tests(KickstartHdfCase):
    MACHINE = "a1200"

    @classmethod
    def firmware(cls):
        return Path(KICKSTART_31)


d.populate(Det02HdfKickstart13Tests, KICKSTART_SCENARIOS, DIVERGENCES)
d.populate(Det02HdfKickstart31Tests, KICKSTART_SCENARIOS, DIVERGENCES)


# --- the owner's Workbench 2.05 as a hard file (T63) ---------------------------------------------------------------------------------------------------------------
# The ADF of Workbench 2.05 (read in memory from the owner's zip, written to a temporary directory outside the repository, never copied, hashed or kept) is, byte for byte, a valid plain
# partition (see tests/hdf/test_workbench_hdf.py): given as a hard file with no floppy in DF0, the A600 boots from DH0. States are taken at boundaries inside the autoconfig, the mount, the
# load of the system by the file system (the EXTER interrupt answering reads) and, with the prompt of the disk's script answered by keys, inside the load that follows the answer to the script's prompt. The guest
# writes nothing in a boot, so what is proved is that the controller state and the (empty) overlay go back the same, which is what DET02 compares.
KICKSTART_205 = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_205 = os.environ.get("P6_WB205_ZIP")
ANSWER_THE_PROMPT = ["--key", "600", "0x0A", "down", "--key", "604", "0x0A", "up", "--key", "620", "0x44", "down", "--key", "624", "0x44", "up"]
WORKBENCH_HDF_SCENARIOS = {
    "workbench-hdf-boot": ("kickstart", "workbench-hdf", (60, 120, 180, 260, 350, 440, 505), (d.SAME_BEAM, d.NEW)),
    "workbench-hdf-screen": ("kickstart", "workbench-hdf-keys", (630, 680, 740), (d.SAME_BEAM, d.NEW)),
    # The volume was not closed (bitmap-valid flag cleared, see tests/hdf/test_workbench_hdf.py): between units 170 and 190 the file system validates it and writes the root block and
    # the bitmap, so these states are taken before (150, 178), just after (190) and well after (300) a real write and carry a real overlay of one 4 KiB chunk.
    "workbench-hdf-unclean": ("kickstart", "workbench-hdf-unclean", (150, 178, 190, 300), (d.SAME_BEAM, d.NEW)),
}


@unittest.skipUnless(KICKSTART_205 and WORKBENCH_205, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to boot the Workbench 2.05 from a hard file")
class Det02HdfWorkbench205Tests(d.Det02Case):
    MACHINE = "a600"

    @classmethod
    def firmware(cls):
        from tests.step.test_kickstart_a600 import member
        rom = cls.dir / "kickstart-2.05.rom"
        rom.write_bytes(member(KICKSTART_205, ".rom", 524288))
        cls.workbench = cls.dir / "workbench-2.05.hdf"
        cls.workbench.write_bytes(member(WORKBENCH_205, ".adf", 901120))
        from tests.hdf.test_workbench_hdf import unclean
        cls.unclean = cls.dir / "workbench-2.05-unclean.hdf"
        cls.unclean.write_bytes(unclean(cls.workbench.read_bytes()))
        return rom

    def arguments(self, extra):
        if extra == "workbench-hdf":
            return ["--hdf", str(self.workbench)]
        if extra == "workbench-hdf-unclean":
            return ["--hdf", str(self.unclean)]
        if extra == "workbench-hdf-keys":
            return ["--hdf", str(self.workbench), *ANSWER_THE_PROMPT]
        return super().arguments(extra)


# Found by T63 and NOT about hard files: the A600 with the Kickstart 2.05 diverges at unit 120 with a floppy in DF0 and with no disk at all too (probed with the A600 scenarios of
# tests/state/test_det02_a600.py at 110, 115, 120, 125 and 130: only 120 fails; 107, 140 and the rest pass). The first unit after the load shows another video digest and another picture,
# the machine is the original's from the second one on. The reproducer is this table (it needs the owner's firmware); the attribution is open (video/picture of the unit in which the
# Kickstart switches the display on), so it is pinned here as an expected failure with its first divergence, not relaxed.
BOOT_120 = (1, ("video", "picture"), "open (not T63)", "A600 Kickstart 2.05 at unit 120, with a floppy and with no disk too: the first unit after the load has another video digest and picture")
WORKBENCH_DIVERGENCES = {f"workbench-hdf-boot@120/{mode}": BOOT_120 for mode in (d.SAME_BEAM, d.NEW)}
d.populate(Det02HdfWorkbench205Tests, WORKBENCH_HDF_SCENARIOS, WORKBENCH_DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
