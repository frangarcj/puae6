"""T30a: the LHA reader of tools/whdload/lha.py reads what it should and refuses what it must (MED03 input side; the archives are untrusted).

Synthetic archives come from lha_fixture.py (a header writer for levels 0, 1 and 2 and a tiny LH5/6/7 encoder that 7-Zip also decodes). The real archives are opt-in:
    P6_WHDLOAD_LHA        the WHDLoad user package (WHDLoad_usr.lha)
    P6_FATALMISSION_LHA   the WHDLoad install of Fatal Mission
and are only read, in memory.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest

from tests.whdload import lha_fixture as fx
from whdload import lha

ROOT = Path(__file__).resolve().parents[2]
DATA = b"hello " + b"A" * 50 + bytes(range(256)) + b"zzzzzzzzz" + b"end"
METHODS = ((b"-lh0-", 13), (b"-lh5-", 13), (b"-lh6-", 15), (b"-lh7-", 16))


class SyntheticArchiveTests(unittest.TestCase):
    def test_every_header_level_and_method_gives_the_bytes_and_the_path(self):
        for level in (0, 1, 2):
            for method, bits in METHODS:
                with self.subTest(level=level, method=method):
                    path = b"sub\xffdir\xff" if level else b""
                    entries = lha.read_archive(fx.archive(fx.member(b"file.txt", DATA, method, level, path, bits)))
                    self.assertEqual([("sub/dir/file.txt" if level else "file.txt", False, DATA)], [(e.name, e.is_dir, e.data) for e in entries])

    def test_a_level_0_name_may_carry_the_path_with_either_separator(self):
        for raw, wanted in ((b"a/b/c.txt", "a/b/c.txt"), (b"a\\b\\c.txt", "a/b/c.txt")):
            entries = lha.read_archive(fx.archive(fx.member(raw, b"x")))
            self.assertEqual(wanted, entries[0].name)

    def test_members_come_in_archive_order_with_directories_and_empty_files(self):
        blob = fx.archive(fx.member(b"", method=b"-lhd-", level=1, directory=b"d\xff"), fx.member(b"b", b"2", level=1, directory=b"d\xff"),
                          fx.member(b"empty", b"", b"-lh5-", 2), fx.member(b"a", b"1", level=2))
        self.assertEqual([("d", True, b""), ("d/b", False, b"2"), ("empty", False, b""), ("a", False, b"1")], [(e.name, e.is_dir, e.data) for e in lha.read_archive(blob)])

    def test_long_runs_use_the_overlapping_copy(self):
        data = b"x" + b"\xaa" * 1000 + b"y" * 3 + b"\x00" * 70000
        for method, bits in METHODS[1:]:
            self.assertEqual(data, lha.read_archive(fx.archive(fx.member(b"run", data, method, 1, method_bits=bits)))[0].data)

    def test_two_blocks_in_one_stream(self):
        data = bytes(range(256)) * 300
        stream = fx.encode(data, 13, block_symbols=1000)
        self.assertEqual(data, lha.decode(stream, 13, len(data)))


class RefusalTests(unittest.TestCase):
    def good(self, **kwargs):
        return bytearray(fx.archive(fx.member(b"f", DATA, b"-lh5-", kwargs.pop("level", 0), **kwargs)))

    def refused(self, blob, fragment, **limits):
        with self.assertRaises(lha.LhaError) as caught:
            lha.read_archive(bytes(blob), **limits)
        self.assertIn(fragment, str(caught.exception))

    def test_a_damaged_header_checksum_the_crc_and_a_truncation(self):
        blob = self.good()
        blob[8] ^= 1
        self.refused(blob, "header checksum")
        self.refused(fx.archive(fx.member(b"f", DATA, b"-lh5-", crc=1234)), "CRC")
        self.refused(self.good()[:-40], "")
        self.refused(self.good()[:10], "truncated")

    def test_names_that_leave_the_volume(self):
        for name in (b"../x", b"a/../b", b"/abs", b"C:x", b"a//b", b".", b"x\x01y", b"a/", b""):
            with self.subTest(name=name):
                self.refused(fx.archive(fx.member(name, b"x")), "")
        self.refused(fx.archive(fx.member(b"n" * 108, b"x")), "unsafe")

    def test_a_method_or_a_header_level_that_is_not_supported(self):
        self.refused(fx.archive(fx.member(b"f", b"x", b"-lz5-")), "not supported")
        blob = fx.archive(fx.member(b"f", b"x"))
        blob = bytearray(blob)
        blob[20] = 3
        self.refused(blob, "level 3")

    def test_limits_apply_before_anything_is_allocated(self):
        self.refused(fx.archive(fx.member(b"a", b"x"), fx.member(b"b", b"x")), "too many entries", max_entries=1)
        self.refused(fx.archive(fx.member(b"a", b"x" * 100)), "larger than the limit", max_file=50)
        self.refused(fx.archive(fx.member(b"a", b"x" * 100), fx.member(b"b", b"x" * 100)), "archive larger", max_file=150, max_total=150)
        bomb = bytearray(self.good())
        bomb[11:15] = (1 << 31).to_bytes(4, "little")           # the header claims 2 GiB of output
        bomb[1] = sum(bomb[2:bomb[0] + 2]) & 0xFF                # keep the header checksum right: the limit is what stops it
        self.refused(bomb, "larger than the limit")

    def test_a_stream_that_lies_about_its_size_or_points_before_its_start(self):
        for claimed in (len(DATA) + 5, len(DATA) - 5):
            blob = bytearray(self.good())
            blob[11:15] = claimed.to_bytes(4, "little")
            blob[1] = sum(blob[2:blob[0] + 2]) & 0xFF
            with self.subTest(claimed=claimed):
                self.refused(blob, "")
        writer = fx.Writer()
        writer.put(1, 16)
        writer.put(12, 5)
        for index in range(12):
            writer.put(1 if index in (3, 11) else 0, 3)
            if index == 2:
                writer.put(0, 2)
        writer.put(257, 9)
        for _ in range(256):
            writer.put(1, 1)
        writer.put(0, 1)
        writer.put(0, 4)
        writer.put(0, 4)
        writer.put(0, 1)                                         # the first symbol is a copy: nothing to copy from
        with self.assertRaises(lha.LhaError):
            lha.decode(writer.bytes(), 13, 3)
        with self.assertRaises(lha.LhaError):
            lha.decode(b"\x00", 13, 3)                            # a stream that ends at once


@unittest.skipUnless(os.environ.get("P6_WHDLOAD_LHA"), "BLOCKED: set P6_WHDLOAD_LHA to read the real WHDLoad package")
class RealWhdloadPackageTests(unittest.TestCase):
    def test_the_package_decodes_with_every_crc_right_and_holds_the_pinned_whdload(self):
        entries = lha.read_archive(Path(os.environ["P6_WHDLOAD_LHA"]).read_bytes())
        files = {e.name: e.data for e in entries if not e.is_dir}
        self.assertGreater(len(files), 100)
        config = json.loads((ROOT / "config/whdload/fatal-mission.json").read_text(encoding="utf-8"))
        pinned = next(f for f in config["files"] if f["to"] == "C/WHDLoad")
        self.assertEqual(pinned["sha256"], hashlib.sha256(files["WHDLoad/C/WHDLoad"]).hexdigest())


@unittest.skipUnless(os.environ.get("P6_FATALMISSION_LHA"), "BLOCKED: set P6_FATALMISSION_LHA to read the real game install")
class RealGameInstallTests(unittest.TestCase):
    def test_the_install_decodes_and_holds_the_pinned_game_files(self):
        files = {e.name: e.data for e in lha.read_archive(Path(os.environ["P6_FATALMISSION_LHA"]).read_bytes()) if not e.is_dir}
        config = json.loads((ROOT / "config/whdload/fatal-mission.json").read_text(encoding="utf-8"))
        for entry in config["files"]:
            if entry["role"] == "game-data":
                self.assertEqual(entry["sha256"], hashlib.sha256(files[entry["from"].split(":", 1)[1]]).hexdigest(), entry["to"])


if __name__ == "__main__":
    unittest.main()
