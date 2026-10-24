"""Boot the A1200 preset (68EC020, AGA, 2 MiB Chip, NO Gayle) with an authorized Kickstart 3.1 and an AGA demo: opt-in, it only runs where the owner supplies them.

    P6_KICKSTART_A1200_ROM  the Kickstart 3.1 (r40.68) .rom of the A1200, raw, 512 KiB
    P6_AGA_ADF              a one-disk ADF (901120 bytes) of an AGA demo the owner may run; the freeware Catabasis (Cocoon) is the one used here

The image and the disk are read from where they are and never copied; nothing here records a hash or a byte of either. What is checked is the
shape of what the machine shows, so the test proves that the 3.1 reaches its insert-disk screen on a machine WITHOUT Gayle and that an AGA
demo starts there, not that the A1200 is compatible. Criteria, deduced from the pictures the units deliver:

  insert-disk screen  with no disk in DF0: one flat background colour that covers most of the raster plus several other colours (the 3.1's
                      logo, text and drive), and a picture that keeps moving (the animation of the disk entering the drive: a hung
                      machine, or one that rebooted in a loop, would freeze or never settle).
  AGA demo            with the disk in DF0: the machine leaves that screen for good; the last picture is none of those the insert-disk run
                      keeps cycling through, it has more than 32 distinct colours, and some of them have a colour gun that is not a
                      multiple of 17: the ECS palette has four bits per gun, which the renderer widens to 8 bits as n * 17, so only the
                      24-bit AGA palette (or HAM8) can put another value on the screen. The picture keeps changing until the end.
  determinism         two runs with the same seed give the same text, unit by unit, and the same last picture.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import array
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADLESS = ROOT / "build/link-audit/p6_headless"
KICKSTART = os.environ.get("P6_KICKSTART_A1200_ROM")
DEMO = os.environ.get("P6_AGA_ADF")
EMPTY_UNITS = 1800
DEMO_UNITS = 3500       # the first AGA scenes of Catabasis are on the screen by unit ~2000; at 3500 the 3D objects are moving
ROW = re.compile(r"unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=([0-9a-f]{16})(?: audio=\d+)?")


def last_picture(directory):
    """(width, height, colour counter, bytes) of the one frame dump in `directory`: header of four uint32, then 32-bit pixels."""
    (path,) = list(Path(directory).glob("frame-*.bin"))
    data = path.read_bytes()
    width, height = struct.unpack_from("<II", data)
    pixels = array.array("I")
    pixels.frombytes(data[16:16 + width * height * 4])
    return width, height, Counter(p & 0xFFFFFF for p in pixels), data


def parse(stdout):
    rows = [ROW.fullmatch(line) for line in stdout.splitlines()[:-1]]
    assert all(rows), "a line of the runner has an unexpected shape"
    return rows


def beyond_ecs(colours):
    """Colours with a gun that an ECS palette (a 4-bit value widened to n * 17) cannot produce."""
    return [c for c in colours if any(((c >> shift) & 0xFF) % 17 for shift in (0, 8, 16))]


@unittest.skipUnless(KICKSTART and HEADLESS.is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM (and build p6_headless) to run the A1200 boot")
class A1200BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.getsize(KICKSTART) != 524288:
            raise unittest.SkipTest("P6_KICKSTART_A1200_ROM is not a 512 KiB image")
        temp = tempfile.TemporaryDirectory(prefix="p6-a1200-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        jobs = {"empty": (None, EMPTY_UNITS)}
        if DEMO:
            if os.path.getsize(DEMO) != 901120:
                raise unittest.SkipTest("P6_AGA_ADF is not a 901120-byte ADF")
            jobs.update({"demo": (DEMO, DEMO_UNITS), "demo again": (DEMO, DEMO_UNITS)})
        with ThreadPoolExecutor(len(jobs)) as pool:
            futures = {name: pool.submit(cls.run_headless, name, *job) for name, job in jobs.items()}
            cls.runs = {name: future.result() for name, future in futures.items()}

    @classmethod
    def run_headless(cls, name, disk, units):
        dump = cls.temp / name.replace(" ", "-")
        dump.mkdir()
        command = [str(HEADLESS), "--run", KICKSTART, "--machine", "a1200", "--units", str(units), "--seed", "1",
                   "--dump", str(dump), "--dump-from", str(units)]
        if disk:
            command += ["--disk", disk]
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            return result.stdout, parse(result.stdout), last_picture(dump)
        raise AssertionError(f"{name}: the runner failed ({result.returncode}): {result.stderr[-400:]}")

    def test_the_a1200_reaches_the_insert_disk_screen_and_keeps_animating(self):
        stdout, rows, (width, height, colours, _) = self.runs["empty"]
        self.assertEqual(len(rows), EMPTY_UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
        hashes = [r[6] for r in rows]
        self.assertGreater(len(set(hashes)), 4, "the picture hardly changed")
        self.assertGreater(len(set(hashes[-200:])), 1, "the last 200 units show one frozen picture: the machine is hung")
        self.assertGreater(colours.most_common(1)[0][1], width * height // 2, "no flat background that covers most of the raster")
        self.assertGreaterEqual(len(colours), 5, "the screen has no content besides its background")

    @unittest.skipUnless(DEMO, "BLOCKED: set P6_AGA_ADF to run the AGA demo")
    def test_an_aga_demo_starts_and_shows_a_picture_only_the_aga_palette_can_make(self):
        _, empty_rows, (_, _, empty_colours, _) = self.runs["empty"]
        _, rows, (width, height, colours, _) = self.runs["demo"]
        self.assertEqual(len(rows), DEMO_UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows))
        shown_without_disk = {r[6] for r in empty_rows[200:]}
        self.assertFalse({r[6] for r in rows[-100:]} & shown_without_disk, "the machine still shows a picture of the insert-disk screen")
        self.assertNotEqual(colours.most_common(1)[0][0], empty_colours.most_common(1)[0][0], "the dominant colour is that of the insert-disk screen")
        self.assertGreater(len(colours), 32, "the picture has no more colours than an OCS screen")
        self.assertGreaterEqual(len(beyond_ecs(colours)), 4, "no colour outside the 12-bit palette: the AGA palette is not on the screen")
        self.assertGreater(len({r[6] for r in rows[-100:]}), 1, "the last 100 units show one frozen picture: the demo is hung")

    @unittest.skipUnless(DEMO, "BLOCKED: set P6_AGA_ADF to run the AGA demo")
    def test_the_same_seed_gives_the_same_units_and_the_same_last_picture(self):
        first, second = self.runs["demo"], self.runs["demo again"]
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2][3], second[2][3])


if __name__ == "__main__":
    unittest.main()
