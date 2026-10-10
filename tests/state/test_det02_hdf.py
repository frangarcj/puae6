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


if __name__ == "__main__":
    unittest.main()
