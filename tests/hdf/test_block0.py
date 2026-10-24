"""T29a: a guest reads a hard file through uaehf.device (MED03: block 0 identical to the file, reproducible by content, no host file system).

The micro-ROM (hdf_rom.py) finds the UAEFS board by autoconfig, runs its DiagArea, takes BeginIO out of the resident of uaehf.device and issues requests; the test
decodes what the guest stored (hdf_rom.RES) from the dump of Chip RAM and compares it with the bytes of the image, which are built here from the layouts of
AmigaDOS alone. The same ROM and image run in two processes with different environments, and in the three machines. Kickstart-based checks are opt-in
(P6_KICKSTART_ROM, P6_KICKSTART_A1200_ROM): the files are read from where the variable points and never copied, hashed or stored.
"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.hdf import hdf_image, hdf_rom

MACHINES = ("a500", "a600", "a1200")
BLOCKS_READ = 5            # the second read, in blocks


def run(exe, directory, rom, machine="a500", standard="pal", seed=1, units=6, images=(), env=None):
    directory = Path(directory)
    rom_path = directory / "hdf.rom"
    rom_path.write_bytes(rom)
    paths = []
    for i, image in enumerate(images):
        path = directory / ("disk%d.hdf" % i)
        path.write_bytes(image)
        paths.append(str(path))
    dump = directory / "chip.bin"
    environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0", **(env or {})}
    result = subprocess.run([str(exe), str(rom_path), machine, standard, str(seed), str(units), str(dump), *paths],
                            capture_output=True, text=True, timeout=600, env=environment, cwd=directory)
    return result, (dump.read_bytes() if dump.exists() else b"")


def seen(ram):
    """What the guest recorded, as a dict."""
    res = hdf_rom.RES
    long = lambda at: struct.unpack(">L", ram[res + at:res + at + 4])[0]
    return dict(
        er_type=ram[res + hdf_rom.R_TYPE], diag_d0=long(hdf_rom.R_DIAG_D0), begin_io=long(hdf_rom.R_BEGINIO), resident=long(hdf_rom.R_RESIDENT),
        open_error=ram[res + hdf_rom.R_OPEN], errors=list(ram[res + hdf_rom.R_ERR:res + hdf_rom.R_ERR + hdf_rom.REQUESTS]),
        actual=[long(hdf_rom.R_ACTUAL + 4 * n) for n in range(hdf_rom.REQUESTS)], done=long(hdf_rom.R_DONE) == hdf_rom.DONE)


def expected_after_the_guest(image):
    """The image once the micro-ROM has run: block 3 is block 0, and blocks 7 and 8 are blocks 0 and N (written as one request across a chunk boundary)."""
    out = bytearray(image)
    out[3 * 512:4 * 512] = image[0:512]
    out[7 * 512:8 * 512] = image[0:512]
    out[8 * 512:9 * 512] = image[BLOCKS_READ * 512:(BLOCKS_READ + 1) * 512]
    return bytes(out)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class Block0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_hdf_probe")
        cls.rom = hdf_rom.rom(read_offset_block=BLOCKS_READ)
        cls.images = {"rdb": hdf_image.rdb_image(), "plain": hdf_image.plain_partition()}
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-hdf-")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, image, **kwargs):
        with tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
            return run(self.exe, directory, self.rom, images=(image,), **kwargs)

    def assert_reads(self, name, ram):
        image = self.images[name]
        guest = seen(ram)
        self.assertTrue(guest["done"], guest)
        self.assertEqual(guest["er_type"] >> 4, 0xD, "the board announces a Zorro II board with a ROM")
        self.assertEqual(guest["diag_d0"], 1, "filesys_diagentry answered")
        self.assertEqual(guest["begin_io"] >> 16, 0xF0, "BeginIO is in the trap area at $F00000: %08x" % guest["begin_io"])
        self.assertEqual(guest["open_error"], 0)
        self.assertEqual(guest["errors"][0:2], [0, 0], "two reads of a block")
        self.assertEqual(guest["actual"][0:2], [512, 512])
        self.assertEqual(ram[hdf_rom.BUF0:hdf_rom.BUF0 + 512], image[0:512], "block 0 is the first 512 bytes of the file")
        offset = 512 * BLOCKS_READ
        self.assertEqual(ram[hdf_rom.BUF1:hdf_rom.BUF1 + 512], image[offset:offset + 512], "the block at %d is the file's" % offset)
        self.assertEqual(guest["errors"][2], 0xFC, "a read beyond the end is IOERR_BADLENGTH (-4)")
        self.assertEqual(guest["actual"][2], 0)
        self.assertEqual(guest["errors"][3:7], [0, 0, 0, 0], "the writes and their read-backs succeed")
        self.assertEqual(guest["actual"][3:7], [512, 512, 1024, 1024])
        self.assertEqual(ram[hdf_rom.BUF3:hdf_rom.BUF3 + 512], image[0:512], "block 3 reads back what was written there")
        self.assertEqual(ram[hdf_rom.BUF4:hdf_rom.BUF4 + 1024], image[0:512] + image[offset:offset + 512], "two blocks across a chunk boundary read back")
        self.assertEqual(guest["errors"][7], 0xFD, "HD_SCSICMD is IOERR_NOCMD (-3): scsi.cpp is not built")

    def test_the_guest_reads_block_zero_and_another_block_of_the_file_on_all_three_machines(self):
        for machine in MACHINES:
            for name, image in self.images.items():
                with self.subTest(machine=machine, image=name):
                    result, ram = self.run_case(image, machine=machine)
                    self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
                    self.assert_reads(name, ram)
                    self.assertIn("hdf 0 buffer_unchanged=1 blob_equal=1", result.stdout)     # neither the caller's bytes nor the registered base changed

    def test_what_the_guest_wrote_is_in_the_export_and_nowhere_else(self):
        for name, image in self.images.items():
            with self.subTest(image=name), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                out = Path(directory, "export.bin")
                result, ram = run(self.exe, directory, self.rom, images=(image,), env={"P6_PROBE_EXPORT": str(out)})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(out.read_bytes(), expected_after_the_guest(image), "the base with the overlay over it")
                self.assertIn("hdf 0 buffer_unchanged=1 blob_equal=1", result.stdout)          # the caller's bytes and the registered base are still the original
                self.assertIn("export_short=7 written=0", result.stdout)                          # a short buffer is CAPACITY and nothing is written
                self.assertIn("export=0 written=%d" % len(image), result.stdout)
                # blocks 3 and 7 are in chunk 0 and 1: two chunks differ from the base (block 3 and 7 are both in chunk 0; block 8 is in chunk 1)
                self.assertIn("status=0 capacity=%d used=2 medium=2 refused=0" % (len(image) // 4096), result.stdout)

    def test_the_image_is_found_by_content_and_two_processes_agree_on_everything(self):
        first, ram_a = self.run_case(self.images["rdb"], seed=7, env={"TZ": "Pacific/Kiritimati", "HOME": "/nonexistent-a"})
        second, ram_b = self.run_case(self.images["rdb"], seed=7, env={"TZ": "America/St_Johns", "HOME": "/nonexistent-b", "LANG": "tr_TR.UTF-8"})
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout, "every unit, register and digest")
        self.assertEqual(ram_a, ram_b)
        other, ram_c = self.run_case(self.images["plain"], seed=7)
        self.assertNotEqual(other.stdout, first.stdout, "another image is another machine")

    def test_a_refused_hard_file_costs_no_machine(self):
        cases = {"empty": b"", "not a block": b"\0" * 513, "half a block": b"\0" * 256}
        for name, image in cases.items():
            with self.subTest(name):
                result, _ = self.run_case(image)
                self.assertEqual(result.returncode, 3, result.stdout)
                self.assertIn("load refused", result.stdout)
        with tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
            five = [bytes([n]) * 512 for n in range(5)]
            result, _ = run(self.exe, directory, self.rom, images=five)
            self.assertIn("load refused: 7 too many hard files", result.stdout)

    def test_a_session_with_a_hard_file_has_a_state_that_loads_back(self):
        """T29c: the gate that said UNSUPPORTED is gone. The bound holds the controller, the section of every hard file's overlay at its largest (the whole image) and the machine; the state
        the guest left (the micro-ROM wrote blocks 3, 7 and 8: three dirty chunks) is smaller than that and loads back into the same session (tests/state/test_det02_hdf.py has the futures)."""
        result, _ = self.run_case(self.images["plain"], env={"P6_PROBE_STATE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("state_bound=0 ", result.stdout)
        save = next(line for line in result.stdout.splitlines() if line.startswith("state_save=0"))
        written, bound = (int(field.split("=")[1]) for field in save.split()[-2:])
        self.assertLess(written, bound)
        self.assertIn("state_load=0 ", result.stdout)

    def test_load_unload_cycles_do_not_grow_the_heap(self):
        result, _ = self.run_case(self.images["plain"], env={"P6_PROBE_CYCLES": "8"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        heaps = [int(line.split("heap=")[1]) for line in result.stdout.splitlines() if line.startswith("cycle")]
        self.assertEqual(len(heaps), 8)
        if not heaps[0]:
            self.skipTest("this C library has no cheap heap statistics")
        self.assertEqual(heaps[3], heaps[-1], heaps)           # the queue, the trap area, the units and the images are given back with the image

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_the_same_scenarios_are_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_hdf_probe")
        for name, image in self.images.items():
            with self.subTest(image=name), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                result, ram = run(exe, directory, self.rom, images=(image,))
                self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-1200:])
                self.assertNotIn("runtime error", result.stderr)
                self.assert_reads(name, ram)

    def test_the_build_has_no_thread_primitives(self):
        for name in ("ports/portable/threaddep/thread.h", "ports/portable/hostfs_unsupported.cpp", "ports/portable/block/hdf_target.cpp"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for forbidden in ("pthread", "std::thread", "CreateThread", "uae_start_thread", "sched_yield", "sleep_millis(", "usleep"):
                self.assertNotIn(forbidden, text.replace("uae_start_thread*", ""), (name, forbidden))

    def boot_kickstart(self, variable, machine, units, image=None, export=False):
        """Boots the Kickstart that `variable` names (read from there, never copied) with an RDB disk mounted and returns the dump of RAM (and the exported image)."""
        with tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
            disk = Path(directory, "disk.hdf")
            disk.write_bytes(image or self.images["rdb"])
            dump = Path(directory, "ram.bin")
            exported = Path(directory, "export.bin")
            result = subprocess.run([str(self.exe), os.environ[variable], machine, "pal", "1", str(units), str(dump), str(disk)],
                                    capture_output=True, text=True, timeout=1800,
                                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0", **({"P6_PROBE_EXPORT": str(exported)} if export else {})})
            self.assertEqual(result.returncode, 0, result.stdout[-800:] + result.stderr[-800:])
            self.assertIn("halted=0", result.stdout)
            self.assertIn("hdf 0 buffer_unchanged=1 blob_equal=1", result.stdout)
            return (dump.read_bytes(), exported.read_bytes(), result.stdout) if export else dump.read_bytes()

    def assert_the_file_system_validated_the_volume(self, variable, machine, units):
        # The root block of the volume says the bitmap is not valid: Kickstart's file system validates the disk when it mounts it and writes the bitmap and the root block.
        image = hdf_image.rdb_image(valid=False)
        _, exported, stdout = self.boot_kickstart(variable, machine, units, image, export=True)
        changed = [b for b in range(len(image) // 512) if exported[b * 512:(b + 1) * 512] != image[b * 512:(b + 1) * 512]]
        self.assertTrue(changed, "the guest wrote the validated volume back")
        root = 64 + (15 * 64 - 1 + 2) // 2         # block of the root: first block of the partition (cylinder 1) + its middle
        self.assertIn(root, changed, changed)
        self.assertEqual(struct.unpack(">L", exported[root * 512 + 4 * 78:root * 512 + 4 * 78 + 4])[0], 0xFFFFFFFF, "the bitmap is valid again")
        self.assertIn("export=0", stdout)
        return changed

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "opt-in: P6_KICKSTART_ROM names a Kickstart 1.3 image")
    def test_kickstart_13_mounts_the_partition_of_the_hard_file(self):
        ram = self.boot_kickstart("P6_KICKSTART_ROM", "a500", 400)
        self.assertIn(b"\x03DH0", ram, "the partition of the RDB reached the parameter packet in guest memory")
        self.assertIn(b"uaehf.device", ram)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A1200_ROM"), "opt-in: P6_KICKSTART_A1200_ROM names a Kickstart 3.1 image")
    def test_kickstart_31_mounts_the_partition_of_the_hard_file(self):
        ram = self.boot_kickstart("P6_KICKSTART_A1200_ROM", "a1200", 500)
        self.assertIn(b"\x03DH0", ram)
        self.assertIn(b"uaehf.device", ram)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A1200_ROM"), "opt-in: P6_KICKSTART_A1200_ROM names a Kickstart 3.1 image")
    def test_kickstart_31_writes_the_validated_volume_into_the_overlay(self):
        # (Kickstart 1.3 mounts the partition but does not touch the volume until a user does, so there is nothing for it to write in a boot with no disk to run.)
        self.assert_the_file_system_validated_the_volume("P6_KICKSTART_A1200_ROM", "a1200", 700)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "opt-in: P6_KICKSTART_ROM names a Kickstart 1.3 image")
    def test_an_rdb_whose_block_size_is_hostile_is_refused_without_the_allocation(self):
        # rdb_BlockBytes is read from the disk and used to be the size of a buffer: 2 GiB. The partition is not mounted and the machine goes on.
        ram = self.boot_kickstart("P6_KICKSTART_ROM", "a500", 400, hdf_image.rdb_image(rdb_block_bytes=0x7FFFFFFF))
        self.assertNotIn(b"\x03DH0", ram)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A1200_ROM"), "opt-in: P6_KICKSTART_A1200_ROM names a Kickstart 3.1 image")
    def test_a_file_system_header_list_that_loops_ends_at_the_bound(self):
        # With a FileSystem.resource (Kickstart 2.0 and later) the loader walks the headers looking for the partition's DosType: a header that is its own successor
        # kept it in a loop for ever. It stops after 1024 headers and the partition mounts with the file system of the ROM.
        ram = self.boot_kickstart("P6_KICKSTART_A1200_ROM", "a1200", 500, hdf_image.rdb_image(fshd_loop=True))
        self.assertIn(b"\x03DH0", ram)


if __name__ == "__main__":
    unittest.main()
