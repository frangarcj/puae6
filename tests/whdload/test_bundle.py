"""T65a: deterministic three-role bundle and opt-in real saves/replay."""
import hashlib
import importlib
import io
from pathlib import Path
import re
import shutil
import unittest
import zipfile

from tests.whdload import support
from whdload import bundle, ffs


class BundleTests(unittest.TestCase):
    def test_roles_data_and_buttonwait_are_reproducible(self):
        files = {"C/WHDLoad": b"program", "S/Startup-Sequence": b"ignored", "Game/A.slave": b"slave", "Game/data/x": b"level",
                 "Devs/Kickstarts/kick40068.A1200.RTB": b"user relocation data"}
        a = bundle.build(files, b"vfs", kickstarts={"kick40068.A1200": b"private"})
        self.assertEqual(a, bundle.build(dict(reversed(list(files.items()))), b"vfs", kickstarts={"kick40068.A1200": b"private"}))
        boot, system, saves = (ffs.read_volume(a[n]) for n in ("WHDLoad.hdf", "RASystem.hdf", "WHDSaves.hdf"))
        self.assertEqual((boot.label, system.label, saves.label), ("WHDLoad", "RASystem", "WHDSaves"))
        self.assertEqual(saves.files, {})
        self.assertIn(b"DATA=RASystem:Game.zip", boot.files["S/Startup-Sequence"])
        self.assertIn(b"SavePath=WHDSaves:", system.files["S/WHDLoad.prefs"])
        self.assertEqual(system.files["Devs/Kickstarts/kick40068.A1200"], b"private")
        self.assertEqual(system.files["Devs/Kickstarts/kick40068.A1200.RTB"], b"user relocation data")
        with zipfile.ZipFile(io.BytesIO(system.files["Game.zip"])) as archive:
            self.assertEqual({n: archive.read(n) for n in archive.namelist()}, {"A.slave": b"slave", "data/x": b"level"})
            for entry in archive.infolist():
                self.assertEqual(entry.create_system, 1, "WHDLoad.VFS requires an Amiga creator")
                self.assertEqual(((entry.external_attr >> 16) & 0xff) ^ 0x0f, 0, "Amiga RWED permissions")
        waited = bundle.build(files, b"vfs", buttonwait=True)
        self.assertIn(b"\nButtonWait\n", ffs.read_volume(waited["RASystem.hdf"]).files["S/WHDLoad.prefs"])
        self.assertEqual(waited["WHDLoad.hdf"], a["WHDLoad.hdf"])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            bundle.build({**files, "Other/B.slave": b"ambiguous"}, b"vfs")


def prepared():
    return bundle.prepare(support.prep.load_config(support.CONFIG), {"whdload": support.WHDLOAD, "game": support.GAME}, {}, kickstarts=[support.KICKSTART])


def assert_saved(case, images, exported):
    system = ffs.read_volume(exported["RASystem.hdf"])
    case.assertEqual(system.files["Game.zip"], ffs.read_volume(images["RASystem.hdf"]).files["Game.zip"], "DATA archive stayed immutable")
    saves = ffs.read_volume(exported["WHDSaves.hdf"])
    found = {p: b for p, b in saves.files.items() if p.lower().endswith("fm1.hisc")}
    case.assertEqual(1, len(found), f"resload_SaveFile did not write FM1.hisc: {list(saves.files)}")
    case.assertEqual(112, len(next(iter(found.values()))))


lr = importlib.import_module("tests.libretro-host.test_libretro")
SAVE, END = 700, 6000


@unittest.skipUnless(lr.TOOLS and support.AVAILABLE, support.SKIP)
class BundleLibretroTests(lr.LibretroHarness):
    def test_real_highscore_save_and_restore_keep_data_immutable(self):
        images = prepared()
        directory, saves = self.work(), self.work()
        for name, image in images.items():
            (directory / name).write_bytes(image)
        content = directory / "bundle.m3u"
        content.write_text("\n".join(images) + "\n")
        system = self.work()
        shutil.copyfile(support.KICKSTART, system / lr.KICKSTART_NAMES_A1200[0])
        options = ["--joy-at", "400:0:1", "--joy-at", "404:0:0", "--joy-at", "800:0:1", "--joy-at", "804:0:0"]
        out = self.frontend("boot", "--runs", END + END - SAVE, *lr.A1200, *options, "--set", "puae6_hdf_save=on_close",
                            "--do", f"{SAVE}:save:s", "--do", f"{END}:save:first", "--do", f"{END}:load:s",
                            "--do", f"{END + END - SAVE}:save:replay", "--save-dir", saves, content=content, system=system).stdout
        units = self.runs(out)
        keys = ("hash", "ahash", "ticks", "unit", "audio")
        self.assertEqual([tuple(u[k] for k in keys) for u in units[SAVE:END]], [tuple(u[k] for k in keys) for u in units[END:]])
        states = re.findall(r"state save (first|replay) ok=1 .* hash=(\w+)", out)
        self.assertEqual(2, len(states))
        self.assertEqual(states[0][1], states[1][1])
        exported = {}
        for name, image in images.items():
            self.assertEqual(image, (directory / name).read_bytes())
            saved = saves / ("puae6-" + hashlib.sha256(image).hexdigest()[:24] + ".hdf")
            exported[name] = saved.read_bytes() if saved.exists() else image
        assert_saved(self, images, exported)


wb = importlib.import_module("tests.whdload.test_waterbox_whdload")
wb_support = importlib.import_module("tests.waterbox-media.support")


@unittest.skipUnless(wb.AVAILABLE and support.AVAILABLE, support.SKIP)
class BundleWaterboxTests(wb.Sessions):
    FIRMWARE = {}

    def test_real_highscore_save_replays_in_same_and_new_sandbox(self):
        images = prepared()
        paths = []
        for name, image in images.items():
            path = self.out / name
            path.write_bytes(image)
            paths.append(str(path))
        (self.media / "bundle.rom").write_bytes(Path(support.KICKSTART).read_bytes())
        self.files_before = wb_support.tree(self.media)
        script = wb.write_script(self.out / "bundle.script", units=END, leave=False)
        lines = script.read_text().splitlines()
        for unit in range(800, 804):
            fields = lines[unit - 1].split()
            fields[9] = "1"
            lines[unit - 1] = " ".join(fields)
        script.write_text("\n".join(lines) + "\n")
        core = lambda n: self.core(n, adf="", writes=False, hdf=",".join(paths), machine="a1200")
        exports = [f"export A {i} file={self.out / ('export-' + name)}" for i, name in enumerate(images)]
        out = self.session([core("A"), f"run A {SAVE} script={script}", "save A s", f"run A {END - SAVE} script={script}", "digest A first", *exports,
                            "load A s", f"run A {END - SAVE} script={script}", "digest A replay", core("B"), "load B s",
                            f"run B {END - SAVE} script={script}", "digest B fresh"], firmware="bundle")
        a, b = ([u[1:] for u in self.units(out, n)] for n in "AB")
        self.assertEqual(a[SAVE:END], a[END:])
        self.assertEqual(a[SAVE:END], b)
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual(3, len(digests))
        self.assertEqual(1, len(set(digests.values())))
        assert_saved(self, images, {name: (self.out / ("export-" + name)).read_bytes() for name in images})
        for name, image in images.items():
            self.assertEqual(image, (self.out / name).read_bytes())
