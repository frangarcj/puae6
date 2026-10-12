"""T30b: the real Fatal Mission, installed by tools/prepare_whdload.py, boots from its WHDLoad volume in the BizHawk core inside the real Waterbox sandbox, takes input, writes to the volume and
comes back from states (WB03, MED03, DET02), through the managed harness of tests/ffi (Puae6Core, A1200 with the owner's Kickstart 3.1).

The core is given the prepared image as its hard file (Puae6Parameters.HardFiles, no floppy) and the Kickstart as its firmware. The input is a script of one line per unit (tests/ffi/CoreCommands.cs):
fire on port 2's joystick from unit 400 to 403 (the second Amiga port, which BizHawk calls P2) and F10 from 1500 to 1503. What the guest writes is exported with `export` (media index 0: the base with
the sandbox's overlay) and is, byte for byte, what the native engine leaves after the same units with the same input (tests/whdload/test_boot_native.py), and a state taken before the script writes
(unit 120) replays to the same machine in the same sandbox, in a new sandbox of the same process and in another process (WB03, WB04).

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet, the local NuGet cache and the opt-in inputs of tests/whdload/support.py; skips without them.
    python3 -m unittest tests.whdload.test_waterbox_whdload
"""
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/waterbox-media"))
from support import AVAILABLE, SKIP, Sessions, sha  # noqa: E402
from tests.whdload import support  # noqa: E402

UNITS, EARLY = 2000, 120
KEY_WORD, KEY_BIT = support.F10 // 32, support.F10 % 32


def script_line(unit):
    """The input of 1-based unit `unit`, as the line of the 0-based unit before it: "k0 k1 k2 k3 p0type p0buttons p0dx p0dy p1type p1buttons p1dx p1dy"."""
    keys = [0, 0, 0, 0]
    if support.QUIT_AT <= unit < support.QUIT_UNTIL:
        keys[KEY_WORD] |= 1 << KEY_BIT
    fire = 1 if support.FIRE_AT <= unit < support.FIRE_UNTIL else 0
    return " ".join([*(f"{k:08x}" for k in keys), "0 0 0 0", "1", str(fire), "0 0"])


def write_script(path, units=UNITS, fire=True, leave=True):
    lines = []
    for unit in range(1, units + 1):
        line = script_line(unit).split()
        if not leave:
            line[:4] = ["00000000"] * 4
        if not fire:
            line[9] = "0"
        lines.append(" ".join(line))
    path.write_text("\n".join(lines) + "\n")
    return path


@unittest.skipUnless(AVAILABLE and support.AVAILABLE, SKIP if not support.AVAILABLE else support.SKIP)
class WhdloadWaterboxTests(Sessions):
    FIRMWARE = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        image, cls.manifest = support.prepare()
        cls.image = image
        (cls.media / "fatal-mission.hdf").write_bytes(image)
        (cls.media / "ks31.rom").write_bytes(Path(support.KICKSTART).read_bytes())
        cls.script = write_script(cls.out / "game.script")
        cls.quiet = write_script(cls.out / "quiet.script", fire=False, leave=False)
        cls.files_before = {path.name: sha(path.read_bytes()) for path in sorted(cls.media.iterdir())}
        cls.cache = {}

    def tearDown(self):
        # the same rule as Sessions.tearDown, for the files of this class
        self.assertEqual(self.files_before, {path.name: sha(path.read_bytes()) for path in sorted(self.media.iterdir())}, "no file the core was given changed")

    def gcore(self, name):
        return self.core(name, adf="", writes=False, hdf=str(self.media / "fatal-mission.hdf"), machine="a1200")

    def run_game(self, lines, **kwargs):
        return self.session(lines, firmware="ks31", **kwargs)

    def played(self):
        """The whole game (fire, F10, the script writes exit.log) in one sandbox: (output, the exported volume), made once."""
        if "played" not in self.cache:
            exported = self.out / "played.hdf"
            out = self.run_game([self.gcore("G"), f"run G {UNITS} script={self.script}", "digest G end", "status G 0", f"export G 0 file={exported}"])
            self.cache["played"] = (out, exported.read_bytes())
        return self.cache["played"]

    def test_the_volume_boots_whdload_runs_and_the_script_writes_to_it(self):
        out, exported = self.played()
        units = self.units(out, "G")
        self.assertEqual(UNITS, len(units))
        volume, original = support.volume(exported), support.volume(self.image)
        self.assertEqual({"boot.log": b"booted\n", "exit.log": b"game ended\n"}, {k: v for k, v in volume.files.items() if k not in original.files})
        for path, data in original.files.items():
            self.assertEqual(data, volume.files[path], path)
        self.assertGreater(len({u[3] for u in units[support.TITLE_UNITS:support.FIRE_AT]}), 20, "the title is animated")
        (status,) = self.statuses(out, "G")
        self.assertEqual((0, -(-len(self.image) // 4096)), (status["result"], status["capacity"]))
        self.assertGreaterEqual(status["used"], 1)

    def test_the_volume_is_the_one_the_native_engine_leaves_for_the_same_input(self):
        _, exported = self.played()
        out = self.out / "native.out"
        from tests.state.harness import built
        probe = built("p6_state_probe")
        hdf = self.out / "native.hdf"
        hdf.write_bytes(self.image)
        result = subprocess.run([str(probe), support.KICKSTART, "--machine", "a1200", "--units", str(UNITS), "--every", "1000", "--hdf", str(hdf), *support.fire(), *support.quit_key(),
                                 "--export-at", str(UNITS), "0", str(out)], capture_output=True, text=True, timeout=3600)
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        self.assertEqual(out.read_bytes(), exported, "the Waterbox core and the native engine disagree about what the guest wrote")

    def test_fire_changes_what_the_game_shows_and_nothing_before_the_button_goes_down(self):
        quiet = self.run_game([self.gcore("Q"), f"run Q 700 script={self.quiet}"])
        out, _ = self.played()
        pressed, still = self.units(out, "G")[:700], self.units(quiet, "Q")
        before = support.FIRE_AT
        self.assertEqual([u[3] for u in still[:before - 1]], [u[3] for u in pressed[:before - 1]], "the same machine until the button goes down")
        self.assertTrue({u[3] for u in still[before + 20:]}.isdisjoint({u[3] for u in pressed[before + 20:]}), "the fire changed what the game shows")

    def test_wb03_a_state_taken_before_the_script_writes_replays_the_game_the_same_in_the_same_sandbox_a_new_one_and_another_process(self):
        m = UNITS - EARLY
        state, first_volume, again_volume = self.out / "game.state", self.out / "first.hdf", self.out / "again.hdf"
        out = self.run_game([self.gcore("A"), f"run A {EARLY} script={self.script}", "save A s", f"savefile A {state}", f"run A {m} script={self.script}", "digest A first", f"export A 0 file={first_volume}",
                             "load A s", f"run A {m} script={self.script}", "digest A second", f"export A 0 file={again_volume}",
                             self.gcore("B"), "load B s", f"run B {m} script={self.script}", "digest B b"])
        a, b = ([u[1:] for u in self.units(out, name)] for name in "AB")
        self.assertEqual(a[EARLY:EARLY + m], a[EARLY + m:], "every unit after the load is the one that followed the save")
        self.assertEqual(a[EARLY:EARLY + m], b, "in a new sandbox (WB04)")
        digests = self.digests(out, "A")
        self.assertEqual(digests["first"], digests["second"])
        self.assertEqual(digests["first"], self.digests(out, "B")["b"])
        self.assertEqual(first_volume.read_bytes(), again_volume.read_bytes(), "the overlay goes back with the state and the script writes the same volume again")
        self.assertEqual(first_volume.read_bytes(), self.played()[1], "and it is the volume of the game played straight")
        # Another process: a new sandbox, the state read from a file.
        other = self.run_game([self.gcore("C"), f"loadfile C {state}", f"run C {m} script={self.script}", "digest C c", f"export C 0 file={self.out / 'other.hdf'}"])
        self.assertEqual(digests["first"], self.digests(other, "C")["c"])
        self.assertEqual([u[1:] for u in self.units(other, "C")], a[EARLY:EARLY + m])
        self.assertEqual(first_volume.read_bytes(), (self.out / "other.hdf").read_bytes())

    def test_a_state_of_the_game_in_play_goes_on_with_the_sound_the_original_made(self):
        # What the native engine and libretro had not got until T15m (tests/whdload/test_det02_whdload.py: the PCM of the game's steady tone was another waveform after a load): the state of the core is
        # the snapshot of the whole sandbox, so Paula's state is in it whatever the engine's own state lacks.
        MID, m = 700, 60
        out = self.run_game([self.gcore("A"), f"run A {MID} script={self.script}", "save A s", f"run A {m} script={self.script}", self.gcore("B"), "load B s", f"run B {m} script={self.script}"])
        a, b = self.units(out, "A")[MID:MID + m], self.units(out, "B")
        self.assertEqual([u[3] for u in a], [u[3] for u in b], "the pictures are the original's")
        self.assertEqual([u[5] for u in a], [u[5] for u in b], "and so is the sound")

    def test_a_state_of_the_game_in_play_is_refused_by_a_core_with_another_volume(self):
        state = self.out / "mid.state"
        self.run_game([self.gcore("A"), f"run A 700 script={self.script}", f"savefile A {state}"])
        other_image = bytearray(self.image)
        other_image[-512:] = bytes(512)                                    # same size, other bytes: another hard file
        if bytes(other_image) == self.image:
            other_image[-1] ^= 1
        (self.media / "other.hdf").write_bytes(bytes(other_image))
        self.files_before["other.hdf"] = sha(bytes(other_image))
        out = self.run_game([self.core("O", adf="", writes=False, hdf=str(self.media / "other.hdf"), machine="a1200"), f"loadfile O {state}", "run O 2"])
        self.assertTrue(self.rejections(out, "O"), out[-600:])


if __name__ == "__main__":
    unittest.main()
