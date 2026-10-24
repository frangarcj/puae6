"""Boot the A600 preset with an authorized Kickstart 2.05 and Workbench 2.05: opt-in, it only runs where the owner supplies them.

    P6_KICKSTART_A600_ZIP  zip that holds the Kickstart 2.05 .rom (A600, 512 KiB)
    P6_WB205_ZIP           zip that holds the Workbench 2.05 ADF (the boot disk)

The zips are read in memory; the image and the disk are written to a temporary directory outside the repository that is
removed at the end. Nothing here records a hash or a byte of either: what is checked is the shape of what the machine
shows, so the test proves that the 2.05 boots on the A600 preset WITHOUT Gayle (no IDE, no PCMCIA), not that it is
compatible. Criteria, deduced from the pictures the units deliver:

  insert-disk screen  with no disk in DF0: from the Kickstart's own graphics, one flat background colour that covers most of
                      the raster plus several other colours, and a picture that keeps moving (the animation of the disk
                      entering the drive: a hung machine would freeze it).
  Workbench boot      with the disk in DF0 (a mount queued before the first unit): the machine leaves that screen for good. The
                      last pictures are none of the pictures the insert-disk run keeps cycling through, the dominant colour is
                      another one (the booted system opens on its own palette), and the screen has content (several colours).
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
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HEADLESS = ROOT / "build/link-audit/p6_headless"
KICKSTART_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_ZIP = os.environ.get("P6_WB205_ZIP")
UNITS = 1800            # the Workbench shell is up by unit ~1550 with the authorized 2.05 and 37.71
ROW = re.compile(r"unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=([0-9a-f]{16})(?: audio=\d+)?")


def member(path, suffix, size):
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(suffix)]
        if len(names) != 1 or archive.getinfo(names[0]).file_size != size:
            raise unittest.SkipTest(f"{path}: expected one {suffix} of {size} bytes")
        return archive.read(names[0])


def last_picture(directory):
    """(width, height, colour counter) of the one frame dump in `directory`: header of four uint32, then 32-bit pixels."""
    (path,) = list(Path(directory).glob("frame-*.bin"))
    data = path.read_bytes()
    width, height = struct.unpack_from("<II", data)
    pixels = array.array("I")
    pixels.frombytes(data[16:16 + width * height * 4])
    return width, height, Counter(pixels), data


def parse(stdout):
    rows = [ROW.fullmatch(line) for line in stdout.splitlines()[:-1]]
    assert all(rows), "a line of the runner has an unexpected shape"
    return rows


@unittest.skipUnless(KICKSTART_ZIP and WORKBENCH_ZIP and HEADLESS.is_file(),
                     "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (and build p6_headless) to run the A600 boot")
class A600BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="p6-a600-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        cls.rom, cls.adf = cls.temp / "kickstart.rom", cls.temp / "workbench.adf"
        cls.rom.write_bytes(member(KICKSTART_ZIP, ".rom", 524288))
        cls.adf.write_bytes(member(WORKBENCH_ZIP, ".adf", 901120))
        jobs = {"empty": (False, 1), "disk": (True, 1), "disk again": (True, 1)}
        with ThreadPoolExecutor(len(jobs)) as pool:
            futures = {name: pool.submit(cls.run_headless, name, *job) for name, job in jobs.items()}
            cls.runs = {name: future.result() for name, future in futures.items()}

    @classmethod
    def run_headless(cls, name, with_disk, seed):
        dump = cls.temp / name.replace(" ", "-")
        dump.mkdir()
        command = [str(HEADLESS), "--run", str(cls.rom), "--machine", "a600", "--units", str(UNITS), "--seed", str(seed),
                   "--dump", str(dump), "--dump-from", str(UNITS)]
        if with_disk:
            command += ["--disk", str(cls.adf)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return result.stdout, parse(result.stdout), last_picture(dump)
        raise AssertionError(f"{name}: the runner failed ({result.returncode}): {result.stderr[-400:]}")

    def test_the_a600_reaches_the_insert_disk_screen_and_keeps_animating(self):
        stdout, rows, (width, height, colours, _) = self.runs["empty"]
        self.assertEqual(len(rows), UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
        hashes = [r[6] for r in rows]
        self.assertGreater(len(set(hashes)), 4, "the picture hardly changed")
        self.assertGreater(len(set(hashes[-200:])), 1, "the last 200 units show one frozen picture: the machine is hung")
        self.assertGreater(colours.most_common(1)[0][1], width * height // 2, "no flat background that covers most of the raster")
        self.assertGreaterEqual(len(colours), 5, "the screen has no content besides its background")

    def test_the_workbench_in_df0_boots_to_another_screen(self):
        _, empty_rows, (_, _, empty_colours, _) = self.runs["empty"]
        _, rows, (width, height, colours, _) = self.runs["disk"]
        self.assertEqual(len(rows), UNITS)
        self.assertTrue(all(r[3] == "1" for r in rows))
        shown_without_disk = {r[6] for r in empty_rows[200:]}
        self.assertFalse({r[6] for r in rows[-100:]} & shown_without_disk,
                         "the machine still shows a picture of the insert-disk screen")
        self.assertNotEqual(colours.most_common(1)[0][0], empty_colours.most_common(1)[0][0],
                            "the dominant colour is that of the insert-disk screen")
        self.assertGreaterEqual(len(colours), 4, "the booted screen has no content")

    def test_the_same_seed_gives_the_same_units_and_the_same_last_picture(self):
        first, second = self.runs["disk"], self.runs["disk again"]
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2][3], second[2][3])


if __name__ == "__main__":
    unittest.main()
