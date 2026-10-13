"""MED01 for the floppy image formats (T57a): which bytes the bridge takes as a disk.

ports/portable/media/trackdisk.h is the only gatekeeper between a file and the engine's readers (docs/adr/0008-media-identity.md), so it is tested on its own, built with ASan and
UBSan and no engine (tests/media/trackdisk_probe.cpp), against a second statement of the rules written here in Python from the format descriptions. Every byte of the header and of the
table of a small extended ADF is set to boundary values one at a time, every truncation of the file is tried, and the answer must be the oracle's; the verdict has a reason when it
refuses. (The engine reading what is accepted is FUZZ01, tests.fuzz.test_fuzz.TrackDiskFuzzTests, and MED01 in test_trackdisk.py.)
Run: python3 -m unittest tests.media.test_trackdisk_formats
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tests.media import trackdisk as td

ROOT = Path(__file__).resolve().parents[2]
COMPILER = shutil.which("clang++")
SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
ENV = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
PLAIN = 901120


def oracle(data):
    """True when `data` is a floppy image (a plain ADF or a well-formed extended ADF), from the descriptions in trackdisk.h and nothing else."""
    n = len(data)
    if data[:8] == bytes([0, 0, 3, 0xF3, 0, 0, 0, 0]):
        return False
    if data[:8] == b"UAE-1ADF":
        if n > 3 << 20 or n < 12 or data[8:10] != b"\0\0":
            return False
        tracks = struct.unpack(">H", data[10:12])[0]
        if not 1 <= tracks <= 166 or 12 + 12 * tracks > n:
            return False
        at = 12 + 12 * tracks
        for i in range(tracks):
            r, flag, kind, length, bits = struct.unpack(">HBBII", data[12 + 12 * i:24 + 12 * i])
            if r or length >> 24 or bits >> 24 or kind > 1 or length > 20000 or at + length > n:
                return False
            if kind == 0 and length < 5632:
                return False
            if kind == 1 and (bits < 16 or (bits + 7) // 8 > length):
                return False
            at += length
        return True
    if data[:8] == b"UAE--ADF":
        if n > 3 << 20 or n < 648:
            return False
        at = 648
        for i in range(160):
            sync, length = struct.unpack(">HH", data[8 + 4 * i:12 + 4 * i])
            if length > 20000 or at + length > n or (length < 5632 if sync == 0 else length < 2):
                return False
            at += length
        return True
    return n == PLAIN


def small_ext2():
    """Six tracks: raw, AmigaDOS, raw of another length and an odd number of bytes, AmigaDOS, raw, AmigaDOS."""
    raw = lambda words, bits=None: ("raw", [0x2AAA] * words, bits if bits is not None else words * 16)
    dos = ("dos", bytes(range(256)) * 22)
    return td.ext2_image([raw(100), dos, raw(101, 1608), dos, raw(7), dos], count=6)


def small_ext1():
    return td.ext1_image([("raw", 0x4489, bytes([i & 255 for i in range(2 + i % 5)])) for i in range(160)])


class Probe:
    exe = None
    tmp = None

    @classmethod
    def start(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-trackdisk-formats-")
        cls.exe = Path(cls.tmp.name) / "trackdisk_probe"
        command = [COMPILER, "-std=c++17", "-O1", "-g", *SANITIZE, "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "ports/portable/media"), str(ROOT / "tests/media/trackdisk_probe.cpp"),
                   "-o", str(cls.exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if built.returncode:
            raise RuntimeError(built.stderr[-3000:])

    @classmethod
    def stop(cls):
        cls.tmp.cleanup()

    @classmethod
    def verdicts(cls, images):
        bundle = Path(cls.tmp.name) / "bundle"
        bundle.write_bytes(b"".join(struct.pack("<I", len(image)) + image for image in images))
        result = subprocess.run([str(cls.exe), str(bundle)], capture_output=True, text=True, timeout=900, env=ENV)
        assert result.returncode == 0 and "Sanitizer" not in result.stderr and "runtime error" not in result.stderr, result.stderr[-1500:]
        lines = result.stdout.splitlines()
        assert len(lines) == len(images)
        return lines


@unittest.skipUnless(COMPILER, "clang++ unavailable")
class FormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Probe.start()

    @classmethod
    def tearDownClass(cls):
        Probe.stop()

    def agree(self, images, label):
        lines = Probe.verdicts(images)
        for index, (image, line) in enumerate(zip(images, lines)):
            expected = oracle(image)
            self.assertEqual(line.startswith("ok "), expected, f"{label} #{index}: {line}")
            if not expected:
                self.assertTrue(len(line) > len("refused: "), "a refusal says why")
        return lines

    def test_the_good_images_are_what_they_are(self):
        adf = bytes(PLAIN)
        self.assertEqual(["ok ADF", "ok UAE-1ADF", "ok UAE--ADF"], self.agree([adf, small_ext2(), small_ext1()], "good"))

    def test_every_byte_of_the_header_and_the_table_of_an_extended_adf_at_boundary_values(self):
        for name, good, span in (("ext2", small_ext2(), 12 + 12 * 6), ("ext1", small_ext1(), 648)):
            images = []
            for at in range(span):
                for value in (0, 1, 0x7F, 0x80, 0xFF, good[at] ^ 1, (good[at] + 1) & 255):
                    image = bytearray(good)
                    image[at] = value
                    images.append(bytes(image))
            lines = self.agree(images, name)
            accepted = sum(line.startswith("ok ") for line in lines)
            self.assertTrue(0 < accepted < len(images), f"{name}: the sweep reaches both answers ({accepted} of {len(images)})")

    def test_every_truncation_and_extension_of_an_extended_adf(self):
        for name, good in (("ext2", small_ext2()), ("ext1", small_ext1())):
            images = [good[:size] for size in range(len(good) + 1)] + [good + bytes(extra) for extra in (1, 2, 3, 100)]
            lines = self.agree(images, name)
            self.assertEqual(lines[len(good)][:3], "ok ")
            self.assertTrue(all(line.startswith("refused") for line in lines[:len(good) - 1]), "a cut file is refused")

    def test_sizes_and_headers_of_plain_images(self):
        images = [bytes(size) for size in (0, 1, 7, 8, 12, 511, PLAIN - 1, PLAIN, PLAIN + 1, 2 * PLAIN)]
        images += [b"UAE-1ADF", b"UAE--ADF", b"UAE-1ADF" + bytes(4), bytes([0, 0, 3, 0xF3, 0, 0, 0, 0]) + bytes(PLAIN - 8), b"UAE-1ADX" + bytes(PLAIN - 8), b"DOS\0" + bytes(PLAIN - 4)]
        big = b"UAE-1ADF" + struct.pack(">HH", 0, 1) + struct.pack(">HBBII", 0, 0, 0, 20000, 0) + bytes(20000)
        images += [big, big + bytes((3 << 20) - len(big)), big + bytes((3 << 20) - len(big) + 1)]
        self.agree(images, "plain")


if __name__ == "__main__":
    unittest.main()
