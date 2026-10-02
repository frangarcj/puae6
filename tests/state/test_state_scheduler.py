"""T15b2: chunk P6SC, the state of the scheduler (bridge/state_cpu.cpp), on the real engine.

What is proven here is what the chunk puts back, not that a machine can be restored (that is the closure of T15): the digest of the scheduler
subsystem and the bytes of the chunk come back to what they were in the unit the state was taken in, in the same process and in a new one, every
field of the body is applied, a body that Validate accepts never fails to apply, a bad body is refused with the machine untouched, and an event
that a state cannot name is not dropped silently.

None of the programs here, nor the owner's Kickstart (with or without a disk), leaves a secondary event pending at a unit boundary: the slots are
tested with real events that the probe schedules through the engine's own call (event2_newevent_xx), one for each handler a state can name.
"""
import os
import subprocess
import unittest

from tests.scenarios.audio.audio import Tone, rom_source as tone_source
from tests.state import envelope as e
from tests.state.harness import FieldTable, ProbeCase, SCENARIOS, SUBSYSTEMS, TRACE, USER, parse_chunks, reforge

SCHEDULER = 1           # index of the chunk in the state
IDS = tuple(range(1, 12))
MISC, SYNC = 2, 0       # indexes of ev_misc and ev_sync in eventtab
POKES = [f"ev2:{i}" for i in IDS]
EVENTS_AT = 5           # the unit in which the events are scheduled


class SchedulerChunkTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "stop": ("stop", None, None), "halt": ("halt", None, None), "input_all": ("input_all", None, None),
            "long_ops": ("long_ops", {"PAD": "3"}, None), "copper_irq": ("copper_irq", {"HP": "2"}, None), "trace": (TRACE, None, None),
            "user": (USER, None, None), "tone": (tone_source(Tone("probe", channels=(0, 1), period=200, volume=64)), None, None)}

    def scenarios(self):
        names = dict(SCENARIOS)
        names["tone"] = (3, 4)
        if "kickstart" in self.roms:
            names["kickstart"] = (60, 10)
        return names

    def poke_arguments(self, unit=EVENTS_AT):
        arguments = []
        for poke in POKES:
            arguments += ["--poke-at", str(unit), poke]
        return arguments

    def table_of(self, rom, unit, *arguments):
        path = self.dir / f"{rom}-{unit}.sched"
        run = self.probe(rom, "--units", str(unit), *arguments, "--save-fields-at", str(unit), str(path))
        return FieldTable(path.read_bytes(), SCHEDULER, run.fields), run

    def events_state(self):
        """The state of vsync after eleven real secondary events were scheduled (one for each id), and what the machine's digests were then."""
        path = self.dir / "events.state"
        run = self.probe("vsync", "--units", str(EVENTS_AT), "--every", "1", *self.poke_arguments(), "--digest-at", str(EVENTS_AT), "--save-fields-at",
                         str(EVENTS_AT), str(path))
        return FieldTable(path.read_bytes(), SCHEDULER, run.fields), run.after[0], run

    # -- the table of handler ids ----------------------------------------------------------------------------------------------------------

    def test_every_handler_a_state_can_name_has_its_own_id_and_the_engine_function_behind_it(self):
        lines = {}
        out = subprocess.run([str(self.exe), str(self.roms["vsync"]), "--units", "0", "--handler-table"], capture_output=True, text=True, timeout=300).stdout
        for line in out.splitlines():
            if line.startswith("handler "):
                _, ident, verdict = line.split()
                lines[ident] = verdict
        self.assertEqual({str(i) for i in IDS} | {"0", "12", "foreign"}, set(lines))
        self.assertEqual({"OK"}, set(lines.values()), lines)

    # -- the chunk puts the scheduler back ---------------------------------------------------------------------------------------------------

    def test_the_chunk_puts_the_scheduler_digest_of_the_unit_it_was_taken_in_back_and_moves_nothing_else(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path = self.dir / f"{rom}.state"
                run = self.probe(rom, "--units", str(n + k), "--every", "1", "--save-at", str(n), str(path), "--load-at", str(n + k), str(path),
                                 "--only", "scheduler")
                self.assertEqual([(n + k, 0, "")], run.loads)
                self.assertNotEqual(run.digests[(n, "scheduler")], run.digests[(n + k, "scheduler")], f"{rom}: the restore would prove nothing")
                self.assertEqual(run.digests[(n, "scheduler")], run.after[0]["scheduler"])
                # The hsync and vsync counters travel in this chunk and the chipset digest hashes them as well, and the rtc digest hashes the cycle
                # counter that the clock is anchored to (the epoch coupling the rtc and chipset chunks will have to follow): those two move.
                others = [s for s in SUBSYSTEMS if s not in ("scheduler", "chipset", "rtc")]
                self.assertEqual({s: run.digests[(n + k, s)] for s in others}, {s: run.after[0][s] for s in others}, "only the scheduler chunk was applied")

    def test_a_fresh_image_gets_the_scheduler_of_the_unit_it_was_taken_in_after_the_reset_head_has_run(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path, again = self.dir / f"{rom}.state", self.dir / f"{rom}.again"
                source = self.probe(rom, "--units", str(n), "--every", "1", "--save-at", str(n), str(path))
                fresh = self.probe(rom, "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), "--only", "cpu", "--only", "scheduler",
                                   "--only", "ram", "--only", "rtc", "--only", "cia")
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(source.digests[(n, "scheduler")], fresh.after[0]["scheduler"])
                self.assertEqual(path.read_bytes()[e.HEADER_SIZE:], again.read_bytes()[e.HEADER_SIZE:],
                                 "the state taken again after the restore has the bytes of the one that was loaded")

    def test_the_state_taken_again_after_a_restore_in_the_same_process_is_the_same_bytes(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                first, second = self.dir / f"{rom}.first", self.dir / f"{rom}.second"
                self.probe(rom, "--units", str(n + k), "--save-at", str(n), str(first), "--load-at", str(n + k), str(first), "--save-at", str(n + k),
                           str(second), "--only", "cpu", "--only", "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia")
                a, b = parse_chunks(first.read_bytes()), parse_chunks(second.read_bytes())
                self.assertEqual(a[SCHEDULER], b[SCHEDULER])

    # -- real secondary events ---------------------------------------------------------------------------------------------------------------

    def test_secondary_events_of_every_handler_are_restored_by_slot_with_their_handler_in_the_same_process_and_in_a_new_one(self):
        table, then, run = self.events_state()
        self.assertEqual(11, sum(table.get(f"ev2.active[{i}]") for i in range(16)))
        self.assertEqual(sorted(IDS), sorted(table.get(f"ev2.handler[{i}]") for i in range(16) if table.get(f"ev2.active[{i}]")))
        self.assertEqual(1, table.get("ev2.handler[0]"), "slot 0 is the blitter's")
        path = self.dir / "events.state"
        again = self.dir / "events.again"
        # The same program, four units later and with no events: the chunk puts the table back, handlers included (the digest names them by id).
        same = self.probe("vsync", "--units", "9", "--every", "1", "--load-at", "9", str(path), "--save-at", "9", str(again), "--only", "cpu", "--only",
                          "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia")
        self.assertEqual([(9, 0, "")], same.loads)
        self.assertNotEqual(then["scheduler"], same.digests[(9, "scheduler")])
        self.assertEqual(then["scheduler"], same.after[0]["scheduler"])
        self.assertEqual(path.read_bytes()[e.HEADER_SIZE:], again.read_bytes()[e.HEADER_SIZE:])
        fresh = self.probe("vsync", "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), "--only", "cpu", "--only", "scheduler",
                           "--only", "ram", "--only", "rtc", "--only", "cia")
        self.assertEqual([(0, 0, "")], fresh.loads)
        self.assertEqual(then["scheduler"], fresh.after[0]["scheduler"])
        self.assertEqual(path.read_bytes()[e.HEADER_SIZE:], again.read_bytes()[e.HEADER_SIZE:])

    def test_a_secondary_event_with_a_handler_a_state_cannot_name_is_refused_when_saving_and_nothing_is_written(self):
        run = self.probe("vsync", "--units", "4", "--poke-at", "4", "ev2-foreign", "--save-at", "4", str(self.dir / "foreign.state"))
        self.assertEqual([(4, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
        self.assertIn("a secondary event has a handler that a state cannot name", run.save_errors[0][2])
        self.assertFalse((self.dir / "foreign.state").exists())

    def test_what_a_fired_event_leaves_in_its_slot_is_written_as_zeros_so_that_two_saves_of_a_machine_are_the_same_bytes(self):
        path = self.dir / "stale.state"
        run = self.probe("vsync", "--units", "4", "--poke-at", "4", "ev2-stale", "--save-fields-at", "4", str(path))
        table = FieldTable(path.read_bytes(), SCHEDULER, run.fields)
        self.assertEqual((0, 0, 0, 0), tuple(table.get(f"ev2.{name}[7]") for name in ("active", "handler", "evtime", "data")))
        # And the body is one that a load accepts: a slot that is not active has nothing in it.
        again = self.probe("vsync", "--units", "6", "--only", "scheduler", "--load-at", "6", str(path))
        self.assertEqual([(6, 0, "")], again.loads)

    def test_a_cycle_counter_that_is_not_at_the_state_of_an_instruction_boundary_is_not_saved(self):
        arguments = []
        for index, poke in enumerate(("start-cycles", "pissoff")):
            arguments += ["--poke-at", "4", poke, "--save-at", "4", str(self.dir / f"counter{index}.state"), "--poke-at", "4", "clear"]
        arguments += ["--save-at", "4", str(self.dir / "counter-clear.state")]
        run = self.probe("vsync", "--units", "4", *arguments)
        self.assertEqual([(4, 2), (4, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
        # start_cycles is the scheduler chunk's own check; pissoff is also a CPU boundary condition, and the cpu chunk is written first.
        self.assertIn("chunk scheduler could not be saved: the cycle counter is not in the state of an instruction boundary", run.save_errors[0][2])
        self.assertIn("chunk cpu could not be saved: a file-local CPU counter is not at its boundary value", run.save_errors[1][2])
        for index in range(2):
            self.assertFalse((self.dir / f"counter{index}.state").exists())
        self.assertEqual(1, len(run.saves), "once the counters are back it is saved")

    # -- every field is applied ---------------------------------------------------------------------------------------------------------------

    def candidates(self, label, table, now):
        """Changes to try for a field, each as {label: value} (several fields at once where the body ties them together), most likely valid first."""
        name, _, index = label.partition("[")
        index = int(index.rstrip("]")) if index else None
        n = table.get(label, signed=name in ("sched.currcycle", "ev.evtime", "ev.oldcycles", "ev2.evtime"))
        if name == "sched.currcycle":
            return [{label: n + 1}, {label: n + 2}, {label: n - 1}]
        if name in ("sched.hsync_counter", "sched.vsync_counter"):
            return [{label: n + 1}, {label: n ^ 1}]
        if name in ("sched.extra_cycle", "sched.cycles_to_add_remain"):
            return [{label: n ^ 1}, {label: 256}]
        if name == "ev.active":
            ev = f"[{index}]"
            if n:
                return [{label: 0, "ev.evtime" + ev: 0}]
            return [{label: 1, "ev.evtime" + ev: now + 100}]
        if name == "ev.evtime":
            return [{label: n + 1}, {label: n + 7}, {label: n - 1}]
        if name == "ev.oldcycles":
            return [{label: n - 1}, {label: n + 1}, {label: n ^ 1}]
        if name == "ev2.idx":
            return [{label: n ^ 1}, {label: n + 1}, {label: n - 1}]
        if name == "ev2.next":
            return [{label: n + 1}, {label: n - 1}]
        slot = f"[{index}]"
        if name == "ev2.active":
            if n:
                return [{label: 0, "ev2.handler" + slot: 0, "ev2.evtime" + slot: 0, "ev2.data" + slot: 0}]
            return [{label: 1, "ev2.handler" + slot: 2, "ev2.evtime" + slot: now + 10_000_000, "ev2.data" + slot: 7}]
        if name == "ev2.handler":
            return [{label: 2 + (n - 1) % 10}, {label: 2 + n % 10}, {label: 3}]
        if name == "ev2.evtime":
            return [{label: n + 1}, {label: n + 5}]
        if name == "ev2.data":
            return [{label: n ^ 1}, {label: n + 1}]
        raise AssertionError(label)

    def test_every_field_of_the_body_is_applied_a_different_value_gives_a_different_scheduler(self):
        table, _, _ = self.events_state()
        now = table.get("sched.currcycle", signed=True)
        base = self.dir / "events.state"
        plan = []                                   # (label, values)
        for label in table.order:
            for values in self.candidates(label, table, now):
                plan.append((label, values))
        self.assertGreater(len(plan), 150)
        accepted, refused = {}, set()
        for start in range(0, len(plan), 40):
            batch = plan[start:start + 40]
            files = []
            arguments = ["--units", "9", "--only", "scheduler", "--load-at", "9", str(base)]
            for index, (label, values) in enumerate(batch):
                path = self.dir / f"flip-{index}.state"
                path.write_bytes(table.with_values(values))
                files.append(path)
                arguments += ["--load-at", "9", str(path)]
            try:
                run = self.probe("vsync", *arguments)
            finally:
                for path in files:
                    path.unlink()
            reference = run.after[0]["scheduler"]
            for (label, values), (_, code, message), after in zip(batch, run.loads[1:], run.after[1:]):
                self.assertIn(code, (0, 9), f"{label} {values}: {message}")
                if code == 0:
                    accepted.setdefault(label, []).append(after["scheduler"] != reference)
                else:
                    refused.add(label)
        # What cannot be changed on its own: the sync event is never scheduled, ev_misc stays armed while secondary events are pending, slot 0 is
        # the blitter's alone, and what an inactive entry or slot holds is zero by definition.
        tied = set()
        for label in table.order:
            name, _, index = label.partition("[")
            index = int(index.rstrip("]")) if index else None
            if name == "ev.active" and index in (SYNC, MISC):
                tied.add(label)
            if name == "ev.evtime" and not table.get(f"ev.active[{index}]"):
                tied.add(label)
            if name == "ev2.handler" and index == 0:
                tied.add(label)
            if name in ("ev2.handler", "ev2.evtime", "ev2.data") and not table.get(f"ev2.active[{index}]"):
                tied.add(label)
        for label in table.order:
            if label in tied:
                self.assertNotIn(label, accepted, f"{label}: a field that must stay as it is was accepted with a different value")
                self.assertIn(label, refused, label)
            else:
                self.assertIn(label, accepted, f"{label}: no valid change was found to try")
                self.assertTrue(any(accepted[label]), f"{label}: the field is written to the body and not applied to the machine")

    # -- Validate and Apply -------------------------------------------------------------------------------------------------------------------

    def test_a_body_that_validate_accepts_never_fails_to_apply_and_a_refusal_never_changes_the_machine(self):
        self.events_state()
        path = self.dir / "events.state"
        for name, source in (("with events", path), ("without", None)):
            if source is None:
                source = self.dir / "plain.state"
                self.probe("vsync", "--units", "5", "--save-at", "5", str(source))
            verdicts = set()
            for units in (6, 10):               # two machines at two moments: the verdict is about the body alone
                run = self.probe("vsync", "--units", str(units), "--only", "scheduler", "--mutate-at", str(units), "scheduler", str(source))
                self.assertEqual([], run.mutate_errors, f"{name}: the good body was refused by the machine it was loaded into")
                (mutated,) = run.mutate
                self.assertEqual(314, mutated["bytes"], name)
                self.assertEqual(0, mutated["faulted"], f"{name}: Validate accepted a body that Apply could not apply")
                self.assertEqual(0, mutated["changed"], f"{name}: a refused state changed the machine")
                self.assertEqual(mutated["attempts"], mutated["accepted"] + mutated["refused"])
                self.assertGreater(mutated["accepted"], 50, name)
                self.assertGreater(mutated["refused"], 300, name)
                verdicts.add((mutated["attempts"], mutated["accepted"], mutated["refused"], mutated["verdicts"]))
            self.assertEqual(1, len(verdicts), f"{name}: the verdict on a body depends on the machine it is loaded into")

    def test_a_bad_body_is_refused_with_its_reason_and_leaves_the_machine_alone(self):
        table, _, _ = self.events_state()
        plain, _ = self.table_of("vsync", 5)
        now = table.get("sched.currcycle", signed=True)
        rng_range = "a field is out of its range"
        misc = table.get(f"ev.evtime[{MISC}]", signed=True)
        slots = [i for i in range(16) if table.get(f"ev2.active[{i}]")]
        free = next(i for i in range(1, 16) if not table.get(f"ev2.active[{i}]") and i not in slots) if len(slots) < 15 else None
        latest = max(table.get(f"ev2.evtime[{i}]", signed=True) for i in slots)
        live = self.dir / "events.state"

        def forge(values, source=table):
            return source.with_values(values)

        attempts = [
            ("a negative cycle counter", forge({"sched.currcycle": -1}), 9, rng_range),
            ("an extra cycle of 512", forge({"sched.extra_cycle": 512}), 9, rng_range),
            ("a remainder of 512", forge({"sched.cycles_to_add_remain": 512}), 9, rng_range),
            ("the sync event scheduled", forge({"ev.active[0]": 1, "ev.evtime[0]": now + 10}), 9, "the sync event is never scheduled"),
            ("an active flag of 2", forge({"ev.active[1]": 2}), 9, rng_range),
            ("a negative event time", forge({"ev.evtime[1]": -1}), 9, rng_range),
            ("an event in the past", forge({"ev.evtime[%d]" % MISC: now - 1}), 9, "an event is in the past"),
            ("an inactive event with a time", forge({"ev.evtime[3]": now + 5}), 9, "an event is in the past, or an inactive one has a time"),
            ("an oldcycles in the future", forge({"ev.oldcycles[1]": now + 1}), 9, "ev.oldcycles: later than the cycle counter"),
            ("a negative oldcycles", forge({"ev.oldcycles[1]": -1}), 9, rng_range),
            ("a secondary round of 17", forge({"ev2.idx": 17}), 9, rng_range),
            ("a next slot of 0", forge({"ev2.next": 0}), 9, rng_range),
            ("a next slot of 16", forge({"ev2.next": 16}), 9, rng_range),
            ("a handler id of 12", forge({"ev2.handler[1]": 12}), 9, rng_range),
            ("an active slot with no handler", forge({"ev2.handler[1]": 0}), 9, "has no handler or is in the past"),
            ("an active slot in the past", forge({"ev2.evtime[1]": now - 1}), 9, "has no handler or is in the past"),
            ("the blitter's id in another slot", forge({"ev2.handler[1]": 1}), 9, "slot 0 holds the blitter's event and nothing else does"),
            ("another id in slot 0", forge({"ev2.handler[0]": 2}), 9, "slot 0 holds the blitter's event and nothing else does"),
            ("secondary events pending and ev_misc off", forge({"ev.active[%d]" % MISC: 0, "ev.evtime[%d]" % MISC: 0}), 9,
             "secondary events are pending but ev_misc is not armed for them"),
            ("ev_misc later than the earliest event", forge({"ev.evtime[%d]" % MISC: latest + 1}), 9,
             "secondary events are pending but ev_misc is not armed for them"),
            ("the body one byte short", self.shorter(table), 9, "chunk scheduler: the body ends before its last field"),
        ]
        if free is not None:
            attempts += [("an inactive slot with a handler", forge({f"ev2.handler[{free}]": 2}), 9, "an inactive secondary event has data"),
                         ("an inactive slot with a time", forge({f"ev2.evtime[{free}]": now + 5}), 9, "an inactive secondary event has data"),
                         ("an inactive slot with data", forge({f"ev2.data[{free}]": 1}), 9, "an inactive secondary event has data")]
        run = self.refusals(attempts, rom="vsync", extra=("--only", "scheduler"), unit=9, units=12)
        self.assertEqual(len(attempts), len(run.after))
        self.assertTrue(live.exists())

    def shorter(self, table):
        return reforge(table.blob, lambda c: c[SCHEDULER].__setitem__(2, c[SCHEDULER][2][:-1]))

    # -- a real Kickstart ---------------------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_gets_its_scheduler_back_at_several_boundaries_with_and_without_a_disk(self):
        blank = self.dir / "blank.adf"
        blank.write_bytes(bytes(901120))
        for extra in ((), ("--adf", str(blank))):
            for n, k in ((25, 10), (140, 6), (290, 5)):
                with self.subTest(unit=n, disk=bool(extra)):
                    path = self.dir / f"kickstart-{n}-{len(extra)}.state"
                    run = self.probe("kickstart", "--units", str(n + k), "--every", "1", *extra, "--save-at", str(n), str(path), "--load-at", str(n + k),
                                     str(path), "--only", "scheduler")
                    self.assertEqual([(n + k, 0, "")], run.loads)
                    self.assertEqual(run.digests[(n, "scheduler")], run.after[0]["scheduler"])
                    again = self.dir / f"kickstart-{n}-{len(extra)}.again"
                    fresh = self.probe("kickstart", "--units", "0", *extra, "--load-at", "0", str(path), "--save-at", "0", str(again), "--only", "cpu",
                                       "--only", "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia")
                    self.assertEqual([(0, 0, "")], fresh.loads)
                    self.assertEqual(run.digests[(n, "scheduler")], fresh.after[0]["scheduler"])
                    self.assertEqual(path.read_bytes()[e.HEADER_SIZE:], again.read_bytes()[e.HEADER_SIZE:])


if __name__ == "__main__":
    unittest.main()
