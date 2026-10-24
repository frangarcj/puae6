"""T30a, MED03: the runner is given the image and nothing else; a folder is not a medium, and the same prepared image replays to the same units (micro-ROM, no Kickstart).

The preparation reads host files and folders; the replay reads ONE file per hard disk, whole, and the engine mounts hard files only (patches 0161-0163, ports/portable/hostfs_unsupported.cpp). Here the
headless runner is pointed at a folder, at a file that is not a whole number of blocks and at an image prepared from synthetic inputs.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe, builder
import prepare_whdload as prep
from tests.whdload.test_prepare import GAME, TOOL, archive, config


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="p6-whd-replay-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.rom = cls.temp / "boot.rom"
        cls.rom.write_bytes(builder.build((ROOT / "tests/fixtures/microrom/boot.s").read_text(encoding="utf-8")))
        game, tool = cls.temp / "game.lha", cls.temp / "tool.lha"
        game.write_bytes(archive(GAME))
        tool.write_bytes(archive(TOOL, 2))
        cls.images = []
        for name in ("a", "b"):
            image, manifest, _ = prep.prepare(config(), {"game": str(game), "tool": str(tool)}, {})
            path = cls.temp / (name + ".hdf")
            path.write_bytes(image)
            cls.images.append(path)

    def run_headless(self, *media, units=4):
        return subprocess.run([str(self.headless), "--run", str(self.rom), "--units", str(units), "--seed", "1", *media], capture_output=True, text=True, timeout=300, cwd=self.temp)

    def test_a_folder_is_not_a_hard_disk(self):
        folder = self.temp / "installed"
        (folder / "Game").mkdir(parents=True, exist_ok=True)
        (folder / "Game/Game.slave").write_bytes(b"slave")
        result = self.run_headless("--hdf", str(folder))
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("done units", result.stdout)

    def test_a_file_that_is_not_a_whole_number_of_blocks_is_refused(self):
        odd = self.temp / "odd.hdf"
        odd.write_bytes(self.images[0].read_bytes()[:-100])
        result = self.run_headless("--hdf", str(odd))
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("done units", result.stdout)

    def test_two_preparations_replay_to_the_same_units_and_the_image_is_not_touched(self):
        before = self.images[0].read_bytes()
        first, second = self.run_headless("--hdf", str(self.images[0])), self.run_headless("--hdf", str(self.images[1]))
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("done units=4", first.stdout)
        self.assertEqual(before, self.images[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
