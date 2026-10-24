"""T30a: the FFS writer of tools/whdload/ffs.py makes volumes AmigaDOS can read and makes the same bytes every time (MED03).

What proves that a real AmigaDOS reads them is the boot of tests/whdload/test_boot.py (T30b): the A1200 Kickstart mounts the volume and runs WHDLoad from it. Here the layout is held to the
rules of the filing system with a reader of its own (ffs.read_volume: checksums, types, one owner per block, the bitmap against the blocks in use) and to the bytes of one pinned image.
"""
import datetime
import hashlib
import struct
import unittest

from tests.whdload import lha_fixture      # noqa: F401  (puts tools/ on sys.path)
from whdload import ffs

TIME = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
FILES = {"C/WHDLoad": bytes(range(256)) * 600, "S/Startup-Sequence": b"Echo hi\n", "Empty": None, "a/b/c/d.txt": b"x" * 5000, "zero": b"",
         "big": bytes((i * 7) & 255 for i in range(512 * 200 + 77))}
# the whole image of this tree, so that no change of the format goes unseen (the format was proven by the guest in T30b)
GOLDEN_SHA256 = "a97731f3b427ca698c67736f6fe07d8d4194520391ba657356e86a696dbb8649"


def image(files=FILES, label="Test", dostype=b"DOS\x01", **kwargs):
    tree = ffs.build_tree(files)
    return ffs.write_volume(tree, label, dostype, ffs.volume_blocks(tree, **kwargs), TIME)


class VolumeTests(unittest.TestCase):
    def test_every_file_and_directory_comes_back_and_the_bitmap_agrees(self):
        data = image()
        volume = ffs.read_volume(data)
        self.assertEqual(({k: v for k, v in FILES.items() if v is not None}), volume.files)
        self.assertEqual(("C", "Empty", "S", "a", "a/b", "a/b/c"), volume.dirs)
        self.assertEqual(("Test", b"DOS\x01"), (volume.label, volume.dostype))
        self.assertEqual(0, len(data) % (32 * 512))
        self.assertEqual(len(data) // 512, volume.blocks)

    def test_the_same_tree_gives_the_same_bytes_whatever_order_it_was_given_in(self):
        reordered = dict(reversed(list(FILES.items())))
        self.assertEqual(image(), image(reordered))
        self.assertEqual(image(), image())

    def test_a_label_a_time_a_dostype_or_a_size_changes_the_bytes(self):
        base = image()
        self.assertNotEqual(base, image(label="Other"))
        self.assertNotEqual(base, image(min_blocks=4096))
        self.assertNotEqual(base, ffs.write_volume(ffs.build_tree(FILES), "Test", b"DOS\x01", len(base) // 512, TIME + datetime.timedelta(minutes=1)))
        self.assertEqual(b"DOS\x03", image(dostype=b"DOS\x03")[:4])

    def test_the_root_block_is_in_the_middle_and_its_checksum_adds_up(self):
        data = image(min_blocks=2048)
        root = (2048 - 1 + 2) // 2
        longs = struct.unpack(">128L", data[root * 512:(root + 1) * 512])
        self.assertEqual((2, 72, 1), (longs[0], longs[3], longs[127]))
        self.assertEqual(0, sum(longs) & 0xFFFFFFFF)
        self.assertEqual(root + 1, longs[79])
        self.assertEqual(0xFFFFFFFF, longs[78])

    def test_a_directory_with_many_entries_chains_the_hash_collisions(self):
        files = {"d/file%03d" % n: bytes([n]) * (n + 1) for n in range(200)}
        volume = ffs.read_volume(image(files))
        self.assertEqual(files, volume.files)

    def test_names_are_ordered_by_their_bytes_so_the_blocks_do_not_depend_on_the_host(self):
        a = ffs.build_tree({"b": b"1", "B2": b"2", "a": b"3"})
        self.assertEqual(["B2", "a", "b"], list(sorted(a.children, key=lambda n: n.encode("latin-1"))))
        self.assertEqual(image({"b": b"1", "B2": b"2", "a": b"3"}), image({"a": b"3", "b": b"1", "B2": b"2"}))

    def test_a_tree_built_by_hand_in_any_order_is_written_in_name_order(self):
        def tree(order):
            root = ffs.Node("", is_dir=True)
            for name in order:
                root.children[name] = ffs.Node(name, data=name.encode() * 700)
            return root
        names = ["zeta", "Alpha", "mid", "beta", "Zed"]
        images = {ffs.write_volume(tree(order), "T", b"DOS\x01", 128, TIME) for order in (names, names[::-1], sorted(names))}
        self.assertEqual(1, len(images))

    def test_protection_bits_and_the_international_dostype(self):
        tree = ffs.build_tree({"Caf\xe9": b"x", "script": b"y"})
        tree.children["script"].protection = 0x40
        volume = ffs.read_volume(ffs.write_volume(tree, "Intl", b"DOS\x03", 64, TIME))
        self.assertEqual({"Caf\xe9": b"x", "script": b"y"}, volume.files)
        self.assertEqual(0x40, volume.protection["script"])
        with self.assertRaises(ffs.FfsError):
            ffs.write_volume(ffs.build_tree({"Caf\xe9": b"x"}), "Plain", b"DOS\x01", 64, TIME)

    def test_the_golden_image(self):
        self.assertEqual(GOLDEN_SHA256, hashlib.sha256(image()).hexdigest())


class RefusalTests(unittest.TestCase):
    def test_names_that_are_not_amigados_names(self):
        for path in ("x" * 31, "a:b", "a//b", "", "/a"):
            with self.subTest(path=path), self.assertRaises(ffs.FfsError):
                ffs.build_tree({path: b"x"})
        with self.assertRaises(ffs.FfsError):
            ffs.build_tree({"A": b"1", "a": b"2"})                    # AmigaDOS folds case: one name
        with self.assertRaises(ffs.FfsError):
            ffs.build_tree({"a": b"1", "a/b": b"2"})                  # a file and a directory

    def test_a_tree_that_does_not_fit_or_a_size_that_is_not_a_volume(self):
        tree = ffs.build_tree({"big": b"x" * 100000})
        with self.assertRaises(ffs.FfsError):
            ffs.write_volume(tree, "T", b"DOS\x01", 64, TIME)
        for blocks in (63, 100):
            with self.assertRaises(ffs.FfsError):
                ffs.write_volume(ffs.build_tree({"a": b"x"}), "T", b"DOS\x01", blocks, TIME)
        with self.assertRaises(ffs.FfsError):
            ffs.write_volume(ffs.build_tree({"a": b"x"}), "T", b"DOS\x09", 64, TIME)
        with self.assertRaises(ffs.FfsError):
            ffs.write_volume(ffs.build_tree({"a": b"x"}), "bad:name", b"DOS\x01", 64, TIME)

    def test_a_volume_that_is_too_big_for_the_bitmap_page_list(self):
        tree = ffs.build_tree({"a": b"x"})
        with self.assertRaises(ffs.FfsError):
            ffs.volume_blocks(tree, min_blocks=25 * 127 * 32 + 64)

    def test_the_reader_refuses_a_damaged_volume(self):
        data = image(min_blocks=1760)
        root = (1760 - 1 + 2) // 2
        headers = [b for b in range(2, 1760) if b != root and struct.unpack(">2L", data[b * 512:b * 512 + 4] + data[b * 512 + 508:b * 512 + 512])[1] in (2, 0xFFFFFFFD)
                   and struct.unpack(">L", data[b * 512:b * 512 + 4])[0] == 2]
        self.assertTrue(headers)
        for what, offset in (("root checksum", root * 512 + 20), ("bitmap bit", (root + 1) * 512 + 40), ("a header", headers[0] * 512 + 20)):
            damaged = bytearray(data)
            damaged[offset] ^= 0xFF
            with self.subTest(what=what), self.assertRaises(ffs.FfsError):
                ffs.read_volume(bytes(damaged))
        with self.assertRaises(ffs.FfsError):
            ffs.read_volume(b"DOS\x00" + bytes(64 * 512 - 4))


if __name__ == "__main__":
    unittest.main()
