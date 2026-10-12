"""T59, MED01: archives of disks in the libretro core: a ZIP with several disks, `file.zip#member.adf`, a 7z and an RP9 package, decoded in memory in a deterministic order.

Two layers. ArchiveReaderTests run adapters/libretro/sevenzip.h and archive.h outside the core (tests/libretro-host/archive_probe.cpp, built with ASan and UBSan, no engine): the 7z
reader against an encoder that is not its own (a 7z writer in pure Python around liblzma's raw LZMA and LZMA2: tests/libretro-host/archives.py) and against 7-Zip itself when it is
installed, the order of a set, the RP9 manifest, and hostile archives. ArchiveCoreTests run the whole core with the minimal frontend: the same set from a ZIP, a 7z, an RP9 package
and a playlist of loose ADFs is the same media in the same order, the same future and the same states.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_archives.py"
"""
import itertools
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import archives as a  # noqa: E402
import test_libretro as lr  # noqa: E402
from tests.media import containers as c  # noqa: E402
import test_state as ts  # noqa: E402  (the module, so that its test classes are not collected twice)

ROOT = Path(__file__).resolve().parents[2]
CLANG = shutil.which("clang++")


def crc(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def disk_lines(disks):
    return [f"disk {name} crc32={crc(data):08x} size={len(data)}" for name, data in disks]


def set_of(n=3):
    """n distinct disks, named like a No-Intro set."""
    return [(f"Game (Disk {i + 1}).adf", c.varied_adf(f"game {i + 1}")) for i in range(n)]


@unittest.skipUnless(CLANG, "clang++ unavailable")
class ArchiveReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-archives-")
        cls.exe = Path(cls.tmp.name) / "archive_probe"
        built = subprocess.run([CLANG, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-I" + str(ROOT / "adapters/libretro"), str(ROOT / "tests/libretro-host/archive_probe.cpp"), "-o", str(cls.exe)],
                               capture_output=True, text=True, timeout=600)
        if built.returncode:
            raise RuntimeError(built.stderr[-2000:])
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe(self, command, data, *extra):
        type(self).counter += 1
        path = Path(self.tmp.name) / f"file-{self.counter}"
        path.write_bytes(data)
        result = subprocess.run([str(self.exe), command, str(path), *map(str, extra)], capture_output=True, text=True, timeout=300,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        return result.stdout.splitlines()

    def sevenzip(self, data, *extra):
        return self.probe("sevenzip", data, *extra)

    @staticmethod
    def data_lines(files):
        return [f"data {n} crc32={crc(d):08x} size={len(d)}" for n, d in files if not n.endswith("/")]

    # --- 7z: what an encoder that is not ours writes --------------------------------------------------------------------------------------

    def test_a_7z_in_every_shape_decodes_to_the_bytes_that_went_in(self):
        rng = random.Random(7)
        noise = bytes(rng.randrange(256) for _ in range(200_000))
        files = [("folder/", b""), ("folder/Game (Disk 1).adf", c.varied_adf("a")), ("Game (Disk 2).adf", c.varied_adf("b")), ("empty.txt", b""),
                 ("noise.bin", noise), ("\u00e9\u20ac \U0001f600.txt", b"unicode"), ("zeros.bin", bytes(3_000_000))]
        for method, solid, encode in itertools.product(("lzma2", "lzma", "copy"), (True, False), (True, False)):
            with self.subTest(method=method, solid=solid, encoded_header=encode):
                archive = a.sevenzip_file(files, method=method, solid=solid, encode_header=encode)
                out = self.sevenzip(archive)
                self.assertEqual([line for line in out if line.startswith("refused")], [])
                self.assertEqual([line for line in out if line.startswith("data")], self.data_lines(files))
                self.assertIn("entry folder dir 0", out)
                self.assertIn("entry empty.txt file 0", out)

    def test_lzma_corner_cases_lc_lp_pb_and_a_chunked_lzma2_stream(self):
        data = c.varied_adf("corner") + bytes(random.Random(3).randrange(256) for _ in range(300_000))
        for method, options in (("lzma", dict(lc=0, lp=0, pb=0)), ("lzma", dict(lc=4, lp=0, pb=4)), ("lzma", dict(lc=0, lp=4, pb=2)), ("lzma", dict(lc=1, lp=3, pb=1)), ("lzma", dict(lc=4, lp=0, pb=0, preset=0)),
                                ("lzma2", dict(lc=0, lp=4, pb=0)), ("lzma2", dict(lc=4, lp=0, pb=4, preset=9)), ("lzma2", dict(dict_size=1 << 12)), ("lzma2", dict(dict_size=4096, preset=1))):
            with self.subTest(method=method, **options):
                out = self.sevenzip(a.sevenzip_file([("x.bin", data)], method=method, **options))
                self.assertEqual([line for line in out if line.startswith("data")], self.data_lines([("x.bin", data)]), out)

    @unittest.skipUnless(shutil.which("7z"), "7-Zip is not installed")
    def test_it_agrees_with_7zip_itself(self):
        rng = random.Random(5)
        files = {"Game (Disk 1).adf": c.varied_adf("a"), "Game (Disk 2).adf": c.varied_adf("b"), "Game (Disk 3).adf": c.adf("c"), "readme.txt": b"hello" * 1000,
                 "big.bin": bytes(rng.randrange(256) for _ in range(1_500_000))}
        expected = sorted(self.data_lines(files.items()))
        with tempfile.TemporaryDirectory(prefix="p6-7z-") as work:
            for name, data in files.items():
                (Path(work) / name).write_bytes(data)
            for switches in (["-m0=lzma2"], ["-m0=lzma"], ["-m0=lzma2", "-ms=off"], ["-m0=copy"], ["-m0=lzma2:d=64k"], ["-m0=lzma2", "-mhc=off"], ["-m0=lzma:lc=0:lp=2:pb=0"], ["-m0=lzma:lc=8:lp=0:pb=4"], ["-m0=lzma:lc=4:lp=4:pb=0"],
                             ["-m0=lzma2:mt=4:c=1m"], ["-mx=9", "-m0=lzma2:d=1m"], ["-mx=1"], ["-mx=0"]):
                with self.subTest(switches=switches):
                    target = Path(work) / "out.7z"
                    target.unlink(missing_ok=True)
                    subprocess.run(["7z", "a", "-bd", *switches, str(target), *files], cwd=work, capture_output=True, check=True)
                    self.assertEqual(sorted(line for line in self.sevenzip(target.read_bytes()) if line.startswith("data")), expected)
            for switches, method in ((["-m0=ppmd"], "PPMd"), (["-m0=bzip2"], "BZip2"), (["-m0=lzma2", "-mf=BCJ"], "BCJ")):
                with self.subTest(switches=switches):
                    target = Path(work) / "out.7z"
                    target.unlink(missing_ok=True)
                    subprocess.run(["7z", "a", "-bd", *switches, str(target), "Game (Disk 3).adf", "readme.txt"], cwd=work, capture_output=True, check=True)
                    out = self.sevenzip(target.read_bytes())
                    refused = [line for line in out if line.startswith("refused")]
                    self.assertTrue(refused and all(method in line or "chain of coders" in line for line in refused), out)

    # --- 7z: bounds and hostile archives ----------------------------------------------------------------------------------------------------

    def test_what_an_archive_unpacks_to_is_capped_before_anything_is_decoded(self):
        big = a.sevenzip_file([("zeros.bin", bytes(6_000_000))])
        self.assertEqual(self.sevenzip(big, 5_000_000), ["refused: the 7z archive unpacks to more than the core reads (4 MiB)"])
        self.assertEqual(len([line for line in self.sevenzip(big, 6_000_000) if line.startswith("data")]), 1)
        # A header that claims 2^60 bytes, and a stream that lies about its own size: refused, never allocated.
        lying = bytearray(a.sevenzip_file([("x.bin", bytes(1000))], encode_header=False))
        header_at = 32 + int.from_bytes(lying[12:20], "little")
        self.assertIn(b"\x0c" + a.number(1000), bytes(lying[header_at:]))
        patched = bytes(lying[:header_at]) + bytes(lying[header_at:]).replace(b"\x0c" + a.number(1000), b"\x0c" + a.number(1 << 60))
        start = lying[12:32]
        start[8:16] = (len(patched) - header_at).to_bytes(8, "little")
        start[16:20] = crc(patched[header_at:]).to_bytes(4, "little")
        patched = bytes(lying[:8]) + crc(bytes(start)).to_bytes(4, "little") + bytes(start) + patched[32:]
        out = self.sevenzip(patched)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("refused: the 7z archive unpacks to more than the core reads"), out)

    def test_damaged_and_truncated_7z_archives_are_refused_and_never_return_wrong_bytes(self):
        files = [("a.bin", c.varied_adf("hostile")), ("b.txt", b"b" * 5000), ("c/", b""), ("c/d.bin", bytes(range(256)) * 40)]
        expected = set(self.data_lines(files))
        for encoded in (True, False):
            archive = a.sevenzip_file(files, encode_header=encoded)
            rng = random.Random(11 + encoded)
            cases = [("cut at %d" % n, archive[:n]) for n in (0, 5, 31, 32, 33, len(archive) // 2, len(archive) - 1)]
            for _ in range(80):
                mutant = bytearray(archive)
                for _ in range(1 + rng.randrange(3)):
                    mutant[rng.randrange(len(mutant))] ^= 1 << rng.randrange(8)
                cases.append(("flip", bytes(mutant)))
            # The same flips with the checksums put right again: the structure has to defend itself, not the CRC.
            for _ in range(80):
                mutant = bytearray(archive)
                at = 32 + rng.randrange(len(mutant) - 32)
                mutant[at] ^= 1 << rng.randrange(8)
                size = int.from_bytes(mutant[20:28], "little")
                offset = int.from_bytes(mutant[12:20], "little")
                if 32 + offset + size <= len(mutant):
                    mutant[28:32] = crc(bytes(mutant[32 + offset:32 + offset + size])).to_bytes(4, "little")
                    mutant[8:12] = crc(bytes(mutant[12:32])).to_bytes(4, "little")
                cases.append(("flip and fix", bytes(mutant)))
            for label, mutant in cases:
                out = self.sevenzip(mutant)
                for line in out:
                    if line.startswith("data"):
                        self.assertIn(line, expected, label)               # a file is either right or not there: its CRC is checked
                    elif not line.startswith(("refused", "entry")):
                        self.fail(f"{label}: {line}")

    def test_what_the_reader_does_not_read_is_named(self):
        self.assertEqual(self.sevenzip(b"7z\xbc\xaf\x27\x1c\x00\x04" + bytes(40)), ["refused: the 7z archive is damaged (bad start header CRC)"])
        self.assertEqual(self.sevenzip(a.sevenzip_file([("x", b"x")])[:20]), ["refused: the file is not a 7z archive"])
        newer = bytearray(a.sevenzip_file([("x", b"x")]))
        newer[6] = 1
        self.assertEqual(self.sevenzip(bytes(newer)), ["refused: the 7z archive is of a newer format version than the core reads"])
        self.assertEqual(self.sevenzip(a.sevenzip_file([])), [])                       # an archive of nothing is nothing

    # --- the order of a set (PUAE5's, made total) ------------------------------------------------------------------------------------------------

    def test_a_set_is_ordered_the_way_puae5_orders_it(self):
        # A space counts as '/' (so "Turrican.adf" comes before "Turrican II.adf", which plain byte order gets wrong), "(Program" comes first, the rest is byte order.
        for names, expected in ((["Turrican III.adf", "Turrican II.adf", "Turrican.adf"], ["Turrican.adf", "Turrican II.adf", "Turrican III.adf"]),
                                (["Game (Data).adf", "Game (Disk 2).adf", "Game (Program).adf"], ["Game (Program).adf", "Game (Data).adf", "Game (Disk 2).adf"]),
                                (["b.adf", "B.adf", "a.adf", "10.adf", "2.adf", "1.adf"], ["1.adf", "10.adf", "2.adf", "B.adf", "a.adf", "b.adf"]),
                                (["x/b.adf", "x/a.adf", "a.adf"], ["a.adf", "x/a.adf", "x/b.adf"])):
            for permutation in itertools.islice(itertools.permutations(names), 40):
                with self.subTest(names=names):
                    self.assertEqual(self.order(permutation), expected)

    def order(self, names):
        return subprocess.run([str(self.exe), "order", *names], capture_output=True, text=True).stdout.splitlines()

    # --- sets from a ZIP, a 7z and an RP9 -----------------------------------------------------------------------------------------------------

    def test_a_zip_with_several_disks_is_a_set_in_the_order_of_their_names_not_of_the_archive(self):
        disks = set_of(4)
        shuffled = [disks[2], disks[0], ("readme.txt", b"hi"), disks[3], ("shots/", b""), ("__MACOSX/._x", b"x"), disks[1]]
        out = self.probe("disks", c.zip_file(shuffled))
        self.assertEqual(out[0], "kind ZIP file")
        self.assertEqual([line for line in out if line.startswith("disk")], disk_lines(disks))
        self.assertTrue(any(line.startswith("note 1 other member(s)") for line in out), out)
        # The same from a 7z whatever its order, and stored or deflated.
        for method in ("lzma2", "lzma", "copy"):
            out = self.probe("disks", a.sevenzip_file(shuffled, method=method))
            self.assertEqual(out[0], "kind 7z archive")
            self.assertEqual([line for line in out if line.startswith("disk")], disk_lines(disks))
        self.assertEqual(self.probe("disks", c.zip_file(shuffled, 0))[1:], self.probe("disks", c.zip_file(list(reversed(shuffled))))[1:])

    def test_a_member_by_name_is_that_disk_alone(self):
        disks = set_of(3)
        archive = c.zip_file(disks + [("sub/Other.adf", c.varied_adf("other"))])
        self.assertEqual(self.probe("disks", archive, "Game (Disk 2).adf")[1:], disk_lines([disks[1]]))
        self.assertEqual(self.probe("disks", archive, "Other.adf")[1:], disk_lines([("sub/Other.adf", c.varied_adf("other"))]))         # by file name when it is unique
        self.assertEqual(self.probe("disks", archive, "sub/Other.adf")[1:], disk_lines([("sub/Other.adf", c.varied_adf("other"))]))
        self.assertEqual(self.probe("disks", archive, "Nothing.adf"), ['refused: the ZIP file has no member named "Nothing.adf"'])
        twin = c.zip_file([("a/x.adf", c.adf("1")), ("b/x.adf", c.adf("2"))])
        self.assertIn("more than one member", self.probe("disks", twin, "x.adf")[0])
        self.assertEqual(self.probe("disks", twin, "b/x.adf")[1:], disk_lines([("b/x.adf", c.adf("2"))]))
        self.assertEqual(self.probe("disks", a.sevenzip_file(disks), "Game (Disk 3).adf")[1:], disk_lines([disks[2]]))
        self.assertIn("not an ADF", " ".join(self.probe("disks", c.zip_file([("notes.txt", b"hello")]), "notes.txt")))

    def test_an_rp9_package_is_its_manifest_floppies_in_priority_order(self):
        d1, d2, d3, spare = c.varied_adf("one"), c.varied_adf("two"), c.varied_adf("three"), c.varied_adf("spare")
        package = a.rp9_file([("disk-b.adf", 2, None), ("disk-c.adf", 3, None), ("disk-a.adf", 1, None), ("elsewhere/gone.adf", 4, "shared")],
                             [("disk-a.adf", d1), ("disk-b.adf", d2), ("disk-c.adf", d3), ("zzz-spare.adf", spare), ("preview.png", b"\x89PNG")],
                             other='<harddrive type="hdf">hd.hdf</harddrive><cd>a.iso</cd>')
        out = self.probe("disks", package)
        self.assertEqual(out[0], "kind RP9 package")
        self.assertEqual([line for line in out if line.startswith("disk")], disk_lines([("disk-a.adf", d1), ("disk-b.adf", d2), ("disk-c.adf", d3)]))   # the spare is not in the manifest
        notes = " ".join(line for line in out if line.startswith("note"))
        self.assertIn("2 hard drive, CD, tape or snapshot entries", notes)
        self.assertIn("configuration (machine, memory, Kickstart, peripherals, boot) is ignored", notes)
        self.assertIn('"elsewhere/gone.adf" is in the player\'s shared folder', notes)
        # Without priorities the order of the document; a path with backslashes, another case, an entity and a CDATA section is still the member.
        package = a.rp9_file([("Disk\\Two.ADF", None, None), ("Disk&#x2f;One.adf", None, None), ("<![CDATA[disk3.adf]]>", None, None)],
                             [("disk/two.adf", d2), ("Disk/One.adf", d1), ("disk3.adf", d3)])
        self.assertEqual([line for line in self.probe("disks", package) if line.startswith("disk")], disk_lines([("disk/two.adf", d2), ("Disk/One.adf", d1), ("disk3.adf", d3)]))
        # A member by name works in a package too, and a package whose manifest has no floppies is refused.
        self.assertEqual(self.probe("disks", package, "disk3.adf")[1:], disk_lines([("disk3.adf", d3)]))
        self.assertIn("lists no floppy", self.probe("disks", a.rp9_file([], [("a.adf", d1)]))[0])
        self.assertEqual(self.probe("disks", a.rp9_file([("a.adf", None, None), ("a.adf", None, None)], [("a.adf", d1)]))[1:][-1], disk_lines([("a.adf", d1)])[0])   # listed twice, loaded once

    def test_a_package_that_the_manifest_contradicts_is_refused_with_a_message(self):
        d1 = c.varied_adf("one")
        cases = {
            "missing": (a.rp9_file([("gone.adf", None, None)], [("a.adf", d1)]), 'does not hold it'),
            "unsafe": (a.rp9_file([("../a.adf", None, None)], [("a.adf", d1)]), "unsafe path"),
            "absolute": (a.rp9_file([("/a.adf", None, None)], [("a.adf", d1)]), "unsafe path"),
            "drive": (a.rp9_file([("C:/a.adf", None, None)], [("a.adf", d1)]), "unsafe path"),
            "doctype": (c.zip_file([("rp9-manifest.xml", b'<!DOCTYPE rp9 [<!ENTITY a "b">]><rp9/>'), ("a.adf", d1)]), "DOCTYPE with an internal subset"),
            "unclosed": (c.zip_file([("rp9-manifest.xml", b"<rp9><application><media><floppy>a.adf"), ("a.adf", d1)]), "is not closed"),
            "mismatch": (c.zip_file([("rp9-manifest.xml", b"<rp9><application></media></application></rp9>"), ("a.adf", d1)]), "do not nest"),
            "not xml": (c.zip_file([("rp9-manifest.xml", b"just text"), ("a.adf", d1)]), "no elements"),
            "deep": (c.zip_file([("rp9-manifest.xml", b"<a>" * 200 + b"</a>" * 200), ("a.adf", d1)]), "nest too deeply"),
            "attribute": (c.zip_file([("rp9-manifest.xml", b"<rp9 a=b/>"), ("a.adf", d1)]), "not quoted"),
        }
        for name, (data, text) in cases.items():
            with self.subTest(name):
                out = self.probe("disks", data)
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("refused: "), out)
                self.assertIn(text, out[0])

    def test_what_an_archive_cannot_be_is_said(self):
        d = set_of(2)
        too_many = [(f"d{i:02}.adf", c.adf(f"many {i}")) for i in range(17)]
        self.assertIn("holds 17 disk images; the core keeps at most 16", self.probe("disks", c.zip_file(too_many))[0])
        self.assertEqual(len([x for x in self.probe("disks", c.zip_file(too_many[:16])) if x.startswith("disk")]), 16)
        self.assertIn("holds no disk image (.adf, .adz or .dms)", self.probe("disks", c.zip_file([("readme.txt", b"x")]))[0])
        self.assertIn("holds no disk image", self.probe("disks", a.sevenzip_file([("readme.txt", b"x")]))[0])
        out = self.probe("disks", c.zip_file(d + [("Game.ipf", b"CAPS" + bytes(100))]))
        self.assertIn('holds "Game.ipf", a disk in a format the core does not read', out[0])
        self.assertIn("holds a disk in a format the core does not read", self.probe("disks", c.zip_file([("Game.fdi", bytes(100))]))[0])
        self.assertIn("format the core does not read", self.probe("disks", c.zip_file(d + [("more.7z", bytes(100))]))[0])        # a 7z inside an archive is not unpacked
        # A member that is not a disk, in a set, names itself.
        out = self.probe("disks", c.zip_file([("a.adf", c.adf("a")), ("b.adf", bytes(1000))]))
        self.assertIn('member "b.adf"', out[0])
        out = self.probe("disks", c.zip_file([("a.adz", c.adz(c.adf("a"))), ("b.dms", c.dms_file(c.adf("b"))), ("c.adf", c.adf("c"))]))
        self.assertEqual([x.split()[1] for x in out if x.startswith("disk")], ["a.adz", "b.dms", "c.adf"])                 # ADZ and DMS members are decoded too
        self.assertIn("a ZIP inside a ZIP is not read", self.probe("disks", c.zip_file([("a.adf", c.zip_file([("x.adf", c.adf("x"))])), ("b.adf", c.adf("b"))]))[0])
        # A bomb and a lie, member by member.
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        bomb = b"".join(body.compress(bytes(1 << 20)) for _ in range(64)) + body.flush()
        self.assertIn("decompresses to more than", self.probe("disks", c.raw_zip("a.adf", 8, bomb, 0, len(bomb), 901120))[0])
        self.assertIn("larger than any disk file", self.probe("disks", c.raw_zip("a.adf", 8, bomb, 0, len(bomb), 0xFFFFFFFF))[0])
        self.assertIn("larger than any disk file", self.probe("disks", a.sevenzip_file([("a.adf", bytes(3 * 1024 * 1024))]))[0])
        self.assertIn("no central directory", self.probe("disks", c.zip_file(d)[:-30])[0])

    def test_a_hostile_set_never_returns_a_wrong_disk(self):
        disks = set_of(3)
        rng = random.Random(21)
        for label, archive in (("zip", c.zip_file(disks)), ("7z", a.sevenzip_file(disks)), ("rp9", a.rp9_file([(n, i + 1, None) for i, (n, _) in enumerate(disks)], disks))):
            expected = set(disk_lines(disks))
            for _ in range(60):
                mutant = bytearray(archive)
                for _ in range(1 + rng.randrange(3)):
                    mutant[rng.randrange(len(mutant))] ^= 1 << rng.randrange(8)
                for line in self.probe("disks", bytes(mutant)):
                    if line.startswith("disk "):
                        self.assertIn(line, expected, label)


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class ArchiveCoreTests(lr.LibretroHarness):
    """The core with the minimal frontend: a set is a playlist, whatever archive it came in."""

    MEDIUM = re.compile(r"medium (\d+) sha256=(\w+)")
    IMAGE = re.compile(r"(?m)^image (\d+) path=(.*) label=(.*)$")

    def written(self, files):
        directory = self.work()
        for name, data in files.items():
            (directory / name).write_bytes(data)
        return directory

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def media(self, out):
        return [m[2] for m in self.MEDIUM.finditer(out)]

    def view(self, out):
        return [(r["unit"], r["ticks"], r["hash"], r["px"], r["audio"], r["ahash"]) for r in self.runs(out)]

    def images(self, out):
        return [(int(m[1]), Path(m[2]).name, m[3]) for m in self.IMAGE.finditer(out)]

    def sets(self, disks):
        """The same disks as a ZIP, a 7z, an RP9 package and a playlist of loose ADFs, in a directory; the archives are in the wrong order on purpose."""
        shuffled = list(reversed(disks))
        files = {"set.zip": c.zip_file(shuffled + [("readme.txt", b"hello")]), "set.7z": a.sevenzip_file(shuffled), "set.rp9": a.rp9_file([(n, i + 1, None) for i, (n, _) in enumerate(disks)], shuffled)}
        for name, data in disks:
            files[name] = data
        directory = self.written(files)
        (directory / "loose.m3u").write_text("".join(f"{name}\n" for name, _ in disks))
        return directory

    def test_the_core_offers_the_new_extensions(self):
        out = self.frontend("boot", "--runs", 1).stdout
        self.assertIn("extensions=adf|adz|dms|zip|7z|rp9|lha|lzh|hdf|hdz|m3u|uae|/", out)

    def test_every_archive_of_a_set_is_the_same_playlist_the_same_media_and_the_same_future(self):
        disks = set_of(3)
        directory = self.sets(disks)
        reference = self.frontend("observer", "--runs", 8, content=directory / "loose.m3u").stdout
        self.assertEqual(len(set(self.media(reference))), 3)
        for name in ("set.zip", "set.7z", "set.rp9"):
            for options in ((), ("--no-vfs",)):
                with self.subTest(name, options=options):
                    out = self.frontend("observer", "--runs", 8, *options, content=directory / name).stdout
                    self.assertEqual(self.media(out), self.media(reference))                       # the same medium ids in the same order
                    self.assertEqual(self.view(out), self.view(reference))                        # the same units, ticks, pictures and audio
                    self.assertIn("images count=3 index=0 eject=0", out)
                    self.assertEqual([(i, label) for i, _, label in self.images(out)], [(0, "Game (Disk 1)"), (1, "Game (Disk 2)"), (2, "Game (Disk 3)")])
                    self.assertEqual([Path(p).name for _, p, _ in self.images(out)], [f"{name}#{n}" for n, _ in disks])
        # The log says which archive it was and in what order.
        out = self.frontend("boot", "--runs", 1, content=directory / "set.rp9").stdout
        self.assertIn("RP9 package of 3 disk(s), in this order: Game (Disk 1).adf, Game (Disk 2).adf, Game (Disk 3).adf", out)
        self.assertIn("the RP9 configuration", out)

    def test_nothing_is_written_next_to_the_archive(self):
        directory = self.sets(set_of(2))
        before = sorted(p.name for p in directory.iterdir())
        self.frontend("observer", "--runs", 4, content=directory / "set.7z")
        self.frontend("observer", "--runs", 4, content=directory / "set.zip")
        self.assertEqual(sorted(p.name for p in directory.iterdir()), before)

    def test_a_member_by_name_is_a_one_disk_playlist_and_a_playlist_may_mix_everything(self):
        disks = set_of(3)
        directory = self.sets(disks)
        for archive in ("set.zip", "set.7z", "set.rp9"):
            with self.subTest(archive):
                out = self.frontend("observer", "--runs", 6, content=directory / f"{archive}#Game (Disk 2).adf").stdout
                self.assertIn("images count=1 index=0 eject=0", out)
                self.assertEqual(self.media(out), self.media(self.frontend("observer", "--runs", 6, content=directory / "Game (Disk 2).adf").stdout))
                self.assertEqual(self.view(out), self.view(self.frontend("observer", "--runs", 6, content=directory / "Game (Disk 2).adf").stdout))
        # A playlist: a member, a loose disk and a whole archive (which expands in place); the same disk twice is one medium.
        (directory / "mixed.m3u").write_text("set.zip#Game (Disk 3).adf|Three\nGame (Disk 1).adf\nset.7z\n")
        out = self.frontend("observer", "--runs", 4, content=directory / "mixed.m3u").stdout
        self.assertIn("images count=5 index=0 eject=0", out)
        self.assertEqual(len(set(self.media(out))), 3)
        self.assertEqual([label for _, _, label in self.images(out)], ["Three", "Game (Disk 1)", "Game (Disk 1)", "Game (Disk 2)", "Game (Disk 3)"])
        # A label given in the playlist leads the labels of an expanded set.
        (directory / "labelled.m3u").write_text("set.7z|Box\n")
        out = self.frontend("boot", "--runs", 1, content=directory / "labelled.m3u").stdout
        self.assertEqual([label for _, _, label in self.images(out)], ["Box - Game (Disk 1)", "Box - Game (Disk 2)", "Box - Game (Disk 3)"])

    def test_a_state_taken_with_one_archive_loads_with_another_and_a_different_set_is_refused(self):
        disks = set_of(3)
        directory = self.sets(disks)
        other = self.written({"other.zip": c.zip_file(set_of(2) + [("Game (Disk 3).adf", c.varied_adf("not the same"))])})
        path = self.work() / "x.state"
        first = self.frontend("input_all", "--runs", 14, *ts.INPUT, "--do", f"6:savefile:{path}", content=directory / "set.zip", system=self.system("input_all")).stdout
        for name in ("set.7z", "set.rp9", "loose.m3u"):
            with self.subTest(name):
                second = self.frontend("input_all", "--runs", 8, *ts.INPUT, "--do", f"0:loadfile:{path}:pos=6", content=directory / name, system=self.system("input_all")).stdout
                self.assertEqual(ts.LOAD.search(second)[3], "1")
                self.assertEqual(ts.StateTests.views(self, second), ts.StateTests.views(self, first)[6:])
        with self.subTest("another set"):
            refused = self.frontend("input_all", "--runs", 8, *ts.INPUT, "--do", f"0:loadfile:{path}:pos=6", content=other / "other.zip", system=self.system("input_all")).stdout
            self.assertEqual(ts.LOAD.search(refused)[3], "0")

    def test_disk_control_adds_a_member_of_an_archive_and_refuses_a_set(self):
        disks = set_of(3)
        directory = self.sets(disks)
        (directory / "one.m3u").write_text("Game (Disk 1).adf\n")
        (directory / "pick.7z").write_bytes(a.sevenzip_file([("d1.adf", disks[0][1]), ("d2.adf", disks[1][1]), ("d3.adf", disks[2][1])]))       # the host's command has no room for spaces in a path
        member = f"{directory / 'pick.7z'}#d2.adf"
        out = self.frontend("observer", "--runs", 40, "--do", "5:disk:eject", "--do", "5:disk:add", "--do", f"6:disk:replace=1:{member}", "--do", f"7:disk:replace=1:{directory / 'pick.7z'}",
                            "--do", "20:images", content=directory / "one.m3u").stdout
        self.assertIn(f"disk 6 replace=1:{member} result=1", out)
        self.assertIn("result=0", [line for line in out.splitlines() if line.startswith("disk 7")][0])
        self.assertTrue(any("holds 3 disks and one is added at a time" in m for m in self.messages(out)), self.messages(out))

    def test_refusals_name_the_file_and_leave_nothing_registered(self):
        many = [(f"d{i:02}.adf", c.adf(f"many {i}")) for i in range(17)]
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        bomb = b"".join(body.compress(bytes(1 << 20)) for _ in range(64)) + body.flush()
        files = {"many.zip": c.zip_file(many), "many.7z": a.sevenzip_file(many), "none.7z": a.sevenzip_file([("readme.txt", b"x")]), "cut.7z": a.sevenzip_file(set_of(2))[:-60],
                 "bomb.7z": a.sevenzip_file([("a.adf", bytes(40_000_000))]), "ipf.zip": c.zip_file(set_of(2) + [("x.ipf", b"CAPS" + bytes(50))]),
                 "ppmd.7z": a.sevenzip_file([("a.adf", c.adf("a"))])[:8] + bytes(24) + b"junk", "bad.rp9": a.rp9_file([("nope.adf", 1, None)], [("a.adf", c.adf("a"))]),
                 "huge.zip": c.raw_zip("a.adf", 8, bomb, 0, len(bomb), 901120), "notdisk.zip": c.zip_file([("a.adf", c.adf("a")), ("b.adf", bytes(1000))])}
        expected = {"many.zip": "holds 17 disk images; the core keeps at most 16", "many.7z": "holds 17 disk images", "none.7z": "no disk image", "cut.7z": "7z archive",
                    "bomb.7z": "unpacks to more than", "ipf.zip": "a disk in a format the core does not read", "ppmd.7z": "7z archive", "bad.rp9": "does not hold it",
                    "huge.zip": "decompresses to more than", "notdisk.zip": 'member "b.adf"'}
        directory = self.written(files)
        for name, text in expected.items():
            with self.subTest(name):
                out = self.frontend("boot", "--runs", 2, content=directory / name, expect=3).stdout
                self.assertIn("load refused", out)
                refusal = " ".join(self.messages(out))
                self.assertIn(text, refusal)
                self.assertIn(name, refusal)
                self.assertEqual(self.media(out), [])
        # Two archives of 10 disks in one playlist do not fit the 16 images a state can carry.
        pair = self.written({"a.zip": c.zip_file([(f"a{i}.adf", c.adf(f"a{i}")) for i in range(10)]), "b.zip": c.zip_file([(f"b{i}.adf", c.adf(f"b{i}")) for i in range(10)])})
        (pair / "both.m3u").write_text("a.zip\nb.zip\n")
        out = self.frontend("boot", "--runs", 2, content=pair / "both.m3u", expect=3).stdout
        self.assertIn("names more than 16 disks", " ".join(self.messages(out)))


if __name__ == "__main__":
    unittest.main()
