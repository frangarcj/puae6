"""T30b: the real Fatal Mission, installed by tools/prepare_whdload.py, boots from its WHDLoad volume in the native engine (the A1200 with the owner's Kickstart 3.1), takes input and writes to
the volume (MED03; the state side is tests/whdload/test_det02_whdload.py, DET02).

The machine has no floppy: the prepared image is DH0:. Kickstart 3.1 boots from it, the script of the volume (generated from the config, nothing of the game's own) writes boot.log, runs
WHDLoad on the slave of the game, and the game shows its title; fire on the joystick of port 1 starts it; F10 is WHDLoad's QuitKey: the script goes on, writes exit.log and ends. The volume that
comes out of the machine (p6_media_export: the base with the guest's 4 KiB overlay over it) is read back with the volume checker of tools/whdload/ffs.py, which verifies every checksum and the bitmap.

Opt-in (tests/whdload/support.py). With P6_STATE_PROBE_BUILD=debug the probe is the -O0 one of build/link-audit.
"""
import re
import subprocess
import unittest

from tests.state.harness import ProbeCase
from tests.whdload import support


STATUS = re.compile(r"^status (\d+) (\d+) (\d+) capacity=(\d+) used=(\d+) medium=(\d+) ", re.M)


@unittest.skipUnless(support.AVAILABLE, support.SKIP)
class WhdloadNativeTests(ProbeCase):
    MACHINE = "a1200"
    UNITS = 2000

    @classmethod
    def firmware(cls):
        from pathlib import Path
        return Path(support.KICKSTART)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.image, cls.manifest = support.prepare()
        cls.hdf = cls.dir / "fatal-mission.hdf"
        cls.hdf.write_bytes(cls.image)
        cls.cache = {}

    def run_game(self, name, units, *input_args, hdf=True):
        """One run of the probe: the Run it printed and the volume the machine exports after the last unit."""
        key = (name, units, tuple(input_args), hdf)
        if key not in self.cache:
            out = self.dir / f"{name}.out"
            args = ["--units", str(units), "--every", "500", *(["--hdf", str(self.hdf), "--export-at", str(units), "0", str(out)] if hdf else []), *input_args]
            run = self.probe("kickstart", *args)
            self.cache[key] = (run, out.read_bytes() if hdf else None)
        return self.cache[key]

    def pictures(self, run, first, last):
        return [run.pictures[unit] for unit in range(first, last + 1)]

    # --- boot ---------------------------------------------------------------------------------------------------------------------

    def test_the_kickstart_boots_dh0_runs_the_script_and_whdload_shows_the_game(self):
        run, exported = self.run_game("boot", 700)
        empty, _ = self.run_game("nodisk", 700, hdf=False)
        volume, original = support.volume(exported), support.volume(self.image)
        self.assertEqual(b"booted\n", volume.files["boot.log"], "DOS ran the startup sequence of the volume")
        self.assertNotIn("exit.log", volume.files, "WHDLoad is still running")
        self.assertEqual(set(original.files) | {"boot.log"}, set(volume.files))
        self.assertTrue(set(self.pictures(run, support.TITLE_UNITS, 700)).isdisjoint(self.pictures(empty, 300, 700)), "the screen is not the one of a machine with nothing to boot")
        self.assertGreater(len(set(self.pictures(run, support.TITLE_UNITS, support.FIRE_AT))), 20, "the title is animated")
        self.assertEqual(self.image, self.hdf.read_bytes(), "the file the engine was given is never touched")

    def test_the_same_run_twice_is_the_same_units_and_the_same_volume(self):
        first, exported = self.run_game("boot", 700)
        second = self.probe("kickstart", "--units", "700", "--every", "500", "--hdf", str(self.hdf), "--export-at", "700", "0", str(self.dir / "again.out"))
        self.assertEqual(first.pictures, second.pictures)
        self.assertEqual(first.digests, second.digests)
        self.assertEqual(exported, (self.dir / "again.out").read_bytes())

    # --- input --------------------------------------------------------------------------------------------------------------------

    def test_fire_changes_what_the_game_shows_and_nothing_before_the_button_goes_down(self):
        quiet, _ = self.run_game("boot", 700)
        pressed, _ = self.run_game("fire", 700, *support.fire())
        self.assertEqual(self.pictures(quiet, 1, support.FIRE_AT - 1), self.pictures(pressed, 1, support.FIRE_AT - 1), "the same machine until the button goes down")
        self.assertTrue(set(self.pictures(quiet, support.FIRE_AT + 20, 700)).isdisjoint(self.pictures(pressed, support.FIRE_AT + 20, 700)), "the fire changed what the game shows")

    def test_f10_quits_whdload_the_script_goes_on_and_the_volume_has_what_it_wrote(self):
        run, exported = self.run_game("quit", self.UNITS, *support.fire(), *support.quit_key())
        playing, still_playing = self.run_game("playing", self.UNITS, *support.fire())
        volume, original = support.volume(exported), support.volume(self.image)
        self.assertEqual({"boot.log": b"booted\n", "exit.log": b"game ended\n"}, {k: v for k, v in volume.files.items() if k not in original.files})
        for path, data in original.files.items():
            self.assertEqual(data, volume.files[path], path)
        self.assertNotIn("exit.log", support.volume(still_playing).files, "without F10 the game goes on and the script does not")
        self.assertNotEqual(self.pictures(run, self.UNITS - 50, self.UNITS), self.pictures(playing, self.UNITS - 50, self.UNITS))

    # --- writes -------------------------------------------------------------------------------------------------------------------

    def test_the_overlay_holds_exactly_the_chunks_that_changed_and_the_host_file_does_not(self):
        _, exported = self.run_game("quit", self.UNITS, *support.fire(), *support.quit_key())
        changed = [c for c in range(0, len(exported), 4096) if exported[c:c + 4096] != self.image[c:c + 4096]]
        out = self.dir / "status.out"
        result = subprocess.run([str(self.exe), str(self.roms["kickstart"]), "--machine", "a1200", "--units", str(self.UNITS), "--every", "500", "--hdf", str(self.hdf), *support.fire(),
                                 *support.quit_key(), "--status-at", str(self.UNITS), "0", "--export-at", str(self.UNITS), "0", str(out)], capture_output=True, text=True, timeout=1800)
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        (status,) = STATUS.findall(result.stdout)
        used, medium = int(status[4]), int(status[5])
        self.assertEqual(exported, out.read_bytes())
        self.assertTrue(changed)
        self.assertLessEqual(len(changed), used, "a chunk changed that the overlay does not count")
        self.assertEqual(used, medium)
        self.assertEqual(-(-len(self.image) // 4096), int(status[3]), "one overlay chunk for each 4 KiB of the image")
        self.assertEqual(self.image, self.hdf.read_bytes(), "the engine never writes the file it was given")


if __name__ == "__main__":
    unittest.main()
