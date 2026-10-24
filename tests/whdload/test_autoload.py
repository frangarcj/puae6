"""T65b2: real libretro autoload, provider validation and reversible HDF saves."""
import hashlib
import importlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from tests.whdload import support
from tests.whdload import test_cpp_bundle as cpp
from tests.whdload.test_cpp_bundle import BUILD, TOOL
from tests.whdload.test_bundle import SAVE, END, RMB_AT, RMB_UNTIL, SCORE_FIRE, SCORE_UNTIL, assert_saved
from tests.hdf.volume_support import lha_archive
from whdload import bundle, ffs, lha

lr = importlib.import_module("tests.libretro-host.test_libretro")


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cpp.CppBundleTests.setUpClass()

    def test_sha1_recognition_padding_matches_hashlib(self):
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            path = Path(temp) / "bytes"
            for size in (0, 3, 55, 56, 63, 64, 65, 512 * 1024):
                data = bytes(n % 251 for n in range(size))
                path.write_bytes(data)
                result = subprocess.run([str(TOOL), "sha1", str(path)], capture_output=True, text=True, check=True, timeout=20)
                self.assertEqual(hashlib.sha1(data).hexdigest(), result.stdout.strip())

    def test_auto_zip_with_slave_and_adf_is_a_pack_disabled_preserves_disk_detection(self):
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            path = Path(temp) / "pack.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Game/A.slave", b"slave")
                archive.writestr("Game/Data/disk.adf", b"game data")
            for mode, expected in (("auto", "volume"), ("disabled", "disk")):
                out = subprocess.run([str(TOOL), "zipkind", str(path), mode], check=True, capture_output=True, text=True, timeout=20)
                self.assertEqual(expected, out.stdout.strip())

    def test_missing_and_modified_provider_fail_clearly(self):
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            for data, message in ((None, "WHDLoad_usr.lha"), (lha_archive({"WHDLoad/C/WHDLoad": b"modified"}), "differs from pinned")):
                if data is not None:
                    (Path(temp) / "WHDLoad_usr.lha").write_bytes(data)
                out = subprocess.run([str(TOOL), "provider", temp], capture_output=True, text=True, timeout=20)
                self.assertEqual(2, out.returncode, out.stderr)
                self.assertIn(message, out.stderr)


@unittest.skipUnless(lr.TOOLS and support.AVAILABLE, support.SKIP)
class AutoloadTests(lr.LibretroHarness):
    def inputs(self):
        directory, system, saves = self.work(), self.work(), self.work()
        content = directory / "FatalMission.lha"
        shutil.copyfile(support.GAME, content)
        shutil.copyfile(support.WHDLOAD, system / "WHDLoad_usr.lha")
        shutil.copyfile(support.KICKSTART, system / lr.KICKSTART_NAMES_A1200[0])
        provider = {e.name: e.data for e in lha.read_archive(Path(support.WHDLOAD).read_bytes()) if not e.is_dir}
        game = {e.name: e.data for e in lha.read_archive(content.read_bytes()) if not e.is_dir}
        game["C/WHDLoad"] = provider["WHDLoad/C/WHDLoad"]
        images = bundle.build(game, provider["WHDLoad/C/WHDLoad.VFS"], kickstarts={"kick40068.A1200": Path(support.KICKSTART).read_bytes()})
        return content, system, saves, images

    def run_pack(self, content, system, saves, runs, *extra):
        return self.frontend("boot", "--runs", runs, "--save-dir", saves, "--set", "puae6_machine=A500", "--set", "puae6_hdf_save=on_close", *extra,
                             content=content, system=system).stdout

    def exports(self, images, saves):
        return {name: (saves / ("puae6-" + hashlib.sha256(image).hexdigest()[:24] + ".hdf")).read_bytes()
                if (saves / ("puae6-" + hashlib.sha256(image).hexdigest()[:24] + ".hdf")).exists() else image for name, image in images.items()}

    def test_auto_game_save_restore_and_next_session_keep_data_immutable(self):
        content, system, saves, images = self.inputs()
        before = content.read_bytes()
        options = ["--joy-at", "400:0:1", "--joy-at", "404:0:0", "--mouse-at", f"{RMB_AT}:0,0,2", "--mouse-at", f"{RMB_UNTIL}:0,0,0",
                   "--joy-at", f"{SCORE_FIRE}:0:1", "--joy-at", f"{SCORE_UNTIL}:0:0"]
        out = self.run_pack(content, system, saves, END + END - SAVE, *options, "--do", f"{SAVE}:save:s", "--do", f"{END}:save:first",
                            "--do", f"{END}:load:s", "--do", f"{END + END - SAVE}:save:replay", "--ahead", "1250:2")
        self.assertIn("machine A1200", out)
        self.assertIn("WHDLoad: three in-memory volumes, ButtonWait off", out)
        units = self.runs(out)
        keys = ("hash", "ahash", "ticks", "unit", "audio")
        self.assertEqual([tuple(u[k] for k in keys) for u in units[SAVE:END]], [tuple(u[k] for k in keys) for u in units[END:]])
        states = re.findall(r"state save (first|replay) ok=1 .* hash=(\w+)", out)
        self.assertEqual(2, len(states))
        self.assertEqual(states[0][1], states[1][1])
        assert_saved(self, images, self.exports(images, saves))
        self.assertEqual(before, content.read_bytes())
        again = self.run_pack(content, system, saves, 1)
        self.assertIn("earlier session saved", again)
        self.assertIn("puae6-" + hashlib.sha256(images["WHDSaves.hdf"]).hexdigest()[:24] + ".hdf", again)
        assert_saved(self, images, self.exports(images, saves))
        abandoned = self.work()
        self.run_pack(content, system, abandoned, END, *options, "--do", f"{SAVE}:save:s", "--do", f"{END}:load:s")
        self.assertFalse(any(p.lower().endswith("fm1.hisc") for p in ffs.read_volume(self.exports(images, abandoned)["WHDSaves.hdf"]).files))

    def test_missing_provider_precedes_firmware_and_disabled_preserves_volume_flow(self):
        content, system, saves, _ = self.inputs()
        (system / "WHDLoad_usr.lha").unlink()
        (system / lr.KICKSTART_NAMES_A1200[0]).unlink()
        out = self.frontend("boot", "--runs", 1, content=content, system=system, expect=3)
        self.assertIn("WHDLoad_usr.lha", out.stdout + out.stderr)
        self.assertNotIn("p6_init", out.stdout + out.stderr)
        directory = self.work()
        (directory / "A.slave").write_bytes(b"not a game")
        disabled = self.frontend("boot", "--runs", 1, "--set", "puae6_whdload=disabled", "--set", "puae6_machine=A1200",
                                 content=directory, system=self.system("boot", lr.KICKSTART_NAMES_A1200[0])).stdout
        self.assertIn("volume DH0", disabled)
        self.assertNotIn("WHDLoad_usr.lha", disabled)

    def test_game_preferences_are_data_and_buttonwait_uses_the_core_recipe(self):
        _, system, saves, _ = self.inputs()
        directory = self.work()
        (directory / "S").mkdir()
        files = {"A.slave": b"synthetic", "S/WHDLoad.prefs": b"SavePath=SYS:\n", "S/Startup-Sequence": b"untrusted"}
        for path, data in files.items():
            (directory / path).write_bytes(data)
        provider = {e.name: e.data for e in lha.read_archive(Path(support.WHDLOAD).read_bytes()) if not e.is_dir}
        expected = bundle.build({**files, "C/WHDLoad": provider["WHDLoad/C/WHDLoad"]}, provider["WHDLoad/C/WHDLoad.VFS"], buttonwait=True,
                                kickstarts={"kick40068.A1200": Path(support.KICKSTART).read_bytes()})
        out = self.run_pack(directory, system, saves, 1, "--set", "puae6_whdload_buttonwait=enabled")
        self.assertIn(hashlib.sha256(expected["RASystem.hdf"]).hexdigest(), out)
        self.assertIn("ButtonWait on", out)
