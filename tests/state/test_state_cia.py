"""T15c2: chunk P6CI, the state of both CIAs and of what cia.cpp keeps beside them (bridge/state_devices.cpp), on the real engine.

What is proven here is what the chunk puts back, not that a machine can be restored (that is the closure of T15c): every field of the body comes back to the
unit the state was taken in, in the same process and in a new one; the raw timers and the instant of the LED stay coherent with the scheduler's chunk whatever
the machine's counter said before; every field is applied and saved again unchanged; a body that Validate accepts never fails to apply; a bad body is refused
with the machine untouched; and a save refuses what the fields alone cannot describe.
"""
import os
import re
import subprocess
import unittest

from tests.state.harness import DIGEST, FieldTable, ProbeCase, SUBSYSTEMS, parse_chunks, reforge

PASS = re.compile(r"^pass (\d+)$")

CIA = 7                 # index of the chunk in the state
FULL = ("--only", "cpu", "--only", "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia")
PART = ("--only", "scheduler", "--only", "rtc", "--only", "cia")
# Both CIAs busy: CIA-A timer A (continuous), timer B (one shot), TOD alarm (counts vertical syncs), serial port; CIA-B timer A, timer B, TOD alarm (counts lines).
# Every interrupt reads the ICR of its CIA (which clears it) and logs it; the one of CIA-A also does the keyboard handshake.
CIAS = """; PUAE6 test program: both CIAs busy.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and power LED outputs
    MOVE.B #@PRA@,$BFE001.L     ; OVL off, power LED off (2) or on (0)
    MOVE.L #ciaa,$68.L          ; level 2: CIA-A
    MOVE.L #ciab,$78.L          ; level 6: CIA-B
    LEA $1000.W,A0
    MOVE.B #$00,$BFE401.L       ; CIA-A timer A latch $0900, continuous
    MOVE.B #$09,$BFE501.L
    MOVE.B #$01,$BFEE01.L
    MOVE.B #$00,$BFE601.L       ; timer B latch $3000, one shot
    MOVE.B #$30,$BFE701.L
    MOVE.B #$09,$BFEF01.L
    MOVE.B #$00,$BFEA01.L       ; TOD = 0 (the high byte stops it, the low byte starts it)
    MOVE.B #$00,$BFE901.L
    MOVE.B #$00,$BFE801.L
    MOVE.B #$80,$BFEF01.L       ; alarm = 3 vertical syncs
    MOVE.B #$00,$BFEA01.L
    MOVE.B #$00,$BFE901.L
    MOVE.B #$03,$BFE801.L
    MOVE.B #$09,$BFEF01.L
    MOVE.B #$8F,$BFED01.L       ; ICR mask: SET | TA | TB | ALRM | SP
    MOVE.B #$00,$BFD400.L       ; CIA-B timer A latch $0500, continuous
    MOVE.B #$05,$BFD500.L
    MOVE.B #$01,$BFDE00.L
    MOVE.B #$00,$BFD600.L       ; timer B latch $1800, one shot
    MOVE.B #$18,$BFD700.L
    MOVE.B #$09,$BFDF00.L
    MOVE.B #$00,$BFDA00.L       ; TOD = 0
    MOVE.B #$00,$BFD900.L
    MOVE.B #$00,$BFD800.L
    MOVE.B #$80,$BFDF00.L       ; alarm = 100 lines
    MOVE.B #$00,$BFDA00.L
    MOVE.B #$00,$BFD900.L
    MOVE.B #$64,$BFD800.L
    MOVE.B #$09,$BFDF00.L
    MOVE.B #$87,$BFDD00.L
    MOVE.W #$E008,$DFF09A.L     ; INTENA: SET | INTEN | PORTS | EXTER
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
ciaa:
    MOVE.B $BFED01.L,D3         ; ICR of CIA-A (reading it clears it)
    ADDQ.L #1,D2
    MOVE.B #@KBD@,$BFEE01.L     ; keyboard handshake: CRA bit 6 up (timer A going on), then down; the same value twice is no handshake
    MOVE.B #$01,$BFEE01.L
    MOVE.W D3,(A0)+
    MOVE.W #$0008,$DFF09C.L     ; INTREQ: clear PORTS
    RTE
ciab:
    MOVE.B $BFDD00.L,D4         ; ICR of CIA-B
    ADDQ.L #1,D5
    MOVE.W D4,(A0)+
    MOVE.W #$2000,$DFF09C.L     ; INTREQ: clear EXTER
    RTE
"""
# The overlay (the ROM at address 0) is on at reset and the program clears it after about three frames: a state at unit 1 or 2 has it on, one at unit 3 on off.
LATE = """; PUAE6 test program: clears the ROM overlay after a sled of NOPs (about 2.8 frames).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    .nops 100000
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
"""

# name of the field without its [k] -> (lowest, highest) value the visitor accepts; the width and the sign are the field's own
RANGES = {
    "cia.pra": (0, 255), "cia.prb": (0, 255), "cia.dra": (0, 255), "cia.drb": (0, 255),
    "cia.timer": (0, 0xFFFF), "cia.latch": (0, 0xFFFF), "cia.inputpipe": (0, 3), "cia.loaddelay": (0, 0xFFFFFFFF), "cia.preovfl": (0, 1), "cia.cr": (0, 255),
    "cia.tod": (0, 0xFFFFFF), "cia.tol": (0, 0xFFFFFF), "cia.alarm": (0, 0xFFFFFF), "cia.tlatch": (0, 1), "cia.todon": (0, 1),
    "cia.tod_event_state": (0, 4), "cia.tod_offset": (-1, 4095),
    "cia.icr1": (0, 255), "cia.icr2": (0, 0x1F), "cia.icr_change": (0, 1), "cia.imask": (0, 0x7F),
    "cia.sdr": (0, 255), "cia.sdr_buf": (0, 255), "cia.sdr_load": (0, 1), "cia.sdr_cnt": (0, 16),
    "cia.cycle_accurate": (0, 1), "kb.state": (0, 3), "kb.lostsync": (0, 1 << 20), "cia.eclockphase": (0, 19),
    "led.on": (0, 1), "led.old_brightness": (-1, 255), "led.age": (0, (1 << 63) - 1), "led.on_cycles": (0, (1 << 31) - 1), "led.off_cycles": (0, (1 << 31) - 1),
    "gui.powerled": (0, 1), "gui.powerled_brightness": (0, 255),
}
SIGNED = {"cia.tod_event_state", "cia.tod_offset", "kb.state", "kb.lostsync", "led.old_brightness", "led.age", "led.on_cycles", "led.off_cycles"}
RANGE_MESSAGE = "a field is out of its range"


def base(label):
    return label.split("[")[0]


def representable(value, size, signed):
    return (-(1 << (8 * size - 1)) <= value < (1 << (8 * size - 1))) if signed else (0 <= value < (1 << (8 * size)))


class CiaChunkTests(ProbeCase):
    ROMS = {"cias": (CIAS, {"PRA": "2", "KBD": "$41"}, None), "cias-noack": (CIAS, {"PRA": "2", "KBD": "$01"}, None),
            "cias-led": (CIAS, {"PRA": "0", "KBD": "$41"}, None), "late": (LATE, None, None), "vsync": ("vsync", None, None),
            "input_all": ("input_all", None, None)}

    def scenarios(self):
        names = {"cias": (3, 4), "cias-noack": (1, 2), "cias-led": (3, 4), "late": (2, 4), "vsync": (3, 4), "input_all": (3, 4)}
        return names

    def passes(self, rom, *arguments):
        """{pass: {(unit, subsystem): digest}} of a run with --loads: every load of the same image is one pass."""
        result = subprocess.run([str(self.exe), str(self.roms[rom]), *arguments], capture_output=True, text=True, timeout=1800)
        assert result.returncode == 0, result.stderr[-400:]
        passes, current = {}, 0
        for line in result.stdout.splitlines():
            if PASS.match(line):
                current = int(PASS.match(line)[1])
            elif DIGEST.match(line):
                unit, subsystem, digest, _ = DIGEST.match(line).groups()
                passes.setdefault(current, {})[(int(unit), subsystem)] = digest
        return passes

    def table_of(self, rom, unit, *arguments):
        path = self.dir / f"{rom}-{unit}.cia"
        run = self.probe(rom, "--units", str(unit), *arguments, "--save-fields-at", str(unit), str(path))
        return FieldTable(path.read_bytes(), CIA, run.fields), run, path

    # -- what the programs leave for the chunk to carry ----------------------------------------------------------------------------------------

    def test_the_programs_leave_what_the_chunk_has_to_carry(self):
        table, _, _ = self.table_of("cias", 3)
        self.assertEqual((1, 1, 1, 1), (table.get("cia.cr[0]"), table.get("cia.cr[2]"), table.get("cia.todon[0]"), table.get("cia.todon[1]")))
        self.assertEqual((2, 0xF, 7), (table.get("cia.tod[0]"), table.get("cia.imask[0]"), table.get("cia.imask[1]")))
        self.assertEqual((0x900, 0x500, 3), (table.get("cia.latch[0]"), table.get("cia.latch[2]"), table.get("kb.state")))
        self.assertNotEqual(table.get("cia.timer[0]"), table.get("cia.latch[0]"), "the raw counter of a timer that is running")
        lit, _, _ = self.table_of("cias-led", 3)
        self.assertEqual((1, 1, 255), (lit.get("led.on"), lit.get("gui.powerled"), lit.get("gui.powerled_brightness")))
        self.assertEqual((0, 0), (table.get("led.on"), table.get("gui.powerled")))
        silent, _, _ = self.table_of("cias-noack", 3)
        self.assertEqual(1, silent.get("kb.state"), "a keyboard that is never acknowledged stays at its first step")
        self.assertGreater(silent.get("kb.lostsync"), 0)

    def test_cia_b_is_in_the_state_of_its_line_increment_at_every_boundary_and_cia_a_is_not(self):
        # The unit ends inside hsync_handler, after CIA-B's TOD handler has run (state 1) and before CIA-A's, which is a vertical-sync counter (state 0): what a
        # save can rely on, and why a pending increment with an event (state 2) is a case that only a poke reaches.
        for rom in ("cias", "late", "vsync"):
            units = list(range(1, 9))
            args = []
            for unit in units:
                args += ["--save-fields-at", str(unit), str(self.dir / f"b-{rom}-{unit}.cia")]
            run = self.probe(rom, "--units", str(units[-1]), *args)
            per_save, current = [], []
            for field in run.fields:
                if current and field[2] < current[-1][2]:
                    per_save.append(current)
                    current = []
                current.append(field)
            per_save.append(current)
            self.assertEqual(len(units), len(per_save))
            for unit, fields in zip(units, per_save):
                with self.subTest(rom=rom, unit=unit):
                    table = FieldTable((self.dir / f"b-{rom}-{unit}.cia").read_bytes(), CIA, fields)
                    self.assertEqual((0, 1), (table.get("cia.tod_event_state[0]"), table.get("cia.tod_event_state[1]")))

    def test_the_body_is_laid_out_as_its_visitor_says_so_that_a_change_of_layout_is_a_change_of_version(self):
        table, _, _ = self.table_of("cias", 3)
        timer = [("cia.timer", 2), ("cia.latch", 2), ("cia.inputpipe", 1), ("cia.loaddelay", 4), ("cia.preovfl", 1), ("cia.cr", 1)]
        one_cia = [("cia.pra", 1), ("cia.prb", 1), ("cia.dra", 1), ("cia.drb", 1)] + timer + timer + \
            [("cia.tod", 4), ("cia.tol", 4), ("cia.alarm", 4), ("cia.tlatch", 1), ("cia.todon", 1), ("cia.tod_event_state", 4), ("cia.tod_offset", 4),
             ("cia.icr1", 1), ("cia.icr2", 1), ("cia.icr_change", 1), ("cia.imask", 1), ("cia.sdr", 1), ("cia.sdr_buf", 1), ("cia.sdr_load", 1), ("cia.sdr_cnt", 1)]
        rest = [("cia.cycle_accurate", 1), ("kb.state", 4), ("kb.lostsync", 4), ("cia.eclockphase", 1), ("led.on", 1), ("led.old_brightness", 2), ("led.age", 8),
                ("led.on_cycles", 4), ("led.off_cycles", 4), ("gui.powerled", 1), ("gui.powerled_brightness", 1)]
        expected = one_cia + one_cia + rest
        self.assertEqual(expected, [(base(label), table.size(label)) for label in table.order])
        self.assertEqual((56, 143), (sum(size for _, size in one_cia), sum(size for _, size in expected)))
        offsets = [table.where[label][0] for label in table.order]
        self.assertEqual(sorted(offsets), offsets)
        self.assertEqual(len(parse_chunks(table.blob)[CIA][2]), sum(size for _, size in expected))

    # -- the state of both CIAs comes back --------------------------------------------------------------------------------------------------

    def test_the_chunk_puts_both_cias_back_to_the_unit_it_was_taken_in_in_the_same_process(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path = self.dir / f"{rom}.state"
                run = self.probe(rom, "--units", str(n + k), "--every", "1", "--save-at", str(n), str(path), "--load-at", str(n + k), str(path), *PART)
                self.assertEqual([(n + k, 0, "")], run.loads)
                self.assertNotEqual(run.digests[(n, "cia")], run.digests[(n + k, "cia")], "the machine did not move: nothing was proven")
                self.assertEqual(run.digests[(n, "cia")], run.after[0]["cia"])
                self.assertEqual(run.digests[(n, "scheduler")], run.after[0]["scheduler"])
                others = [s for s in SUBSYSTEMS if s not in ("scheduler", "chipset", "rtc", "cia")]
                self.assertEqual({s: run.digests[(n + k, s)] for s in others}, {s: run.after[0][s] for s in others}, "only the chunks asked for were applied")

    def test_a_fresh_image_gets_both_cias_of_the_unit_it_was_taken_in_after_the_reset_head_has_run(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path, again = self.dir / f"{rom}.state", self.dir / f"{rom}.again"
                source = self.probe(rom, "--units", str(n), "--every", "1", "--save-at", str(n), str(path))
                fresh = self.probe(rom, "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), *FULL)
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(source.digests[(n, "cia")], fresh.after[0]["cia"])
                a, b = parse_chunks(path.read_bytes()), parse_chunks(again.read_bytes())
                self.assertEqual([a[i] for i in (0, 1, 2, 3, CIA)], [b[i] for i in (0, 1, 2, 3, CIA)])

    def test_the_overlay_is_made_again_from_pra_and_ddra_whichever_way_the_load_changes_it(self):
        # unit 2 has the ROM at address 0 (the overlay latch set), unit 5 has Chip RAM there; the digest hashes what the latch selects.
        early, late = self.dir / "overlay-on.state", self.dir / "overlay-off.state"
        run = self.probe("late", "--units", "5", "--every", "1", "--save-at", "2", str(early), "--save-at", "5", str(late),
                         "--load-at", "5", str(early), *PART, "--load-at", "5", str(late), *PART)
        on, off = run.digests[(2, "cia")], run.digests[(5, "cia")]
        self.assertNotEqual(on, off)
        self.assertEqual([on, off], [after["cia"] for after in run.after], "the overlay of the machine it was loaded into was kept")
        # and in the other order, 2 units earlier, so that the state that is loaded was taken after the machine's own unit
        run = self.probe("late", "--units", "2", "--every", "1", "--load-at", "2", str(late), *PART, "--load-at", "2", str(early), *PART)
        self.assertEqual([off, on], [after["cia"] for after in run.after])

    # -- the rule of epoch ------------------------------------------------------------------------------------------------------------------------

    def test_a_state_loaded_into_an_earlier_or_a_later_unit_keeps_the_timers_and_the_led_in_the_epoch_of_the_scheduler(self):
        for rom in ("cias", "cias-led"):
            for taken, loaded in ((3, 7), (9, 4)):                     # the machine is ahead of the state, and behind it
                with self.subTest(rom=rom, taken=taken, loaded=loaded):
                    path = self.dir / f"{rom}-{taken}.epoch"
                    saved = self.probe(rom, "--units", str(taken), "--every", "1", "--save-at", str(taken), str(path))
                    run = self.probe(rom, "--units", str(loaded + 2), "--every", "1", "--load-at", str(loaded), str(path), *FULL)
                    self.assertEqual([(loaded, 0, "")], run.loads)
                    # led_cycle travels as an age: it is the same age against the counter that the scheduler's chunk has put back, and the timers are raw
                    # counters in the epoch of eventtab[ev_cia].oldcycles that the same chunk puts back: both are in the digest.
                    self.assertEqual(saved.digests[(taken, "cia")], run.after[0]["cia"])
                    self.assertIn((loaded + 2, "cia"), run.digests, "the machine went on running two units after the load")

    # -- every field is applied ---------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def another_value(label, value, size):
        low, high = RANGES[base(label)]
        signed = base(label) in SIGNED
        for candidate in (value + 1, value - 1, high, low, value ^ 1):
            if low <= candidate <= high and candidate != value and representable(candidate, size, signed):
                if base(label) == "cia.tod_event_state" and candidate == 2:
                    continue                                          # a pending increment: a save refuses it without its event, a field of its own below
                if base(label) == "cia.loaddelay" and candidate & 0x88888888:
                    continue
                if base(label) == "cia.icr1" and ((candidate & 0x40 and not candidate & 0x80) or (candidate & 0x20 and candidate & 0xC0 != 0xC0)):
                    continue
                return candidate
        raise AssertionError(f"no other value for {label}")

    def test_every_field_of_the_body_is_applied_and_saved_again_unchanged(self):
        table, _, base_path = self.table_of("cias-led", 6)
        flips = [(label, self.another_value(label, table.get(label, signed=base(label) in SIGNED), table.size(label))) for label in table.order]
        base_out = self.dir / "flip-base.out"
        arguments = ["--units", "6", *FULL, "--load-at", "6", str(base_path), "--save-at", "6", str(base_out)]
        files = [base_out]
        for index, (label, value) in enumerate(flips):
            path, out = self.dir / f"flip-{index}.state", self.dir / f"flip-{index}.out"
            path.write_bytes(table.with_values({label: value}))
            files += [path, out]
            arguments += ["--load-at", "6", str(path), "--save-at", "6", str(out)]
        try:
            run = self.probe("cias-led", *arguments)
            self.assertEqual([(6, 0, "")] * (1 + len(flips)), run.loads)
            self.assertEqual([], run.save_errors)
            reference = run.after[0]["cia"]
            self.assertEqual(parse_chunks(table.blob)[CIA][2], parse_chunks(base_out.read_bytes())[CIA][2], "the state taken in is not the state put back")
            for index, ((label, _), after) in enumerate(zip(flips, run.after[1:])):
                with self.subTest(field=label):
                    expected = parse_chunks((self.dir / f"flip-{index}.state").read_bytes())[CIA][2]
                    body = parse_chunks((self.dir / f"flip-{index}.out").read_bytes())[CIA][2]
                    self.assertEqual(expected, body, f"{label}: written to the body and not applied, or applied and saved as something else")
                    self.assertNotEqual(reference, after["cia"], f"{label}: the digest of the machine does not see the field")
        finally:
            for path in files:
                if path.exists():
                    path.unlink()

    # -- Validate and Apply ---------------------------------------------------------------------------------------------------------------------------

    def test_a_body_that_validate_accepts_never_fails_to_apply_and_a_refusal_never_changes_the_machine(self):
        _, _, path = self.table_of("cias-led", 3)
        verdicts = set()
        for unit in (4, 9):
            run = self.probe("cias-led", "--units", str(unit), *PART, "--mutate-at", str(unit), "cia", str(path))
            self.assertEqual([], run.mutate_errors, "the good body was refused by the machine it was loaded into")
            (mutated,) = run.mutate
            self.assertEqual(143, mutated["bytes"])
            self.assertEqual(0, mutated["faulted"], "Validate accepted a body that Apply could not apply")
            self.assertEqual(0, mutated["changed"], "a refused state changed the machine")
            self.assertEqual(mutated["attempts"], mutated["accepted"] + mutated["refused"])
            self.assertGreater(mutated["accepted"], 100)
            self.assertGreater(mutated["refused"], 100)
            verdicts.add((mutated["attempts"], mutated["accepted"], mutated["refused"], mutated["verdicts"]))
        self.assertEqual(1, len(verdicts), "the verdict on a body depends on the machine it is loaded into")

    def test_a_bad_body_is_refused_with_its_reason_and_leaves_the_machine_alone(self):
        table, _, _ = self.table_of("cias-led", 3)
        attempts = []
        for label in table.order:
            if base(label) not in RANGES:
                raise AssertionError(f"{label} has no range in the test")
            low, high = RANGES[base(label)]
            size, signed = table.size(label), base(label) in SIGNED
            for what, value in (("below", low - 1), ("above", high + 1)):
                if representable(value, size, signed):
                    attempts.append((f"{label} {what} its range", table.with_values({label: value}), 9, RANGE_MESSAGE))
        for what, value, message in (("INT without its latch", 0x40, "cia.icr1: INT asserted without its latch"), ("INT and flag without the latch", 0x60, "cia.icr1: INT asserted"),
                                     ("the flag alone", 0x20, "cia.icr1: the interrupt flag without"), ("the flag with the latch only", 0xA0, "cia.icr1: the interrupt flag without")):
            attempts.append((f"icr1 of CIA-A: {what}", table.with_values({"cia.icr1[0]": value}), 9, message))
            attempts.append((f"icr1 of CIA-B: {what}", table.with_values({"cia.icr1[1]": value}), 9, message))
        attempts.append(("a loaddelay bit that no writer sets", table.with_values({"cia.loaddelay[0]": 0x00000008}), 9, "cia.loaddelay: a bit no writer sets"))
        attempts.append(("the high bit of a loaddelay", table.with_values({"cia.loaddelay[3]": 0x80000000}), 9, "cia.loaddelay: a bit no writer sets"))
        attempts.append(("the body one byte short", reforge(table.blob, lambda c: c[CIA].__setitem__(2, c[CIA][2][:-1])), 9, "chunk cia: the body ends before its last field"))
        attempts.append(("the body one byte long (refused by the envelope)", reforge(table.blob, lambda c: c[CIA].__setitem__(2, c[CIA][2] + b"\0")), 9, "chunk_length: a chunk claims more bytes"))     # the chunk's own bound
        self.assertGreater(len(attempts), 45)
        run = self.refusals(attempts, rom="cias-led", extra=FULL, unit=6, units=9)
        self.assertEqual(len(attempts), len(run.after))

    def test_the_combinations_of_the_icr_bits_that_rethinkicr_makes_are_accepted(self):
        table, _, base_path = self.table_of("cias-led", 6)
        files = []
        arguments = ["--units", "6", *FULL]
        for value in (0x00, 0x80, 0xC0, 0xE0, 0xE8, 0x1F):
            path = self.dir / f"icr-{value:02x}.state"
            path.write_bytes(table.with_values({"cia.icr1[0]": value, "cia.icr1[1]": value}))
            files.append(path)
            arguments += ["--load-at", "6", str(path)]
        try:
            run = self.probe("cias-led", *arguments)
        finally:
            for path in files:
                path.unlink()
        self.assertEqual([(6, 0, "")] * len(files), run.loads)

    # -- a state is taken from CIAs that fields can describe ------------------------------------------------------------------------------------------------

    def test_a_save_refuses_what_the_fields_cannot_describe_and_writes_nothing(self):
        cases = (("cia-tod2", "a TOD increment is pending in a CIA and its event is not scheduled"),
                 ("cia-ovl", "the overlay latch disagrees with CIA-A"),
                 ("kbmode", "a keyboard mode other than the A500's own"))
        for poke, text in cases:
            with self.subTest(poke=poke):
                refused, accepted = self.dir / f"{poke}.state", self.dir / f"{poke}-clear.state"
                run = self.probe("cias", "--units", "4", "--poke-at", "4", poke, "--save-at", "4", str(refused), "--poke-at", "4", "clear", "--save-at", "4", str(accepted))
                self.assertEqual([(4, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
                self.assertIn("chunk cia could not be saved: " + text, run.save_errors[0][2])
                self.assertFalse(refused.exists())
                self.assertEqual(1, len(run.saves), "with the CIAs back it is saved")
                self.assertTrue(accepted.exists())

    def test_a_tod_increment_pending_with_its_event_is_saved_and_comes_back_in_a_new_process(self):
        path = self.dir / "tod2.state"
        source = self.probe("cias", "--units", "4", "--poke-at", "4", "cia-tod2-event", "--digest-at", "4", "--save-fields-at", "4", str(path))
        self.assertEqual([], source.save_errors)
        self.assertEqual(2, FieldTable(path.read_bytes(), CIA, source.fields).get("cia.tod_event_state[0]"))
        fresh = self.probe("cias", "--units", "0", "--load-at", "0", str(path), *FULL)
        self.assertEqual([(0, 0, "")], fresh.loads)
        for subsystem in ("cia", "scheduler"):               # the event is the scheduler's: it comes back with its handler, the state of the CIA with the chunk
            self.assertEqual(source.after[0][subsystem], fresh.after[0][subsystem], subsystem)

    # -- the power LED a session leaves lit is not in the next load -----------------------------------------------------------------------------------------

    def test_a_load_after_a_session_that_ended_with_the_led_lit_is_a_clean_load(self):
        # p6_cia_session_begin did not put powerled, its brightness and led_old_brightness back (patch 0083): the next load started with the filter of the
        # first unit on, and the digest of a reload was not the one of a clean process. Unit 0 is the machine before its reset head has run: not compared.
        for rom in ("cias-led", "cias"):
            with self.subTest(rom=rom):
                clean = self.passes(rom, "--units", "5")[0]
                loads = self.passes(rom, "--units", "5", "--loads", "3")
                self.assertEqual({1, 2, 3}, set(loads))
                for number in (1, 2, 3):
                    different = sorted(key for key in clean if key[0] >= 1 and clean[key] != loads[number][key])
                    self.assertEqual([], different, f"load {number} differs from a clean process")

    # -- NTSC and the owner's Kickstart --------------------------------------------------------------------------------------------------------------

    def test_an_ntsc_machine_puts_both_cias_back(self):
        path = self.dir / "ntsc.state"
        run = self.probe("cias", "--ntsc", "--units", "7", "--every", "1", "--save-at", "3", str(path), "--load-at", "7", str(path), *PART)
        self.assertEqual([(7, 0, "")], run.loads)
        self.assertEqual(run.digests[(3, "cia")], run.after[0]["cia"])

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_puts_its_cias_back_at_the_boundaries_where_they_are_busiest(self):
        # 60: the CIAs untouched, the overlay on until about unit 20; 107: the keyboard handshake in flight (CRA bit 6 up, a byte in the SDR); 108: the stream
        # over, timer B running with its input pipe; 140: the steady state with the LED lit and the TOD counting.
        for taken in (60, 107, 108, 140):
            with self.subTest(taken=taken):
                path, again = self.dir / f"kickstart-{taken}.state", self.dir / f"kickstart-{taken}.again"
                run = self.probe("kickstart", "--units", str(taken + 3), "--every", "1", "--save-at", str(taken), str(path), "--load-at", str(taken + 3), str(path), *PART)
                self.assertEqual([(taken + 3, 0, "")], run.loads)
                self.assertEqual(run.digests[(taken, "cia")], run.after[0]["cia"])
                fresh = self.probe("kickstart", "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), *FULL)
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(run.digests[(taken, "cia")], fresh.after[0]["cia"])
                a, b = parse_chunks(path.read_bytes()), parse_chunks(again.read_bytes())
                self.assertEqual(a[CIA], b[CIA])


if __name__ == "__main__":
    unittest.main()
