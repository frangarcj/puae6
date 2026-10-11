"""T30a, MED03/SRC01 with the real inputs (opt-in; nothing is downloaded and nothing is copied into the repository).

    P6_WHDLOAD_LHA        WHDLoad_usr.lha from https://whdload.de/whdload/WHDLoad_usr.lha (the 20.0 build 7051 package the config pins)
    P6_FATALMISSION_LHA   FatalMission.lha from https://whdload.de/games/FatalMission.lha (the data of the game is in it)

Both are read in memory by tools/prepare_whdload.py. With them the config config/whdload/fatal-mission.json makes ONE image on every machine: its SHA-256 is pinned in the config
(`image_sha256`), so a preparation that differs from the one the pin was made with fails by itself.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import prepare_whdload as prep
from tests.whdload import lha_fixture      # noqa: F401  (puts tools/ on sys.path)
from whdload import ffs

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/whdload/fatal-mission.json"
WHDLOAD, GAME = os.environ.get("P6_WHDLOAD_LHA"), os.environ.get("P6_FATALMISSION_LHA")


@unittest.skipUnless(WHDLOAD and GAME, "BLOCKED: set P6_WHDLOAD_LHA and P6_FATALMISSION_LHA (free downloads, see config/whdload/fatal-mission.json) to prepare the real image")
class FatalMissionPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="p6-whd-real-")
        cls.addClassCleanup(temp.cleanup)
        cls.temp = Path(temp.name)
        cls.config = prep.load_config(CONFIG)

    def prepare(self, name, whdload=WHDLOAD, game=GAME):
        out = self.temp / name
        with contextlib.redirect_stdout(io.StringIO()):
            rc = prep.main(["prepare", str(CONFIG), "--input", "whdload=" + whdload, "--input", "game=" + game, "--out", str(out)])
        self.assertEqual(0, rc)
        return out / "fatal-mission.hdf", out / "fatal-mission.manifest.json"

    def test_two_preparations_from_different_places_give_the_pinned_image_and_the_same_manifest(self):
        moved = self.temp / "moved"
        moved.mkdir()
        shutil.copy(WHDLOAD, moved / "w.lha")
        shutil.copy(GAME, moved / "g.lha")
        first, second = self.prepare("a"), self.prepare("b", str(moved / "w.lha"), str(moved / "g.lha"))
        self.assertEqual(first[0].read_bytes(), second[0].read_bytes())
        self.assertEqual(first[1].read_bytes(), second[1].read_bytes())
        manifest = json.loads(first[1].read_text(encoding="utf-8"))
        self.assertEqual(self.config["image_sha256"], manifest["image"]["sha256"])
        self.assertTrue(manifest["complete"])
        self.assertFalse(manifest["redistributable"], "WHDLoad's terms are not a redistribution grant: the image is for the user who supplied it")
        self.assertEqual("WHDLoad 20.0 [build 7051] (27.03.2026)", next(f["version"] for f in manifest["files"] if f["path"] == "C/WHDLoad"))

    def test_the_image_holds_exactly_the_pinned_files_and_the_generated_boot(self):
        image = self.prepare("c")[0].read_bytes()
        volume = ffs.read_volume(image)
        self.assertEqual(901120, len(image))                   # 880 KiB: what the state probe's export buffer holds
        self.assertEqual(("DOS\x01", "WHDGame"), (volume.dostype.decode("latin-1"), volume.label))
        self.assertEqual(sorted(f["to"] for f in self.config["files"]), sorted(volume.files))
        startup = volume.files["S/Startup-Sequence"].decode("latin-1")
        self.assertIn("WHDLoad SLAVE=DH0:FatalMission/FatalMission.slave", startup)
        self.assertNotIn("\r", startup)

    def test_another_whdload_version_is_refused_not_mixed_in(self):
        other = self.temp / "other.lha"
        other.write_bytes(Path(GAME).read_bytes())             # a valid archive that has no WHDLoad in it
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = prep.main(["prepare", str(CONFIG), "--input", "whdload=" + str(other), "--input", "game=" + GAME, "--out", str(self.temp / "d")])
        self.assertEqual(2, rc)
        self.assertIn("WHDLoad/C/WHDLoad is not in input whdload", err.getvalue())
        self.assertFalse((self.temp / "d").exists())


if __name__ == "__main__":
    unittest.main()
