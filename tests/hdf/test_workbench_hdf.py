"""T63: a hard file with the owner's Workbench 2.05 boots from DH0 on the A600 preset and its writes go out only by the explicit export (opt-in, owner's files only).

    P6_KICKSTART_A600_ZIP  zip that holds the Kickstart 2.05 .rom (A600, 512 KiB)
    P6_WB205_ZIP           zip that holds the Workbench 2.05 ADF (Disk 1 of 4, the boot disk)

Nothing of either file is copied into the repository, hashed or recorded: both zips are read in memory and the ROM and the hard file are written to a temporary directory outside
the repository that is removed at the end. What is checked is the shape of what the machine shows and counts.

The hard file. A hard file without an RDB is mounted by the engine (T29a) as ONE partition of 32 sectors, 1 head and 2 reserved blocks, whose DosType is the first long of block 0. The
Workbench ADF is 901120 bytes = 1760 blocks = 55 cylinders of 32, starts with "DOS\\0" and has its root block at block 880 = (1760 - 1 + 2) / 2, which is where a partition of that size puts
it: the bytes of the disk, as they are, are a valid partition. So "the Workbench hard file" is the ADF read in memory and given to p6_load as a P6_MEDIA_HDF, with NO floppy in DF0:
the Kickstart finds DH0 and boots from it.

  boot       the last picture of that machine is, pixel for pixel, the last picture of the same Kickstart booting the same bytes from DF0 (an AmigaDOS window with the output of the
             disk's own startup sequence), and it is not the insert-disk screen a machine with nothing to boot shows. The hard file arrives there much earlier than the floppy (no
             mechanical drive: no 2400 units of loading), so what is compared is the stable end.
  workbench  the headless runner has no keyboard: the state probe presses 0 and Return at the prompt of that script and the machine leaves the shell for a new, stable picture
             (what the script goes on to load from DH0: the probe only hashes pictures, it is not looked at pixel by pixel).
  determinism same seed, same text for every unit and the same hard file out; the file the host gave is the same after the run.
  writes     p6_media_write_status counts the chunks the guest wrote and the export is the image with exactly those 4 KiB chunks changed (Workbench 2.05 writes nothing while it
             boots, so the count is zero there: what the test asserts is the invariant, not the absence; the write path itself is proven with micro-ROMs in test_block0.py and with
             Kickstart 3.1 in tests/libretro-host/test_hdf.py).
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.step.test_kickstart_a600 import KICKSTART_ZIP, WORKBENCH_ZIP, last_picture, member, parse

CHUNK = 4096
HDF_UNITS = 1000        # the shell script of the disk waits for an answer by unit ~510
FLOPPY_UNITS = 2600     # the same screen from DF0 is stable from unit ~2370
ROOT_BLOCK, BITMAP_VALID = 880, 78       # (1760 - 1 + 2) / 2; long 128 - 50 of the root block
KEY_ZERO, KEY_RETURN = "0x0A", "0x44"
PICTURE = re.compile(r"^picture (\d+) (?:none|\S+ p\d \S+ ([0-9a-f]{64}))$", re.M)
STATUS = re.compile(r"^status (\d+) (\d+) (\d+) capacity=(\d+) used=(\d+) medium=(\d+) ", re.M)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unclean(disk):
    """The disk as AmigaDOS leaves a volume that was not closed properly: the bitmap-valid flag of the root block (long 78) cleared and its checksum redone. The file system
    validates such a volume when it mounts it, which writes the bitmap and the root block back: a real guest write on a real volume (derived in memory from the owner's bytes)."""
    root = ROOT_BLOCK * 512
    longs = list(struct.unpack(">128L", disk[root:root + 512]))
    assert longs[0] == 2 and longs[127] == 1 and longs[BITMAP_VALID] == 0xFFFFFFFF, "block 880 is not the root block of a validated volume"
    longs[BITMAP_VALID], longs[5] = 0, 0
    longs[5] = (-sum(longs)) & 0xFFFFFFFF
    return disk[:root] + struct.pack(">128L", *longs) + disk[root + 512:]


def blocks_changed(before, after):
    return [b for b in range(len(before) // 512) if before[b * 512:(b + 1) * 512] != after[b * 512:(b + 1) * 512]]


def stable_from(rows):
    """Unit at which the picture hash of the last unit was first reached and never left."""
    hashes = [r[6] for r in rows]
    at = len(hashes)
    while at > 0 and hashes[at - 1] == hashes[-1]:
        at -= 1
    return at + 1


@unittest.skipUnless(KICKSTART_ZIP and WORKBENCH_ZIP and TOOLS, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (and have a toolchain) to boot the Workbench from a hard file")
class WorkbenchHardFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="p6-wbhdf-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.probe = build_probe(ROOT / "build/link-audit", False, "p6_state_probe")
        cls.rom = cls.temp / "kickstart.rom"
        cls.rom.write_bytes(member(KICKSTART_ZIP, ".rom", 524288))
        disk = member(WORKBENCH_ZIP, ".adf", 901120)
        assert len(disk) % 512 == 0
        cls.image = disk
        cls.hdf, cls.adf = cls.temp / "workbench.hdf", cls.temp / "workbench.adf"
        cls.hdf.write_bytes(disk)
        cls.adf.write_bytes(disk)
        cls.dirty_image = unclean(disk)
        cls.dirty = cls.temp / "unclean.hdf"
        cls.dirty.write_bytes(cls.dirty_image)
        jobs = {"floppy": (["--disk", str(cls.adf)], FLOPPY_UNITS), "hdf": (["--hdf", str(cls.hdf)], HDF_UNITS),
                "hdf again": (["--hdf", str(cls.hdf)], HDF_UNITS), "unclean": (["--hdf", str(cls.dirty)], HDF_UNITS), "empty": ([], HDF_UNITS)}
        with ThreadPoolExecutor(2) as pool:
            futures = {name: pool.submit(cls.run_headless, name, *job) for name, job in jobs.items()}
            cls.runs = {name: future.result() for name, future in futures.items()}

    @classmethod
    def run_headless(cls, name, media, units):
        folder = name.replace(" ", "-")
        dump = cls.temp / folder
        dump.mkdir()
        exported = cls.temp / (folder + ".out")
        command = [str(cls.headless), "--run", str(cls.rom), "--machine", "a600", "--units", str(units), "--seed", "1", "--dump", str(dump), "--dump-from", str(units), *media]
        if "--hdf" in media:
            command += ["--export-hdf", str(exported)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise AssertionError(f"{name}: the runner failed ({result.returncode}): {result.stderr[-400:]}")
        return result.stdout, parse(result.stdout), last_picture(dump), (exported.read_bytes() if exported.exists() else None)

    # --- boot ---------------------------------------------------------------------------------------------------------------------

    def test_the_hard_file_boots_the_machine_to_the_screen_the_floppy_reaches(self):
        _, rows, (width, height, colours, pixels), _ = self.runs["hdf"]
        _, floppy_rows, (f_width, f_height, f_colours, f_pixels), _ = self.runs["floppy"]
        self.assertEqual(len(rows), HDF_UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
        self.assertEqual((width, height), (f_width, f_height))
        self.assertEqual(pixels, f_pixels, "the last picture is not the one the same disk reaches from DF0")
        self.assertGreaterEqual(len(colours), 4, "the booted screen has no content")
        self.assertEqual(set(colours), set(f_colours))
        self.assertLess(stable_from(rows), HDF_UNITS - 200, "the picture was still changing at the end: the boot did not settle")
        self.assertLess(stable_from(rows), stable_from(floppy_rows), "a hard file has no mechanical drive to wait for")

    def test_without_the_hard_file_the_machine_stays_on_the_insert_disk_screen(self):
        _, rows, (_, _, colours, pixels), _ = self.runs["empty"]
        _, hdf_rows, (_, _, hdf_colours, hdf_pixels), _ = self.runs["hdf"]
        self.assertNotEqual(pixels, hdf_pixels)
        self.assertNotEqual(colours.most_common(1)[0][0], hdf_colours.most_common(1)[0][0], "the dominant colour is that of the insert-disk screen")
        self.assertFalse({r[6] for r in rows[200:]} & {r[6] for r in hdf_rows[-100:]}, "the hard file run shows a picture of the insert-disk run")

    # --- determinism and the host's file ------------------------------------------------------------------------------------------

    def test_the_same_seed_gives_the_same_units_picture_and_hard_file_and_the_host_file_is_not_touched(self):
        first, second = self.runs["hdf"], self.runs["hdf again"]
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2][3], second[2][3])
        self.assertEqual(first[3], second[3])
        self.assertEqual(self.hdf.read_bytes(), self.image, "the file the core was given changed")

    # --- writes: explicit export ----------------------------------------------------------------------------------------------------

    def run_probe(self, units, *extra, hdf=None):
        result = subprocess.run([str(self.probe), str(self.rom), "--machine", "a600", "--units", str(units), "--every", "50", "--hdf", str(hdf or self.hdf), *extra],
                                capture_output=True, text=True, timeout=1800, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        return result.stdout

    def test_the_export_is_the_image_with_exactly_the_chunks_the_guest_wrote(self):
        out = self.temp / "probe.out"
        text = self.run_probe(700, "--status-at", "700", "0", "--export-at", "700", "0", str(out))
        (status,) = STATUS.findall(text)
        _, index, result, capacity, used, medium = map(int, status)
        self.assertEqual((0, 0, -(-len(self.image) // CHUNK)), (index, result, capacity), "the overlay has one chunk for each 4 KiB of the image (the last one short)")
        exported = out.read_bytes()
        self.assertEqual(len(self.image), len(exported))
        changed = [c for c in range(0, len(exported), CHUNK) if exported[c:c + CHUNK] != self.image[c:c + CHUNK]]
        self.assertLessEqual(len(changed), used, "a chunk changed that the overlay does not count")
        self.assertEqual(used, medium)
        self.assertEqual(self.hdf.read_bytes(), self.image)

    def test_the_workbench_screen_loads_from_dh0_after_the_prompt_is_answered(self):
        keys = ["--key", "600", KEY_ZERO, "down", "--key", "604", KEY_ZERO, "up", "--key", "620", KEY_RETURN, "down", "--key", "624", KEY_RETURN, "up"]
        text = self.run_probe(1100, *keys)
        pictures = {int(unit): digest for unit, digest in PICTURE.findall(text)}
        shell, final = pictures[550], pictures[1100]
        self.assertTrue(shell and final)
        self.assertEqual(pictures[1050], final, "the new screen is not stable")
        self.assertNotEqual(shell, final, "the answer to the prompt changed nothing")

    def test_a_volume_that_was_not_closed_is_validated_by_the_guest_and_the_writes_go_out_only_by_the_export(self):
        _, rows, (_, _, _, pixels), exported = self.runs["unclean"]
        self.assertEqual(self.runs["hdf"][2][3], pixels, "the same screen as the clean volume")
        self.assertEqual(blocks_changed(self.dirty_image, exported), [ROOT_BLOCK, ROOT_BLOCK + 1], "the guest rewrote the root block and the bitmap and nothing else")
        longs = struct.unpack(">128L", exported[ROOT_BLOCK * 512:(ROOT_BLOCK + 1) * 512])
        self.assertEqual((longs[BITMAP_VALID], sum(longs) & 0xFFFFFFFF), (0xFFFFFFFF, 0), "the bitmap is valid again and the checksum of the root block adds up")
        self.assertEqual(exported[:ROOT_BLOCK * 512], self.dirty_image[:ROOT_BLOCK * 512])
        self.assertEqual(self.dirty.read_bytes(), self.dirty_image, "the host file is never written")
        out = self.temp / "unclean.out"
        text = self.run_probe(700, "--status-at", "700", "0", "--export-at", "700", "0", str(out), hdf=self.dirty)
        (status,) = STATUS.findall(text)
        used, medium = int(status[4]), int(status[5])
        self.assertEqual((1, 1), (used, medium), "blocks 880 and 881 share the chunk 110: one 4 KiB chunk written")
        self.assertEqual(exported, out.read_bytes(), "the export after 700 units is already the final one")


if __name__ == "__main__":
    unittest.main()
