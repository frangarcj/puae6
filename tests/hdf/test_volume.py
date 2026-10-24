"""T64a: the volume builder of the libretro adapter (adapters/libretro/volume.h, ffs_volume.h, lha.h) against its oracle, tools/whdload/ffs.py and lha.py (MED03, no host file system).

tests/hdf/volume_tool.cpp is the headers alone, built with ASan and UBSan. A tree goes in as a manifest, a directory of the host, an LHA or a ZIP and the image that comes out must be the
bytes the Python writer gives for the same tree: the same blocks, the same hash chains, the same bitmap. The image must not depend on where the content came from (a directory read from
another place with other modification times and another creation order, an LHA, a ZIP), and the real WHDLoad packages (opt-in, P6_WHDLOAD_LHA and P6_FATALMISSION_LHA) must give the image
the oracle gives. Hostile input is a message, never a crash.
Run: python3 -m unittest tests.hdf.test_volume
"""
import hashlib
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from whdload import ffs, lha   # noqa: E402
from tests.hdf.volume_support import CONTENT, lha_archive, oracle, write_directory, zip_file   # noqa: E402
from tests.whdload import lha_fixture as fx   # noqa: E402

TOOL = ROOT / "build/volume-tool/volume_tool"
SOURCES = [ROOT / "tests/hdf/volume_tool.cpp", *sorted((ROOT / "adapters/libretro").glob("*.h"))]
HAVE_CLANG = shutil.which("clang++") is not None


def build_tool():
    newest = max(path.stat().st_mtime for path in SOURCES)
    if TOOL.exists() and TOOL.stat().st_mtime >= newest:
        return
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-I" + str(ROOT / "references/libretro-api/include"), str(ROOT / "tests/hdf/volume_tool.cpp"), "-o", str(TOOL)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if built.returncode:
        raise RuntimeError(built.stderr[-3000:])


def run_tool(*args, timeout=300):
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
    return subprocess.run([str(TOOL), *map(str, args)], capture_output=True, text=True, timeout=timeout, env=env)


def hexed(text):
    return text.encode("latin-1").hex()


def manifest(files):
    lines = []
    for path, data in files.items():
        lines.append("D %s" % hexed(path) if data is None else "F %s %s" % (hexed(path), data.hex() or "-"))
    return "\n".join(lines) + "\n"


def folded(name):
    return bytes(ffs.upper(b, True) for b in name.encode("latin-1"))


def random_tree(rng, high=False, big=False):
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- !"
    if high:
        alphabet += "".join(chr(c) for c in list(range(0xC0, 0xD7)) + list(range(0xD8, 0x100)))
    files, taken, directories = {}, {"": set()}, [""]
    sizes = [0, 1, 7, 511, 512, 513, 1024, 4096, 36863, 36864, 36865, 37376, 70000]
    for _ in range(rng.randint(8, 40)):
        parent = rng.choice(directories)
        name = "".join(rng.choice(alphabet) for _ in range(rng.choice([1, 2, 5, 12, 30])))
        if folded(name) in taken[parent] or name.startswith("."):
            continue
        taken[parent].add(folded(name))
        path = (parent + "/" if parent else "") + name
        if rng.random() < 0.25 and len(parent.split("/")) < 5:
            files[path] = None
            directories.append(path)
            taken[path] = set()
        else:
            size = rng.choice(sizes) if rng.random() < 0.6 else rng.randint(0, 3000)
            files[path] = rng.randbytes(size)
    if big:
        files["big/data.bin"] = rng.randbytes(600000)
    return files


@unittest.skipUnless(HAVE_CLANG, "toolchain unavailable")
class VolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_tool()
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-volume-")
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def work(self):
        return Path(tempfile.mkdtemp(dir=self.root))

    def build(self, mode, source, **kwargs):
        out = self.work() / "image"
        result = run_tool(mode, source, out, **kwargs)
        return result, (out.read_bytes() if out.exists() else b"")

    def image_of(self, mode, source):
        result, image = self.build(mode, source)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        return image

    def from_manifest(self, files):
        path = self.work() / "tree.txt"
        path.write_text(manifest(files))
        return self.image_of("tree", path)

    def refused(self, mode, source, fragment):
        result, image = self.build(mode, source)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr[-800:])
        self.assertIn(fragment, result.stderr)
        self.assertEqual(image, b"")

    def write_directory(self, files, order=None, times=None):
        return write_directory(self.work() / "dir", files, order, times)

    # --- the writer against the oracle ------------------------------------------------------------------------------------------------------

    def test_random_trees_are_the_oracle_image_byte_for_byte_and_a_valid_volume(self):
        for seed in range(12):
            files = random_tree(random.Random(seed), high=seed % 3 == 2, big=seed == 4)
            with self.subTest(seed=seed):
                image = self.from_manifest(files)
                self.assertEqual(image, oracle(files))
                volume = ffs.read_volume(image)                        # checksums, hash chains, bitmap against the blocks in use
                self.assertEqual(volume.label, "PUAE6")
                self.assertEqual({k: v for k, v in files.items() if v is not None}, volume.files)

    def test_the_smallest_volumes(self):
        for name, files in (("a_file", {"a": b"x"}), ("empty_file", {"a": b""}), ("one_dir", {"d": None}), ("nested_empty", {"a/b/c": None})):
            with self.subTest(name):
                self.assertEqual(self.from_manifest(files), oracle(files))

    def test_order_of_the_entries_and_of_the_files_does_not_matter(self):
        files = random_tree(random.Random(77))
        keys = list(files)
        random.Random(5).shuffle(keys)
        self.assertEqual(self.from_manifest(files), self.from_manifest({k: files[k] for k in keys}))

    def test_a_name_with_a_high_byte_needs_the_international_dos_type(self):
        image = self.from_manifest({"caf\xe9": b"x", "plain": b"y"})
        self.assertEqual(image[:4], b"DOS\x03")
        self.assertEqual(self.from_manifest({"cafe": b"x"})[:4], b"DOS\x01")

    def test_the_tree_must_be_a_volume(self):
        cases = {
            "name": ({"n" * 31: b"x"}, "cannot be an AmigaDOS name"),
            "colon": ({"a:b": b"x"}, "cannot be an AmigaDOS name"),
            "case": ({"Readme": b"x", "README": b"y"}, "differ only in case"),
            "file_and_dir": ({"a": b"x", "a/b": b"y"}, "both a file and a directory"),
            "dir_and_file": ({"a/b": b"y", "a": b"x"}, "both a file and a directory"),
            "twice": (None, "appears twice"),
        }
        for name, (files, fragment) in cases.items():
            with self.subTest(name):
                path = self.work() / "tree.txt"
                path.write_text("F %s 41\nF %s 42\n" % (hexed("same"), hexed("same")) if files is None else manifest(files))
                self.refused("tree", path, fragment)
        with self.subTest("too deep"):
            deep = "/".join("d" * 3 for _ in range(65))
            path = self.work() / "tree.txt"
            path.write_text("D %s\n" % hexed(deep))
            self.refused("tree", path, "nested more than")
        with self.subTest("too many"):
            path = self.work() / "tree.txt"
            path.write_text("".join("D %s\n" % hexed("d%d" % n) for n in range(8200)))
            self.refused("tree", path, "more than 8192 files and directories")

    # --- a directory ----------------------------------------------------------------------------------------------------------------------------

    def test_a_directory_is_the_image_of_its_content_whatever_its_place_its_dates_or_creation_order(self):
        files = random_tree(random.Random(3))
        expected = oracle(files)
        first = self.write_directory(files, times=1_000_000_000)
        keys = sorted(files, reverse=True)
        second = self.write_directory(files, order=keys, times=1_700_000_000)
        self.assertNotEqual(first, second)
        self.assertEqual(self.image_of("dir", first), expected)
        self.assertEqual(self.image_of("dir", second), expected)
        self.assertEqual(self.image_of("dir", str(first) + "/"), expected)               # a trailing separator is the same place

    def test_macos_litter_is_ignored_and_other_dot_files_are_files(self):
        files = {"S/Startup-Sequence": b"echo hi\n", "game.slave": b"s", ".hidden": b"h"}
        base = self.write_directory(files)
        (base / ".DS_Store").write_bytes(b"junk")
        (base / "S" / ".DS_Store").write_bytes(b"junk")
        (base / "._game.slave").write_bytes(b"fork")
        (base / "__MACOSX").mkdir()
        (base / "__MACOSX" / "x").write_bytes(b"y")
        self.assertEqual(self.image_of("dir", base), oracle(files))

    def test_a_name_in_utf8_is_the_latin_1_name_and_one_outside_it_is_refused(self):
        base = self.write_directory({"caf\xe9": b"x", "n\xf1": b"y"})
        self.assertEqual(self.image_of("dir", base), oracle({"caf\xe9": b"x", "n\xf1": b"y"}))
        wide = self.write_directory({"abc": b"x"})
        (wide / "euro€").write_bytes(b"y")
        self.refused("dir", wide, "outside ISO 8859-1")

    def test_links_are_what_they_point_to_a_loop_is_stopped_and_empty_or_missing_directories_are_refused(self):
        base = self.write_directory({"a": b"x", "d/inner": b"y"})
        os.symlink(base / "a", base / "link")
        os.symlink(base / "d", base / "dlink")
        self.assertEqual(self.image_of("dir", base), oracle({"a": b"x", "link": b"x", "d/inner": b"y", "dlink/inner": b"y"}))
        os.symlink(base, base / "d" / "loop")
        result, _ = self.build("dir", base)               # the C library stops a chain of links by itself (ELOOP) before the nesting cap does
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, "nested more than 64|cannot be read")
        empty = self.work() / "empty"
        empty.mkdir()
        self.refused("dir", empty, "empty")
        self.refused("dir", self.work() / "nothing", "cannot be listed")
        only_litter = self.work() / "litter"
        only_litter.mkdir()
        (only_litter / ".DS_Store").write_bytes(b"x")
        self.refused("dir", only_litter, "empty")

    def test_a_deep_directory_and_the_size_caps(self):
        base = self.work() / "deep"
        base.mkdir()
        path = base
        for n in range(70):
            path = path / "d"
            path.mkdir()
        self.refused("dir", base, "nested more than 64")
        big = self.work() / "big"
        big.mkdir()
        with open(big / "data.bin", "wb") as handle:
            handle.truncate(49 * 1024 * 1024)
        self.refused("dir", big, "larger than the 48 MiB")
        fits = self.work() / "fits"
        fits.mkdir()
        with open(fits / "data.bin", "wb") as handle:
            handle.truncate(47 * 1024 * 1024)
        image = self.image_of("dir", fits)
        self.assertEqual(ffs.read_volume(image).files["data.bin"], bytes(47 * 1024 * 1024))
        tight = self.work() / "tight"
        tight.mkdir()
        with open(tight / "data.bin", "wb") as handle:
            handle.truncate(48 * 1024 * 1024)
        self.refused("dir", tight, "does not fit in a volume")

    # --- an LHA ----------------------------------------------------------------------------------------------------------------------------------

    def test_an_lha_of_every_level_and_method_is_the_image_of_its_content(self):
        content = CONTENT
        expected = oracle(content)
        for level in (0, 1, 2):
            for method, bits in ((b"-lh0-", 13), (b"-lh5-", 13), (b"-lh6-", 15), (b"-lh7-", 16)):
                with self.subTest(level=level, method=method):
                    if level == 0 and any(v is None for v in content.values()):
                        continue                       # level 0 headers have no directory entry in this writer
                    path = self.work() / "game.lha"
                    path.write_bytes(lha_archive(CONTENT, level, method, bits))
                    self.assertEqual(self.image_of("lha", path), expected)

    def test_the_same_content_from_a_directory_an_lha_and_a_zip_is_the_same_image(self):
        content = CONTENT
        directory = self.write_directory(content, times=1_234_567_890)
        archive = self.work() / "game.lha"
        archive.write_bytes(lha_archive(CONTENT, 2, b"-lh6-", 15))
        archive_zip = zip_file(self.work() / "game.zip", CONTENT)
        images = {mode: self.image_of(mode, source) for mode, source in (("dir", directory), ("lha", archive), ("zip", archive_zip))}
        self.assertEqual(images["dir"], images["lha"])
        self.assertEqual(images["dir"], images["zip"])
        self.assertEqual(images["dir"], oracle(content))

    def test_an_lha_the_oracle_refuses_is_refused_too(self):
        good = lha_archive({"f": b"hello"}, 1, b"-lh5-", 13)
        damaged = bytearray(good)
        damaged[10] ^= 1
        cases = {
            "header checksum": bytes(damaged),
            "CRC": fx.archive(fx.member(b"f", b"hello world", b"-lh5-", crc=1234)),
            "truncated": good[:10],
            "not supported": fx.archive(fx.member(b"f", b"x", b"-lz5-")),
            "unsafe": fx.archive(fx.member(b"../x", b"x")),
        }
        for fragment, blob in cases.items():
            with self.subTest(fragment):
                path = self.work() / "bad.lha"
                path.write_bytes(blob)
                self.refused("lha", path, fragment)
                with self.assertRaises(lha.LhaError):
                    lha.read_archive(blob)
        with self.subTest("no files"):
            path = self.work() / "none.lha"
            path.write_bytes(b"\0")
            self.refused("lha", path, "holds no files")

    def test_truncated_and_flipped_lha_archives_never_crash(self):
        good = lha_archive(CONTENT, 1, b"-lh5-", 13)
        rng = random.Random(11)
        cases = [good[:n] for n in (0, 1, 5, 23, 24, 30, 60, len(good) // 2, len(good) - 1)]
        for _ in range(60):
            blob = bytearray(good)
            for _ in range(rng.randint(1, 4)):
                blob[rng.randrange(len(blob))] ^= 1 << rng.randrange(8)
            cases.append(bytes(blob))
        for n, blob in enumerate(cases):
            path = self.work() / "x.lha"
            path.write_bytes(blob)
            result, _ = self.build("lha", path)
            self.assertIn(result.returncode, (0, 2), "case %d: %s" % (n, result.stderr[-600:]))

    # --- a ZIP ----------------------------------------------------------------------------------------------------------------------------------

    def make_zip(self, entries, compression=zipfile.ZIP_DEFLATED, utf8=False):
        path = self.work() / "files.zip"
        with zipfile.ZipFile(path, "w", compression) as handle:
            for name, data in entries:
                handle.writestr(zipfile.ZipInfo(name, (1999, 1, 1, 0, 0, 0)), data)
        return path

    def test_a_zip_of_files_is_a_volume_and_a_zip_with_a_disk_is_not(self):
        files = self.make_zip([("a.txt", b"x")])
        disk = self.make_zip([("a.adf", b"x"), ("readme", b"y")])
        ipf = self.make_zip([("a.ipf", b"x")])
        dirs = self.make_zip([("only/", b"")])
        self.assertEqual(run_tool("iszipvolume", files).stdout.strip(), "volume")
        for path in (disk, ipf, dirs):
            self.assertEqual(run_tool("iszipvolume", path).stdout.strip(), "disk")

    def test_zip_names_litter_and_unsafe_names(self):
        path = self.make_zip([("Game/", b""), ("Game/run", b"r"), ("__MACOSX/Game/._run", b"f"), ("Game/.DS_Store", b"j"), ("Game/._run", b"f"), ("top", b"t")])
        self.assertEqual(self.image_of("zip", path), oracle({"Game": None, "Game/run": b"r", "top": b"t"}))
        for name in ("../x", "/abs", "a//b", "./x", "a/./b"):
            with self.subTest(name):
                self.refused("zip", self.make_zip([(name, b"x")]), "unsafe name")

    def test_a_zip_with_utf8_and_latin_1_names(self):
        path = self.work() / "names.zip"
        with zipfile.ZipFile(path, "w") as handle:
            info = zipfile.ZipInfo("café", (1999, 1, 1, 0, 0, 0))              # zipfile sets the UTF-8 flag for a non-ASCII name
            handle.writestr(info, b"x")
        self.assertEqual(self.image_of("zip", path), oracle({"caf\xe9": b"x"}))
        wide = self.work() / "wide.zip"
        with zipfile.ZipFile(wide, "w") as handle:
            handle.writestr(zipfile.ZipInfo("euro€", (1999, 1, 1, 0, 0, 0)), b"x")
        self.refused("zip", wide, "outside ISO 8859-1")

    def test_a_zip_that_lies_or_is_damaged(self):
        good = self.make_zip([("a", b"hello" * 100)]).read_bytes()
        cases = {"no central directory": good[:-30], "empty": b"PK\x05\x06" + bytes(18)}
        for fragment, blob in cases.items():
            with self.subTest(fragment):
                path = self.work() / "bad.zip"
                path.write_bytes(blob)
                result, _ = self.build("zip", path)
                self.assertEqual(result.returncode, 2)
        flipped = bytearray(good)
        flipped[45] ^= 0xFF                                                         # inside the stored data
        path = self.work() / "flip.zip"
        path.write_bytes(bytes(flipped))
        result, _ = self.build("zip", path)
        self.assertEqual(result.returncode, 2, result.stdout)
        # a compressed entry that declares more than the cap
        handle = self.work() / "bomb.zip"
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("zeros", bytes(60 << 20))
        self.refused("zip", handle, "larger than the 48 MiB")

    # --- the real packages (opt-in) ------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_WHDLOAD_LHA") or os.environ.get("P6_FATALMISSION_LHA"), "opt-in: P6_WHDLOAD_LHA / P6_FATALMISSION_LHA")
    def test_the_real_lha_packages_give_the_oracle_image(self):
        for variable in ("P6_WHDLOAD_LHA", "P6_FATALMISSION_LHA"):
            source = os.environ.get(variable)
            if not source:
                continue
            with self.subTest(variable):
                blob = Path(source).read_bytes()
                entries = lha.read_archive(blob)
                files = {e.name: (None if e.is_dir else e.data) for e in entries}
                try:
                    expected = oracle(files)
                except ffs.FfsError as error:
                    self.refused("lha", source, str(error)[:20])
                    continue
                self.assertEqual(self.image_of("lha", source), expected)


if __name__ == "__main__":
    unittest.main()
