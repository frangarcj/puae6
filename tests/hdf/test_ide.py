"""T56: a guest reads and writes a hard file through the IDE port of Gayle (A600 and A1200), MED02/MED03 for the second controller of the hard files.

The micro-ROM (ide_rom.py) talks to the task file of Gayle's IDE the way a Kickstart does: it reads the Gayle ID, identifies the drive, reads blocks, writes blocks (across the chunk
boundary of the overlay) and asks for a block past the end. The test decodes what the guest stored (ide_rom.RES) from the dump of Chip RAM and compares it with the bytes of the
image, which are built here from the layouts of AmigaDOS alone, and the export of the image (p6_media_export) with what the guest should have written: the base is never touched.
Gayle is switched on only while a hard file is behind it: with the IDE asked for and no hard file the run is, digest by digest, the run without the IDE.
"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.hdf import hdf_image, ide_rom

BLOCKS_READ = ide_rom.BLOCKS_READ


def run(exe, directory, rom, machine="a600", standard="pal", seed=1, units=40, images=(), ide=True, export=False, env=None):
    directory = Path(directory)
    rom_path = directory / "ide.rom"
    rom_path.write_bytes(rom)
    paths = []
    for i, image in enumerate(images):
        path = directory / ("disk%d.hdf" % i)
        path.write_bytes(image)
        paths.append(str(path))
    dump = directory / "chip.bin"
    environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0", **(env or {})}
    environment.pop("P6_PROBE_IDE", None)
    if ide:
        environment["P6_PROBE_IDE"] = "1"
    if export:
        environment["P6_PROBE_EXPORT"] = str(directory / "export.hdf")
    result = subprocess.run([str(exe), str(rom_path), machine, standard, str(seed), str(units), str(dump), *paths],
                            capture_output=True, text=True, timeout=600, env=environment, cwd=directory)
    exported = directory / "export.hdf"
    return result, (dump.read_bytes() if dump.exists() else b""), (exported.read_bytes() if export and exported.exists() else None)


def seen(ram):
    """What the guest recorded, as a dict."""
    res = ide_rom.RES
    return dict(gayle_id=list(ram[res + ide_rom.R_ID:res + ide_rom.R_ID + 8]), identify_status=ram[res + ide_rom.R_IDENTIFY_STATUS], irq_pending=ram[res + ide_rom.R_IRQ_AFTER_IDENTIFY],
                irq_after_status=ram[res + ide_rom.R_IRQ_AFTER_READ_STATUS], past_status=ram[res + ide_rom.R_PAST_STATUS], past_error=ram[res + ide_rom.R_PAST_ERROR],
                done=struct.unpack(">L", ram[res + ide_rom.R_DONE:res + ide_rom.R_DONE + 4])[0] == ide_rom.DONE)


def expected_after_the_guest(image):
    """The image once the micro-ROM has run: block 3 is block 0, and blocks 7 and 8 are blocks 0 and N (written as one request across a chunk boundary)."""
    out = bytearray(image)
    out[3 * 512:4 * 512] = image[0:512]
    out[7 * 512:8 * 512] = image[0:512]
    out[8 * 512:9 * 512] = image[BLOCKS_READ * 512:(BLOCKS_READ + 1) * 512]
    return bytes(out)


def digests(stdout):
    return [line for line in stdout.splitlines() if line.startswith("unit ")]


def swapped(ram, at, count):
    """Words of the IDENTIFY DEVICE sector as the ATA specification numbers them: the Amiga reads the data port byte swapped."""
    return [struct.unpack("<H", ram[at + 2 * k:at + 2 * k + 2])[0] for k in range(count)]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class IdeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_hdf_probe")
        cls.rom = ide_rom.rom()
        cls.rdb = hdf_image.rdb_image(cylinders=64)
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-ide-")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, rom=None, images=None, **kwargs):
        with tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
            return run(self.exe, directory, rom or self.rom, images=(self.rdb,) if images is None else images, **kwargs)

    # --- the controller, on the two machines that have Gayle ------------------------------------------------------------------------------------------------------
    def check_io(self, machine, gayle_id):
        result, ram, exported = self.run_case(machine=machine, export=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        guest = seen(ram)
        self.assertTrue(guest["done"], guest)
        self.assertEqual(guest["gayle_id"], gayle_id, "the ID register of Gayle")
        self.assertEqual(guest["identify_status"] & 0xD1, 0x50, "DRDY and DSC, no BSY, no ERR after IDENTIFY: %02x" % guest["identify_status"])
        self.assertTrue(guest["identify_status"] & 0x08, "IDENTIFY hands the sector over (DRQ)")
        self.assertEqual(guest["irq_pending"], 0x80, "the drive's interrupt shows in the status of Gayle")
        self.assertEqual(guest["irq_after_status"], 0x80, "Gayle keeps the bit until it is written")
        self.assertEqual(guest["past_status"] & 0x01, 0x01, "a read past the end sets ERR")
        self.assertEqual(guest["past_error"], 0x10, "...with IDNF")
        image = self.rdb
        res = ide_rom
        self.assertEqual(ram[res.BUF0:res.BUF0 + 512], image[0:512], "block 0")
        self.assertEqual(ram[res.BUF1:res.BUF1 + 512], image[BLOCKS_READ * 512:(BLOCKS_READ + 1) * 512], "block N")
        self.assertEqual(ram[res.BUF3:res.BUF3 + 512], image[0:512], "block 3 read back after the write")
        self.assertEqual(ram[res.BUF4:res.BUF4 + 1024], image[0:512] + image[BLOCKS_READ * 512:(BLOCKS_READ + 1) * 512], "blocks 7 and 8 read back (across a chunk of the overlay)")
        words = swapped(ram, res.IDBUF, 256)
        size = len(image) // 512
        self.assertEqual((words[61] << 16) | words[60], size, "IDENTIFY: the number of LBA sectors is the size of the file")
        self.assertEqual(exported, expected_after_the_guest(image), "the image as the guest left it: the base with the overlay over it")
        self.assertIn("buffer_unchanged=1 blob_equal=1", result.stdout, "the caller's buffer and the registered base are untouched")

    def test_a600_reads_and_writes_through_gayle(self):
        self.check_io("a600", [0x80, 0x80, 0, 0x80, 0, 0, 0, 0])

    def test_a1200_reads_and_writes_through_gayle(self):
        self.check_io("a1200", [0x80, 0x80, 0, 0x80, 0, 0, 0, 0x80])      # the AGA Gayle answers one more

    def test_two_processes_with_other_environments_agree(self):
        first = self.run_case(env={"TZ": "UTC", "HOME": "/nonexistent", "LANG": "C"})
        second = self.run_case(env={"TZ": "Pacific/Auckland", "HOME": "/tmp", "LANG": "es_ES.UTF-8"})
        self.assertEqual(first[0].returncode, 0, first[0].stdout + first[0].stderr)
        self.assertEqual(digests(first[0].stdout), digests(second[0].stdout))
        self.assertEqual(first[1], second[1], "the RAM of the two runs")

    def test_a_plain_partition_is_given_a_virtual_rdb_like_on_the_uae_controller(self):
        plain = hdf_image.plain_partition()
        result, ram, _ = self.run_case(images=(plain,))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ram[ide_rom.BUF0:ide_rom.BUF0 + 4], b"RDSK", "block 0 of the disk is the RDB the engine makes for a bare partition")
        words = swapped(ram, ide_rom.IDBUF, 256)
        self.assertGreater((words[61] << 16) | words[60], len(plain) // 512, "the disk is the partition and the blocks of its RDB")

    def test_the_master_and_the_slave_and_the_second_channel_are_different_drives(self):
        # four hard files: unit 0 master, 1 slave of the channel of Gayle, 2 master, 3 slave behind the IDE splitter
        images = [hdf_image.rdb_image(cylinders=64, name="DH%d" % n) for n in range(4)]
        self.assertEqual(len({bytes(i) for i in images}), 4)
        for unit, (channel, drive) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
            rom = ide_rom.rom(channel=channel, drive=drive)
            result, ram, _ = self.run_case(rom=rom, images=images)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(seen(ram)["done"], (channel, drive))
            self.assertEqual(ram[ide_rom.BUF0:ide_rom.BUF0 + 512], images[unit][0:512], "block 0 of drive %d" % unit)

    def test_load_unload_cycles_do_not_grow_the_heap(self):
        # The drives, their sector buffers and the table of units are given back with the image.
        for machine in ("a600", "a1200"):
            with self.subTest(machine=machine):
                result, _, _ = self.run_case(rom=ide_rom.loop_rom(), machine=machine, env={"P6_PROBE_CYCLES": "8"})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                heaps = [int(line.split("heap=")[1]) for line in result.stdout.splitlines() if line.startswith("cycle")]
                self.assertEqual(len(heaps), 8)
                if not heaps[0]:
                    self.skipTest("this C library has no cheap heap statistics")
                self.assertEqual(heaps[3], heaps[-1], heaps)

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_the_same_scenarios_are_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_hdf_probe")
        for machine in ("a600", "a1200"):
            with self.subTest(machine=machine), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                result, ram, _ = run(exe, directory, self.rom, machine=machine, images=(self.rdb,))
                self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-1200:])
                self.assertTrue(seen(ram)["done"])
                self.assertNotIn("runtime error", result.stderr)

    CHAOS_SEEDS = (1, 7, 0x1234ABCD, 0xDEADBEEF, 99, 31337)

    def test_a_guest_that_pokes_at_gayle_at_random_cannot_stop_the_machine_and_the_state_goes_and_comes_back(self):
        # chaos_rom: the registers of both channels, the interrupt registers and what is around them, written and read at random with bytes, words and longs, with the interrupt enabled.
        for machine in ("a600", "a1200"):
            for seed in self.CHAOS_SEEDS:
                with self.subTest(machine=machine, seed=seed):
                    rom = ide_rom.chaos_rom(seed)
                    result, ram, _ = self.run_case(rom=rom, machine=machine, units=60, env={"P6_PROBE_STATE": "1"})
                    self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
                    self.assertIn("state_save=0", result.stdout)
                    self.assertIn("state_load=0 ", result.stdout)
                    again, ram_again, _ = self.run_case(rom=rom, machine=machine, units=60, env={"P6_PROBE_STATE": "1"})
                    self.assertEqual(digests(result.stdout), digests(again.stdout), "the same guest and the same disk give the same machine")
                    self.assertEqual(ram, ram_again)

    # --- Gayle is there only while a hard file is behind it -----------------------------------------------------------------------------------------------------
    def test_ide_without_a_hard_file_is_the_default_machine(self):
        # no hard file: the ID register answers as the A600 without Gayle always did (the custom chip mirror), and the run is the run of the default config
        with_ide = self.run_case(images=(), machine="a600", ide=True)
        without = self.run_case(images=(), machine="a600", ide=False)
        self.assertEqual(with_ide[0].returncode, 0, with_ide[0].stdout + with_ide[0].stderr)
        self.assertEqual(digests(with_ide[0].stdout), digests(without[0].stdout))
        self.assertEqual(with_ide[1], without[1])
        self.assertEqual(seen(with_ide[1])["gayle_id"], seen(without[1])["gayle_id"])
        self.assertNotEqual(seen(with_ide[1])["gayle_id"][:2], [0x80, 0x80], "there is no Gayle ID without Gayle")

    def test_the_other_controller_is_not_changed_by_the_option_existing(self):
        # a hard file on the UAE controller: the same machine as before T56 (no Gayle: the ID register is silent)
        result, ram, _ = self.run_case(images=(self.rdb,), ide=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(seen(ram)["gayle_id"][:2], [0x80, 0x80])

    def test_the_machines_without_gayle_refuse_the_ide(self):
        result, _, _ = self.run_case(machine="a500")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("A600 and the A1200", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
