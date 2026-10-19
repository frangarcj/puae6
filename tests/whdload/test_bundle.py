"""T65a: deterministic three-role bundle and opt-in real saves/replay."""
import hashlib
import importlib
import io
from pathlib import Path
import re
import shutil
import struct
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
# Observed phases of this pinned installation: game after initial decompression
# at 900; RMB there reaches the score table by 1200 (including its 200 VBL wait).
# Score zero needs no name entry: one Fire then executes the slave's SaveFile
# hook at FM1+$aca. By 1700 the title is visible again after decompression.
SAVE, END = 900, 1700
RMB_AT, RMB_UNTIL, SCORE_FIRE, SCORE_UNTIL = 901, 908, 1201, 1208


def prefs_probe_program():
    """Original AmigaDOS v36 HUNK: copy the assigned prefs to the save volume.

    Open/Read/Write/Close use dos.library -30/-42/-48/-36; this checks the
    mounted filesystem and the S: assignment without pretending to save a game.
    """
    code, labels, fixups = bytearray(), {}, []
    def words(*values):
        code.extend(struct.pack(">" + "H" * len(values), *(v & 0xffff for v in values)))
    def relative(opcode, label):
        words(opcode)
        fixups.append((len(code), label))
        words(0)
    words(0x48e7, 0x3c02, 0x2c78, 4)  # preserve d2-d5/a6; execbase
    relative(0x43fa, "dos")
    words(0x7024, 0x4eae, -552, 0x4a80)
    relative(0x6700, "fail")
    words(0x2c40)
    relative(0x41fa, "source")
    words(0x2208, 0x243c, 0, 1005, 0x4eae, -30, 0x2a00)  # MODE_OLDFILE; d5=fh
    relative(0x6700, "closefail")
    words(0x2205)
    relative(0x41fa, "buffer")
    words(0x2408, 0x263c, 0, 512, 0x4eae, -42, 0x2800, 0x2205, 0x4eae, -36)
    words(0x4a84)
    relative(0x6f00, "closefail")  # read must return positive bytes
    relative(0x41fa, "destination")
    words(0x2208, 0x243c, 0, 1006, 0x4eae, -30, 0x2a00)  # MODE_NEWFILE
    relative(0x6700, "closefail")
    words(0x2205)
    relative(0x41fa, "buffer")
    words(0x2408, 0x2604, 0x4eae, -48, 0x9880, 0x2205, 0x4eae, -36)  # d4=expected-written
    relative(0x6000, "close")
    labels["closefail"] = len(code)
    words(0x7814)
    labels["close"] = len(code)
    words(0x224e, 0x2c78, 4, 0x4eae, -414, 0x2004)
    relative(0x6000, "end")
    labels["fail"] = len(code)
    words(0x7014)
    labels["end"] = len(code)
    words(0x4cdf, 0x403c, 0x4e75)
    for name, value in (("dos", b"dos.library\0"), ("source", b"S:WHDLoad.prefs\0"),
                        ("destination", b"WHDSaves:read-prefs.txt\0"), ("buffer", bytes(512))):
        labels[name] = len(code)
        code.extend(value)
        code.extend(b"\0" * (len(code) % 2))
    for at, label in fixups:
        struct.pack_into(">h", code, at, labels[label] - at)
    code.extend(b"\0" * (-len(code) % 4))
    size = len(code) // 4
    return struct.pack(">8I", 1011, 0, 1, 0, 0, size, 1001, size) + code + struct.pack(">I", 1010)


@unittest.skipUnless(lr.TOOLS and support.AVAILABLE, support.SKIP)
class BundleLibretroTests(lr.LibretroHarness):
    def title_exit(self, *, probe=False, replay=False):
        images = prepared()
        if probe:
            tree = dict(ffs.read_volume(images["WHDLoad.hdf"]).files)
            tree["C/P6PrefsProbe"] = prefs_probe_program()
            tree["S/Startup-Sequence"] = tree["S/Startup-Sequence"].replace(b"C:WHDLoad SLAVE=", b"C:P6PrefsProbe\nC:WHDLoad SLAVE=")
            root = ffs.build_tree(tree)
            images["WHDLoad.hdf"] = ffs.write_volume(root, "WHDLoad", blocks=ffs.volume_blocks(root, free_blocks=2048))
        directory, saves, system = self.work(), self.work(), self.work()
        for name, image in images.items():
            (directory / name).write_bytes(image)
        content = directory / "bundle.m3u"
        content.write_text("\n".join(images) + "\n")
        shutil.copyfile(support.KICKSTART, system / lr.KICKSTART_NAMES_A1200[0])
        options = ["--do", "350:save:s", "--do", "450:save:first", "--do", "450:load:s", "--do", "550:save:replay"] if replay else []
        out = self.frontend("boot", "--runs", 550 if replay else 450, *lr.A1200, "--mouse-at", "400:0,0,2", "--mouse-at", "404:0,0,0",
                            "--set", "puae6_hdf_save=on_close", *options, "--save-dir", saves, content=content, system=system).stdout
        exported = {}
        for name, image in images.items():
            self.assertEqual(image, (directory / name).read_bytes())
            saved = saves / ("puae6-" + hashlib.sha256(image).hexdigest()[:24] + ".hdf")
            exported[name] = saved.read_bytes() if saved.exists() else image
        return images, exported, out

    def test_assigned_prefs_are_readable_and_save_volume_is_writable(self):
        images, exported, _ = self.title_exit(probe=True)
        prefs = ffs.read_volume(images["RASystem.hdf"]).files["S/WHDLoad.prefs"]
        self.assertEqual(prefs, ffs.read_volume(exported["WHDSaves.hdf"]).files["read-prefs.txt"])
        self.assertEqual(images["RASystem.hdf"], exported["RASystem.hdf"])

    def test_title_rmb_exit_does_not_invoke_the_highscore_save(self):
        # Slave source patches $aca (end-of-game scores); title RMB goes from
        # $706 to $ada and returns without executing that SaveFile hook.
        images, exported, out = self.title_exit(replay=True)
        units = self.runs(out)
        keys = ("hash", "ahash", "ticks", "unit", "audio")
        self.assertEqual([tuple(u[k] for k in keys) for u in units[350:450]], [tuple(u[k] for k in keys) for u in units[450:]])
        self.assertEqual({}, ffs.read_volume(exported["WHDSaves.hdf"]).files)
        self.assertEqual(images["RASystem.hdf"], exported["RASystem.hdf"])

    def test_real_highscore_save_and_restore_keep_data_immutable(self):
        images = prepared()
        directory, saves = self.work(), self.work()
        for name, image in images.items():
            (directory / name).write_bytes(image)
        content = directory / "bundle.m3u"
        content.write_text("\n".join(images) + "\n")
        system = self.work()
        shutil.copyfile(support.KICKSTART, system / lr.KICKSTART_NAMES_A1200[0])
        options = ["--joy-at", "400:0:1", "--joy-at", "404:0:0", "--mouse-at", f"{RMB_AT}:0,0,2", "--mouse-at", f"{RMB_UNTIL}:0,0,0",
                   "--joy-at", f"{SCORE_FIRE}:0:1", "--joy-at", f"{SCORE_UNTIL}:0:0"]
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
        for unit in range(1, END + 1):
            fields = lines[unit - 1].split()
            fields[4] = "2"  # same mouse on Amiga port 1 as the LR frontend
            fields[5] = "2" if RMB_AT <= unit < RMB_UNTIL else "0"
            if SCORE_FIRE <= unit < SCORE_UNTIL:
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
