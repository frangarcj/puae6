"""T15c1: chunk P6RT, the state of the clock (bridge/state_devices.cpp), on the real engine.

What is proven here is what the chunk puts back, not that a machine can be restored (that is the closure of T15): the clock and the registers of the
MSM6242B come back to what they were in the unit the state was taken in, in the same process and in a new one; the clock goes on from there whatever the
machine's cycle counter said before (the rule of epoch that T15b could not test: a state loaded into an earlier or a later unit); every field is applied;
a body that Validate accepts never fails to apply; and a bad body is refused with the machine untouched.
"""
import os
import unittest

from tests.state import envelope as e
from tests.state.harness import FieldTable, ProbeCase, SUBSYSTEMS, TRACE, parse_chunks, reforge

RTC = 3                 # index of the chunk in the state
FULL = ("--only", "cpu", "--only", "scheduler", "--only", "ram", "--only", "rtc")
# The vertical-blank program of vsync.s after it has written the control registers D, E and F of the clock chip (reg * 4 + 1 from $DC0000).
CLOCK = """; PUAE6 test program: vsync.s, after writing the MSM6242B's control registers (D = 9, E = $B, F = 5; the chip keeps 1|8, 4 and 4 bits).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$09,$DC0035.L
    MOVE.B #$0B,$DC0039.L
    MOVE.B #$05,$DC003D.L
    MOVE.L #handler,$6C.L
    LEA $1000.W,A0
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVE.L D0,(A0)+
    RTE
"""


def total_ticks(clock):
    return clock["seconds"] * clock["ticks"] + clock["subsecond"]


class ClockChunkTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "stop": ("stop", None, None), "input_all": ("input_all", None, None), "trace": (TRACE, None, None),
            "clock": (CLOCK, None, None)}

    def scenarios(self):
        names = {"vsync": (3, 4), "stop": (3, 4), "input_all": (3, 4), "trace": (3, 4), "clock": (3, 4)}
        if "kickstart" in self.roms:
            names["kickstart"] = (60, 10)
        return names

    def table_of(self, rom, unit, *arguments):
        path = self.dir / f"{rom}-{unit}.rtc"
        run = self.probe(rom, "--units", str(unit), *arguments, "--save-fields-at", str(unit), str(path))
        return FieldTable(path.read_bytes(), RTC, run.fields), run, path

    # -- the clock and the chip's registers come back -------------------------------------------------------------------------------------

    def test_the_program_that_writes_the_chip_leaves_what_the_chunk_has_to_carry(self):
        run = self.probe("clock", "--units", "3", "--rtc-at", "3")
        self.assertEqual((9, 11, 5), (run.rtc[0]["d"], run.rtc[0]["e"], run.rtc[0]["f"]))
        table, _, _ = self.table_of("clock", 3)
        self.assertEqual((9, 11, 5), (table.get("rtc.msm_d"), table.get("rtc.msm_e"), table.get("rtc.msm_f")))

    def test_the_chunk_puts_the_clock_and_the_registers_of_the_unit_it_was_taken_in_back(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path = self.dir / f"{rom}.state"
                run = self.probe(rom, "--units", str(n + k), "--every", "1", "--rtc-at", str(n), "--save-at", str(n), str(path), "--rtc-at", str(n + k),
                                 "--load-at", str(n + k), str(path), "--rtc-at", str(n + k), "--only", "scheduler", "--only", "rtc")
                self.assertEqual([(n + k, 0, "")], run.loads)
                saved, before, after = run.rtc
                self.assertEqual(0, after["status"])
                self.assertNotEqual(saved["cycle"], before["cycle"])
                self.assertEqual({k: v for k, v in saved.items() if k != "unit"}, {k: v for k, v in after.items() if k != "unit"})
                self.assertEqual(run.digests[(n, "rtc")], run.after[0]["rtc"])
                self.assertEqual(run.digests[(n, "scheduler")], run.after[0]["scheduler"])
                others = [s for s in SUBSYSTEMS if s not in ("scheduler", "chipset", "rtc")]
                self.assertEqual({s: run.digests[(n + k, s)] for s in others}, {s: run.after[0][s] for s in others}, "only the chunks asked for were applied")

    def test_a_fresh_image_gets_the_clock_of_the_unit_it_was_taken_in_after_the_reset_head_has_run(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path, again = self.dir / f"{rom}.state", self.dir / f"{rom}.again"
                source = self.probe(rom, "--units", str(n), "--every", "1", "--rtc-at", str(n), "--save-at", str(n), str(path))
                fresh = self.probe(rom, "--units", "0", "--load-at", "0", str(path), "--rtc-at", "0", "--save-at", "0", str(again), *FULL)
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual({k: v for k, v in source.rtc[0].items() if k != "unit"}, {k: v for k, v in fresh.rtc[0].items() if k != "unit"})
                self.assertEqual(source.digests[(n, "rtc")], fresh.after[0]["rtc"])
                a, b = parse_chunks(path.read_bytes()), parse_chunks(again.read_bytes())
                self.assertEqual([a[i] for i in range(4)], [b[i] for i in range(4)])

    # -- the rule of epoch ---------------------------------------------------------------------------------------------------------------

    def test_a_state_loaded_into_an_earlier_or_a_later_unit_leaves_a_clock_that_goes_on_from_the_instant_it_was_saved(self):
        for rom in ("vsync", "clock"):
            for taken, loaded in ((3, 7), (9, 4)):                     # the machine is ahead of the state, and behind it
                with self.subTest(rom=rom, taken=taken, loaded=loaded):
                    path = self.dir / f"{rom}-{taken}.epoch"
                    saved = self.probe(rom, "--units", str(taken), "--rtc-at", str(taken), "--save-at", str(taken), str(path)).rtc[0]
                    run = self.probe(rom, "--units", str(loaded + 2), "--load-at", str(loaded), str(path), *FULL, "--rtc-at", str(loaded),
                                     "--rtc-at", str(loaded + 2))
                    self.assertEqual([(loaded, 0, "")], run.loads)
                    right_after, later = run.rtc
                    self.assertEqual((0, 0), (right_after["status"], later["status"]), "the clock service failed: the anchor was not put back")
                    self.assertEqual({k: v for k, v in saved.items() if k != "unit"}, {k: v for k, v in right_after.items() if k != "unit"})
                    # Two units of the program ran: the clock advanced by exactly the cycles that elapsed since the load, not by what the old anchor said.
                    elapsed = later["cycle"] - right_after["cycle"]
                    self.assertGreater(elapsed, 0)
                    self.assertEqual(elapsed, total_ticks(later) - total_ticks(right_after))

    # -- every field is applied ---------------------------------------------------------------------------------------------------------------

    def test_every_field_of_the_body_is_applied_a_different_value_gives_a_different_clock(self):
        table, run, base = self.table_of("clock", 3)
        values = {"rtc.seconds": table.get("rtc.seconds", signed=True) + 7, "rtc.subsecond": table.get("rtc.subsecond") + 5, "rtc.msm_d": 1,
                  "rtc.msm_e": 3, "rtc.msm_f": 12}
        files = []
        arguments = ["--units", "6", "--only", "scheduler", "--only", "rtc", "--load-at", "6", str(base), "--rtc-at", "6"]
        for label, value in values.items():
            path = self.dir / f"flip-{len(files)}.state"
            path.write_bytes(table.with_values({label: value}))
            files.append(path)
            arguments += ["--load-at", "6", str(path), "--rtc-at", "6"]
        try:
            result = self.probe("clock", *arguments)
        finally:
            for path in files:
                path.unlink()
        self.assertEqual([(6, 0, "")] * (1 + len(values)), result.loads)
        reference = result.rtc[0]
        field = {"rtc.seconds": "seconds", "rtc.subsecond": "subsecond", "rtc.msm_d": "d", "rtc.msm_e": "e", "rtc.msm_f": "f"}
        for (label, value), clock in zip(values.items(), result.rtc[1:]):
            self.assertEqual(value, clock[field[label]], f"{label}: the field is written to the body and not applied")
            self.assertNotEqual(reference[field[label]], clock[field[label]])
            self.assertEqual({k: v for k, v in reference.items() if k not in ("unit", field[label])}, {k: v for k, v in clock.items() if k not in ("unit", field[label])})

    # -- Validate and Apply ---------------------------------------------------------------------------------------------------------------

    def test_a_body_that_validate_accepts_never_fails_to_apply_and_a_refusal_never_changes_the_machine(self):
        _, _, path = self.table_of("clock", 3)
        verdicts = set()
        for units in (4, 9):
            run = self.probe("clock", "--units", str(units), "--only", "scheduler", "--only", "rtc", "--mutate-at", str(units), "rtc", str(path))
            self.assertEqual([], run.mutate_errors, "the good body was refused by the machine it was loaded into")
            (mutated,) = run.mutate
            self.assertEqual(31, mutated["bytes"])
            self.assertEqual(0, mutated["faulted"], "Validate accepted a body that Apply could not apply")
            self.assertEqual(0, mutated["changed"], "a refused state changed the machine")
            self.assertEqual(mutated["attempts"], mutated["accepted"] + mutated["refused"])
            self.assertGreater(mutated["accepted"], 10)
            self.assertGreater(mutated["refused"], 40)
            verdicts.add((mutated["attempts"], mutated["accepted"], mutated["refused"], mutated["verdicts"]))
        self.assertEqual(1, len(verdicts), "the verdict on a body depends on the machine it is loaded into")

    def test_a_bad_body_is_refused_with_its_reason_and_leaves_the_machine_alone(self):
        table, _, _ = self.table_of("clock", 3)
        rate = table.get("rtc.ticks_per_second")
        ntsc = 3579545 * 512                                  # CHIPSET_CLOCK_NTSC * CYCLE_UNIT: the other video standard's ticks per second
        self.assertNotEqual(rate, ntsc)
        rng_range = "a field is out of its range"
        attempts = [
            ("seconds before year 1", table.with_values({"rtc.seconds": -62135596800 - 1}), 9, rng_range),
            ("seconds after year 9999", table.with_values({"rtc.seconds": 253402300799 + 1}), 9, rng_range),
            ("the ticks of another video standard", table.with_values({"rtc.ticks_per_second": ntsc}), 9, rng_range),
            ("a subsecond of one whole second", table.with_values({"rtc.subsecond": rate}), 9, rng_range),
            ("another seed", table.with_values({"rtc.seed": table.get("rtc.seed") + 1}), 9, rng_range),
            ("a seed of zero", table.with_values({"rtc.seed": 0}), 9, rng_range),
            ("a register D bit the chip does not keep", table.with_values({"rtc.msm_d": 2}), 9, "rtc.msm_d: a bit the MSM6242B does not keep"),
            ("a register D above its range", table.with_values({"rtc.msm_d": 10}), 9, rng_range),
            ("a register E of 16", table.with_values({"rtc.msm_e": 16}), 9, rng_range),
            ("a register F of 16", table.with_values({"rtc.msm_f": 16}), 9, rng_range),
        ]
        attempts.append(("the body one byte short", reforge(table.blob, lambda c: c[RTC].__setitem__(2, c[RTC][2][:-1])), 9, "chunk rtc: the body ends before its last field"))
        run = self.refusals(attempts, rom="clock", extra=("--only", "scheduler", "--only", "rtc"), unit=6, units=9)
        self.assertEqual(len(attempts), len(run.after))

    # -- a state is taken from a clock that works ------------------------------------------------------------------------------------------

    def test_a_clock_that_has_failed_is_not_saved_and_nothing_is_written(self):
        run = self.probe("clock", "--units", "4", "--poke-at", "4", "rtc-fault", "--save-at", "4", str(self.dir / "failed.state"))
        self.assertEqual([(4, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
        self.assertIn("chunk rtc could not be saved: rtc: the clock service failed", run.save_errors[0][2])
        self.assertFalse((self.dir / "failed.state").exists())

    def test_an_unsupported_clock_chip_is_not_saved_and_nothing_is_written(self):
        run = self.probe("clock", "--units", "4", "--poke-at", "4", "cs-rtc", "--save-at", "4", str(self.dir / "chip.state"), "--poke-at", "4", "clear",
                         "--save-at", "4", str(self.dir / "chip-clear.state"))
        self.assertEqual([(4, 2)], [(unit, code) for unit, code, _ in run.save_errors])
        self.assertIn("rtc: only the MSM6242B and RF5C01A are supported", run.save_errors[0][2])
        self.assertFalse((self.dir / "chip.state").exists())
        self.assertEqual(1, len(run.saves), "with the chip back it is saved")

    def test_an_ntsc_machine_has_the_ticks_of_its_own_standard_and_loads_its_own_state(self):
        path = self.dir / "ntsc.state"
        run = self.probe("clock", "--ntsc", "--units", "7", "--rtc-at", "3", "--save-at", "3", str(path), "--load-at", "7", str(path), "--rtc-at", "7",
                         "--only", "scheduler", "--only", "rtc")
        self.assertEqual([(7, 0, "")], run.loads)
        saved, after = run.rtc
        self.assertEqual(3579545 * 512, saved["ticks"])
        self.assertEqual({k: v for k, v in saved.items() if k != "unit"}, {k: v for k, v in after.items() if k != "unit"})

    # -- a real Kickstart ---------------------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_keeps_its_clock_across_a_load_into_an_earlier_and_a_later_unit(self):
        # Going back in time the clock goes on at once. Going forward (a state newer than the machine) Paula has to catch up on a sample clock that only the
        # audio chunk (T15e) puts back, so the next unit overflows the PCM queue: there the clock is checked right after the load and no unit is run.
        for taken, loaded in ((60, 100), (140, 80)):
            with self.subTest(taken=taken, loaded=loaded):
                path = self.dir / f"kickstart-{taken}.state"
                saved = self.probe("kickstart", "--units", str(taken), "--rtc-at", str(taken), "--save-at", str(taken), str(path)).rtc[0]
                goes_on = taken < loaded
                units = loaded + 2 if goes_on else loaded
                arguments = ["--units", str(units), "--load-at", str(loaded), str(path), *FULL, "--rtc-at", str(loaded)]
                run = self.probe("kickstart", *arguments, *(["--rtc-at", str(loaded + 2)] if goes_on else []))
                self.assertEqual([(loaded, 0, "")], run.loads)
                self.assertEqual(0, run.rtc[0]["status"])
                self.assertEqual({k: v for k, v in saved.items() if k != "unit"}, {k: v for k, v in run.rtc[0].items() if k != "unit"})
                if goes_on:
                    right_after, later = run.rtc
                    self.assertEqual(0, later["status"])
                    self.assertEqual(later["cycle"] - right_after["cycle"], total_ticks(later) - total_ticks(right_after))


if __name__ == "__main__":
    unittest.main()
