"""T30b: the real Fatal Mission, installed by tools/prepare_whdload.py, boots from its WHDLoad volume in the libretro core, takes input, writes to the volume and comes back from a state
(LR01, MED03, DET02), run by the minimal frontend of tests/libretro-host.

The content is the prepared .hdf (the one file the core is given), the Kickstart 3.1 of the owner goes to a temporary system directory under the name the core looks for, the A1200 is chosen with
the option puae6_machine. Player 1's pad is the joystick of Amiga port 2 (the second port): B is fire. F10 is the RETROK_F10 key. What the guest writes is exported by the core only when the
content closes (puae6_hdf_save=on_close), atomically, to puae6-<24 hex of the SHA-256 of the image>.hdf in the save directory, and that file is, byte for byte, what the native engine leaves after
the same units with the same input (the same test in tests/whdload/test_boot_native.py reads it back).

Opt-in (tests/whdload/support.py): P6_WHDLOAD_LHA, P6_FATALMISSION_LHA, P6_KICKSTART_A1200_ROM.
Run: python3 -m unittest tests.whdload.test_libretro_whdload
"""
import hashlib
from pathlib import Path
import re
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/libretro-host"))
import test_libretro as lr  # noqa: E402
from tests.whdload import support  # noqa: E402

RETROK_F10 = 291
JOY_B = 1
RUNS = 2000                      # the script has written exit.log long before (F10 at 1500)
SAVE_ON = ("--set", "puae6_hdf_save=on_close")
SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def play(*, fire=True, leave=True):
    """The input of the timeline of support.py, as the frontend's options."""
    options = []
    if fire:
        options += ["--joy-at", f"{support.FIRE_AT}:0:{JOY_B}", "--joy-at", f"{support.FIRE_UNTIL}:0:0"]
    if leave:
        options += ["--key-at", f"{support.QUIT_AT}:{RETROK_F10}:1", "--key-at", f"{support.QUIT_UNTIL}:{RETROK_F10}:0"]
    return options


@unittest.skipUnless(lr.TOOLS and support.AVAILABLE, support.SKIP)
class WhdloadLibretroTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.image, cls.manifest = support.prepare()
        cls.files = Path(cls.tmp.name) / "whdload-files"
        cls.files.mkdir()
        cls.hdf = cls.files / "fatal-mission.hdf"
        cls.hdf.write_bytes(cls.image)
        cls.system_dir = Path(cls.tmp.name) / "whdload-system"
        cls.system_dir.mkdir()
        shutil.copyfile(support.KICKSTART, cls.system_dir / lr.KICKSTART_NAMES_A1200[0])      # a temporary directory outside the repository, removed with the class
        cls.cache = {}

    def boot(self, runs, *options, save_dir=None):
        extra = ["--save-dir", save_dir] if save_dir else []
        return self.frontend("boot", "--runs", runs, *lr.A1200, *extra, *options, content=self.hdf, system=self.system_dir).stdout

    def played(self):
        """The whole game (fire, F10, the script writes exit.log): (output, the file the core exported when the content closed), made once."""
        if "played" not in self.cache:
            saves = self.work()
            text = self.boot(RUNS, *SAVE_ON, *play(), save_dir=saves)
            exported = saves / ("puae6-" + sha(self.image)[:24] + ".hdf")
            self.cache["played"] = (text, exported.read_bytes() if exported.exists() else None, sorted(p.name for p in saves.iterdir()))
        return self.cache["played"]

    def test_the_volume_boots_runs_whdload_and_the_core_says_what_it_mounted(self):
        text, _, _ = self.played()
        self.assertRegex(text, r"hard disk DH0 \(.*fatal-mission\.hdf\): 1760 blocks, one partition")
        runs = self.runs(text)
        self.assertEqual(RUNS, len(runs))
        self.assertTrue(all(r["video"] if "video" in r else True for r in runs))
        # WHDLoad's title and the game animate: the picture is not the one of a machine without a bootable disk
        insert_disk = self.runs(self.frontend("boot", "--runs", 700, *lr.A1200, content=None, system=self.system_dir).stdout)
        self.assertFalse({r["hash"] for r in insert_disk[400:700]} & {r["hash"] for r in runs[400:700]})
        self.assertGreater(len({r["hash"] for r in runs[support.TITLE_UNITS:support.FIRE_AT]}), 20, "the title is animated")
        self.assertEqual(self.image, self.hdf.read_bytes(), "the file the core was given is never touched")

    def test_fire_starts_the_game_and_the_run_is_the_same_until_the_fire(self):
        self.assertIn("exit.log", support.volume(self.played()[1]).files)
        quiet = self.runs(self.boot(900, *play(fire=False, leave=False)))
        pressed = self.runs(self.boot(900, *play(leave=False)))
        before = support.FIRE_AT
        self.assertEqual([r["hash"] for r in quiet[:before - 1]], [r["hash"] for r in pressed[:before - 1]], "the same machine until the button goes down")
        self.assertFalse({r["hash"] for r in quiet[before + 20:]} & {r["hash"] for r in pressed[before + 20:]}, "the fire changed what the game shows")

    def test_f10_quits_whdload_and_the_script_writes_to_the_volume_which_is_exported_atomically_when_the_content_closes(self):
        text, exported, names = self.played()
        self.assertEqual(["puae6-" + sha(self.image)[:24] + ".hdf"], names)
        self.assertEqual(len(self.image), len(exported))
        volume, original = support.volume(exported), support.volume(self.image)
        self.assertEqual({"boot.log": b"booted\n", "exit.log": b"game ended\n"}, {k: v for k, v in volume.files.items() if k not in original.files})
        for path, data in original.files.items():
            self.assertEqual(data, volume.files[path], path)
        changed = [c for c in range(0, len(exported), 4096) if exported[c:c + 4096] != self.image[c:c + 4096]]
        self.assertTrue(changed)
        self.assertNotIn("changes are discarded", text)
        discarded = self.work()
        self.assertRegex(self.boot(RUNS, *play(), save_dir=discarded), r"1 hard disk\(s\) were changed and the changes are discarded")
        self.assertEqual([], sorted(p.name for p in discarded.iterdir()))

    def test_the_exported_volume_is_the_one_the_native_engine_leaves_for_the_same_input(self):
        import subprocess
        _, exported, _ = self.played()
        out = self.work() / "native.out"
        result = subprocess.run([str(self.probe), support.KICKSTART, "--machine", "a1200", "--units", str(RUNS), "--every", "1000", "--hdf", str(self.hdf), *support.fire(), *support.quit_key(),
                                 "--export-at", str(RUNS), "0", str(out)], capture_output=True, text=True, timeout=1800)
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        self.assertEqual(out.read_bytes(), exported, "the libretro core and the native engine disagree about what the guest wrote")

    def test_the_same_run_twice_is_the_same_pictures_audio_and_volume(self):
        text, exported, _ = self.played()
        saves = self.work()
        again = self.boot(RUNS, *SAVE_ON, *play(), save_dir=saves)
        key = lambda r: (r["n"], r["size"], r["hash"], r["ahash"], r["unit"], r["ticks"])
        self.assertEqual([key(r) for r in self.runs(text)], [key(r) for r in self.runs(again)])
        self.assertEqual(exported, next(saves.iterdir()).read_bytes())

    def test_a_state_taken_before_the_script_writes_comes_back_in_the_same_process_and_in_another_one(self):
        EARLY = 120                                   # DOS has not run the script yet
        saves = self.work()
        runs = RUNS + (RUNS - EARLY)
        text = self.boot(runs, *SAVE_ON, *play(), "--do", f"{EARLY}:save:early", "--do", f"{RUNS}:save:late", "--do", f"{RUNS}:load:early", "--do", f"{runs}:save:again", save_dir=saves)
        saved = {m[1]: m[5] for m in SAVE.finditer(text)}
        self.assertNotEqual(saved["early"], saved["late"], "the writes of the script are part of the state")
        self.assertEqual(saved["again"], saved["late"], "the early state replays to the same machine, the volume's writes included")
        self.assertTrue(all(m[3] == "1" for m in LOAD.finditer(text)))
        first = [r["hash"] for r in self.runs(text)]
        self.assertEqual(first[EARLY:RUNS], first[RUNS:runs], "every picture after the load is the one that followed the save")
        # Another process: the state of the middle of the game brings the machine, its overlay and the position back, and the run goes on to the volume the straight run had.
        MID = 700
        state = self.work() / "mid.state"
        self.boot(MID, *play(leave=False), "--do", f"{MID}:savefile:{state}")
        other = self.work()
        second = self.boot(60, *SAVE_ON, "--do", f"0:loadfile:{state}:pos={MID}", *play(), save_dir=other)
        self.assertEqual("1", LOAD.search(second)[3])
        straight = self.runs(self.boot(MID + 60, *play()))
        self.assertEqual([r["hash"] for r in straight[MID:MID + 60]], [r["hash"] for r in self.runs(second)][:60], "a new process goes on as the original did")

    def test_the_sound_after_a_state_of_the_game_in_play_is_the_one_the_original_made(self):
        # The finding of T30b that T15m closed (tests/whdload/test_det02_whdload.py): the pictures after the load were the original's and the PCM of the game's steady tone another waveform, because
        # Paula mixed the volume of the register where the original mixed the one it had latched at its last period (patch 0250).
        MID = 700
        state = self.work() / "mid.state"
        self.boot(MID, *play(leave=False), "--do", f"{MID}:savefile:{state}")
        second = self.boot(60, "--do", f"0:loadfile:{state}:pos={MID}", *play())
        straight = self.runs(self.boot(MID + 60, *play()))
        self.assertEqual([r["ahash"] for r in straight[MID:MID + 60]], [r["ahash"] for r in self.runs(second)][:60])


if __name__ == "__main__":
    unittest.main()
