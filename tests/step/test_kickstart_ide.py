"""T56: the owner's Kickstarts boot the owner's Workbench 2.05 from a hard file on the IDE port of Gayle (opt-in, owner's files only).

    P6_KICKSTART_A600_ZIP   zip that holds the Kickstart 2.05 .rom (A600, 512 KiB)            -> the A600
    P6_KICKSTART_A1200_ROM  the Kickstart 3.1 (r40.68) .rom of the A1200, raw, 512 KiB        -> the A1200
    P6_WB205_ZIP            zip that holds the Workbench 2.05 ADF (Disk 1 of 4, the boot disk)

Nothing of either file is copied into the repository, hashed or recorded: the zip is read in memory and the ROM and the hard file are written to a temporary directory outside the
repository that is removed at the end. What is checked is the shape of what the machine shows and counts.

The hard file is the one of tests/hdf/test_workbench_hdf.py: the bytes of the ADF are a valid plain partition, which the engine makes a virtual RDB for. On the IDE the Kickstart finds Gayle
by its ID register, the drive by IDENTIFY, reads the RDB and mounts the partition: the 2.05 (CHS addressing, the geometry of the drive) and the 3.1 (LBA) both boot from it.

  boot         the last picture is, pixel for pixel, the last picture of the same Kickstart booting the same bytes from the UAE controller (an AmigaDOS window with the output of the startup
               sequence of the disk) and it is not the insert-disk screen of a machine with nothing to boot. The geometry the drive announces covers the RDB of the engine (the 2.05
               answered "read error on disk block 1163" with a geometry that covered the partition alone).
  no hard file the IDE asked for and no hard file given is the machine that has no Gayle: the insert-disk screen, with the same pictures as the default config.
  determinism  same seed, same text for every unit and the same hard file out; the file the host gave is the same after the run.
  writes       a volume that was not closed is validated by the file system through the IDE (the scsi.device of the Kickstart writes the root block and the bitmap with WRITE SECTORS): the
               export is the image with exactly blocks 880 and 881 changed, and the host's file is never written.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from tests.hdf.test_workbench_hdf import BITMAP_VALID, ROOT_BLOCK, blocks_changed, stable_from, unclean
from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.step.test_kickstart_a600 import KICKSTART_ZIP, WORKBENCH_ZIP, last_picture, member, parse

KICKSTART_31 = os.environ.get("P6_KICKSTART_A1200_ROM")
UNITS = 1000
CASES = {"a600": ("A600, Kickstart 2.05", KICKSTART_ZIP), "a1200": ("A1200, Kickstart 3.1", KICKSTART_31)}


def have(machine):
    return bool(WORKBENCH_ZIP and CASES[machine][1] and TOOLS)


class IdeBootTests:
    MACHINE = None

    @classmethod
    def kickstart(cls):
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="p6-ide-boot-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.rom = cls.temp / "kickstart.rom"
        cls.rom.write_bytes(cls.kickstart())
        disk = member(WORKBENCH_ZIP, ".adf", 901120)
        cls.image = disk
        cls.hdf = cls.temp / "workbench.hdf"
        cls.hdf.write_bytes(disk)
        cls.dirty_image = unclean(disk)
        cls.dirty = cls.temp / "unclean.hdf"
        cls.dirty.write_bytes(cls.dirty_image)
        ide = ["--hdf-controller", "ide"]
        jobs = {"ide": (["--hdf", str(cls.hdf), *ide], True), "ide again": (["--hdf", str(cls.hdf), *ide], True), "uae": (["--hdf", str(cls.hdf)], False),
                "unclean": (["--hdf", str(cls.dirty), *ide], True), "empty ide": (ide, False), "empty": ([], False)}
        with ThreadPoolExecutor(2) as pool:
            futures = {name: pool.submit(cls.run_headless, name, *job) for name, job in jobs.items()}
            cls.runs = {name: future.result() for name, future in futures.items()}

    @classmethod
    def run_headless(cls, name, media, export):
        folder = name.replace(" ", "-")
        dump = cls.temp / folder
        dump.mkdir()
        exported = cls.temp / (folder + ".out")
        command = [str(cls.headless), "--run", str(cls.rom), "--machine", cls.MACHINE, "--units", str(UNITS), "--seed", "1", "--dump", str(dump), "--dump-from", str(UNITS), *media]
        if export:
            command += ["--export-hdf", str(exported)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise AssertionError(f"{name}: the runner failed ({result.returncode}): {result.stderr[-400:]}")
        return result.stdout, parse(result.stdout), last_picture(dump), (exported.read_bytes() if exported.exists() else None)

    def test_the_hard_file_on_the_ide_boots_to_the_screen_the_uae_controller_reaches(self):
        _, rows, (width, height, colours, pixels), _ = self.runs["ide"]
        _, uae_rows, (u_width, u_height, u_colours, u_pixels), _ = self.runs["uae"]
        self.assertEqual(len(rows), UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
        self.assertEqual((width, height), (u_width, u_height))
        self.assertEqual(pixels, u_pixels, "the last picture is not the one the same disk reaches from the UAE controller")
        self.assertGreaterEqual(len(colours), 4, "the booted screen has no content")
        self.assertLess(stable_from(rows), UNITS - 100, "the picture was still changing at the end: the boot did not settle")

    def test_without_a_hard_file_the_ide_option_changes_nothing(self):
        # Gayle is switched on only while a hard file is behind it: the machine is the default one, unit by unit and picture by picture, and it stays on the insert-disk screen.
        self.assertEqual(self.runs["empty ide"][0], self.runs["empty"][0])
        self.assertEqual(self.runs["empty ide"][2][3], self.runs["empty"][2][3])
        self.assertNotEqual(self.runs["empty"][2][3], self.runs["ide"][2][3])

    def test_the_same_seed_gives_the_same_units_picture_and_hard_file_and_the_host_file_is_not_touched(self):
        first, second = self.runs["ide"], self.runs["ide again"]
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2][3], second[2][3])
        self.assertEqual(first[3], second[3])
        self.assertEqual(self.hdf.read_bytes(), self.image, "the file the core was given changed")

    def test_a_volume_that_was_not_closed_is_validated_through_the_ide_and_the_writes_go_out_only_by_the_export(self):
        _, rows, (_, _, _, pixels), exported = self.runs["unclean"]
        self.assertEqual(self.runs["ide"][2][3], pixels, "the same screen as the clean volume")
        self.assertEqual(blocks_changed(self.dirty_image, exported), [ROOT_BLOCK, ROOT_BLOCK + 1], "the guest rewrote the root block and the bitmap and nothing else")
        longs = struct.unpack(">128L", exported[ROOT_BLOCK * 512:(ROOT_BLOCK + 1) * 512])
        self.assertEqual((longs[BITMAP_VALID], sum(longs) & 0xFFFFFFFF), (0xFFFFFFFF, 0), "the bitmap is valid again and the checksum of the root block adds up")
        self.assertEqual(exported[:ROOT_BLOCK * 512], self.dirty_image[:ROOT_BLOCK * 512])
        self.assertEqual(self.dirty.read_bytes(), self.dirty_image, "the host file is never written")


@unittest.skipUnless(have("a600"), "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (and have a toolchain) to boot the Workbench from the IDE of the A600")
class A600IdeBootTests(IdeBootTests, unittest.TestCase):
    MACHINE = "a600"

    @classmethod
    def kickstart(cls):
        return member(KICKSTART_ZIP, ".rom", 524288)


@unittest.skipUnless(have("a1200"), "BLOCKED: set P6_KICKSTART_A1200_ROM and P6_WB205_ZIP (and have a toolchain) to boot the Workbench from the IDE of the A1200")
class A1200IdeBootTests(IdeBootTests, unittest.TestCase):
    MACHINE = "a1200"

    @classmethod
    def kickstart(cls):
        return Path(KICKSTART_31).read_bytes()


if __name__ == "__main__":
    unittest.main()
