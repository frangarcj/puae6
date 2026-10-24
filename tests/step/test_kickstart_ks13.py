"""Boot the A500 OG, the A1000 and the A2000 OG with the owner's Kickstart 1.3 (opt-in; nothing here is copied, hashed or recorded).

    P6_KICKSTART_ROM  the Kickstart 1.3 r34.5 .rom (256 KiB, A500-A1000-A2000-CDTV): the same image runs the three machines
    P6_GAME_ADF       (optional) a game disk; it is mounted in DF0 at boot in the "game" run (the owner's Speedball 2)

What is checked is the shape of what each machine shows, as in tests/step/test_kickstart.py (the A500) and test_kickstart_a600.py: the machine runs, every unit has a picture, the picture
changes (the animation of the "insert disk" hand), the OCS machines end on the A500's screen, the same seed gives the same text unit by unit. With the game disk the screens after the
Kickstart are other ones (the machine leaves the insert-disk screen for good). It proves the Kickstart 1.3 boots on those presets with their own chipset tables (the A1000's chips and
ROM-as-RAM flag, the A2000's clock and mains TOD), not that they are compatible with anything else. The A500+ (Kickstart 2.04) and the A2000 (Kickstart 3.1 r40.063) are NOT here:
there is no authorized copy of either, so their boot with a real Kickstart is untested; their presets are exercised with micro-ROMs (tests/step/test_machines.py).
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADLESS = ROOT / "build/link-audit/p6_headless"
ROM = os.environ.get("P6_KICKSTART_ROM")
GAME = os.environ.get("P6_GAME_ADF")
MACHINES = ("a500", "a500og", "a1000", "a2000og")      # the A500 is the control: the picture of the insert-disk screen the other three are compared with
UNITS = 300
GAME_UNITS = 1200
ROW = re.compile(r"unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=([0-9a-f]{16})(?: audio=\d+)?")


def parse(stdout):
    rows = [ROW.fullmatch(line) for line in stdout.splitlines()[:-1]]
    assert all(rows), "a line of the runner has an unexpected shape"
    return rows


@unittest.skipUnless(ROM and Path(ROM).is_file() and HEADLESS.is_file(), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3 (and build p6_headless)")
class Kickstart13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        jobs = {(m, seed, game): None for m in MACHINES for seed in (1, 2) for game in ((False,) if seed == 2 else (False, True)) if not game or GAME}
        with ThreadPoolExecutor(3) as pool:
            futures = {key: pool.submit(cls.run_headless, *key) for key in jobs}
            cls.runs = {key: future.result() for key, future in futures.items()}

    @staticmethod
    def run_headless(machine, seed, game):
        command = [str(HEADLESS), "--run", ROM, "--machine", machine, "--units", str(GAME_UNITS if game else UNITS), "--seed", str(seed)]
        if game:
            command += ["--disk", GAME]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise AssertionError(f"{machine}: the runner failed ({result.returncode}): {result.stderr[-400:]}")
        return result.stdout, parse(result.stdout)

    def test_each_machine_runs_300_units_with_a_picture_that_changes(self):
        for machine in MACHINES:
            _, rows = self.runs[(machine, 1, False)]
            with self.subTest(machine=machine):
                self.assertEqual(len(rows), UNITS)
                self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
                self.assertGreater(len({r[6] for r in rows}), 4, "the picture hardly changed: the animation of the insert-disk hand")

    def test_the_ocs_machines_end_on_the_insert_disk_screen_of_the_a500(self):
        # The same chips draw the same screen: the picture of the last unit is the A500's (the clock of the A2000 and the A1000's chips change nothing the Kickstart 1.3 draws there).
        control = self.runs[("a500", 1, False)][1][-1][6]
        for machine in ("a500og", "a1000", "a2000og"):
            with self.subTest(machine=machine):
                self.assertEqual(self.runs[(machine, 1, False)][1][-1][6], control)

    def test_the_same_seed_gives_the_same_text_and_another_seed_a_different_start(self):
        for machine in MACHINES:
            first, again = self.runs[(machine, 1, False)], self.run_headless(machine, 1, False)
            with self.subTest(machine=machine):
                self.assertEqual(first[0], again[0])
                self.assertNotEqual([r[2] for r in first[1]], [r[2] for r in self.runs[(machine, 2, False)][1]], "the seed changes the start-up delay: other ticks")

    @unittest.skipUnless(GAME, "set P6_GAME_ADF to a game disk to boot it")
    def test_the_game_disk_leaves_the_insert_disk_screen(self):
        for machine in MACHINES:
            empty = {r[6] for r in self.runs[(machine, 1, False)][1][100:]}
            _, rows = self.runs[(machine, 1, True)]
            with self.subTest(machine=machine):
                self.assertEqual(len(rows), GAME_UNITS)
                self.assertTrue(all(r[3] == "1" for r in rows))
                self.assertNotIn(rows[-1][6], empty, "the machine still shows the insert-disk screen")
                self.assertGreater(len({r[6] for r in rows[-100:]}), 1, "the last 100 units show one frozen picture")


if __name__ == "__main__":
    unittest.main()
