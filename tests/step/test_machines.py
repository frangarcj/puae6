"""AV01 and the boot of the T50 presets (A500 OG, A500+, A1000, A2000 OG, A2000) with micro-ROMs.

Same method as tests/step/test_ecs.py: programs whose result is deduced from the program and the hardware documentation (tests/scenarios/machines/machines.py says
how), run through the STEP01 probe on each machine in PAL and NTSC. No Kickstart: the opt-in boots are tests/step/test_kickstart_ks13.py.

  * identification   VPOSR / DENISEID of the chips the preset names (the A1000 reads as the OCS family, the A2000 has an ECS Agnus and an OCS Denise).
  * Chip RAM         512 KiB wraps, 1 MiB does not.
  * clock            the MSM6242B answers with the configured date on the A500+ and the A2000s and not on the machines without it.
  * TOD              the CIA-A TOD counts the vertical sync on the A500 family and the mains on the A1000 and the A2000s.
  * pictures         the OCS and ECS displays of the A500 and A600 tests, on the machine with the same chipset, equal to the picture the model predicts.
  * repeatability    the same program twice gives the same output; the hashes of the pictures are fixed in machines-baselines.json after the prediction matched.

The pictures are not kept: a run is judged picture by picture and only its facts (the mismatches, the hash) are remembered.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.scenarios.a500.display import HEIGHT, WIDTH
from tests.scenarios.ecs import ecs
from tests.scenarios.machines import machines as M
from tests.step.test_display import load_dump, mismatches, picture_hash
from tests.step.test_ecs import STANDARDS, log_words
from tests.step.test_step import ROOT, TOOLS, builder, configure_trace_tree, run_probe

BASELINES = ROOT / "tests/scenarios/machines/machines-baselines.json"
E_CLOCK = {"pal": 3546895 / 5, "ntsc": 3579545 / 5}                       # CIA clock: the colour clock over five
FRAME_E = {"pal": 14164.8, "ntsc": 11921.0}                               # one frame in E clocks: 312 x 227 clocks (PAL), 262 lines of 227.5 clocks (NTSC)
MAINS_HZ = {"pal": 50, "ntsc": 60}
OCS_PICTURES = ("lores-bars", "hires-bars")
ALL_NEW = M.NEW_MACHINES


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class MachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-machines-")
        cls.cache = {}
        cls.facts_cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_log(self, key, source, standard, machine, units, mode="frames-norender"):
        """(log words by unit, raw output) of a program run for `units` units, without pictures."""
        cache_key = (key, standard, machine, units, mode)
        if cache_key not in self.cache:
            directory = Path(self.tmp.name) / ("-".join(map(str, cache_key)))
            directory.mkdir()
            rom = directory / "program.rom"
            rom.write_bytes(builder.build(source))
            code, out = run_probe(self.exe, rom, units, mode, 1, timeout=600, extra=[standard, machine], env={"P6_DUMP_LOG": "1", "P6_UNIT_LIMIT_LINES": "6000"})
            self.assertEqual(code, 0, out[-600:])
            self.cache[cache_key] = (log_words(out), out)
        return self.cache[cache_key]

    def run_pictures(self, key, source, standard, machine, expected=None, units=6):
        """(raw output, mismatches of units 2..units against `expected`, hash of the last picture, set of the colours inside the display), the pictures dropped as they are judged."""
        cache_key = (key, standard, machine, units, expected is None)
        if cache_key not in self.facts_cache:
            directory = Path(self.tmp.name) / ("pic-" + "-".join(map(str, cache_key)))
            directory.mkdir()
            rom = directory / "program.rom"
            rom.write_bytes(builder.build(source))
            code, out = run_probe(self.exe, rom, units, "frames", 1, timeout=600, extra=[standard, machine],
                                  env={"P6_FRAME_DUMP": str(directory), "P6_UNIT_LIMIT_LINES": "6000"})
            self.assertEqual(code, 0, out[-600:])
            wrong, last, inside = {}, None, set()
            for unit in range(1, units + 1):
                picture = load_dump(directory / f"frame-{unit:03d}.bin")
                self.assertEqual((picture["width"], picture["height"]), (WIDTH, HEIGHT[standard]))
                if expected is not None and unit >= 2:
                    found = mismatches(picture, expected)
                    if found:
                        wrong[unit] = found
                if unit == units:
                    last = picture_hash(picture)
                    inside = {p for row in picture["rows"][60:480] for p in row[100:700]}
            self.facts_cache[cache_key] = (out, wrong, last, inside)
        return self.facts_cache[cache_key]

    def test_the_chips_identify_themselves(self):
        for machine in ALL_NEW:
            agnus, ecs_denise = M.MACHINES[machine][2:4]
            for standard in STANDARDS:
                units = self.run_log("ident", ecs.ident_source(), standard, M.PROBE_NAMES[machine], 6, "frames")[0]
                _, _, _, inside = self.run_pictures("ident", ecs.ident_source(), standard, M.PROBE_NAMES[machine])
                words = [w for unit in sorted(units) for w in units[unit]]
                pairs = list(zip(words[0::2], words[1::2]))
                with self.subTest(machine=machine, standard=standard):
                    self.assertGreaterEqual(len(pairs), 6)
                    for vposr, deniseid in pairs:
                        self.assertEqual(vposr & 0x7000, agnus | (0x1000 if standard == "ntsc" else 0), hex(vposr))
                        self.assertEqual(deniseid == 0xFFFC, ecs_denise, hex(deniseid))
                    self.assertEqual(inside == {ecs.rgb(0xFFC)}, ecs_denise)

    def test_chip_ram_is_what_the_machine_has(self):
        for machine in ALL_NEW:
            chip = M.MACHINES[machine][0]
            for standard in STANDARDS:
                units = self.run_log("chip", M.chip_source(), standard, M.PROBE_NAMES[machine], 4)[0]
                with self.subTest(machine=machine, standard=standard):
                    self.assertEqual(units[1], M.chip_expected(chip))

    def test_the_clock_chip_answers_only_where_the_machine_has_it(self):
        for machine in ALL_NEW:
            has_clock = M.MACHINES[machine][4]
            for standard in STANDARDS:
                units = self.run_log("clock", M.clock_source(), standard, M.PROBE_NAMES[machine], 4)[0]
                digits = [w & 15 for w in units[1][:6]]
                with self.subTest(machine=machine, standard=standard):
                    if has_clock:
                        # 2000-01-01: day 01, month 01 and the year register of the chip (years since 1900, 100: tens digit $A, units 0) as upstream's rtc.cpp stores it
                        self.assertEqual(digits, M.CLOCK_EPOCH_DIGITS)
                    else:
                        self.assertNotEqual(digits, M.CLOCK_EPOCH_DIGITS)

    def tod_mean(self, machine, standard):
        units = self.run_log("tod", M.tod_source(), standard, M.PROBE_NAMES[machine], M.TOD_TICKS + 8)[0]
        periods = M.tod_periods([w for unit in sorted(units) for w in units[unit]])
        self.assertGreaterEqual(len(periods), M.TOD_TICKS - 2)
        return sum(periods[1:]) / len(periods[1:])

    def test_the_tod_counts_the_sync_or_the_mains(self):
        for machine in ALL_NEW:
            mains = M.MACHINES[machine][5]
            for standard in STANDARDS:
                mean = self.tod_mean(machine, standard)
                with self.subTest(machine=machine, standard=standard, mean=mean):
                    if mains:
                        # 1 / 50 s or 1 / 60 s in E clocks, plus the wait of upstream's mains counter for a scanline boundary (measured: one to two lines of 45 E clocks, cia_hsync_do in custom.cpp)
                        exact = E_CLOCK[standard] / MAINS_HZ[standard]
                        self.assertGreater(mean, exact)
                        self.assertLess(mean, exact + 100)
                    else:
                        self.assertAlmostEqual(mean, FRAME_E[standard], delta=3)           # one tick per vertical sync

    def test_the_mains_tod_is_not_the_frame_rate(self):
        # The point of the two tables above in one number: the same program gives the machines of the two families another period (a frame is 50.08 Hz, the mains 50 Hz).
        self.assertGreater(self.tod_mean("A2000", "pal") - self.tod_mean("A500PLUS", "pal"), 20)

    def displays(self, machine):
        if machine == "A500PLUS":
            return ecs.DISPLAYS                                  # the whole ECS catalogue, as the A600 runs it
        if machine == "A2000":                                   # ECS Agnus with the OCS Denise: the OCS modes and the 1 MiB of Chip
            return {name: ecs.DISPLAYS[name] for name in (*OCS_PICTURES, "chip1m")}
        return {name: ecs.DISPLAYS[name] for name in OCS_PICTURES}

    def test_the_pictures_are_the_ones_the_programs_describe(self):
        baselines = json.loads(BASELINES.read_text(encoding="utf-8"))["pictures"] if BASELINES.exists() else {}
        recorded = {}
        for machine in ALL_NEW:
            for name, display in self.displays(machine).items():
                for standard in STANDARDS:
                    out, wrong, last, _ = self.run_pictures(name, ecs.rom_source(display), standard, M.PROBE_NAMES[machine], ecs.predict(display, standard))
                    with self.subTest(machine=machine, display=name, standard=standard):
                        self.assertEqual(wrong, {})
                        recorded[f"{machine}-{name}-{standard}"] = last
                        # the same program twice is the same run
                        code, again = run_probe(self.exe, Path(self.tmp.name) / ("pic-" + "-".join(map(str, (name, standard, M.PROBE_NAMES[machine], 6, False)))) / "program.rom", 6,
                                                "frames", 1, timeout=600, extra=[standard, M.PROBE_NAMES[machine]])
                        self.assertEqual(code, 0)
                        self.assertEqual(out.splitlines(), again.splitlines())
        if os.environ.get("P6_RECORD_BASELINES") == "1":
            BASELINES.write_text(json.dumps({"pictures": dict(sorted(recorded.items()))}, indent=2) + "\n", encoding="utf-8")
        else:
            self.assertEqual(recorded, baselines)

    def differing_columns(self, name, standard, machine_a, machine_b):
        """The columns in which the last pictures of a display on two machines differ (the pictures are not kept)."""
        pictures = []
        for machine in (machine_a, machine_b):
            directory = Path(self.tmp.name) / ("cols-" + "-".join((name, standard, machine)))
            directory.mkdir(exist_ok=True)
            rom = directory / "program.rom"
            rom.write_bytes(builder.build(ecs.rom_source(ecs.DISPLAYS[name])))
            code, out = run_probe(self.exe, rom, 6, "frames", 1, timeout=600, extra=[standard, machine], env={"P6_FRAME_DUMP": str(directory)})
            self.assertEqual(code, 0, out[-400:])
            pictures.append(load_dump(directory / "frame-006.bin")["rows"])
        return {c for ra, rb in zip(*pictures) for c, (x, y) in enumerate(zip(ra, rb)) if x != y}

    def test_the_a1000_draws_what_the_a500_draws_except_two_columns_in_ntsc(self):
        # MEASURED, not deduced: the picture of the A1000 (the 8361 Agnus and the 8362 Denise of upstream's table) equals the A500's in PAL; in NTSC the two leftmost columns of every
        # other pair of rows (outside the model's description: the wrap of the previous line) are black where the A500 shows the end of the previous line.
        for name in OCS_PICTURES:
            self.assertEqual(self.differing_columns(name, "pal", "a500", "a1000"), set(), name)
            self.assertLessEqual(self.differing_columns(name, "ntsc", "a500", "a1000"), {0, 1}, name)

    def test_the_machines_that_share_a_chipset_share_their_pictures(self):
        # Equal chipset and memory, equal picture: the machines that differ from the A500 only in the clock, the Slow RAM or the mains TOD draw what it draws, and the A500+ what the A600 does.
        for name in OCS_PICTURES:
            display = ecs.DISPLAYS[name]
            for standard in STANDARDS:
                hashes = {machine: self.run_pictures(name, ecs.rom_source(display), standard, machine, None)[2] for machine in ("a500", "a500og", "a2000og")}
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(len(set(hashes.values())), 1, hashes)
        for name, display in ecs.DISPLAYS.items():
            for standard in STANDARDS:
                a600 = self.run_pictures(name, ecs.rom_source(display), standard, "a600", None)[2]
                plus = self.run_pictures(name, ecs.rom_source(display), standard, "a500plus", None)[2]
                with self.subTest(display=name, standard=standard):
                    self.assertEqual(plus, a600)


if __name__ == "__main__":
    unittest.main()
