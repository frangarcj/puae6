"""T27b: floppy writes through the libretro core (MED02, LR02, STATE01), run by the minimal frontend.

The Kickstart is the micro-ROM of tests/media-cow/med02.py (it carries its three track buffers and copies them into Chip RAM, since a frontend cannot poke RAM), and the content is
a disk. With puae6_floppy_writes the guest writes tracks to it. The disk file is never touched; the changes live in the machine, in save states and in rewind; they reach a file
only when the content closes and puae6_floppy_save asks for it, by an atomic replace in the save directory, and never during a run, a state load or a run-ahead.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_floppy_write.py"
"""
import hashlib
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/media-cow"))
import test_libretro as lr  # noqa: E402
import med02  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402

FILE = re.compile(r"^file (\S+) (?:missing|size=(\d+) hash=(\w+))$", re.M)
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
SIZE = re.compile(r"^state size=(\d+)", re.M)
RUNS = 320                    # every marker of med02 is reached by unit ~262 (in both standards), the buffers are copied in the first two
WRITES = ("--set", "puae6_floppy_writes=enabled")
KEEP = ("--set", "puae6_floppy_save=on_close")


def sha(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class FloppyWriteTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "med02.rom"
        rom.write_bytes(lr.builder.build(med02.rom_source(embedded=True)))
        cls.roms["med02"] = rom
        cls.a = disk.make_adf(med02.LABEL_A)
        cls.expected = med02.expected_image(cls.a, ["buf2", "buf3"])

    def disk_file(self):
        directory = self.work()
        path = directory / "game.adf"
        path.write_bytes(self.a)
        return path

    def play(self, path, save_dir, *options, runs=RUNS, system=None):
        return self.frontend("med02", "--runs", runs, "--save-dir", save_dir, *options, content=path, system=system or self.system("med02")).stdout

    @staticmethod
    def files(text):
        return {m[1]: (None if m[2] is None else (int(m[2]), m[3])) for m in FILE.finditer(text)}

    @staticmethod
    def saved_files(directory):
        return sorted(p.name for p in Path(directory).iterdir())

    def test_without_the_option_the_disks_are_protected_and_nothing_is_saved(self):
        path, saves = self.disk_file(), self.work()
        text = self.play(path, saves, *KEEP)
        self.assertEqual(self.saved_files(saves), [])
        self.assertEqual(path.read_bytes(), self.a)
        self.assertNotIn("changed", text)

    def test_writes_without_saving_are_reported_and_discarded_and_the_file_is_never_touched(self):
        path, saves = self.disk_file(), self.work()
        text = self.play(path, saves, *WRITES)
        self.assertEqual(self.saved_files(saves), [])
        self.assertEqual(path.read_bytes(), self.a)
        self.assertRegex(text, r"1 disk\(s\) were changed and the changes are discarded: Save Floppy Changes is off")

    def test_the_changes_are_saved_once_when_the_content_closes_and_never_while_it_runs(self):
        path, saves = self.disk_file(), self.work()
        target = saves / lr_name(self.a)
        # The file is not there while the guest writes (runs 100 and 250 are inside and after the writes) nor right before the unload.
        text = self.play(path, saves, *WRITES, *KEEP, "--do", f"100:file:{target}", "--do", f"250:file:{target}", "--do", f"{RUNS}:file:{target}")
        seen = self.files(text)
        self.assertEqual(seen[str(target)], None)
        self.assertEqual(self.saved_files(saves), [target.name])
        self.assertEqual(target.read_bytes(), self.expected)
        self.assertEqual(path.read_bytes(), self.a)
        self.assertRegex(text, r"1 disk\(s\) saved in ")

    def test_rewind_run_ahead_and_state_loads_write_nothing_and_the_saved_disk_is_the_final_timeline(self):
        path, saves = self.disk_file(), self.work()
        target = saves / lr_name(self.a)
        # A state before the writes, a load of it after them (the writes are taken back), runs ahead after every third run, and the same writes happen again.
        text = self.play(path, saves, *WRITES, *KEEP, "--ahead-every", "3", "--do", "20:save:a", "--do", "290:save:before", "--do", "290:load:a", "--do", "290:file:" + str(target),
                         "--do", "290:save:again", runs=RUNS + 280)
        self.assertEqual(self.files(text)[str(target)], None, "a state load wrote a file")
        saves_ = {m[1]: m[5] for m in SAVE.finditer(text)}
        self.assertNotEqual(saves_["before"], saves_["a"])
        self.assertEqual(saves_["again"], saves_["a"], "the state saved right after the load is the state that was loaded")
        self.assertEqual(self.saved_files(saves), [target.name])
        self.assertEqual(target.read_bytes(), self.expected)

    def test_the_next_session_uses_the_saved_copy_and_a_session_without_writes_does_not(self):
        path, saves = self.disk_file(), self.work()
        target = saves / lr_name(self.a)
        self.play(path, saves, *WRITES, *KEEP)
        first = target.stat().st_mtime_ns
        text = self.play(path, saves, *WRITES, *KEEP)
        self.assertIn("uses the changes an earlier session saved in", text)
        # The guest writes the same sectors again: the disk equals what was loaded, so nothing is written.
        self.assertEqual(target.stat().st_mtime_ns, first)
        self.assertEqual(target.read_bytes(), self.expected)
        plain = self.play(path, saves, "--set", "puae6_floppy_save=on_close")
        self.assertNotIn("uses the changes", plain)
        self.assertEqual(target.read_bytes(), self.expected)
        self.assertEqual(path.read_bytes(), self.a)

    def test_a_saved_file_that_is_not_a_disk_is_reported_and_the_original_is_used(self):
        path, saves = self.disk_file(), self.work()
        (saves / lr_name(self.a)).write_bytes(b"not a disk")
        text = self.play(path, saves, *WRITES, *KEEP, runs=10)
        self.assertIn("the saved changes of a disk", text)
        self.assertIn("is not a plain 901120-byte ADF", text)

    def test_the_serialization_size_includes_the_overlay_and_stays_put(self):
        path, saves = self.disk_file(), self.work()
        without = int(SIZE.search(self.play(path, saves, runs=3))[1])
        # The host fails any run in which the size grew, so a long run with writes is the check that the guest's writes do not move it.
        sizes = {int(m[1]) for m in SIZE.finditer(self.play(path, saves, *WRITES, runs=RUNS))}
        self.assertEqual(len(sizes), 1, sizes)
        self.assertEqual(sizes.pop() - without, 2017456)

    def test_the_options_are_offered_with_the_media_category(self):
        out = self.frontend("boot", "--runs", 1).stdout
        self.assertRegex(out, r"option puae6_floppy_writes default=disabled values=disabled\|enabled")
        self.assertRegex(out, r"option puae6_floppy_save default=off values=off\|on_close")


def lr_name(adf):
    """The file the core saves a disk to: puae6- and the first 24 hex digits of the SHA-256 of the disk as its file gave it."""
    return "puae6-" + sha(adf)[:24] + ".adf"


if __name__ == "__main__":
    unittest.main()
