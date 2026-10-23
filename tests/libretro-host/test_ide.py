"""T56b: hard disks on the IDE port of Gayle through the libretro core (LR01, MED03, DET02), run by the minimal frontend.

The option is puae6_hdf_controller (reboot class, config/schema/options.json): `ide` puts the hard disks (.hdf, .hdz, a playlist's) on Gayle's IDE of the A600 or the A1200 instead of the UAE
controller. The Kickstart is the micro-ROM of tests/hdf/ide_rom.py under the A600's file name: it reads the Gayle ID, identifies the drive and reads and writes blocks through the
registers, leaving what it saw in Chip RAM, where the frontend peeks at it through the memory map. What is checked: the guest reads and writes the disk (and not the UAE board, which is not
there), the changes reach a file only when the content closes (the same rule as for the UAE controller), states, rewind and run-ahead converge with the controller busy, a state of the
other controller is refused, and the machines without Gayle (the A500) and a load with no hard disk behave as the option says. With P6_KICKSTART_A600_ZIP + P6_WB205_ZIP (read in place,
never copied) one test boots the Workbench 2.05 from an IDE disk to the screen the UAE controller reaches.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_ide.py"
"""
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import test_libretro as lr  # noqa: E402
from tests.hdf import hdf_image, ide_rom  # noqa: E402
from tests.hdf.test_ide import expected_after_the_guest  # noqa: E402

PEEK = re.compile(r"^peek (0x[0-9a-f]+) (\d+) (\w+)$", re.M)
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
FILE = re.compile(r"^file (\S+) (?:missing|size=(\d+) hash=(\w+))$", re.M)
RUNS = 40
IDE = ("--set", "puae6_hdf_controller=ide")
SAVE_ON = ("--set", "puae6_hdf_save=on_close")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def saved_name(image):
    return "puae6-" + sha(image)[:24] + ".hdf"


def guest_memory(text):
    ram = bytearray(0x8000)
    for m in PEEK.finditer(text):
        data = bytes.fromhex(m[3])
        ram[int(m[1], 16):int(m[1], 16) + len(data)] = data
    return ram


def peeks_at(run):
    out = []
    for address, length in ((ide_rom.RES, 32), (ide_rom.BUF0, 512), (ide_rom.BUF1, 512), (ide_rom.BUF3, 512), (ide_rom.BUF4, 1024)):
        out += ["--do", f"{run}:peek:{address}:{length}"]
    return out


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class IdeTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name, rom in (("ide", ide_rom.rom()), ("ideloop", ide_rom.loop_rom()), ("idechaos", ide_rom.chaos_rom(0x1234ABCD))):
            path = Path(cls.tmp.name) / f"{name}.rom"
            path.write_bytes(rom)
            cls.roms[name] = path
        cls.image = hdf_image.rdb_image(cylinders=64)

    def system_a600(self, rom):
        return self.system_for(rom, *lr.KICKSTART_NAMES_A600)

    def hdf_file(self, image, name="disk.hdf"):
        path = self.work() / name
        path.write_bytes(image)
        return path

    def play(self, content, save_dir=None, *options, runs=RUNS, rom="ide", peek=True, machine=lr.A600, controller=IDE, expect=0):
        extra = ["--save-dir", save_dir] if save_dir else []
        return self.frontend(rom, "--runs", runs, *machine, *controller, *extra, *options, *(peeks_at(runs) if peek else []), content=content, system=self.system_a600(rom), expect=expect)

    def test_the_guest_reads_and_writes_the_disk_through_the_registers_of_gayle(self):
        path = self.hdf_file(self.image)
        saves = self.work()
        text = self.play(path, saves, *SAVE_ON, "--do", f"20:file:{saves / saved_name(self.image)}").stdout
        ram = guest_memory(text)
        res = ide_rom.RES
        self.assertEqual(struct.unpack(">L", ram[res + ide_rom.R_DONE:res + ide_rom.R_DONE + 4])[0], ide_rom.DONE)
        self.assertEqual(list(ram[res + ide_rom.R_ID:res + ide_rom.R_ID + 8]), [0x80, 0x80, 0, 0x80, 0, 0, 0, 0], "the ID of Gayle (ECS)")
        self.assertEqual(bytes(ram[ide_rom.BUF0:ide_rom.BUF0 + 512]), self.image[0:512])
        self.assertEqual(bytes(ram[ide_rom.BUF1:ide_rom.BUF1 + 512]), self.image[ide_rom.BLOCKS_READ * 512:(ide_rom.BLOCKS_READ + 1) * 512])
        self.assertEqual(bytes(ram[ide_rom.BUF3:ide_rom.BUF3 + 512]), self.image[0:512], "block 3 read back after the write")
        self.assertEqual(self.files(text)[str(saves / saved_name(self.image))], None, "nothing is written while the content runs")
        self.assertEqual((saves / saved_name(self.image)).read_bytes(), expected_after_the_guest(self.image), "the disk is exported when the content closes")
        self.assertEqual(path.read_bytes(), self.image, "the file the core was given is never touched")

    @staticmethod
    def files(text):
        return {m[1]: (None if m[2] is None else (int(m[2]), m[3])) for m in FILE.finditer(text)}

    def test_the_same_disk_on_the_uae_controller_is_another_machine_and_the_ide_without_a_disk_is_the_default_one(self):
        path = self.hdf_file(self.image)
        # the micro-ROM looks for the board of the UAE controller nowhere: on the IDE it is the Gayle that answers, and with no hard disk there is no Gayle (the ID register reads the custom chip mirror)
        uae = guest_memory(self.play(path, None, controller=(), rom="ide").stdout)
        self.assertNotEqual(list(uae[ide_rom.RES:ide_rom.RES + 2]), [0x80, 0x80], "there is no Gayle on the UAE controller")
        none = guest_memory(self.play(None, None, rom="ide").stdout)
        self.assertNotEqual(list(none[ide_rom.RES:ide_rom.RES + 2]), [0x80, 0x80], "Gayle is switched on only while a hard disk is behind it")

    def test_the_machine_without_gayle_refuses_the_ide_with_the_reason(self):
        path = self.hdf_file(self.image)
        result = self.frontend("ide", "--runs", 3, "--set", "puae6_machine=A500", *IDE, content=path, system=self.system("ide"), expect=3)
        self.assertIn("this machine has no supported IDE port", result.stdout + result.stderr)

    def test_a_state_takes_the_writes_back_with_the_controller_busy_and_loads_in_another_process(self):
        path = self.hdf_file(self.image)
        saves = self.work()
        text = self.play(path, saves, *SAVE_ON, "--do", "7:save:before", "--do", "20:save:after", "--do", "21:load:before", "--do", "34:save:again", runs=34, rom="ideloop", peek=False).stdout
        saved = {m[1]: m[5] for m in SAVE.finditer(text)}
        self.assertNotEqual(saved["before"], saved["after"], "the state carries the controller and the overlay")
        self.assertTrue(all(m[3] == "1" for m in LOAD.finditer(text)))
        state = self.work() / "x.state"
        self.play(path, None, "--do", f"12:savefile:{state}", runs=12, rom="ideloop", peek=False)
        second = self.frontend("ideloop", "--runs", 3, *lr.A600, *IDE, "--do", f"0:loadfile:{state}:pos=12", content=path, system=self.system_a600("ideloop")).stdout
        self.assertEqual(LOAD.search(second)[3], "1")
        # the state of the IDE does not load into the UAE controller, or into a machine with no disk
        for name, controller, content in (("the UAE controller", (), path), ("no disk", IDE, None)):
            with self.subTest(name):
                refused = self.frontend("ideloop", "--runs", 3, *lr.A600, *controller, "--do", f"0:loadfile:{state}:pos=12", content=content, system=self.system_a600("ideloop")).stdout
                self.assertEqual(LOAD.search(refused)[3], "0")

    def test_rewind_and_run_ahead_change_nothing_on_the_way_and_the_random_guest_too(self):
        for rom in ("ideloop", "idechaos"):
            with self.subTest(rom=rom):
                path, saves = self.hdf_file(self.image), self.work()
                plain = self.play(path, self.work(), *SAVE_ON, rom=rom, runs=30, peek=False).stdout
                ahead = self.play(path, saves, *SAVE_ON, "--ahead-every", "3", rom=rom, runs=30, peek=False).stdout
                key = lambda text: [(r["n"], r["size"], r["hash"], r["unit"], r["ticks"]) for r in self.runs(text)]
                self.assertEqual(len(key(plain)), 30)
                self.assertEqual(key(plain), key(ahead), "the visible runs are the plain ones")

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A600_ZIP") and os.environ.get("P6_WB205_ZIP"), "opt-in: P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (the owner's files, read in place)")
    def test_the_workbench_boots_from_the_ide_to_the_screen_the_uae_controller_reaches(self):
        from tests.step.test_kickstart_a600 import member
        files = self.work()
        disk = member(os.environ["P6_WB205_ZIP"], ".adf", 901120)
        (files / "wb.hdf").write_bytes(disk)
        (files / "kick.rom").write_bytes(member(os.environ["P6_KICKSTART_A600_ZIP"], ".rom", 524288))
        system = self.work()
        for name in lr.KICKSTART_NAMES_A600:
            (system / name).write_bytes((files / "kick.rom").read_bytes())
        outputs = {}
        for name, controller in (("ide", IDE), ("uae", ())):
            outputs[name] = self.frontend("boot", "--runs", 700, *lr.A600, *controller, content=files / "wb.hdf", system=system).stdout
        pictures = {name: self.runs(text) for name, text in outputs.items()}
        self.assertEqual(len(pictures["ide"]), 700)
        self.assertEqual(pictures["ide"][-1]["hash"], pictures["uae"][-1]["hash"], "the last picture is the one the UAE controller reaches")
        self.assertEqual(pictures["ide"][-50]["hash"], pictures["ide"][-1]["hash"], "the screen is stable")


if __name__ == "__main__":
    unittest.main()
