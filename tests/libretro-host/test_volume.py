"""T64a: a directory, an LHA and a ZIP of files are volumes in the libretro core (LR01, MED02, MED03, DET02), run by the minimal frontend.

The core reads the source ONCE, through the frontend's VFS (version 3: stat and the directory calls) or the C library, and hands the machine one FFS hard file built in memory
(adapters/libretro/volume.h; docs/adr/0011-volumes-from-the-host.md). Nothing of the host is mounted: the identity of the medium is the SHA-256 of that image, so the same content from a
directory, an LHA or a ZIP, from any place, with any modification time, is the same medium, with the same states. The micro-ROM of tests/hdf/hdf_rom.py reads and writes the blocks of the image
through uaehf.device; the writes live in the machine (states, rewind, run-ahead) and leave it only when the content closes and puae6_hdf_save asks for it. The source is never written.
With a Kickstart 3.1 (P6_KICKSTART_A1200_ROM) and the free WHDLoad packages (P6_WHDLOAD_LHA, P6_FATALMISSION_LHA) a real game boots from a directory and from an LHA.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_volume.py"
"""
import hashlib
import os
from pathlib import Path
import random
import re
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import test_libretro as lr  # noqa: E402
import test_hdf as hd  # noqa: E402
from tests.hdf import hdf_rom  # noqa: E402
from tests.hdf.test_block0 import BLOCKS_READ, expected_after_the_guest  # noqa: E402
from tests.hdf.volume_support import CONTENT, lha_archive, oracle, whdload_tree, write_directory, zip_file  # noqa: E402
from tests.whdload import lha_fixture as fx  # noqa: E402

SAVE_ON, RUNS, sha, saved_name = hd.SAVE_ON, hd.RUNS, hd.sha, hd.saved_name
MEDIUM = re.compile(r"medium (\d+) sha256=(\w+)")
VOLUME = re.compile(r"volume DH(\d) \((.*?)\): (directory|LHA archive|ZIP file) of (\d+) files and (\d+) directories \((\d+) bytes\), built as a hard disk of (\d+) bytes")
OPENDIR = re.compile(r"^vfs opendir (.*?)( \(failed\))?$", re.M)


def snapshot(root):
    """Every path under `root` with its size and modification time: the proof that the source was only read."""
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in Path(root).rglob("*")) + [(".", Path(root).stat().st_mtime_ns)]


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class VolumeTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name, rom in (("hdf", hdf_rom.rom(read_offset_block=BLOCKS_READ)), ("hdfloop", hdf_rom.loop_rom())):
            path = Path(cls.tmp.name) / f"{name}.rom"
            path.write_bytes(rom)
            cls.roms[name] = path
        cls.image = oracle(CONTENT)

    def tree(self, name="game", times=1_000_000_000):
        return write_directory(self.work() / name, CONTENT, times=times)

    def play(self, content, save_dir=None, *options, runs=RUNS, peek=True, rom="hdf", expect=0):
        return hd.HardFileTests.play(self, content, save_dir, *options, runs=runs, peek=peek, rom=rom, expect=expect)

    def lha(self, content=CONTENT, name="game.lha", **kwargs):
        path = self.work() / name
        path.write_bytes(lha_archive(content, **kwargs))
        return path

    def medium(self, text):
        return MEDIUM.search(text)[2]

    # --- a directory ----------------------------------------------------------------------------------------------------------------------------

    def test_a_directory_is_a_volume_the_guest_reads_and_writes_like_a_hard_disk_and_the_source_is_only_read(self):
        source = self.tree()
        before = snapshot(source)
        saves = self.work()
        text = self.play(source, saves, *SAVE_ON)
        hd.HardFileTests.assert_the_guest_read(self, self.image, text)
        found = VOLUME.search(text)
        self.assertEqual(found.group(1, 3, 4, 5, 6, 7), ("0", "directory", "5", "4", str(sum(len(v) for v in CONTENT.values() if v)), str(len(self.image))))
        self.assertRegex(text, r"hard disk DH0 \(.*game\): \d+ blocks, one partition")
        self.assertEqual(self.medium(text), sha(self.image), "the identity is the image of the content")
        self.assertEqual(snapshot(source), before, "the directory was only read: nothing created, changed or touched")
        self.assertEqual([p.name for p in saves.iterdir()], [saved_name(self.image)])
        self.assertEqual((saves / saved_name(self.image)).read_bytes(), expected_after_the_guest(self.image), "the base with the guest's writes over it")

    def test_the_directory_is_listed_through_the_frontend_vfs_and_through_the_c_library_without_one(self):
        source = self.tree()
        through_vfs = self.play(source)
        self.assertIn(str(source), {m[1] for m in OPENDIR.finditer(through_vfs)}, "the core asked the frontend to list the directory")
        self.assertEqual({m[2] for m in OPENDIR.finditer(through_vfs)} - {None}, set(), "no listing failed")
        self.assertIn("directory of 5 files and 4 directories", through_vfs)
        for name, options in (("a frontend with VFS version 1", ("--vfs-version", "1")), ("no VFS at all", ("--no-vfs",))):
            with self.subTest(name):
                text = self.play(source, None, *options)
                self.assertEqual(OPENDIR.findall(text), [], "no directory call of the VFS")
                self.assertEqual(self.medium(text), self.medium(through_vfs))
                hd.HardFileTests.assert_the_guest_read(self, self.image, text)

    def test_the_content_is_the_identity_not_the_place_the_dates_the_name_or_the_order(self):
        first = self.tree("alpha", 1_000_000_000)
        base = self.work() / "somewhere" / "else" / "beta"
        second = write_directory(base, CONTENT, order=sorted(CONTENT, reverse=True), times=1_700_000_000)
        self.assertNotEqual(snapshot(first)[0][2], snapshot(second)[0][2])
        a, b = self.play(first, peek=False), self.play(second, peek=False)
        self.assertEqual(self.medium(a), self.medium(b))
        self.assertEqual(self.medium(a), sha(self.image))
        self.assertEqual(self.medium(self.play(str(first) + "/", peek=False)), sha(self.image), "a trailing separator is the same place")

    def test_macos_litter_in_a_directory_does_not_change_the_medium(self):
        clean = self.tree()
        dirty = self.tree("dirty")
        (dirty / ".DS_Store").write_bytes(b"junk")
        (dirty / "Game" / "._readme").write_bytes(b"fork")
        (dirty / "__MACOSX").mkdir()
        (dirty / "__MACOSX" / "x").write_bytes(b"y")
        self.assertEqual(self.medium(self.play(clean, peek=False)), self.medium(self.play(dirty, peek=False)))

    # --- an LHA and a ZIP -----------------------------------------------------------------------------------------------------------------------

    def test_the_same_content_as_a_directory_an_lha_and_a_zip_is_one_medium_in_every_header_level(self):
        medium = sha(self.image)
        sources = {"directory": self.tree()}
        for level in (1, 2):
            sources[f"lha level {level}"] = self.lha(level=level, method=b"-lh5-" if level == 1 else b"-lh6-", bits=13 if level == 1 else 15)
        sources["lha stored"] = self.lha(level=1, method=b"-lh0-")
        sources["zip"] = zip_file(self.work() / "game.zip", CONTENT)
        for name, source in sources.items():
            with self.subTest(name):
                text = self.play(source)
                hd.HardFileTests.assert_the_guest_read(self, self.image, text)
                self.assertEqual(self.medium(text), medium)
        self.assertIn("LHA archive of 5 files and 1 directories", self.play(sources["lha level 1"], peek=False))
        self.assertIn("ZIP file of 5 files and 1 directories", self.play(sources["zip"], peek=False))

    def test_a_zip_with_a_disk_is_still_a_disk_and_a_zip_of_files_bigger_than_a_disk_file_is_a_volume(self):
        disk = self.work() / "disk.zip"
        zip_file(disk, {"game.adf": lr.adf("volume-test"), "readme.txt": b"hello"})
        text = self.play(disk, peek=False, rom="boot")
        self.assertRegex(text, r"disk 1: medium 0")
        self.assertNotIn("volume DH0", text)
        big = self.work() / "big.zip"
        rng = random.Random(4)
        zip_file(big, {"data.bin": rng.randbytes(3 * 1024 * 1024), "S/Startup-Sequence": b"echo\n"}, compression=0)      # stored: the file is bigger than the 2 MiB of a disk file
        self.assertGreater(big.stat().st_size, 3 * 1024 * 1024)
        text = self.play(big, peek=False)
        self.assertRegex(text, r"ZIP file of 2 files and 0 directories \(\d+ bytes\)")

    def test_an_lha_with_a_damaged_member_a_dangerous_name_or_a_method_the_core_does_not_read_fails_the_load_with_the_file_in_the_message(self):
        good = lha_archive({"f": b"hello world" * 100}, level=1)
        cases = {
            "crc.lha": (fx.archive(fx.member(b"f", b"hello", b"-lh5-", crc=1234)), "CRC"),
            "cut.lha": (good[:40], "beyond the end of the archive"),
            "dots.lha": (lha_archive({"../x": b"x"}, level=0), "unsafe member name"),
            "method.lha": (fx.archive(fx.member(b"f", b"x", b"-lz5-")), "not supported"),
            "empty.lha": (b"\0", "holds no files"),
        }
        for name, (blob, reason) in cases.items():
            with self.subTest(name):
                path = self.work() / name
                path.write_bytes(blob)
                text = self.play(path, None, expect=3, peek=False)
                self.assertIn("load refused", text)
                self.assertRegex(text, reason)
                self.assertRegex(text, re.escape(name))
                self.assertNotRegex(text, r"hard disk DH0")

    # --- directories the core refuses -----------------------------------------------------------------------------------------------------------

    def test_a_directory_that_is_not_a_volume_fails_the_load_with_a_message(self):
        empty = self.work() / "empty"
        empty.mkdir()
        wide = write_directory(self.work() / "wide", {"abc": b"x"})
        (wide / "euro€").write_bytes(b"y")
        loop = write_directory(self.work() / "loop", {"abc": b"x"})
        os.symlink(loop, loop / "again")
        long_name = write_directory(self.work() / "longname", {"n" * 31: b"x"})
        nested = self.work() / "nested"
        path = nested
        for _ in range(70):
            path = path / "d"
        path.mkdir(parents=True)
        for name, source, reason in (("empty", empty, "directory is empty"), ("not latin-1", wide, "outside ISO 8859-1"), ("link loop", loop, "nested more than 64|cannot be read"),
                                     ("long name", long_name, "cannot be an AmigaDOS name"), ("deep", nested, "nested more than 64")):
            with self.subTest(name):
                text = self.play(source, None, expect=3, peek=False)
                self.assertRegex(text, reason)
                self.assertRegex(text, re.escape(source.name))
                self.assertNotRegex(text, r"hard disk DH0")

    def test_too_many_files_and_too_many_bytes_are_refused_before_they_are_read(self):
        many = self.work() / "many"
        many.mkdir()
        for n in range(4100):
            (many / f"f{n}").write_bytes(b"")
        self.assertRegex(self.play(many, None, expect=3, peek=False), r"more than 4096 entries")
        big = self.work() / "big"
        big.mkdir()
        with open(big / "data.bin", "wb") as handle:                 # sparse: nothing is written to the disk
            handle.truncate(49 * 1024 * 1024)
        self.assertRegex(self.play(big, None, expect=3, peek=False), r"larger than the 48 MiB a volume takes")
        claim = self.tree()
        text = self.play(claim, None, "--vfs-claim", str(1 << 40), expect=3, peek=False)       # a frontend that says every file is huge: the core does not believe it, and says no
        self.assertIn("load refused", text)

    # --- playlists ------------------------------------------------------------------------------------------------------------------------------

    def test_a_playlist_mixes_floppies_hard_files_and_volumes_in_order_and_disk_control_lists_only_the_floppies(self):
        directory = self.work()
        (directory / "a.adf").write_bytes(lr.adf("a.adf"))
        (directory / "b.adf").write_bytes(lr.adf("b.adf"))
        write_directory(directory / "game", CONTENT)
        (directory / "game.lha").write_bytes(lha_archive({"x": b"second volume"}, level=1))
        (directory / "list.m3u").write_text("a.adf|First\ngame/|The game\nb.adf|Second\ngame.lha\n")
        text = self.play(directory / "list.m3u", None, "--do", "5:images")
        self.assertEqual(hd.IMAGES.search(text).groups(), ("2", "0", "0"), "the volumes are not in Disk Control")
        self.assertRegex(text, r"(?m)volume DH0 \(.*game/\): directory")
        self.assertRegex(text, r"(?m)volume DH1 \(.*game\.lha\): LHA archive")
        self.assertRegex(text, r"hard disk DH0: medium 2 sha256=" + sha(self.image), "the volumes follow the floppies in the manifest, in the order named")
        self.assertRegex(text, r"hard disk DH1: medium 3 sha256=")

    def test_the_same_content_twice_cannot_be_mounted_twice(self):
        directory = self.work()
        write_directory(directory / "one", CONTENT)
        (directory / "one.lha").write_bytes(lha_archive(CONTENT, level=1))
        (directory / "list.m3u").write_text("one/\none.lha\n")
        self.assertRegex(self.play(directory / "list.m3u", None, expect=3, peek=False), r"same bytes as an earlier hard disk")

    # --- the writes -----------------------------------------------------------------------------------------------------------------------------

    def test_the_next_session_uses_the_saved_volume_and_changing_the_source_gives_another_medium(self):
        source, saves = self.tree(), self.work()
        target = saves / saved_name(self.image)
        self.play(source, saves, *SAVE_ON)
        self.assertEqual(target.read_bytes(), expected_after_the_guest(self.image))
        again = self.play(source, saves, *SAVE_ON)
        self.assertIn("the hard disk uses the changes an earlier session saved in", again)
        (source / "Game" / "readme").write_bytes(b"read me, changed\n")
        changed = self.play(source, saves, *SAVE_ON)
        self.assertNotIn("uses the changes", changed, "another content is another volume: its saved copy has another name")
        self.assertEqual(len(list(saves.iterdir())), 2)

    # --- states, rewind and run-ahead ----------------------------------------------------------------------------------------------------------

    def test_a_state_of_a_directory_loads_in_another_process_with_the_same_content_from_an_lha_and_not_with_other_content(self):
        source = self.tree()
        state = self.work() / "x.state"
        first = self.play(source, None, "--do", f"12:savefile:{state}", runs=12)
        self.assertIn("1 hard disk(s) were changed", first)
        for name, content in (("the directory", source), ("an lha of it", self.lha(level=2)), ("a zip of it", zip_file(self.work() / "g.zip", CONTENT))):
            with self.subTest(name):
                saves = self.work()           # a saved copy of an earlier session would be another medium: each load has an empty save directory
                second = self.frontend("hdf", "--runs", 3, "--save-dir", saves, *SAVE_ON, "--do", f"0:loadfile:{state}:pos=12", content=content, system=self.system("hdf")).stdout
                self.assertEqual(hd.LOAD.search(second)[3], "1")
                self.assertEqual((saves / saved_name(self.image)).read_bytes(), expected_after_the_guest(self.image), "the state carried the writes")
        other = dict(CONTENT)
        other["Game/readme"] = b"another"
        for name, content in (("other content", write_directory(self.work() / "other", other)), ("no content", None)):
            with self.subTest(name):
                refused = self.frontend("hdf", "--runs", 3, "--do", f"0:loadfile:{state}:pos=12", content=content, system=self.system("hdf")).stdout
                self.assertEqual(hd.LOAD.search(refused)[3], "0")

    def test_a_state_takes_the_writes_back_and_run_ahead_changes_nothing_on_the_way(self):
        source = self.tree()
        plain = self.play(source, self.work(), *SAVE_ON, "--do", "14:save:at14", runs=14, peek=False, rom="hdfloop")
        saves = self.work()
        text = self.play(source, saves, *SAVE_ON, "--do", "3:save:before", "--do", "12:save:after", "--do", "13:load:before", "--do", "24:save:again",
                         runs=24, peek=False, rom="hdfloop")
        saved = {m[1]: m[5] for m in hd.SAVE.finditer(text)}
        self.assertNotEqual(saved["before"], saved["after"], "the state carries the overlay of the volume")
        self.assertTrue(all(m[3] == "1" for m in hd.LOAD.finditer(text)))
        self.assertEqual(saved["again"], {m[1]: m[5] for m in hd.SAVE.finditer(plain)}["at14"], "the machine after the load is the machine of the plain run")
        ahead = self.play(source, self.work(), "--ahead-every", "3", *SAVE_ON)
        straight = self.play(source, self.work(), *SAVE_ON)
        self.assertEqual(hd.guest_memory(ahead), hd.guest_memory(straight), "the guest saw the same volume with and without speculative runs")

    # --- a real game from a directory and from an LHA (opt-in) ------------------------------------------------------------------------------


KICKSTART = os.environ.get("P6_KICKSTART_A1200_ROM")
WHDLOAD, GAME = os.environ.get("P6_WHDLOAD_LHA"), os.environ.get("P6_FATALMISSION_LHA")
REAL = bool(KICKSTART and WHDLOAD and GAME and all(Path(p).is_file() for p in (KICKSTART, WHDLOAD, GAME)))


@unittest.skipUnless(lr.TOOLS and REAL, "BLOCKED: set P6_KICKSTART_A1200_ROM, P6_WHDLOAD_LHA and P6_FATALMISSION_LHA (the owner's Kickstart 3.1 and the free WHDLoad downloads)")
class RealGameVolumeTests(lr.LibretroHarness):
    """Fatal Mission with WHDLoad, as a directory and as an LHA of the same tree: it boots from DH0 on the A1200 with the Kickstart 3.1 of the owner, WHDLoad shows its title, the fire button
    starts the game, F10 ends it and the boot script writes boot.log and exit.log to the volume. Both give the same medium, run the same pictures and export the same volume."""

    RUNS_REAL = 2000

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tree = whdload_tree()
        cls.system_dir = Path(cls.tmp.name) / "real-system"
        cls.system_dir.mkdir()
        shutil.copyfile(KICKSTART, cls.system_dir / lr.KICKSTART_NAMES_A1200[0])      # a temporary directory outside the repository, removed with the class
        cls.sources = {"directory": write_directory(Path(cls.tmp.name) / "fatal-mission", cls.tree), "lha": Path(cls.tmp.name) / "fatal-mission.lha"}
        cls.sources["lha"].write_bytes(lha_archive(cls.tree, level=1, method=b"-lh0-"))
        cls.cache = {}

    def boot(self, kind, runs, *options, save_dir=None):
        extra = ["--save-dir", save_dir] if save_dir else []
        return self.frontend("boot", "--runs", runs, *lr.A1200, *extra, *options, content=self.sources[kind], system=self.system_dir).stdout

    def played(self, kind):
        """The whole game (fire, F10, the script writes exit.log): (output, the file the core exported when the content closed, the names in the save directory), made once."""
        if kind not in self.cache:
            from tests.whdload import test_libretro_whdload as w
            saves = self.work()
            text = self.boot(kind, self.RUNS_REAL, *SAVE_ON, *w.play(), save_dir=saves)
            self.cache[kind] = (text, next(saves.iterdir()).read_bytes() if list(saves.iterdir()) else None, sorted(p.name for p in saves.iterdir()))
        return self.cache[kind]

    def test_the_game_boots_from_a_directory_and_from_an_lha_and_the_script_writes_to_the_volume(self):
        from whdload import ffs
        image = oracle(self.tree)
        for kind in ("directory", "lha"):
            with self.subTest(kind):
                text, exported, names = self.played(kind)
                self.assertRegex(text, r"hard disk DH0 \(.*\): \d+ blocks, one partition")
                self.assertEqual(self.runs(text)[-1]["n"], self.RUNS_REAL)
                self.assertEqual(names, [saved_name(image)])
                volume, original = ffs.read_volume(exported), ffs.read_volume(image)
                self.assertEqual({"boot.log": b"booted\n", "exit.log": b"game ended\n"}, {k: v for k, v in volume.files.items() if k not in original.files})
                for path, data in original.files.items():
                    self.assertEqual(data, volume.files[path], path)
                self.assertGreater(len({r["hash"] for r in self.runs(text)[330:400]}), 20, "WHDLoad's title is animated")

    def test_a_directory_and_an_lha_of_the_same_tree_are_the_same_run_and_the_same_volume(self):
        a, exported_a, _ = self.played("directory")
        b, exported_b, _ = self.played("lha")
        key = lambda r: (r["n"], r["size"], r["hash"], r["ahash"], r["unit"], r["ticks"])
        self.assertEqual([key(r) for r in self.runs(a)], [key(r) for r in self.runs(b)])
        self.assertEqual(exported_a, exported_b)

    def test_a_state_taken_before_the_script_writes_comes_back_and_a_state_of_the_game_in_play_loads_from_the_lha_in_another_process(self):
        from tests.whdload import test_libretro_whdload as w
        EARLY, MID = 120, 700                       # DOS has not run the script yet; the game is in play
        saves = self.work()
        runs = self.RUNS_REAL + (self.RUNS_REAL - EARLY)
        text = self.boot("directory", runs, *SAVE_ON, *w.play(), "--do", f"{EARLY}:save:early", "--do", f"{self.RUNS_REAL}:save:late", "--do", f"{self.RUNS_REAL}:load:early",
                         "--do", f"{runs}:save:again", save_dir=saves)
        saved = {m[1]: m[5] for m in hd.SAVE.finditer(text)}
        self.assertNotEqual(saved["early"], saved["late"], "the writes of the script are part of the state")
        self.assertEqual(saved["again"], saved["late"], "the early state replays to the same machine, the volume's writes included")
        self.assertTrue(all(m[3] == "1" for m in hd.LOAD.finditer(text)))
        state = self.work() / "mid.state"
        self.boot("directory", MID, *w.play(leave=False), "--do", f"{MID}:savefile:{state}")
        other = self.work()
        second = self.boot("lha", 60, *SAVE_ON, "--do", f"0:loadfile:{state}:pos={MID}", *w.play(), save_dir=other)         # the state of the DIRECTORY, the content is the LHA
        self.assertEqual("1", hd.LOAD.search(second)[3])
        straight = self.runs(self.boot("directory", MID + 60, *w.play()))
        self.assertEqual([r["hash"] for r in straight[MID:MID + 60]], [r["hash"] for r in self.runs(second)][:60], "a new process, with the LHA, goes on as the original did")
        self.assertEqual([r["ahash"] for r in straight[MID:MID + 60]], [r["ahash"] for r in self.runs(second)][:60])


if __name__ == "__main__":
    unittest.main()
