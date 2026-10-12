"""T63a: hard disks through the libretro core (LR01, MED03, DET02), run by the minimal frontend.

The Kickstart is the micro-ROM of tests/hdf/hdf_rom.py (it finds the UAEFS board, takes BeginIO out of uaehf.device and reads and writes blocks of the hard file, leaving what it saw in Chip
RAM, where the frontend peeks at it through the memory map), and the content is an .hdf, an .hdz, or an M3U that mixes floppies and hard disks. The hard file is never touched; the guest's
writes live in the machine (in save states, rewind and run-ahead) and reach a file only when the content closes and puae6_hdf_save asks for it, by an atomic replace in the save
directory, and never during a run, a state load or a run-ahead. With P6_KICKSTART_A1200_ROM (a Kickstart 3.1, read from there, never copied) one test boots a real Kickstart from an RDB disk.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_hdf.py"
"""
import gzip
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import test_libretro as lr  # noqa: E402
from tests.hdf import hdf_image, hdf_rom  # noqa: E402
from tests.hdf.test_block0 import BLOCKS_READ, expected_after_the_guest  # noqa: E402

FILE = re.compile(r"^file (\S+) (?:missing|size=(\d+) hash=(\w+))$", re.M)
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
SIZE = re.compile(r"^state size=(\d+)", re.M)
PEEK = re.compile(r"^peek (0x[0-9a-f]+) (\d+) (\w+)$", re.M)
IMAGES = re.compile(r"^state images count=(\d+) index=(\d+) eject=(\d)$", re.M)
UNITS_13 = int(os.environ.get("P6_HDF_UNITS_13", "700"))     # Kickstart 1.3 gives up on a disk that is not bootable before it gets to the hard disk
RUNS = 14                      # the micro-ROM is done by unit ~6
SAVE_ON = ("--set", "puae6_hdf_save=on_close")
MIB = 1 << 20


def sha(data):
    return hashlib.sha256(data).hexdigest()


def saved_name(image):
    """The file the core saves a hard disk to: puae6- and the first 24 hex digits of the SHA-256 of the disk as its file gave it."""
    return "puae6-" + sha(image)[:24] + ".hdf"


def peeks_at(run):
    """The actions that read what the guest left in Chip RAM, after visible run `run`."""
    out = []
    for address, length in ((hdf_rom.RES, hdf_rom.R_DONE + 4), (hdf_rom.BUF0, 512), (hdf_rom.BUF1, 512), (hdf_rom.BUF3, 512), (hdf_rom.BUF4, 1024)):
        out += ["--do", f"{run}:peek:{address}:{length}"]
    return out


def guest_memory(text, nth=-1):
    """Chip RAM as the peeks showed it (the nth time each range was read), as a bytearray from 0."""
    ram = bytearray(0x8000)
    by_address = {}
    for m in PEEK.finditer(text):
        by_address.setdefault(int(m[1], 16), []).append(bytes.fromhex(m[3]))
    for address, reads in by_address.items():
        data = reads[nth]
        ram[address:address + len(data)] = data
    return ram


def seen(ram):
    """What the micro-ROM recorded (tests/hdf/test_block0.seen over a RAM image)."""
    res = hdf_rom.RES
    long = lambda at: struct.unpack(">L", ram[res + at:res + at + 4])[0]
    return dict(er_type=ram[res + hdf_rom.R_TYPE], diag_d0=long(hdf_rom.R_DIAG_D0), begin_io=long(hdf_rom.R_BEGINIO), open_error=ram[res + hdf_rom.R_OPEN],
                errors=list(ram[res + hdf_rom.R_ERR:res + hdf_rom.R_ERR + hdf_rom.REQUESTS]),
                actual=[long(hdf_rom.R_ACTUAL + 4 * n) for n in range(hdf_rom.REQUESTS)], done=long(hdf_rom.R_DONE) == hdf_rom.DONE)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class HardFileTests(lr.LibretroHarness):
    def frontend(self, rom="boot", *options, **keywords):
        return super().frontend(rom, *lr.hard_disk_on_the_a500(options), **keywords)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "hdf.rom"
        rom.write_bytes(hdf_rom.rom(read_offset_block=BLOCKS_READ))
        cls.roms["hdf"] = rom
        loop = Path(cls.tmp.name) / "hdfloop.rom"
        loop.write_bytes(hdf_rom.loop_rom())
        cls.roms["hdfloop"] = loop
        cls.images = {"rdb": hdf_image.rdb_image(), "plain": hdf_image.plain_partition()}

    def hdf_file(self, image, name="disk.hdf"):
        path = self.work() / name
        path.write_bytes(image)
        return path

    def play(self, content, save_dir=None, *options, runs=RUNS, system=None, expect=0, peek=True, rom="hdf"):
        extra = ["--save-dir", save_dir] if save_dir else []
        return self.frontend(rom, "--runs", runs, *extra, *options, *(peeks_at(runs) if peek else []), content=content, system=system or self.system(rom), expect=expect).stdout

    @staticmethod
    def saved_files(directory):
        return sorted(p.name for p in Path(directory).iterdir())

    @staticmethod
    def files(text):
        return {m[1]: (None if m[2] is None else (int(m[2]), m[3])) for m in FILE.finditer(text)}

    def assert_the_guest_read(self, image, text):
        guest = seen(ram := guest_memory(text))
        self.assertTrue(guest["done"], guest)
        self.assertEqual(guest["er_type"] >> 4, 0xD, "the UAEFS board announces a Zorro II board with a ROM")
        self.assertEqual(guest["begin_io"] >> 16, 0xF0)
        self.assertEqual(guest["open_error"], 0)
        self.assertEqual(guest["errors"][0:2], [0, 0])
        self.assertEqual(guest["actual"][0:2], [512, 512])
        self.assertEqual(bytes(ram[hdf_rom.BUF0:hdf_rom.BUF0 + 512]), image[0:512], "block 0 is the first 512 bytes of the file")
        offset = 512 * BLOCKS_READ
        self.assertEqual(bytes(ram[hdf_rom.BUF1:hdf_rom.BUF1 + 512]), image[offset:offset + 512])
        self.assertEqual(guest["errors"][2], 0xFC, "a read beyond the end is IOERR_BADLENGTH")
        self.assertEqual(guest["errors"][3:7], [0, 0, 0, 0], "the writes and their read-backs succeed")
        self.assertEqual(bytes(ram[hdf_rom.BUF3:hdf_rom.BUF3 + 512]), image[0:512], "block 3 reads back what was written there")
        self.assertEqual(bytes(ram[hdf_rom.BUF4:hdf_rom.BUF4 + 1024]), image[0:512] + image[offset:offset + 512])

    # --- mounting -------------------------------------------------------------------------------------------------------------------------

    def test_an_hdf_with_an_rdb_and_a_plain_partition_mount_and_the_guest_reads_and_writes_them(self):
        for name, image in self.images.items():
            with self.subTest(image=name):
                path, saves = self.hdf_file(image), self.work()
                text = self.play(path, saves)
                self.assert_the_guest_read(image, text)
                self.assertRegex(text, r"hard disk DH0 \(.*disk\.hdf\): \d+ blocks" + (" with a Rigid Disk Block" if name == "rdb" else ", one partition"))
                self.assertEqual(path.read_bytes(), image, "the file is never touched")
                self.assertEqual(self.saved_files(saves), [])
                self.assertRegex(text, r"1 hard disk\(s\) were changed and the changes are discarded: Save Hard Disk Changes is off")

    def test_an_hdz_is_the_same_disk_as_its_hdf_and_is_decoded_in_memory(self):
        image = self.images["rdb"]
        plain, zipped = self.hdf_file(image), self.hdf_file(b"", "disk.hdz")
        zipped.write_bytes(gzip.compress(image, 9, mtime=0))
        a, b = self.play(plain), self.play(zipped)
        self.assert_the_guest_read(image, b)
        self.assertEqual(guest_memory(a), guest_memory(b))
        self.assertEqual(re.search(r"medium 0 sha256=(\w+)", a)[1], re.search(r"medium 0 sha256=(\w+)", b)[1], "the identity is that of the image, not of the container")
        self.assertEqual(re.search(r"medium 0 sha256=(\w+)", a)[1], sha(image))

    def test_an_hdz_that_is_a_zip_of_one_hdf_is_read_too_and_a_zip_of_two_is_refused(self):
        image = self.images["plain"]
        zipped = self.work() / "disk.hdz"
        with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("__MACOSX/._disk.hdf", b"fork")
            archive.writestr("games/Disk.HDF", image)
            archive.writestr("readme.txt", b"text")
        text = self.play(zipped)
        self.assert_the_guest_read(image, text)
        self.assertEqual(re.search(r"medium 0 sha256=(\w+)", text)[1], sha(image), "the identity is that of the image, not of the container")
        two = self.work() / "two.hdz"
        with zipfile.ZipFile(two, "w") as archive:
            archive.writestr("a.hdf", image)
            archive.writestr("b.hdf", self.images["rdb"])
        out = self.play(two, None, expect=3, peek=False)
        self.assertRegex(out, r"the ZIP file holds 2 hard files")

    def test_the_hard_file_is_found_in_the_playlist_with_floppies_and_disk_control_does_not_list_it(self):
        image = self.images["plain"]
        directory = self.work()
        (directory / "a.adf").write_bytes(lr.adf("a.adf"))
        (directory / "b.adf").write_bytes(lr.adf("b.adf"))
        (directory / "dh0.hdf").write_bytes(image)
        (directory / "list.m3u").write_text("a.adf|First\ndh0.hdf|Disk\nb.adf|Second\n")
        text = self.play(directory / "list.m3u", None, "--do", "5:images")
        self.assert_the_guest_read(image, text)
        self.assertEqual(IMAGES.search(text).groups(), ("2", "0", "0"), "two floppies in the list, the hard disk is not one of them")
        self.assertRegex(text, r"(?m)^state image 1 path=.*/b\.adf label=Second")
        self.assertRegex(text, r"hard disk DH0: medium 2 sha256=" + sha(image), "the hard disk follows the floppies in the manifest")
        # a list of hard disks only: all of them mounted at once, none in Disk Control
        (directory / "all.m3u").write_text("dh0.hdf\ndh1.hdf\n")
        (directory / "dh1.hdf").write_bytes(self.images["rdb"])
        both = self.play(directory / "all.m3u", None, "--do", "5:images")
        self.assertRegex(both, r"hard disk DH0 \(.*dh0\.hdf\)")
        self.assertRegex(both, r"hard disk DH1 \(.*dh1\.hdf\): \d+ blocks with a Rigid Disk Block")
        self.assertEqual(IMAGES.search(both).groups(), ("0", "0", "0"))
        self.assert_the_guest_read(image, both)

    def test_a_disk_can_still_be_swapped_in_a_playlist_that_also_has_a_hard_disk(self):
        directory = self.work()
        (directory / "a.adf").write_bytes(lr.adf("a.adf"))
        (directory / "b.adf").write_bytes(lr.adf("b.adf"))
        (directory / "dh0.hdf").write_bytes(self.images["plain"])
        (directory / "list.m3u").write_text("dh0.hdf\na.adf\nb.adf\n")
        text = self.play(directory / "list.m3u", None, "--do", "3:disk:eject", "--do", "3:disk:index=1", "--do", "3:disk:insert", "--do", "8:images")
        self.assertIn("DF0: medium 0 inserted", text)
        self.assertIn("DF0: medium 1 inserted", text)
        self.assertEqual(IMAGES.search(text).groups(), ("2", "1", "0"))

    def test_what_the_core_refuses_it_refuses_with_a_message_and_loads_nothing(self):
        image = self.images["plain"]
        for name, data, reason in (
                ("empty.hdf", b"", "a whole number of 512-byte blocks"),
                ("odd.hdf", b"\0" * 513, "a whole number of 512-byte blocks"),
                ("half.hdf", b"\0" * 256, "a whole number of 512-byte blocks"),
                ("notgzip.hdz", image, "must be a gzip file"),
                ("cut.hdz", gzip.compress(image)[:-9], "truncated"),
                ("badcrc.hdz", gzip.compress(image)[:-8] + b"\0\0\0\0" + gzip.compress(image)[-4:], "CRC")):
            with self.subTest(name):
                out = self.play(self.hdf_file(data, name), None, expect=3, peek=False)
                self.assertRegex(out, reason)
                self.assertRegex(out, re.escape(name))

    def test_a_gzip_bomb_and_an_oversized_file_are_refused_at_the_cap_without_the_allocation(self):
        cap = 128 * MIB
        path = self.work() / "bomb.hdz"
        with open(path, "wb") as out:
            body = zlib.compressobj(9, zlib.DEFLATED, 31)
            for _ in range(cap // MIB + 1):                      # one MiB past the cap, a few hundred KB of file
                out.write(body.compress(bytes(MIB)))
            out.write(body.flush())
        self.assertLess(path.stat().st_size, 2 * MIB)
        out = self.play(path, None, expect=3, peek=False)
        self.assertRegex(out, r"decompresses to more than it declares or than the core reads \(more than the core reads\)")
        big = self.work() / "big.hdf"
        with open(big, "wb") as file:                            # sparse: nothing is written to the disk
            file.truncate(cap + 512)
        out = self.play(big, None, expect=3, peek=False)
        self.assertRegex(out, r"is larger than the \d+ bytes of hard disk this core reads")

    def test_more_than_four_hard_disks_and_the_same_disk_twice_are_refused(self):
        directory = self.work()
        for n in range(5):
            (directory / f"d{n}.hdf").write_bytes(hdf_image.ffs_partition(64 + n, "D%d" % n))
        (directory / "five.m3u").write_text("".join(f"d{n}.hdf\n" for n in range(5)))
        out = self.play(directory / "five.m3u", None, expect=3, peek=False)
        self.assertRegex(out, r"the core mounts at most 4 hard disks")
        (directory / "dup.m3u").write_text("d0.hdf\nd1.hdf\nd0.hdf\n")
        out = self.play(directory / "dup.m3u", None, expect=3, peek=False)
        self.assertRegex(out, r"same bytes as an earlier hard disk")
        (directory / "four.m3u").write_text("".join(f"d{n}.hdf\n" for n in range(4)))
        out = self.play(directory / "four.m3u", None, peek=False)
        self.assertRegex(out, r"hard disk DH3 \(.*d3\.hdf\)")

    def test_the_options_and_the_extensions_are_offered(self):
        out = self.frontend("boot", "--runs", 1).stdout
        self.assertRegex(out, r"option puae6_hdf_save default=off values=off\|on_close")

    # --- the writes ------------------------------------------------------------------------------------------------------------------------

    def test_the_changes_are_saved_once_when_the_content_closes_and_never_while_it_runs(self):
        for name, image in self.images.items():
            with self.subTest(image=name):
                path, saves = self.hdf_file(image), self.work()
                target = saves / saved_name(image)
                text = self.play(path, saves, *SAVE_ON, "--do", f"3:file:{target}", "--do", f"9:file:{target}", "--do", f"{RUNS}:file:{target}")
                self.assertEqual(set(self.files(text).values()), {None}, "the file is not there while the content runs")
                self.assertEqual(self.saved_files(saves), [target.name], "no temporary file is left")
                self.assertEqual(target.read_bytes(), expected_after_the_guest(image), "the base with the overlay over it")
                self.assertEqual(path.read_bytes(), image)
                self.assertRegex(text, r"1 hard disk\(s\) saved in ")
                self.assert_the_guest_read(image, text)

    def test_the_next_session_uses_the_saved_copy_and_a_session_without_the_option_does_not(self):
        image = self.images["rdb"]
        path, saves = self.hdf_file(image), self.work()
        target = saves / saved_name(image)
        self.play(path, saves, *SAVE_ON)
        first = target.stat().st_mtime_ns
        text = self.play(path, saves, *SAVE_ON)
        self.assertIn("the hard disk uses the changes an earlier session saved in", text)
        # The guest writes the same blocks again: the disk equals what was loaded, so nothing is written.
        self.assertEqual(target.stat().st_mtime_ns, first)
        self.assertEqual(target.read_bytes(), expected_after_the_guest(image))
        plain = self.play(path, saves)
        self.assertNotIn("uses the changes", plain)
        self.assertEqual(target.read_bytes(), expected_after_the_guest(image))
        self.assertEqual(path.read_bytes(), image)

    def test_a_saved_file_of_the_wrong_size_is_reported_and_the_original_is_used(self):
        image = self.images["plain"]
        path, saves = self.hdf_file(image), self.work()
        (saves / saved_name(image)).write_bytes(b"\0" * 1024)
        text = self.play(path, saves, *SAVE_ON, runs=10, peek=False)
        self.assertIn("the saved changes of a hard disk", text)
        self.assertIn("does not have the size of the hard disk it was saved from", text)

    def test_a_failed_save_leaves_the_old_file_and_says_so(self):
        image = self.images["plain"]
        path, saves = self.hdf_file(image), self.work()
        target = saves / saved_name(image)
        target.write_bytes(b"old")
        os_dir = saves / "x"
        os_dir.mkdir()
        os.chmod(saves, 0o500)                                 # the temporary file cannot be created next to the old one
        try:
            if os.access(saves, os.W_OK):
                self.skipTest("this account can write to a read-only directory")
            text = self.play(path, saves, *SAVE_ON, runs=10, peek=False)
        finally:
            os.chmod(saves, 0o700)
        self.assertIn("the changes were not saved", text)
        self.assertEqual(target.read_bytes(), b"old")

    def test_without_a_save_directory_the_changes_are_discarded_and_said_so(self):
        text = self.play(self.hdf_file(self.images["plain"]), None, *SAVE_ON, runs=10, peek=False)
        self.assertRegex(text, r"the frontend has no save directory: the changes to the hard disks are discarded")

    # --- states, rewind and run-ahead -----------------------------------------------------------------------------------------------

    def test_a_state_takes_the_writes_back_and_the_disk_saved_is_the_final_timeline(self):
        # The loop program writes two blocks across a chunk boundary in every unit, a new chunk each time (hdf_rom.loop_program), so every state holds another overlay.
        image = self.images["plain"]
        path = self.hdf_file(image)
        plain_saves = self.work()
        plain = self.play(path, plain_saves, *SAVE_ON, "--do", "14:save:at14", runs=14, peek=False, rom="hdfloop")
        target = plain_saves / saved_name(image)
        self.assertNotEqual(target.read_bytes(), image)
        saves = self.work()
        # A state at unit 3, one at unit 12; the load of the first at run 13 takes the writes of units 4 to 12 back, and the loop writes them again.
        text = self.play(path, saves, *SAVE_ON, "--do", "3:save:before", "--do", "12:save:after", "--do", "13:load:before", "--do", "13:file:" + str(saves / saved_name(image)),
                         "--do", "24:save:again", runs=24, peek=False, rom="hdfloop")
        self.assertEqual(self.files(text)[str(saves / saved_name(image))], None, "a state load wrote a file")
        saved = {m[1]: m[5] for m in SAVE.finditer(text)}
        self.assertNotEqual(saved["before"], saved["after"], "the state carries the overlay")
        self.assertTrue(all(m[3] == "1" for m in LOAD.finditer(text)))
        # 24 - 13 = 11 runs after the load of the state at unit 3: the machine is at unit 14, as in a plain run of 14, and its overlay is that run's.
        self.assertEqual(saved["again"], {m[1]: m[5] for m in SAVE.finditer(plain)}["at14"], "the machine after the load is the machine of the plain run")
        self.assertEqual((saves / saved_name(image)).read_bytes(), target.read_bytes(), "the disk saved is the one of the final timeline")

    def test_a_disk_the_guest_never_wrote_is_not_exported_or_saved(self):
        image = self.images["plain"]
        path, saves = self.hdf_file(image), self.work()
        text = self.play(path, saves, *SAVE_ON, runs=10, peek=False, rom="boot")
        self.assertEqual(self.saved_files(saves), [])
        self.assertNotIn("hard disk(s)", text)

    def test_rewind_and_run_ahead_change_nothing_on_the_way_and_end_in_the_same_disk(self):
        image = self.images["rdb"]
        path, saves = self.hdf_file(image), self.work()
        target = saves / saved_name(image)
        plain = self.play(path, self.work(), *SAVE_ON)
        text = self.play(path, saves, *SAVE_ON, "--ahead-every", "3", "--do", f"6:file:{target}", "--do", f"{RUNS}:file:{target}")
        self.assertEqual(set(self.files(text).values()), {None}, "run-ahead wrote a file")
        self.assertEqual(target.read_bytes(), expected_after_the_guest(image))
        self.assertEqual(guest_memory(text), guest_memory(plain), "the guest saw the same disk with and without speculative runs")

    def test_the_state_of_one_process_loads_in_another_and_carries_the_writes(self):
        image = self.images["plain"]
        path, saves = self.hdf_file(image), self.work()
        state = self.work() / "x.state"
        first = self.play(path, None, "--do", f"12:savefile:{state}", runs=12)
        self.assertIn("1 hard disk(s) were changed", first)
        # A new process, a new machine at power-on: the state brings the writes in (they are the overlay of the state), and closing the content saves them.
        target = saves / saved_name(image)
        second = self.frontend("hdf", "--runs", 3, "--save-dir", saves, *SAVE_ON, "--do", f"0:loadfile:{state}:pos=12", content=path, system=self.system("hdf")).stdout
        self.assertEqual(LOAD.search(second)[3], "1")
        self.assertEqual(target.read_bytes(), expected_after_the_guest(image))
        # Another disk, or none, is another machine: the state is refused before anything changes.
        for name, content in (("other disk", self.hdf_file(hdf_image.ffs_partition(2048, "Other"))), ("no disk", None)):
            with self.subTest(name):
                refused = self.frontend("hdf", "--runs", 3, "--do", f"0:loadfile:{state}:pos=12", content=content, system=self.system("hdf")).stdout
                self.assertEqual(LOAD.search(refused)[3], "0")

    def test_the_size_of_a_state_follows_the_hard_disk_and_never_grows_while_it_writes(self):
        small, big = hdf_image.ffs_partition(2048, "A"), hdf_image.ffs_partition(4096, "B")
        sizes = []
        for image in (small, big):
            text = self.play(self.hdf_file(image), None, runs=RUNS, peek=False)
            found = {int(m[1]) for m in SIZE.finditer(text)}
            self.assertEqual(len(found), 1, found)                # the host fails any run in which the size grew
            sizes.append(found.pop())
        self.assertEqual(sizes[1] - sizes[0], len(big) - len(small) + 4 * (len(big) - len(small)) // 4096, "the image plus 4 bytes per 4 KiB chunk")

    # --- a real Kickstart (opt-in) --------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "opt-in: P6_KICKSTART_ROM names a Kickstart 1.3 image")
    def test_kickstart_13_mounts_the_partition_of_an_rdb_disk_and_of_a_playlist(self):
        rom = Path(os.environ["P6_KICKSTART_ROM"])
        self.assertTrue(rom.is_file())
        system = self.work()
        (system / lr.KICKSTART_NAMES[0]).symlink_to(rom)                            # never copied
        directory = self.work()
        (directory / "dh0.hdf").write_bytes(hdf_image.rdb_image())
        (directory / "a.adf").write_bytes(lr.adf("a.adf"))
        (directory / "list.m3u").write_text("a.adf\ndh0.hdf\n")
        for content in (directory / "dh0.hdf", directory / "list.m3u"):
            with self.subTest(content=content.name):
                out = self.frontend("boot", "--runs", UNITS_13, "--do", f"{UNITS_13}:peek:0:524288",
                                    "--do", f"{UNITS_13}:peek:{0xC00000}:524288", content=content, system=system).stdout
                text = b"".join(bytes.fromhex(m[3]) for m in PEEK.finditer(out))        # Chip and Slow RAM: exec takes its allocations from the Slow RAM first
                self.assertTrue(b"\x03DH0" in text, "the partition of the RDB reached the parameter packet in guest memory")
                self.assertTrue(b"uaehf.device" in text)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A1200_ROM"), "opt-in: P6_KICKSTART_A1200_ROM names a Kickstart 3.1 image")
    def test_kickstart_31_validates_the_volume_and_the_core_saves_it_when_the_content_closes(self):
        rom = Path(os.environ["P6_KICKSTART_A1200_ROM"])
        self.assertTrue(rom.is_file())
        system = self.work()
        (system / lr.KICKSTART_NAMES_A1200[0]).symlink_to(rom)                    # never copied
        # The root block says the bitmap is not valid: the file system validates the disk when it mounts it and writes the bitmap and the root block.
        image = hdf_image.rdb_image(valid=False)
        path, saves = self.hdf_file(image), self.work()
        text = self.frontend("boot", "--runs", int(os.environ.get("P6_HDF_UNITS", "700")), "--save-dir", saves, *lr.A1200, *SAVE_ON, content=path, system=system).stdout
        saved = saves / saved_name(image)
        self.assertTrue(saved.is_file(), text[-800:])
        exported = saved.read_bytes()
        self.assertEqual(len(exported), len(image))
        root = 64 + (15 * 64 - 1 + 2) // 2
        self.assertNotEqual(exported[root * 512:(root + 1) * 512], image[root * 512:(root + 1) * 512])
        self.assertEqual(struct.unpack(">L", exported[root * 512 + 4 * 78:root * 512 + 4 * 78 + 4])[0], 0xFFFFFFFF, "the bitmap is valid again")
        self.assertEqual(path.read_bytes(), image)


# --- the owner's Workbench 2.05 as an .hdf (T63, opt-in) ----------------------------------------------------------------------------------------------------------------
# tests/hdf/test_workbench_hdf.py explains why the bytes of the Workbench 2.05 ADF are a valid hard file (one 32-sector partition, root block at 880): here the core is given that
# .hdf as the content (an A600 whose Kickstart 2.05 comes from the owner's zip, read in memory and written to a temporary directory outside the repository that goes away at the end;
# no hash, byte or copy is kept anywhere) and boots it from DH0 to the AmigaDOS window of the disk's own startup script, the same last picture the floppy reaches. WB 2.05 does not write
# while it boots, so the writes of these tests come from a copy whose volume was not closed (bitmap-valid flag cleared in memory): the file system validates it and rewrites the root block and
# the bitmap, the guest's real writes on a real volume. Those writes live in the machine (states, rewind, run-ahead carry them) and reach the host only by the explicit export on close.
KICKSTART_A600_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_205_ZIP = os.environ.get("P6_WB205_ZIP")
WB_RUNS = 700                  # the disk's script waits for an answer from run ~510; the validation of the unclean volume is done by 600
WB_FLOPPY_RUNS = 2600          # the same screen from DF0 is stable from ~2370


@unittest.skipUnless(lr.TOOLS and KICKSTART_A600_ZIP and WORKBENCH_205_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to boot the Workbench 2.05 from an .hdf")
class WorkbenchHardFileTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from tests.hdf.test_workbench_hdf import unclean
        from tests.step.test_kickstart_a600 import member
        cls.system_dir = Path(cls.tmp.name) / "wb-system"
        cls.system_dir.mkdir()
        (cls.system_dir / lr.KICKSTART_NAMES_A600[0]).write_bytes(member(KICKSTART_A600_ZIP, ".rom", 524288))
        cls.disk = member(WORKBENCH_205_ZIP, ".adf", 901120)
        cls.unclean = unclean(cls.disk)
        cls.files = Path(cls.tmp.name) / "wb-files"
        cls.files.mkdir()
        for name, data in (("clean.hdf", cls.disk), ("unclean.hdf", cls.unclean), ("floppy.adf", cls.disk)):
            (cls.files / name).write_bytes(data)

    def boot(self, name, runs, *options, save_dir=None):
        extra = ["--save-dir", save_dir] if save_dir else []
        return self.frontend("boot", "--runs", runs, *lr.A600, *extra, *options, content=self.files / name, system=self.system_dir).stdout

    @staticmethod
    def last(text):
        return HardFileTests.runs(text)[-1]

    def test_the_hdf_boots_from_dh0_to_the_screen_the_floppy_reaches_and_exports_nothing_it_did_not_write(self):
        saves = self.work()
        text = self.boot("clean.hdf", WB_RUNS, *SAVE_ON, save_dir=saves)
        floppy = self.boot("floppy.adf", WB_FLOPPY_RUNS)
        self.assertRegex(text, r"hard disk DH0 \(.*clean\.hdf\): 1760 blocks, one partition")
        runs = HardFileTests.runs(text)
        self.assertEqual(len(runs), WB_RUNS)
        self.assertEqual(self.last(floppy)["hash"], runs[-1]["hash"], "the last picture is the one the same bytes reach from DF0")
        self.assertEqual(runs[-100]["hash"], runs[-1]["hash"], "the screen is stable")
        self.assertEqual((self.files / "clean.hdf").read_bytes(), self.disk, "the file is never touched")
        self.assertEqual(HardFileTests.saved_files(saves), [], "a boot that wrote nothing exports nothing")
        self.assertNotIn("hard disk(s)", text)

    def test_the_writes_of_the_guest_are_exported_atomically_only_when_the_content_closes(self):
        from tests.hdf.test_workbench_hdf import BITMAP_VALID, ROOT_BLOCK, blocks_changed
        saves = self.work()
        target = saves / saved_name(self.unclean)
        text = self.boot("unclean.hdf", WB_RUNS, *SAVE_ON, "--do", f"{WB_RUNS // 2}:file:{target}", "--do", f"{WB_RUNS}:file:{target}", save_dir=saves)
        self.assertEqual([m[2] for m in FILE.finditer(text)], [None, None], "nothing is written during the run (the file is missing both times it is looked for)")
        self.assertEqual(HardFileTests.saved_files(saves), [target.name])
        exported = target.read_bytes()
        self.assertEqual(blocks_changed(self.unclean, exported), [ROOT_BLOCK, ROOT_BLOCK + 1])
        longs = struct.unpack(">128L", exported[ROOT_BLOCK * 512:(ROOT_BLOCK + 1) * 512])
        self.assertEqual((longs[BITMAP_VALID], sum(longs) & 0xFFFFFFFF), (0xFFFFFFFF, 0), "the volume is valid again")
        self.assertEqual((self.files / "unclean.hdf").read_bytes(), self.unclean, "the file is never touched")
        # Off by default: the changes are reported and discarded.
        discarded = self.work()
        self.assertRegex(self.boot("unclean.hdf", WB_RUNS, save_dir=discarded), r"1 hard disk\(s\) were changed and the changes are discarded")
        self.assertEqual(HardFileTests.saved_files(discarded), [])
        # The next load with the option on uses the saved copy, which is already valid: nothing is rewritten and the screen is the same.
        again = self.boot("unclean.hdf", WB_RUNS, *SAVE_ON, save_dir=saves)
        self.assertIn("the hard disk uses the changes an earlier session saved in", again)
        self.assertEqual(target.read_bytes(), exported)
        self.assertEqual(self.last(again)["hash"], self.last(text)["hash"])

    def test_a_state_taken_before_the_guest_writes_converges_after_it_wrote_and_loads_in_another_process(self):
        EARLY, LATE = 150, 650        # the guest rewrites the root block and the bitmap between units 170 and 190
        saves = self.work()
        target = saves / saved_name(self.unclean)
        # One timeline: states before (EARLY) and after (LATE) the validation, on to WB_RUNS; then the early state is loaded back and the machine runs to where LATE was taken.
        runs = WB_RUNS + (LATE - EARLY)
        text = self.boot("unclean.hdf", runs, "--do", f"{EARLY}:save:early", "--do", f"{LATE}:save:late", "--do", f"{WB_RUNS}:load:early", "--do", f"{runs}:save:again",
                         *SAVE_ON, save_dir=saves)
        saved = {m[1]: m[5] for m in SAVE.finditer(text)}
        self.assertNotEqual(saved["early"], saved["late"], "the writes are part of the state")
        self.assertEqual(saved["again"], saved["late"], "the early state replays to the same machine, writes included")
        self.assertTrue(all(m[3] == "1" for m in LOAD.finditer(text)))
        exported = target.read_bytes()
        # Another process, a new machine at power-on: the late state brings the writes in and closing the content exports them.
        state = self.work() / "late.state"
        self.boot("unclean.hdf", LATE, "--do", f"{LATE}:savefile:{state}")
        other_saves = self.work()
        second = self.boot("unclean.hdf", 20, *SAVE_ON, "--do", f"0:loadfile:{state}:pos={LATE}", save_dir=other_saves)
        self.assertEqual(LOAD.search(second)[3], "1")
        self.assertEqual((other_saves / target.name).read_bytes(), exported, "the state carried the writes; the process exported them")
        # Another disk, or none, is another machine: refused before anything changes.
        refused = self.boot("clean.hdf", 20, "--do", f"0:loadfile:{state}:pos={LATE}")
        self.assertEqual(LOAD.search(refused)[3], "0")

    def test_run_ahead_across_the_validation_changes_nothing_on_the_way_and_ends_in_the_same_disk(self):
        # Run-ahead from the first run: after runs 1, 60, 100 and 120 (inside the window in which the Kickstart 2.05 switches the display on, T15l), 150 (before the guest writes), 190 (just
        # after), 300 and 600, 50 speculative runs each time, then the state goes back. The guest writes inside the speculative window of run 150 and the real timeline writes again when it
        # gets there: what the frontend sees, and the disk the content closes with, are the plain run's.
        saves, plain_saves = self.work(), self.work()
        target = saves / saved_name(self.unclean)
        plain = self.boot("unclean.hdf", WB_RUNS, *SAVE_ON, save_dir=plain_saves)
        ahead = [x for run in (1, 60, 100, 120, 150, 190, 300, 600) for x in ("--ahead", f"{run}:50")]
        text = self.boot("unclean.hdf", WB_RUNS, *SAVE_ON, *ahead, "--do", f"{WB_RUNS // 2}:file:{target}", "--do", f"{WB_RUNS}:file:{target}", save_dir=saves)
        self.assertEqual([m[2] for m in FILE.finditer(text)], [None, None], "run-ahead wrote a file")
        key = lambda r: (r["n"], r["size"], r["hash"], r["unit"], r["ticks"])
        self.assertEqual([key(r) for r in HardFileTests.runs(plain)], [key(r) for r in HardFileTests.runs(text)], "the visible runs are the plain ones")
        self.assertEqual(target.read_bytes(), (plain_saves / target.name).read_bytes())

    def test_run_ahead_after_every_run_of_the_boot_changes_nothing_on_the_way(self):
        # A600 + Kickstart 2.05: a run-ahead of 3 speculative runs after every visible run from the first one, through the window (units ~95-125) in which the display is switched on
        # (T15l: the monitor's no-signal countdown was not in the state, so a state taken inside it drew the picture early and the visible line left the plain one).
        plain = self.boot("clean.hdf", 260)
        text = self.boot("clean.hdf", 260, "--ahead-every", "3")
        key = lambda r: (r["n"], r["size"], r["hash"], r["unit"], r["ticks"])
        self.assertEqual([key(r) for r in HardFileTests.runs(plain)], [key(r) for r in HardFileTests.runs(text)], "the visible runs are the plain ones")

if __name__ == "__main__":
    unittest.main()
