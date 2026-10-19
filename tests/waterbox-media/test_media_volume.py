"""T64b: LHA archives and ZIP files of files are volumes in the PUAE6 core of BizHawk (Puae6Core), in the real Waterbox sandbox.

The core takes the archive from the loader (Puae6Parameters.Volumes; EmuHawk's constructor hands over the .lha, .lzh and the .zip that holds files and no disk), builds ONE FFS hard file in memory with
Puae6Volumes (integration/bizhawk/core/Puae6/Volumes.cs, ADR 0011) and mounts it exactly like an .hdf, after the hard files. The host is never mounted: what the machine has is the image, and the identity of
the medium is the hash of its bytes. The algorithm is the same as the libretro adapter's (adapters/libretro/{ffs_volume,lha,volume}.h) and the offline preparation's (tools/whdload/ffs.py and lha.py), and the
three give the same bytes.

ORACLE  the image the managed code builds from an LHA (levels 0, 1 and 2; -lh0-, -lh5-, -lh6-, -lh7-) and from a ZIP (stored and deflate) is, byte for byte, the oracle's, and is the same image the C++ builder of the
        adapter gives (tests/hdf/test_volume.py); the same content from an LHA and from a ZIP is one image; trees at random, with names above 127 and files of hundreds of blocks
REFUSED hostile archives (cut, flipped, bigger than the caps, names that leave the volume) are a reason, never a crash; a core is not created from them, and the reason names the volume
MOUNT   the hard file the core exports after N units of the micro-ROM is, byte for byte, the one the native engine exports for the same image; the SaveRam holds only the chunks the guest wrote
WB03    save at N, go on M, load, go on M again: the same units and digest, the overlay goes back with the load; a new sandbox and another process continue the same; the state of a volume loads in a core given the
        same content as a ZIP and is refused for other content
MED02   the files the core was given (the archives, the ROM) are the same after everything the tests do
REAL    (opt-in) Fatal Mission with WHDLoad, from an LHA, boots on the A1200 with the owner's Kickstart 3.1, takes input, writes to the volume and comes back from a state

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-media -p "test_media_volume.py"
"""
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import unittest

from support import AVAILABLE, SKIP, ROOT, Sessions, sha, tree, build_waterbox  # noqa: F401
from tests.hdf import hdf_rom  # noqa: E402
from tests.hdf.test_volume import random_tree  # noqa: E402
from tests.hdf.volume_support import CONTENT, GAME, WHDLOAD, lha_archive, oracle, whdload_tree, zip_file  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe  # noqa: E402

UNITS = 40
CHUNK = 4096
KICKSTART = os.environ.get("P6_KICKSTART_A1200_ROM")


def ffi_volume(archive, out=None):
    """(exit code, the line the harness printed) for `Ffi.dll volume`."""
    args = ["volume", "--archive", str(archive)] + (["--out", str(out)] if out else [])
    result = build_waterbox.run_ffi(*args)
    return result.returncode, (result.stdout + result.stderr).strip()


@unittest.skipUnless(AVAILABLE, SKIP)
class VolumeTests(Sessions):
    FIRMWARE = {}
    native_probe = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.media / "hdfloop.rom").write_bytes(hdf_rom.loop_rom())
        cls.image = oracle(CONTENT)
        cls.lha = cls.media / "game.lha"
        cls.lha.write_bytes(lha_archive(CONTENT, level=1))
        cls.lha2 = cls.media / "game2.lha"
        cls.lha2.write_bytes(lha_archive(CONTENT, level=2, method=b"-lh6-", bits=15))
        cls.zip = zip_file(cls.media / "game.zip", CONTENT)
        cls.other = dict(CONTENT)
        cls.other["Game/readme"] = b"another"
        cls.other_zip = zip_file(cls.media / "other.zip", cls.other)
        cls.files_before = tree(cls.media)
        cls.cache = {}

    def vcore(self, name, volumes="game.lha", **options):
        return self.core(name, adf="", writes=False, volume=",".join(str(self.media / v) for v in volumes.split(",")), **options)

    def run_loop(self, lines, **kwargs):
        return self.session(lines, firmware="hdfloop", **kwargs)

    def native(self, image, units):
        """The hard file the native engine leaves after `units` units of the loop ROM on `image`."""
        key = ("native", sha(image), units)
        if key not in self.cache:
            headless = type(self).native_probe or build_probe(ROOT / "build/link-audit", False, "p6_headless")
            type(self).native_probe = headless
            source, out = self.out / f"native-{len(self.cache)}.hdf", self.out / f"native-{len(self.cache)}.bin"
            source.write_bytes(image)
            result = subprocess.run([str(headless), "--run", str(self.media / "hdfloop.rom"), "--units", str(units), "--hdf", str(source), "--export-hdf", str(out)],
                                    capture_output=True, text=True, timeout=900)
            self.assertEqual(0, result.returncode, result.stderr)
            self.cache[key] = out.read_bytes()
        return self.cache[key]

    # --- ORACLE ------------------------------------------------------------------------------------------------------------------

    def test_the_image_the_managed_code_builds_is_the_oracle_one_from_every_source(self):
        for path in (self.lha, self.lha2, self.zip):
            with self.subTest(path.name):
                out = self.out / f"{path.name}.img"
                code, line = ffi_volume(path, out)
                self.assertEqual(0, code, line)
                self.assertEqual(self.image, out.read_bytes())
                self.assertIn(f"sha={sha(self.image)}", line)

    def test_every_header_level_and_method_and_both_zip_compressions_give_the_same_image(self):
        content = {"S/Startup-Sequence": b"echo hi\n", "Game/data/level1.dat": b"A" * 5000 + bytes(range(256)) * 20, "Game/run": b"", "Devs": None}
        expected = oracle(content)
        for level in (1, 2):
            for method, bits in ((b"-lh0-", 13), (b"-lh5-", 13), (b"-lh6-", 15), (b"-lh7-", 16)):
                with self.subTest(level=level, method=method):
                    path = self.out / "level.lha"
                    path.write_bytes(lha_archive(content, level=level, method=method, bits=bits))
                    out = self.out / "level.img"
                    self.assertEqual(0, ffi_volume(path, out)[0])
                    self.assertEqual(expected, out.read_bytes())
        for compression in (0, 8):
            with self.subTest(compression=compression):
                path = zip_file(self.out / "c.zip", content, compression=compression)
                out = self.out / "c.img"
                self.assertEqual(0, ffi_volume(path, out)[0])
                self.assertEqual(expected, out.read_bytes())

    def test_random_trees_are_the_oracle_image_from_an_lha_and_from_a_zip(self):
        for seed in range(6):
            files = random_tree(random.Random(100 + seed), high=seed % 2 == 1, big=seed == 3)
            # byte 0xFF is the path separator of an LHA header, so no LHA can carry it in a name: the tree of these tests has no such name
            files = {name.replace("\xff", "\xfe"): data for name, data in files.items()}
            expected = oracle(files)
            for kind, path in (("lha", self.out / f"r{seed}.lha"), ("zip", self.out / f"r{seed}.zip")):
                with self.subTest(seed=seed, kind=kind):
                    if kind == "lha":
                        path.write_bytes(lha_archive(files, level=1 + seed % 2, method=b"-lh0-"))
                    else:
                        zip_file(path, files)
                    out = self.out / f"r{seed}.img"
                    code, line = ffi_volume(path, out)
                    self.assertEqual(0, code, line)
                    self.assertEqual(expected, out.read_bytes())

    def test_a_zip_with_a_disk_is_not_a_volume_and_one_of_files_is(self):
        disk = zip_file(self.out / "disk.zip", {"game.adf": bytes(901120), "readme": b"x"})
        files = zip_file(self.out / "files.zip", {"a.txt": b"x"})
        self.assertIn("zip=False", ffi_volume(disk)[1])
        self.assertIn("zip=True", ffi_volume(files)[1])

    # --- REFUSED -----------------------------------------------------------------------------------------------------------------

    def test_hostile_archives_are_a_reason_and_never_a_crash(self):
        good_lha = lha_archive(CONTENT, level=1, method=b"-lh5-")
        good_zip = self.zip.read_bytes()
        rng = random.Random(9)
        cases = []
        for good, name in ((good_lha, "lha"), (good_zip, "zip")):
            cases += [(f"{name}-cut-{n}", good[:n]) for n in (0, 3, 5, 23, 30, len(good) // 2, len(good) - 1)]
            for i in range(25):
                blob = bytearray(good)
                for _ in range(rng.randint(1, 3)):
                    blob[rng.randrange(len(blob))] ^= 1 << rng.randrange(8)
                cases.append((f"{name}-flip-{i}", bytes(blob)))
        cases += [("neither", bytes(100)), ("dots", lha_archive({"../x": b"x"}, level=0, method=b"-lh0-")), ("zip-dots", zip_file(self.out / "dots.zip", {"../x": b"x"}).read_bytes())]
        refused = 0
        for name, blob in cases:
            path = self.out / "hostile.bin"
            path.write_bytes(blob)
            code, line = ffi_volume(path)
            self.assertIn(code, (0, 2), f"{name}: {line[-300:]}")
            if code == 2:
                refused += 1
                self.assertRegex(line, r"^volume refused \S")
        self.assertGreater(refused, len(cases) // 2, "most damaged archives are refused")

    def test_a_core_is_not_created_from_an_archive_that_is_not_a_volume_and_the_reason_names_it(self):
        bad = self.out / "bad.lha"
        bad.write_bytes(lha_archive({"f": b"hello"}, level=1)[:40])
        self.run_loop([self.core("X", adf="", writes=False, volume=str(bad))], expect=1)
        self.assertRegex(self.stderr, r"volume 1 cannot be a volume: .*beyond the end of the archive|bad\.lha cannot be a volume")

    def test_volumes_and_hard_files_share_the_limit_and_the_same_content_cannot_be_mounted_twice(self):
        hdf = self.out / "same.hdf"
        hdf.write_bytes(self.image)
        self.run_loop([self.core("D", adf="", writes=False, hdf=str(hdf), volume=str(self.lha))], expect=1)
        self.assertRegex(self.stderr, r"are the same image")
        self.run_loop([self.vcore("T", "game.lha,game.zip")], expect=1)
        self.assertRegex(self.stderr, r"are the same image")
        variants = []
        for n in range(5):
            variants.append(zip_file(self.out / f"v{n}.zip", {**CONTENT, "Game/readme": b"variant %d" % n}))
        self.run_loop([self.core("F", adf="", writes=False, volume=",".join(str(p) for p in variants))], expect=1)
        self.assertRegex(self.stderr, r"at most 4 hard files")
        out = self.run_loop([self.core("F", adf="", writes=False, volume=",".join(str(p) for p in variants[:4])), "run F 3"])
        self.assertEqual(3, len(self.units(out, "F")))

    # --- MOUNT -------------------------------------------------------------------------------------------------------------------

    def test_the_volume_the_core_exports_after_the_guest_wrote_it_is_the_native_one_and_the_saveram_is_sparse(self):
        native = self.native(self.image, UNITS)
        exported, saveram = self.out / "mount.bin", self.out / "mount.saveram"
        out = self.run_loop([self.vcore("A"), "services A", "status A 0", f"run A {UNITS}", "status A 0", f"export A 0 file={exported}", "saveram A r", f"writesaveram r {saveram}"])
        self.assertIn("services A saveram=1 memorydomains=1", out, "a volume is writable for the guest like any hard file")
        before, after = self.statuses(out, "A")
        self.assertEqual((0, len(self.image) // CHUNK, 0), (before["result"], before["capacity"], before["used"]))
        self.assertGreaterEqual(after["used"], UNITS)
        self.assertEqual(native, exported.read_bytes())
        self.assertNotEqual(self.image, native, "the guest wrote the volume")
        self.assertLess(saveram.stat().st_size, len(self.image), "sparse: the chunks the guest wrote and not the image")

    def test_the_same_content_from_an_lha_and_a_zip_is_one_medium_with_one_history(self):
        out = self.run_loop([self.vcore("L", "game.lha"), self.vcore("Z", "game.zip"), self.vcore("M", "game2.lha"), f"run L {UNITS}", f"run Z {UNITS}", f"run M {UNITS}",
                             "digest L l", "digest Z z", "digest M m", "export L 0", "export Z 0", "export M 0"])
        l, z, m = ([u[1:] for u in self.units(out, name)] for name in "LZM")
        self.assertEqual(l, z)
        self.assertEqual(l, m)
        digests = {name: self.digests(out, name) for name in "LZM"}
        self.assertEqual(digests["L"]["l"], digests["Z"]["z"])
        self.assertEqual(digests["L"]["l"], digests["M"]["m"])
        exports = [self.exports(out, name)[0][3] for name in "LZM"]
        self.assertEqual(1, len(set(exports)))
        self.assertEqual(sha(self.native(self.image, UNITS)), exports[0])

    # --- WB03 --------------------------------------------------------------------------------------------------------------------

    def test_wb03_save_go_on_load_and_repeat_and_the_state_of_a_volume_loads_with_the_same_content_as_a_zip(self):
        n, m = 20, 20
        state = self.out / "volume.state"
        mid, end, back = self.out / "mid.bin", self.out / "end.bin", self.out / "back.bin"
        out = self.run_loop([self.vcore("A"), f"run A {n}", "save A s", f"savefile A {state}", f"export A 0 file={mid}", f"run A {m}", "digest A first", f"export A 0 file={end}",
                             "load A s", f"export A 0 file={back}", f"run A {m}", "digest A second",
                             self.vcore("B", "game.lha"), "load B s", f"run B {m}", "digest B b",
                             self.vcore("C", "game.zip"), f"loadfile C {state}", f"run C {m}", "digest C c"])
        a, b, c = ([u[1:] for u in self.units(out, name)] for name in "ABC")
        self.assertEqual(a[n:n + m], a[n + m:], "every unit after the load is the one that followed the save")
        self.assertEqual(a[n:n + m], b, "in a new sandbox (WB04)")
        self.assertEqual(a[n:n + m], c, "and in another process, with the same content as a ZIP (WB15)")
        digests = self.digests(out, "A")
        self.assertEqual(digests["first"], digests["second"])
        self.assertEqual({digests["first"]}, {self.digests(out, "B")["b"], self.digests(out, "C")["c"]})
        self.assertEqual(sha(self.native(self.image, n)), sha(mid.read_bytes()))
        self.assertEqual(mid.read_bytes(), back.read_bytes(), "the load took the later writes of the volume back")
        self.assertNotEqual(mid.read_bytes(), end.read_bytes())

    def test_a_state_of_other_content_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        out = self.run_loop([self.vcore("M"), "run M 30", "save M s",
                             self.vcore("T", "other.zip"), "run T 12", "load T s", "run T 25", "digest T after", self.vcore("W", "other.zip"), "run W 12", "run W 25", "digest W twin"])
        rejected = {m[1]: m[2] for m in re.finditer(r"^rejected (\S+) s reason=(\w+) message=(.*)$", out, re.M)}
        self.assertEqual("OtherMedia", rejected["T"], "the same size, other bytes: another medium")
        self.assertEqual([u[1:] for u in self.units(out, "W")], [u[1:] for u in self.units(out, "T")])
        self.assertEqual(self.digests(out, "W")["twin"], self.digests(out, "T")["after"])

    # --- REAL (opt-in) -----------------------------------------------------------------------------------------------------------


REAL = bool(KICKSTART and WHDLOAD and GAME and all(Path(p).is_file() for p in (KICKSTART, WHDLOAD, GAME)))


@unittest.skipUnless(AVAILABLE and REAL, "BLOCKED: set P6_KICKSTART_A1200_ROM, P6_WHDLOAD_LHA and P6_FATALMISSION_LHA (the owner's Kickstart 3.1 and the free WHDLoad downloads)")
class RealGameVolumeTests(Sessions):
    """Fatal Mission with WHDLoad as an LHA (the files of config/whdload/fatal-mission.json, with their boot script): it boots on the A1200 from DH0, WHDLoad shows its title, the fire button starts the
    game, F10 ends it and the script writes boot.log and exit.log to the volume. A state of the middle of the game loads in another process and the run goes on as the original did."""

    FIRMWARE = {}
    UNITS = 2000

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from tests.whdload.test_waterbox_whdload import write_script
        cls.tree = whdload_tree()
        cls.image = oracle(cls.tree)
        (cls.media / "fatal-mission.lha").write_bytes(lha_archive(cls.tree, level=1, method=b"-lh0-"))
        (cls.media / "ks31.rom").write_bytes(Path(KICKSTART).read_bytes())
        cls.script = write_script(cls.out / "game.script", units=cls.UNITS)
        cls.files_before = {p.name: sha(p.read_bytes()) for p in sorted(cls.media.iterdir())}
        cls.cache = {}

    def tearDown(self):
        self.assertEqual(self.files_before, {p.name: sha(p.read_bytes()) for p in sorted(self.media.iterdir())}, "no file the core was given changed")

    def gcore(self, name):
        return self.core(name, adf="", writes=False, volume=str(self.media / "fatal-mission.lha"), machine="a1200", whdload="Disabled")

    def test_the_game_boots_from_an_lha_writes_to_the_volume_and_comes_back_from_a_state(self):
        from whdload import ffs
        EARLY = 120
        state, exported, again = self.out / "game.state", self.out / "played.hdf", self.out / "again.hdf"
        m = self.UNITS - EARLY
        out = self.session([self.gcore("G"), f"run G {EARLY} script={self.script}", "save G s", f"savefile G {state}", f"run G {m} script={self.script}", "digest G first", f"export G 0 file={exported}",
                            "load G s", f"run G {m} script={self.script}", "digest G second", f"export G 0 file={again}",
                            self.gcore("N"), f"loadfile N {state}", f"run N {m} script={self.script}", "digest N n"], firmware="ks31")
        units = self.units(out, "G")
        self.assertGreater(len({u[3] for u in units[330:400]}), 20, "WHDLoad's title is animated")
        volume, original = ffs.read_volume(exported.read_bytes()), ffs.read_volume(self.image)
        self.assertEqual({"boot.log": b"booted\n", "exit.log": b"game ended\n"}, {k: v for k, v in volume.files.items() if k not in original.files})
        for path, data in original.files.items():
            self.assertEqual(data, volume.files[path], path)
        digests = self.digests(out, "G")
        self.assertEqual(digests["first"], digests["second"], "the early state replays to the same machine, the volume's writes included")
        self.assertEqual(digests["first"], self.digests(out, "N")["n"], "and in another process")
        self.assertEqual(exported.read_bytes(), again.read_bytes())


if __name__ == "__main__":
    unittest.main()
