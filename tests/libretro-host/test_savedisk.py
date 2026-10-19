"""T60: #SAVEDISK format, persistence on close, and speculative timelines."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
import test_floppy_write as floppy
import med02

COMPILER = shutil.which("clang++")


def blank_adf(label="Scores", index=0):
    image = bytearray(901120)
    image[:4] = b"DOS\0"
    root, bitmap = [0] * 128, [0] * 128
    root[0], root[3], root[78], root[79], root[127] = 2, 72, 0xffffffff, 881, 1
    root[107] = root[123] = index
    name = bytes([len(label)]) + label.encode()
    body = bytearray(struct.pack(">128I", *root))
    body[432:432 + len(name)] = name
    struct.pack_into(">I", body, 20, -sum(struct.unpack(">128I", body)) & 0xffffffff)
    image[880 * 512:881 * 512] = body
    for block in set(range(2, 1760)) - {880, 881}:
        bitmap[1 + (block - 2) // 32] |= 1 << ((block - 2) % 32)
    bitmap[0] = -sum(bitmap) & 0xffffffff
    image[881 * 512:882 * 512] = struct.pack(">128I", *bitmap)
    return bytes(image)


@unittest.skipUnless(COMPILER, "clang++ unavailable")
class SaveDiskFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-savedisk-")
        cls.probe = Path(cls.tmp.name) / "probe"
        built = subprocess.run([COMPILER, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-I" + str(lr.ROOT / "references/libretro-api/include"),
                                str(Path(__file__).with_name("savedisk_probe.cpp")), "-o", str(cls.probe)], capture_output=True)
        if built.returncode:
            raise RuntimeError(built.stderr.decode())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_probe(cls, *args, text=b""):
        result = subprocess.run([str(cls.probe), *args], input=text, capture_output=True, check=True,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        if result.stderr:
            raise AssertionError(result.stderr.decode())
        return result.stdout

    def test_directives_are_entries_in_order_including_an_empty_volume_name(self):
        lines = self.run_probe("parse", text=b"\xef\xbb\xbf#EXTM3U\r\na.adf|Game\r#SAVEDISK:Scores\n#SAVEDISK:\r\nb.adf\n").decode().splitlines()
        self.assertEqual(lines[0], "ok 2")
        self.assertEqual([line.split()[:2] for line in lines[1:]], [["0", "0"], ["1", "0"], ["1", "1"], ["0", "0"]])
        self.assertEqual(bytes.fromhex(lines[2].split()[3]), b"Scores")
        self.assertEqual(lines[3].split()[3], "-")
        self.assertTrue(self.run_probe("parse", text=b"#SAVEDISK:\n").startswith(b"ok 1\n"))

    def test_save_disks_share_the_sixteen_disk_limit_and_nul_refusal(self):
        self.assertTrue(self.run_probe("parse", text=b"#SAVEDISK:\n" * 16).startswith(b"ok 16\n"))
        self.assertIn(b"line 17: too many", self.run_probe("parse", text=b"#SAVEDISK:\n" * 17))
        self.assertIn(b"NUL", self.run_probe("parse", text=b"#SAVEDISK:a\0b\n"))

    def test_the_ofs_image_has_a_valid_root_bitmap_fixed_dates_and_distinct_ordinals(self):
        for name, expected in (("", "Save Disk 0"), ("EMPTY", "Save Disk 0"), ("a/b:c", "a_b_c"), ("X" * 40, "X" * 30)):
            with self.subTest(name=name):
                image = self.run_probe("disk", "0", name)
                self.assertEqual(len(image), 901120)
                self.assertEqual(image[:4], b"DOS\0")
                root = image[880 * 512:881 * 512]
                longs = struct.unpack(">128I", root)
                self.assertEqual((longs[0], longs[3], longs[78], longs[79], longs[127]), (2, 72, 0xffffffff, 881, 1))
                self.assertEqual(sum(longs) & 0xffffffff, 0)
                self.assertEqual(root[433:433 + root[432]], expected.encode())
                self.assertEqual(image, blank_adf(expected))
                bitmap = struct.unpack(">128I", image[881 * 512:882 * 512])
                self.assertEqual(sum(bitmap) & 0xffffffff, 0)
                free = {block for block in range(2, 1760) if bitmap[1 + (block - 2) // 32] & (1 << ((block - 2) % 32))}
                self.assertEqual(free, set(range(2, 1760)) - {880, 881})
                self.assertEqual(image, self.run_probe("disk", "0", name))
                self.assertNotEqual(image, self.run_probe("disk", "1", name))


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class SaveDiskFrontendTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "med02.rom"
        rom.write_bytes(lr.builder.build(med02.rom_source(embedded=True)))
        cls.roms["med02"] = rom

    def playlist(self, text="#SAVEDISK:Scores\n"):
        path = self.work() / "game.m3u"
        path.write_text(text)
        return path

    def play(self, path, saves, *args, runs=floppy.RUNS):
        return self.frontend("med02", "--runs", runs, "--save-dir", saves, *args, content=path).stdout

    def test_the_guest_writes_the_save_disk_and_only_closing_persists_it_then_reopening_uses_it(self):
        path, saves = self.playlist(), self.work()
        target = saves / "game.save0.adf"
        original = path.read_bytes()
        text = self.play(path, saves, *floppy.WRITES, *floppy.KEEP, "--ahead-every", 3,
                         "--do", f"100:file:{target}", "--do", f"{floppy.RUNS}:file:{target}")
        self.assertEqual(floppy.FloppyWriteTests.files(text)[str(target)], None)
        self.assertEqual(sorted(p.name for p in saves.iterdir()), [target.name])
        image = target.read_bytes()
        self.assertEqual(image, med02.expected_image(blank_adf(), ["buf2", "buf3"]))
        self.assertEqual(image[880 * 512 + 433:880 * 512 + 439], b"Scores")
        before = target.stat().st_mtime_ns
        text = self.play(path, saves, *floppy.WRITES, *floppy.KEEP)
        self.assertIn("uses the changes an earlier session saved in", text)
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertEqual(target.read_bytes(), image)
        self.assertEqual(path.read_bytes(), original)

    def test_rewinding_before_the_writes_discards_them_and_never_creates_a_save_file(self):
        path, saves = self.playlist(), self.work()
        target = saves / "game.save0.adf"
        text = self.play(path, saves, *floppy.WRITES, *floppy.KEEP, "--ahead-every", 3,
                         "--do", "20:save:early", "--do", "320:load:early", "--do", f"320:file:{target}")
        self.assertEqual(floppy.FloppyWriteTests.files(text)[str(target)], None)
        self.assertEqual(list(saves.iterdir()), [])

    def test_two_identical_labels_are_separate_disks_and_writing_and_saving_remain_opt_in(self):
        path, saves = self.playlist("#SAVEDISK:Scores\n#SAVEDISK:Scores\n"), self.work()
        text = self.play(path, saves, *floppy.WRITES, *floppy.KEEP, runs=2)
        self.assertIn("save disk 0: medium 0", text)
        self.assertIn("save disk 1: medium 1", text)
        self.assertEqual(list(saves.iterdir()), [])
        text = self.play(path, saves, *floppy.KEEP)
        self.assertIn("#SAVEDISK disks are read-only", text)
        self.assertEqual(list(saves.iterdir()), [])
        self.play(path, saves, *floppy.WRITES)
        self.assertEqual(list(saves.iterdir()), [])

    def test_selecting_the_second_save_disk_writes_only_its_own_file(self):
        path, saves = self.playlist("#SAVEDISK:Scores\n#SAVEDISK:Scores\n"), self.work()
        self.play(path, saves, *floppy.WRITES, *floppy.KEEP, "--initial", f"1:{path}#SAVEDISK:1")
        self.assertEqual(sorted(p.name for p in saves.iterdir()), ["game.save1.adf"])
        self.assertEqual((saves / "game.save1.adf").read_bytes(), med02.expected_image(blank_adf(index=1), ["buf2", "buf3"]))


if __name__ == "__main__":
    unittest.main()
