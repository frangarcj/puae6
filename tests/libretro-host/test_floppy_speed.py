"""T62 (LR01, DET02): the floppy drive speed through the libretro core, run by the minimal frontend.

puae6_floppy_speed (100, 200, 400, 800, turbo) is a restart option: it is p6_config.floppy_speed. What is decided here:
  * the default is the real drive: a session that names 100 is the session that names nothing, and every speed is the machine the headless runner builds with --floppy-speed (the same ticks,
    audio and picture of every unit);
  * the speed accelerates the disk: units until a read of one track ends, and tracks read in 40 units, by speed (the numbers of tests/state/test_det02_floppy.py through the core);
  * a state is loaded by the same speed and refused by any other (the speed is in the configuration identity), the rewind and the run-ahead converge with each speed, turbo included;
  * a change made while the machine runs is reported and applies at the next load; a value of no schema fails the load naming the key.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_floppy_speed.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

from tests.state.harness import parse_chunks  # noqa: E402
from tests.state.test_det02_floppy import DISK_COUNT_PLAIN  # noqa: E402

SPEED = "puae6_floppy_speed"
SPEEDS = ("100", "200", "400", "800", "turbo")
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
RAM = 2                 # the chunk of Chip RAM in the native state
UNITS = 40


def option(value):
    return ("--set", f"{SPEED}={value}")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class FloppySpeedTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "disk_count.rom"
        rom.write_bytes(lr.builder.build(DISK_COUNT_PLAIN))
        cls.roms["disk_count"] = rom
        cls.disk = Path(cls.tmp.name) / "game.adf"
        cls.disk.write_bytes(lr.adf("floppy speed"))

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def play(self, *options, runs=12, extra=()):
        return self.frontend("disk_count", "--runs", runs, *options, *extra, content=self.disk).stdout

    def state_file(self, *options, at=UNITS):
        path = self.work() / "x.state"
        self.frontend("disk_count", "--runs", at + 2, *options, "--do", f"{at}:savefile:{path}", content=self.disk)
        return path

    @staticmethod
    def longs(path, address, count=1):
        """Longs of Chip RAM in the native state inside a state file of the core (after the adapter's own header and section)."""
        data = path.read_bytes()
        start = data.index(b"P6NATIVE")
        length = 336 + int.from_bytes(data[start + 40:start + 48], "little")          # the header and its payload: the adapter pads the state with zeros up to serialize_size
        chunks = parse_chunks(data[start:start + length])
        body = chunks[RAM][2]
        size = int.from_bytes(body[0:4], "little")
        chip = body[8:8 + size]
        return [int.from_bytes(chip[address + 4 * i:address + 4 * i + 4], "big") for i in range(count)]

    def measure(self, *options):
        """(vertical blanks from the start of the first read to its end, reads that ended in UNITS units), counted by the program."""
        path = self.state_file(*options)
        reads, = self.longs(path, 0x604)
        start, = self.longs(path, 0x608)
        first, = self.longs(path, 0x700)
        self.assertGreater(reads, 0)
        return first - start, reads

    # --- the default is the real drive, and the headless runner builds every speed --------------------------------------------------------------

    def test_naming_the_real_drive_is_naming_nothing(self):
        plain = self.runs(self.play())
        self.assertEqual([(r["hash"], r["ahash"], r["ticks"]) for r in self.runs(self.play(*option("100")))], [(r["hash"], r["ahash"], r["ticks"]) for r in plain])

    def test_every_speed_is_the_machine_the_headless_runner_builds(self):
        for speed in SPEEDS:
            with self.subTest(speed=speed):
                out = self.play(*option(speed), runs=16)
                headless = self.headless_units("disk_count", 16, "--disk", str(self.disk), "--floppy-speed", speed)
                ticks = {int(m[1]): int(m[2]) for m in lr.TICKS.finditer(out)}
                runs = {r["n"]: r for r in self.runs(out)}
                self.assertEqual(sorted(runs), sorted(headless))
                for n, expected in headless.items():
                    self.assertEqual(ticks[n], expected["ticks"], f"unit {n}")
                    self.assertEqual(runs[n]["audio"], expected["audio"], f"unit {n}")

    # --- the speed accelerates the load ----------------------------------------------------------------------------------------------------------

    def test_the_faster_drives_and_the_turbo_one_read_the_disk_sooner(self):
        found = {speed: self.measure(*option(speed)) for speed in SPEEDS}
        print("\nT62 floppy speed through the core (units until the first read of $1900 words ends / reads ended in %d units): " % UNITS +
              ", ".join(f"{speed} {found[speed][0]}/{found[speed][1]}" for speed in SPEEDS))
        self.assertEqual(found["100"], self.measure())
        self.assertGreaterEqual(found["100"][0], 9)
        for slow, fast in zip(SPEEDS, SPEEDS[1:]):
            self.assertLess(found[fast][0], found[slow][0], f"{fast} is not sooner than {slow}")
            self.assertGreater(found[fast][1], found[slow][1], f"{fast} did not read more than {slow}")
        self.assertLessEqual(found["turbo"][0], 1)
        self.assertGreater(found["turbo"][1], found["100"][1] * 30)

    # --- identity, state, rewind and run-ahead --------------------------------------------------------------------------------------------------------

    def load_ok(self, path, *options):
        out = self.frontend("disk_count", "--runs", 3, *options, "--do", f"0:loadfile:{path}:pos=6", content=self.disk).stdout
        return LOAD.search(out)[3] == "1"

    def test_a_state_loads_at_the_same_speed_and_is_refused_at_any_other(self):
        for saved in ("100", "400", "turbo"):
            path = self.state_file(*option(saved), at=6)
            for loaded in SPEEDS:
                with self.subTest(saved=saved, loaded=loaded):
                    self.assertEqual(self.load_ok(path, *option(loaded)), saved == loaded)
            self.assertEqual(self.load_ok(path), saved == "100", "no option at all is the real drive")

    def config_hash_of(self, path):
        data = path.read_bytes()
        start = data.index(b"P6NATIVE")
        return data[start + 176:start + 208]

    def test_the_config_hash_is_the_series_one_at_the_real_drive_and_carries_the_speed_in_bits_25_to_27(self):
        from tests.state import envelope as e
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(self.config_hash_of(self.state_file(at=3)), base)
        self.assertEqual(self.config_hash_of(self.state_file(*option("100"), at=3)), base)
        for step, speed in enumerate(SPEEDS[1:], 1):
            self.assertEqual(self.config_hash_of(self.state_file(*option(speed), at=3)), e.config_hash(1, 1, 48000, step << 25, 1, 946684800), speed)

    def test_a_load_replays_each_unit_at_every_speed_and_the_runahead_leaves_the_run_as_a_plain_one_has_it(self):
        for speed in SPEEDS:
            with self.subTest(speed=speed):
                out = self.play(*option(speed), runs=30, extra=("--do", "10:save:a", "--do", "20:load:a"))
                seen = {}
                for run in self.runs(out):
                    seen.setdefault(run["unit"], []).append((run["ticks"], run["audio"], run["ahash"], run["hash"]))
                replayed = [views for views in seen.values() if len(views) > 1]
                self.assertGreaterEqual(len(replayed), 6)
                for views in replayed:
                    self.assertEqual(len(set(views)), 1, views)
                plain = self.runs(self.play(*option(speed), runs=16))
                ahead = self.runs(self.play(*option(speed), runs=16, extra=("--ahead-every", 2)))
                self.assertEqual([(r["audio"], r["ahash"], r["hash"]) for r in ahead], [(r["audio"], r["ahash"], r["hash"]) for r in plain])

    # --- refusals and changes while the machine runs ---------------------------------------------------------------------------------------------

    def test_a_value_of_no_schema_fails_the_load_naming_the_key(self):
        for value in ("0", "150", "fast"):
            with self.subTest(value=value):
                out = self.frontend("disk_count", *option(value), content=self.disk, expect=3).stdout
                self.assertIn(f'the option {SPEED} has the value "{value}", which this core does not support (allowed: 100, 200, 400, 800, turbo)', out)
                self.assertNotIn("run 1 ", out)

    def test_a_change_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("disk_count", lr.KICKSTART_NAMES[0])
        base = self.frontend("disk_count", "--runs", 12, system=system, content=self.disk).stdout
        changed = self.frontend("disk_count", "--runs", 12, "--set-at", f"5:{SPEED}=turbo", system=system, content=self.disk).stdout
        self.assertEqual(self.messages(changed), [f"PUAE6: {SPEED} is now turbo but the machine runs with 100: reload the content to apply it"])
        self.assertEqual([(r["audio"], r["ahash"], r["hash"]) for r in self.runs(changed)], [(r["audio"], r["ahash"], r["hash"]) for r in self.runs(base)])


if __name__ == "__main__":
    unittest.main()
