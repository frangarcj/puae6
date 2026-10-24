"""T17d, MED01: disk containers (ADZ, ZIP, DMS) as libretro content, run by the minimal frontend.

The core decodes a container in memory to the 901120-byte ADF it holds (adapters/libretro/container.h) and registers THAT with p6_load, so the SHA-256 of the
ADF is the medium's identity whatever the file was: the same content id, the same units (ticks, pictures, audio) and a save state that loads with any other
container of the same disk. What the core does not read, and files that are damaged or hostile, fail the load with a message that says why. The decoders
themselves, their fuzzing and their agreement with WinUAE's xDMS are in tests/media/test_container.py.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_containers.py"
"""
import hashlib
from pathlib import Path
import re
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402
from tests.media import containers as c  # noqa: E402
import test_state as ts  # noqa: E402  (the module, so that its test classes are not collected twice)

MEDIUM = re.compile(r"medium (\d+) sha256=(\w+)")


def containers(disk):
    """The same disk in every container the core reads, by file name."""
    return {
        "game.adf": disk,
        "game.adz": c.adz(disk),
        "game.zip": c.zip_file([("readme.txt", b"hello"), ("game.adf", disk)]),
        "game.dms": c.dms_file(disk, lambda n: n % 3 % 2),
        "stored.zip": c.zip_file([("game.adf", disk)], 0),
        "inner.zip": c.zip_file([("game.dms", c.dms_file(disk))]),
    }


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class ContainerCoreTests(lr.LibretroHarness):
    def written(self, files):
        directory = self.work()
        for name, data in files.items():
            (directory / name).write_bytes(data)
        return directory

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def media(self, out):
        return [m[2] for m in MEDIUM.finditer(out)]

    def view(self, out):
        return [(r["unit"], r["ticks"], r["hash"], r["px"], r["audio"], r["ahash"]) for r in self.runs(out)]

    def test_the_core_offers_the_container_extensions(self):
        out = self.frontend("boot", "--runs", 1).stdout
        self.assertIn("extensions=adf|adz|dms|fdi|ipf|zip|7z|rp9|lha|lzh|hdf|hdz|m3u|uae|iso|cue|ccd|nrg|mds|chd|/", out)

    def test_a_disk_in_any_container_is_the_same_medium_and_the_same_future(self):
        disk = c.varied_adf("the game")
        directory = self.written(containers(disk))
        results = {}
        for name in containers(disk):
            for options in ((), ("--no-vfs",)):
                with self.subTest(name, options=options):
                    out = self.frontend("observer", "--runs", 8, *options, content=directory / name).stdout
                    results[(name, options)] = (self.media(out), self.view(out))
        self.assertEqual(len({str(v) for v in results.values()}), 1)
        digest = next(iter(results.values()))[0]
        self.assertEqual(len(digest), 1)
        self.assertEqual(digest[0], hashlib.sha256(disk).hexdigest())
        # Another disk is another medium, in any container.
        other = self.written(containers(c.varied_adf("another game")))
        self.assertNotEqual(self.media(self.frontend("boot", "--runs", 1, content=other / "game.adz").stdout), digest)

    def test_an_extended_adf_an_fdi_and_an_ipf_are_media_by_their_own_bytes_in_any_container(self):
        """T57, T58: the bridge reads them as they are, so the identity is the SHA-256 of the file and the same file in an ADZ or a ZIP is the same medium with the same future."""
        from tests.media import fdi, ipf, trackdisk as td
        adf = c.varied_adf("t57")
        images = {"ext.adf": td.ext2_image([("raw", td.raw_track(5), td.RAW_WORDS * 16)] + [("dos", td.dos_track(adf, t)) for t in range(1, 160)]),
                  "game.fdi": fdi.fdi_image([("amiga", td.dos_track(adf, 0), 0), ("amiga", td.dos_track(adf, 1), 0)]),
                  "game.ipf": ipf.build([ipf.Track(0, 0, ipf.dos_blocks(adf, 0, weak_bytes=12)), ipf.Track(0, 1, ipf.dos_blocks(adf, 1))])}
        for name, image in images.items():
            directory = self.written({name: image, "x.adz": c.adz(image), "x.zip": c.zip_file([(name, image)])})
            results = {}
            for file in (name, "x.adz", "x.zip"):
                with self.subTest(name, file=file):
                    out = self.frontend("observer", "--runs", 8, content=directory / file).stdout
                    results[file] = (self.media(out), self.view(out))
            self.assertEqual(len({str(v) for v in results.values()}), 1, name)
            self.assertEqual(results[name][0], [hashlib.sha256(image).hexdigest()])

    def test_a_playlist_may_name_containers_and_the_same_disk_in_two_containers_is_one_medium(self):
        one, two = c.varied_adf("one"), c.varied_adf("two")
        directory = self.written({"one.adf": one, "one.adz": c.adz(one), "two.zip": c.zip_file([("two.adf", two)]), "two.dms": c.dms_file(two), "plain.adf": two})
        (directory / "set.m3u").write_text("one.adf|One\none.adz|One again\ntwo.zip|Two\ntwo.dms|Two as dms\nplain.adf|Two plain\n")
        (directory / "plain.m3u").write_text("one.adf|One\ntwo.adf|Two\n")
        (directory / "two.adf").write_bytes(two)
        out = self.frontend("boot", "--runs", 2, content=directory / "set.m3u").stdout
        self.assertEqual(len(self.media(out)), 2)                                          # five entries, two distinct disks
        self.assertIn("images count=5 index=0 eject=0", out)
        self.assertEqual(self.media(out), self.media(self.frontend("boot", "--runs", 2, content=directory / "plain.m3u").stdout))
        self.assertRegex(out, r"(?m)^image 1 path=.*/one\.adz label=One again$")

    def test_a_state_saved_with_one_container_loads_with_another_and_a_different_disk_is_refused(self):
        disk = c.varied_adf("states")
        directory = self.written(containers(disk))
        (directory / "other.adf").write_bytes(c.varied_adf("not the same"))
        path = self.work() / "x.state"
        first = self.frontend("input_all", "--runs", 14, *ts.INPUT, "--do", f"6:savefile:{path}", content=directory / "game.adf",
                              system=self.system("input_all")).stdout
        for name in ("game.adz", "game.zip", "game.dms", "inner.zip"):
            with self.subTest(name):
                second = self.frontend("input_all", "--runs", 8, *ts.INPUT, "--do", f"0:loadfile:{path}:pos=6", content=directory / name,
                                       system=self.system("input_all")).stdout
                self.assertEqual(ts.LOAD.search(second)[3], "1")
                self.assertEqual(ts.StateTests.views(self, second), ts.StateTests.views(self, first)[6:])
        with self.subTest("another disk"):
            refused = self.frontend("input_all", "--runs", 8, *ts.INPUT, "--do", f"0:loadfile:{path}:pos=6", content=directory / "other.adf",
                                    system=self.system("input_all")).stdout
            self.assertEqual(ts.LOAD.search(refused)[3], "0")

    def test_formats_the_core_does_not_read_and_hostile_files_fail_the_load_with_a_message(self):
        disk = c.adf("refused")
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        bomb = b"".join(body.compress(bytes(1 << 20)) for _ in range(64)) + body.flush()          # 64 MiB of zeros in a few tens of KB
        files = {
            "game.ipf": b"CAPS" + bytes(3000), "disguised.adf": b"CAPS" + bytes(3000), "game.fdi": b"Formatted Disk Image file\r\n" + bytes(3000), "lying.fdi": bytes(3000),
            "game.scp": bytes(100), "game.7z": b"7z" + bytes(100), "game.rar": bytes(100),
            "none.zip": c.zip_file([("readme/", b"")]), "short.adz": c.adz(bytes(900000)), "long.adz": c.adz(bytes(901121)),
            "cut.adz": c.adz(disk)[:100], "cut.zip": c.zip_file([("a.adf", disk)])[:-40], "cut.dms": c.dms_file(disk)[:5000],
            "partial.dms": c.dms_file(disk, tracks=range(40)), "crypt.dms": c.dms_file(disk, info=2),
            "bomb.adz": b"\x1f\x8b\x08\0\0\0\0\0\0\3" + bomb + struct.pack("<II", 0, 0),
            "bomb.zip": c.raw_zip("a.adf", 8, bomb, 0, len(bomb), 901120), "lying.zip": c.raw_zip("a.adf", 8, bomb, 0, len(bomb), 0xFFFFFFFF),
        }
        directory = self.written(files)
        expected = {
            "game.ipf": "an extended ADF, an FDI or an IPF", "disguised.adf": "an extended ADF, an FDI or an IPF", "game.fdi": "version the core does not read", "lying.fdi": "an extended ADF, an FDI, an IPF or a supported container", "game.scp": "not supported",
            "game.7z": "not an ADF", "game.rar": "only ZIP, 7z and RP9 archives are read", "none.zip": "no disk image", "short.adz": "holds 900000 bytes",
            "long.adz": "not an ADF of 901120", "cut.adz": "truncated", "cut.zip": "no central directory", "cut.dms": "truncated", "partial.dms": "40 of 80 tracks",
            "crypt.dms": "encrypted", "bomb.adz": "more than", "bomb.zip": "decompresses to more than", "lying.zip": "larger than any disk file",
        }
        for name, text in expected.items():
            with self.subTest(name):
                out = self.frontend("boot", "--runs", 2, content=directory / name, expect=3).stdout
                self.assertIn("load refused", out)
                refusal = " ".join(self.messages(out))
                self.assertIn(text, refusal)
                self.assertIn(name, refusal)                      # the message names the file
                self.assertEqual(self.media(out), [])             # nothing was registered
        # In a playlist the message names the disk too, and nothing is half loaded.
        (directory / "list.m3u").write_text("game.ipf\n")
        (directory / "good.m3u").write_text("none.zip\n")
        out = self.frontend("boot", "--runs", 2, content=directory / "good.m3u", expect=3).stdout
        self.assertIn("disk 1 (", " ".join(self.messages(out)))
        out = self.frontend("boot", "--runs", 2, content=directory / "list.m3u", expect=3).stdout
        self.assertIn("an extended ADF, an FDI or an IPF", " ".join(self.messages(out)))

    def test_a_container_load_cycle_repeats_exactly_and_writes_nothing(self):
        disk = c.varied_adf("cycle")
        directory = self.written(containers(disk))
        before = sorted(p.name for p in directory.iterdir())
        first = self.frontend("observer", "--runs", 6, content=directory / "game.dms").stdout
        again = self.frontend("observer", "--runs", 6, content=directory / "game.dms").stdout
        self.assertEqual(self.view(first), self.view(again))
        self.assertEqual(sorted(p.name for p in directory.iterdir()), before)             # no extraction next to the file


if __name__ == "__main__":
    unittest.main()
