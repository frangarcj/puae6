"""MED01 through the headless runner (T57): `p6_headless --disk` takes an extended ADF and an FDI as it takes an ADF, runs the guest that reads them, and refuses a damaged one with the bridge's reason.

The runner is the one the link test builds; the guest is the micro-ROM of tests/media/trackdisk.py (cylinder 0, heads 0 and 1 by disk DMA). The same image gives the same output in two processes.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import builder
from tests.media import fdi, trackdisk as td
from tests.scenarios.disk import disk

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/link-audit"


@unittest.skipUnless(all(shutil.which(t) for t in ("cmake", "ninja", "clang++")), "toolchain unavailable")
class HeadlessFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.build_support import build_targets
        cls.exe = build_targets(BUILD, ["p6_headless"], sanitizers=False)["p6_headless"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-headless-formats-")
        directory = Path(cls.tmp.name)
        cls.rom = directory / "trackdisk.rom"
        cls.rom.write_bytes(builder.build(td.rom_source()))
        adf = disk.make_adf(0x71)
        dos = [("dos", td.dos_track(adf, t)) for t in range(160)]
        cls.images = {
            "ext2": td.ext2_image([("raw", td.raw_track(3), td.RAW_WORDS * 16)] + dos[1:]),
            "ext1": td.ext1_image([("raw", td.SYNC, bytes(range(100)))] + dos[1:]),
            "fdi": fdi.fdi_image([("amiga", td.dos_track(adf, 0), 0), ("amiga", td.dos_track(adf, 1), 0)]),
            "plain": adf,
        }
        cls.paths = {}
        for name, image in cls.images.items():
            cls.paths[name] = directory / f"{name}.img"
            cls.paths[name].write_bytes(image)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_headless(self, name, units=40):
        return subprocess.run([str(self.exe), "--run", str(self.rom), "--disk", str(self.paths[name]), "--units", str(units)], capture_output=True, text=True, timeout=600)

    def test_the_formats_run_and_the_same_image_gives_the_same_run(self):
        for name in self.images:
            with self.subTest(name):
                first = self.run_headless(name)
                self.assertEqual(first.returncode, 0, first.stderr[-400:])
                again = self.run_headless(name)
                self.assertEqual(again.stdout, first.stdout)

    def test_a_damaged_extended_adf_or_fdi_is_refused_with_a_reason(self):
        for name, cut in (("ext2", 5000), ("ext1", 5000), ("fdi", 3000)):
            with self.subTest(name):
                path = Path(self.tmp.name) / f"cut-{name}.img"
                path.write_bytes(self.images[name][:cut])
                run = subprocess.run([str(self.exe), "--run", str(self.rom), "--disk", str(path), "--units", "5"], capture_output=True, text=True, timeout=300)
                self.assertNotEqual(run.returncode, 0)
                self.assertRegex(run.stderr, re.compile(r"extended ADF|FDI|disk image", re.I))


if __name__ == "__main__":
    unittest.main()
