"""T15h: chunk P6BR, the bookkeeping of the API session (bridge/state.cpp), on the real engine.

P6BR holds what the host has asked of the image and the image has not done yet, and what the bridge counts: the ordered queue of media events not yet applied, the
sequence guard that decides which events are accepted next, the drive contents asked for, the pending reset, the start flag, the audio frame count and the delivery flag
of the unit. The unit and tick counters travel in the header and `cycles_seen` is derived from the scheduler's counter: a load sets it to currcycle, the restored one when P6SC is applied (never
from the destination's counter). Identity (configuration, firmware, media) is validated by the envelope and never loaded; the edge memory of the input is P6IN's and the clock's anchor P6RT's.

What is proven here:
  - every field of the body comes back to the unit the state was taken in, in the same process and in a new one, and is saved again unchanged (the chunk, P6IN and the
    header);
  - the future of the session: a machine that is rewound to the state (the same image, a few units later, with none of the host calls the original made) goes on
    exactly as the original, in every subsystem the probe digests except the picture: the three queued events are applied in order, the pending reset runs first, the held
    key is still held, and the sequence guard refuses a repeated number and accepts the next one, as in the original; changing one field of the body changes that future;
  - the chunk alone puts the session back and a save afterwards is accepted (cycles_seen still matches the destination's counter); a load of every chunk into a machine at another
    cycle leaves cycles_seen at the restored currcycle (the probe hashes it with the scheduler, whose counter it follows), so the session is the original's;
  - a body that does not describe an image (a queue above its bound, an index that does not fit its event type, events with no accepted sequence, a drive that is not
    DF0 or DF1, a flag that is not 0 or 1) is refused before anything is applied and leaves the machine as it was.

What is NOT covered here: the physical state of the drives and Paula, which the upstream bodies carry (T15i2: tests/state/test_state_upstream.py), and the beam, which
they lose (T15f). The scenario is chosen so that no disk event has been applied between the boot and the saved unit: the drive is in the same state in the original and in
the machine the state is loaded into, and the events that are still queued are the first ones the engine sees. A new process is checked by exact re-emission (load, then
save: the same bytes), not by running.
"""
import hashlib
import re
import struct
import subprocess

from tests.state.harness import FieldTable, ProbeCase, Run, SUBSYSTEMS, parse_chunks, reforge

BRIDGE, INPUT = 10, 9
# The video digest is the picture, which no chunk restores. The chipset digest hashes the beam, which the upstream bodies do not carry (restore_custom reads VPOSR and
# VHPOSR and throws them away), so a loaded machine is not the original in it until T15f puts the beam back; this module is about the session, which is in the others.
COMPARED = tuple(s for s in SUBSYSTEMS if s not in ("video", "chipset"))
N = 4                   # the unit the state is taken in
UNITS_AFTER = 5
STALE = "event sequence numbers must increase"


class BridgeStateTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adfs = []
        for name in ("a", "b"):
            path = cls.dir / f"{name}.adf"
            path.write_bytes(hashlib.sha256(f"disk {name}".encode()).digest() * (901120 // 32))
            cls.adfs.append(path)
        cls.media = ("--adf", str(cls.adfs[0]), "--adf", str(cls.adfs[1]))

    @classmethod
    def probe(cls, rom, *args):
        result = subprocess.run([str(cls.exe), str(cls.roms[rom]), *args], capture_output=True, text=True, timeout=1800)
        assert result.returncode == 0, result.stderr[-400:]
        run = Run(result.stdout)
        run.events = [(int(u), int(code), msg) for u, code, msg in re.findall(r"^event (\d+) (-?\d+) ?(.*)$", result.stdout, re.M)]
        run.resets = [(int(u), int(code), msg) for u, code, msg in re.findall(r"^reset (\d+) (-?\d+) ?(.*)$", result.stdout, re.M)]
        return run

    # -- the scenario ---------------------------------------------------------------------------------------------------------------------------

    def common(self, standard):
        """What every process of the scenario has: both disks registered, DF0 mounted at boot with the first (an event before the first unit), key 0x20 held from unit 3."""
        return ["--every", "1", *self.media, *standard, "--event-at", "0", "0", "0", "1", "--key", "3", "0x20", "down"]

    def original(self, standard, units, path, extra=()):
        """The run that takes the state at unit N with three media events queued (mount DF0, eject DF0, mount DF1: sequences 10, 11 and 12) and a reset pending, and goes on.
        Its `after[0]` is the machine at that boundary with those calls made (the digests of the unit itself are printed before them)."""
        args = ["--units", str(units), *self.common(standard), "--event-at", str(N), "0", "1", "10", "--event-at", str(N), "0", "-1", "11",
                "--event-at", str(N), "1", "0", "12", "--reset-at", str(N), "--save-fields-at", str(N), str(path), "--digest-at", str(N), *extra]
        return self.probe("vsync", *args)

    @staticmethod
    def bodies(path):
        return {tag: body for tag, _version, body in parse_chunks(path.read_bytes())}

    @staticmethod
    def header(path):
        return struct.unpack_from("<QQ", path.read_bytes(), 24)

    def distance(self, standard, control, path):
        """How many units later the same image is, before the load, in the state of the original at unit N in every subsystem compared (the bridge's digest included, which only the
        load of P6BR can make equal): the machine the state is put back into."""
        for k in range(1, 13):
            run = self.probe("vsync", "--units", str(N + k), *self.common(standard), "--load-at", str(N + k), str(path))
            if run.loads == [(N + k, 0, "")] and all(run.after[0][s] == control.after[0][s] for s in COMPARED):
                return k
        return None

    # -- the fields -----------------------------------------------------------------------------------------------------------------------------

    def assert_saved_fields(self, control, path):
        table = FieldTable(path.read_bytes(), BRIDGE, control.fields)
        self.assertEqual(3, table.get("bridge.media_queued"))
        self.assertEqual(1, table.get("bridge.reset_queued"))
        self.assertEqual(1, table.get("bridge.started"))
        self.assertEqual(1, table.get("bridge.have_sequence"))
        self.assertEqual(12, table.get("bridge.last_sequence"))
        self.assertEqual([0, -1, -1, -1], [table.get(f"bridge.drive_media[{i}]", signed=True) for i in range(4)], "DF0 was mounted at boot")
        self.assertEqual([1, 2, 1], [table.get(f"bridge.event_type[{i}]") for i in range(3)])
        self.assertEqual([0, 0, 1], [table.get(f"bridge.event_drive[{i}]") for i in range(3)])
        self.assertEqual([1, 0xFFFFFFFF, 0], [table.get(f"bridge.event_index[{i}]") for i in range(3)])
        self.assertGreater(table.get("bridge.audio_frames_total"), 0)
        self.assertEqual(40 + 6 * 3, len(self.bodies(path)[b"P6BR"]))
        self.assertEqual(1, FieldTable(path.read_bytes(), INPUT, control.fields).get("applied.keys[1]") & 1, "key 0x20 is held when the state is taken")
        self.assertEqual(N, self.header(path)[0])
        self.assertGreater(self.header(path)[1], 0)
        return table

    def assert_drained(self, path):
        """The unit after the saved one applied the reset and the three events: nothing is pending, DF1 has the second disk and DF0 was ejected again."""
        body = self.bodies(path)[b"P6BR"]
        _audio, handed, reset, started, have = struct.unpack_from("<QBBBB", body)
        self.assertEqual((1, 0, 1, 1), (handed, reset, started, have))
        self.assertEqual((-1, 0, -1, -1), struct.unpack_from("<iiii", body, 20))
        self.assertEqual(0, struct.unpack_from("<I", body, 36)[0])
        self.assertEqual(40, len(body))

    # -- the future -----------------------------------------------------------------------------------------------------------------------------

    def test_pending_events_reset_and_held_key_continue_in_the_same_image_and_are_saved_again_unchanged_in_a_new_process(self):
        for standard in ((), ("--ntsc",)):
            with self.subTest(standard=standard):
                self.roundtrip(standard)

    def roundtrip(self, standard):
        source, source5 = self.dir / "source.state", self.dir / "source-5.state"
        # Units 5 to 9 of the original; at unit 6 it calls the API again: the number 12 it already accepted (refused) and the next one (accepted).
        control = self.original(standard, N + UNITS_AFTER, source, ["--save-at", str(N + 1), str(source5), "--event-at", "6", "0", "1", "12", "--event-at", "6", "1", "-1", "13"])
        self.assertEqual([(0, 0, ""), (N, 0, ""), (N, 0, ""), (N, 0, ""), (6, 1, STALE), (6, 0, "")], control.events)
        self.assertEqual([(N, 0, "")], control.resets)
        self.assert_saved_fields(control, source)
        self.assert_drained(source5)

        # The same image, k units later and with none of the calls above, is put back at the state: the queue, the sequence guard, the reset, the counters and the drive contents
        # come from the state, and the units that follow are the original's.
        k = self.distance(standard, control, source)
        self.assertIsNotNone(k, "no unit in 1..12 at which the machine, after the load, is the original at the saved unit in every subsystem compared")
        same, same_1 = self.dir / "same.state", self.dir / "same-1.state"
        run = self.probe("vsync", "--units", str(N + k + UNITS_AFTER), *self.common(standard), "--load-at", str(N + k), str(source), "--save-at", str(N + k), str(same),
                         "--save-at", str(N + k + 1), str(same_1), "--event-at", str(N + k + 2), "0", "1", "12", "--event-at", str(N + k + 2), "1", "-1", "13")
        self.assertEqual([(N + k, 0, "")], run.loads)
        self.assertEqual([(0, 0, ""), (N + k + 2, 1, STALE), (N + k + 2, 0, "")], run.events, "the sequence guard came back with the state")
        self.assertEqual([], run.resets, "the reset is the state's, not a call of this process")
        for tag in (b"P6BR", b"P6IN"):
            self.assertEqual(self.bodies(source)[tag], self.bodies(same)[tag], tag)
            self.assertEqual(self.bodies(source5)[tag], self.bodies(same_1)[tag], tag)
        self.assertEqual(self.header(source), self.header(same), "a load keeps the unit and the ticks of the state")
        self.assertEqual(self.header(source5), self.header(same_1))
        self.assert_drained(same_1)
        for j in range(1, UNITS_AFTER + 1):
            different = [s for s in COMPARED if control.digests[(N + j, s)] != run.digests[(N + k + j, s)]]
            self.assertEqual([], different, f"{j} units after the load the machine is not the original's in {different}")

        # A new process: the image has not run its reset head, so the load runs it and then applies the chunks. Saved again at once, the session is the same bytes. The sequence guard is the state's here too.
        fresh = self.dir / "fresh.state"
        run = self.probe("vsync", "--units", "0", "--every", "1", *self.media, *standard, "--event-at", "0", "0", "0", "1", "--load-at", "0", str(source),
                         "--event-at", "0", "0", "1", "12", "--save-at", "0", str(fresh))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual([(0, 0, ""), (0, 1, STALE)], run.events, "DF0 is mounted at boot, as in the state: a state taken with a disk in a drive is refused by an image that has none there")
        for tag in (b"P6BR", b"P6IN", b"P6SC", b"P6CP", b"P6RT", b"P6CI"):
            self.assertEqual(self.bodies(source)[tag], self.bodies(fresh)[tag], tag)
        self.assertEqual(self.header(source), self.header(fresh))

    def test_the_chunk_alone_puts_the_session_back_and_a_load_that_applies_the_scheduler_sets_the_cycle_seen_to_the_restored_counter(self):
        for standard in ((), ("--ntsc",)):
            with self.subTest(standard=standard):
                source = self.dir / "alone.state"
                control = self.original(standard, N, source)
                for k in (1, 2, 5):
                    # P6BR alone, into a machine that is at another cycle: its fields come back, the counter and cycles_seen are the destination's and agree (a save is accepted).
                    alone, again = self.dir / "alone-loaded.state", self.dir / "alone-again.state"
                    run = self.probe("vsync", "--units", str(N + k), *self.common(standard), "--load-at", str(N + k), str(source), "--only", "bridge",
                                     "--save-at", str(N + k), str(again))
                    self.assertEqual([(N + k, 0, "")], run.loads)
                    self.assertEqual([], run.save_errors, "cycles_seen still matches the live counter after a load that did not apply the scheduler")
                    self.assertNotEqual(control.after[0]["scheduler"], run.after[0]["scheduler"], "the machine the state is put into is at another cycle")
                    self.assertEqual(control.after[0]["bridge"], run.after[0]["bridge"], "every field of the session is the original's (the cycle seen is in the scheduler digest)")
                    self.assertEqual(self.bodies(source)[b"P6BR"], self.bodies(again)[b"P6BR"])
                    self.assertEqual(self.header(source), self.header(again))
                    # Every chunk, into the same machine: the scheduler puts the counter back and cycles_seen follows it, so the whole session is the original's.
                    run = self.probe("vsync", "--units", str(N + k), *self.common(standard), "--load-at", str(N + k), str(source), "--save-at", str(N + k), str(alone))
                    self.assertEqual([], run.save_errors)
                    self.assertEqual(control.after[0]["bridge"], run.after[0]["bridge"], f"k={k}: unit, ticks, audio count, queue, guard, reset and drives")

    def test_changing_a_field_of_the_body_before_it_is_loaded_changes_the_units_that_follow(self):
        standard = ()
        source = self.dir / "sensitive.state"
        control = self.original(standard, N + UNITS_AFTER, source, ["--event-at", "6", "0", "1", "12"])
        table = self.assert_saved_fields(control, source)
        k = self.distance(standard, control, source)
        self.assertIsNotNone(k)
        changes = (
            ("the pending reset", {"bridge.reset_queued": 0}, ("cpu", "scheduler"), None),
            ("the third queued event mounts the second disk in DF1", {"bridge.event_index[2]": 1}, ("bridge",), None),
            ("the sequence guard", {"bridge.last_sequence": 5}, ("bridge",), [(0, 0, ""), (N + k + 2, 0, "")]),
            ("the audio frame count", {"bridge.audio_frames_total": table.get("bridge.audio_frames_total") + 1}, ("bridge",), None),
        )
        for name, values, seen_in, events in changes:
            with self.subTest(change=name):
                path = self.dir / "sensitive-changed.state"
                path.write_bytes(table.with_values(values))
                run = self.probe("vsync", "--units", str(N + k + UNITS_AFTER), *self.common(standard), "--load-at", str(N + k), str(path),
                                 "--event-at", str(N + k + 2), "0", "1", "12")
                self.assertEqual([(N + k, 0, "")], run.loads)
                if events:
                    self.assertEqual(events, run.events)
                different = [s for s in COMPARED if control.digests[(N + UNITS_AFTER, s)] != run.digests[(N + k + UNITS_AFTER, s)]]
                for subsystem in seen_in:
                    self.assertIn(subsystem, different, f"{name}: nothing of the {subsystem} differs {UNITS_AFTER} units later")

    # -- a full queue ---------------------------------------------------------------------------------------------------------------------------

    def test_a_full_queue_fits_the_bound_and_the_event_after_it_is_refused_without_a_trace_in_the_state(self):
        empty, full, again = self.dir / "capacity-empty.state", self.dir / "capacity-full.state", self.dir / "capacity-again.state"
        events = [arg for i in range(17) for arg in ("--event-at", "2", str(i % 2), str(i % 2), str(100 + i))]
        run = self.probe("vsync", "--units", "2", *self.media, "--save-at", "1", str(empty), *events, "--save-fields-at", "2", str(full))
        self.assertEqual([0] * 16 + [7], [code for _, code, _ in run.events], "16 accepted and the 17th is CAPACITY")
        self.assertEqual([], run.save_errors)
        self.assertEqual(run.saves[0][3], run.saves[1][3], "the bound of the state does not grow when the queue fills")
        table = FieldTable(full.read_bytes(), BRIDGE, run.fields)
        self.assertEqual(16, table.get("bridge.media_queued"))
        self.assertEqual(115, table.get("bridge.last_sequence"), "the refused event did not move the sequence")
        self.assertEqual(136, len(self.bodies(full)[b"P6BR"]))
        restored = self.probe("vsync", "--units", "0", *self.media, "--load-at", "0", str(full), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], restored.loads)
        self.assertEqual(self.bodies(full)[b"P6BR"], self.bodies(again)[b"P6BR"])
        self.assertEqual(self.header(full), self.header(again))

    # -- a bad body -----------------------------------------------------------------------------------------------------------------------------

    def test_a_body_that_does_not_describe_an_image_is_refused_before_anything_is_applied(self):
        for standard in ((), ("--ntsc",)):
            with self.subTest(standard=standard):
                self.refused_bodies(standard)

    def refused_bodies(self, standard):
        good = self.dir / "refusals.state"
        control = self.original(standard, N, good)
        table = self.assert_saved_fields(control, good)
        blob = good.read_bytes()
        range_error, count_error = "a field is out of its range", "a list is longer than its bound"
        attempts = []
        for name, values, text in (
                ("a queue above its bound", {"bridge.media_queued": 17}, count_error),
                ("a state of an image that never started", {"bridge.started": 0}, range_error),
                ("events without an accepted sequence", {"bridge.have_sequence": 0}, "bridge: events or sequence without an accepted event"),
                ("an event type that does not exist (0)", {"bridge.event_type[0]": 0}, range_error),
                ("an event type that does not exist (3)", {"bridge.event_type[1]": 3}, range_error),
                ("an event for a drive the A500 does not have", {"bridge.event_drive[0]": 2}, range_error),
                ("a mount of a medium that was not passed to p6_load", {"bridge.event_index[0]": 2}, "invalid media index for its type"),
                ("a mount with the eject index", {"bridge.event_index[0]": 0xFFFFFFFF}, "invalid media index for its type"),
                ("an eject with a medium index", {"bridge.event_index[1]": 0}, "invalid media index for its type"),
                ("DF2 with a disk", {"bridge.drive_media[2]": 0}, range_error),
                ("a disk in DF0 that was not passed to p6_load", {"bridge.drive_media[0]": 2}, range_error),
                ("a drive index below the eject value", {"bridge.drive_media[1]": -2}, range_error),
                ("a delivery flag that is not a flag", {"bridge.handed_over": 2}, range_error),
                ("a pending reset that is not a flag", {"bridge.reset_queued": 2}, range_error)):
            attempts.append((name, table.with_values(values), 9, text))
        attempts.append(("the body one byte longer", reforge(blob, lambda c: c[BRIDGE].__setitem__(2, c[BRIDGE][2] + b"\0")), 9, "bytes after the last field"))
        attempts.append(("the body one byte shorter", reforge(blob, lambda c: c[BRIDGE].__setitem__(2, c[BRIDGE][2][:-1])), 9, "the body ends before its last field"))
        attempts.append(("the body of a queue that says one event less", table.with_values({"bridge.media_queued": 2}), 9, "bytes after the last field"))
        # Loaded into a machine that has run its units and has called nothing: every attempt is refused and the machine goes on as one that never saw them.
        self.refusals(attempts, extra=[*self.common(standard)], unit=N + 2, units=N + 6)


if __name__ == "__main__":
    import unittest
    unittest.main()
