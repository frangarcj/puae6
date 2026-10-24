"""T15c3: chunk P6IN, the state of the input (bridge/state_devices.cpp), on the real engine.

What is proven here is what the chunk puts back, not that a machine can be restored (that is the closure of T15c): every field of the body comes back to the unit the
state was taken in, in the same process and in a new one; the keys, buttons and directions the host holds are still held after a load, and are released by the next packet
as they would have been (the engine's edge memory and the bridge's agree, so nothing stays stuck); the reset chord in flight completes; every field is applied and saved
again unchanged; a body that Validate accepts never fails to apply; a bad body is refused with the machine untouched; and a save refuses what the fields cannot describe.
"""
import os
import re
import subprocess
import unittest

from tests.state.harness import DIGEST, FieldTable, ProbeCase, SUBSYSTEMS, parse_chunks, reforge, with_bytes

INPUT = 9               # index of the chunk in the state
PART = ("--only", "scheduler", "--only", "rtc", "--only", "cia", "--only", "input")
FULL = ("--only", "cpu", "--only", "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia", "--only", "input")
PASS = re.compile(r"^pass (\d+)$")
# VERTB: the first one starts a pot measurement (POTGO bit 0, every line an input) and the vector then points to @NEXT@: this handler again, or one that only acknowledges.
POTGO = """; PUAE6 test program: starts a measurement of the pot lines in the first vertical blank (and again in every one, or never again).
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #first,$6C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
first:
    MOVE.W #$0001,$DFF034.L     ; POTGO: start, all lines inputs
    MOVE.L #@NEXT@,$6C.L
    MOVE.W #$0020,$DFF09C.L
    RTE
again:
    MOVE.W #$0020,$DFF09C.L
    RTE
"""

# the keys: CTRL 0x63, left Amiga 0x66, right Amiga 0x67, left Alt 0x64
CHORD = ["--key", "4", "0x63", "down", "--key", "5", "0x66", "down", "--key", "6", "0x67", "down"]
RELEASE_CHORD = ["--key", "1", "0x63", "up", "--key", "1", "0x66", "up", "--key", "1", "0x67", "up"]
HOLD_CHORD = ["--key", "0", "0x63", "down", "--key", "0", "0x66", "down", "--key", "0", "0x67", "down"]

# name -> (program, unit the state is taken in, units that run before it is loaded back, probe arguments)
SCENARIOS = {
    "mouse": ("input_all", 5, 3, ["--port", "0:2:1:40:-25", "--port", "1:1:65:0:0"]),
    "mouse-pending": ("vsync", 5, 3, ["--port", "0:2:1:40:-25", "--port", "1:1:65:0:0"]),
    "keys": ("vsync", 6, 3, ["--key", "2", "0x20", "down", "--key", "3", "0x21", "down", "--key", "3", "0x40", "down", "--key", "4", "0x20", "up", "--key", "5", "0x45", "down"]),
    "chord": ("vsync", 7, 3, CHORD),
    "chord-hard": ("vsync", 7, 3, CHORD + ["--key", "4", "0x64", "down"]),
    "pot-measuring": ("potgo-once", 6, 3, ["--port", "1:1:6:0:0"]),
    "pot-discharging": ("potgo-every", 4, 3, []),
    "types": ("input_all", 7, 3, ["--port", "0:2:1:5:3", "--port-at", "3", "0:1:5:0:0", "--port-at", "5", "1:1:6:0:0", "--port-at", "6", "0:2:2:-4:9"]),
    "joystick": ("vsync", 5, 3, ["--port", "0:1:9:0:0", "--port", "1:1:22:0:0"]),
}
KICKSTART_INPUT = ["--port", "0:2:0:2:1", "--key", "30", "0x40", "down", "--key", "60", "0x40", "up", "--port-at", "70", "0:2:1:-3:2", "--port-at", "90", "1:1:5:0:0",
                   "--key", "110", "0x45", "down", "--key", "112", "0x45", "up", "--port-at", "120", "0:1:3:0:0", "--key", "125", "0x50", "down",
                   "--port-at", "130", "0:2:4:7:7"]

# name of the field without its [k] -> (lowest, highest) value the visitor accepts; the width and the sign are the field's own
RANGES = {
    "pot.potgo": (0, 0xFFFF), "pot.cap": (0, 511), "pot.dat": (0, 255), "pot.dat_act": (0, 2),
    "joy.button": (0, 7), "joy.dir": (0, 15), "joy.left": (0, 1), "joy.right": (0, 1), "joy.top": (0, 1), "joy.bottom": (0, 1),
    "mouse.delta": (-32768, 32767), "mouse.noreset": (0, 1), "mouse.x": (-32768, 32767), "mouse.y": (-32768, 32767),
    "mouse.frame_x": (-32768, 32767), "mouse.frame_y": (-32768, 32767),
    "input.frame": (0, (1 << 32) - 1), "input.prev_frame": (0, (1 << 32) - 1), "input.prev_vpos": (0, 4095),
    "edge.joy_buttons": (0, 7), "edge.joy_state": (-1, 1), "edge.mouse_buttons": (0, 7),
    "kb.keys": (0, 255), "kb.reset_mode": (0, 4), "kb.reset_seq": (0, (1 << 31) - 1), "kb.ring_count": (0, 255), "kb.ring": (0, 255),
    "applied.keys": (0, (1 << 32) - 1), "applied.type": (0, 5), "applied.buttons": (0, 7), "applied.axis": (-1, 1), "parallel.type": (0, 1), "parallel.buttons": (0, 127),
}
SIGNED = {"pot.cap", "mouse.delta", "mouse.x", "mouse.y", "mouse.frame_x", "mouse.frame_y", "edge.joy_state", "kb.reset_seq", "applied.axis"}
RANGE_MESSAGE = "a field is out of its range"


def base(label):
    return label.split("[")[0]


def index(label):
    return int(label.split("[")[1][:-1]) if "[" in label else 0


def representable(value, size, signed):
    return (-(1 << (8 * size - 1)) <= value < (1 << (8 * size - 1))) if signed else (0 <= value < (1 << (8 * size)))


class InputChunkTests(ProbeCase):
    ROMS = {"input_all": ("input_all", None, None), "vsync": ("vsync", None, None),
            "potgo-once": (POTGO, {"NEXT": "again"}, None), "potgo-every": (POTGO, {"NEXT": "first"}, None)}

    def table_of(self, scenario, unit=None, *extra):
        rom, n, _, args = SCENARIOS[scenario]
        unit = n if unit is None else unit
        path = self.dir / f"{scenario}-{unit}.input"
        run = self.probe(rom, "--units", str(unit), *args, *extra, "--save-fields-at", str(unit), str(path))
        return FieldTable(path.read_bytes(), INPUT, run.fields), run, path

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

    @staticmethod
    def key_state(table, code):
        """(is it down in the engine's table, is it down in the bridge's memory)"""
        return (table.get(f"kb.keys[{code // 8}]") >> (code % 8)) & 1, (table.get(f"applied.keys[{code // 32}]") >> (code % 32)) & 1

    @staticmethod
    def ring_of(table):
        if "kb.ring" not in table.where:
            return []
        offset, size = table.where["kb.ring"]
        return list(table.blob[offset:offset + size])

    # -- what the programs leave for the chunk to carry ---------------------------------------------------------------------------------------

    def test_the_programs_leave_what_the_chunk_has_to_carry(self):
        mouse, _, _ = self.table_of("mouse-pending")
        self.assertEqual((1, 2, 1, 1), (mouse.get("edge.mouse_buttons"), mouse.get("applied.type[0]"), mouse.get("applied.type[1]"), mouse.get("applied.buttons[0]")))
        self.assertNotEqual(0, mouse.get("mouse.delta[0]", signed=True) | mouse.get("mouse.delta[1]", signed=True), "motion pending: the mouse is in flight")
        self.assertEqual(1, mouse.get("edge.joy_state[26]", signed=True), "the last raw value of joystick 1's x axis")
        keys, _, _ = self.table_of("keys")
        self.assertEqual([(1, 1), (1, 1), (1, 1), (0, 0)], [self.key_state(keys, code) for code in (0x21, 0x40, 0x45, 0x20)])
        self.assertEqual([0x40, 0x42, 0x80, 0x41, 0x8A], self.ring_of(keys), "five edges wait in the ring: nobody acknowledges them")
        chord, _, _ = self.table_of("chord")
        self.assertEqual((1, [(1, 1)] * 3), (chord.get("kb.reset_mode"), [self.key_state(chord, code) for code in (0x63, 0x66, 0x67)]))
        self.assertEqual(2, self.table_of("chord-hard")[0].get("kb.reset_mode"))
        pot, _, _ = self.table_of("pot-measuring")
        self.assertEqual((2, 2), (pot.get("pot.dat_act[2]"), pot.get("pot.dat_act[3]")), "a button down keeps a pot line counting")
        discharging, _, _ = self.table_of("pot-discharging")
        self.assertEqual({1}, {discharging.get(f"pot.dat_act[{i}]") for i in range(4)}, "the lines are discharging in the first lines after POTGO")
        joystick, _, _ = self.table_of("joystick")
        self.assertEqual((1, 4, 6, 8), (joystick.get("joy.button[0]"), joystick.get("joy.dir[0]"), joystick.get("joy.button[1]"), joystick.get("joy.dir[1]")))

    def test_the_body_is_laid_out_as_its_visitor_says_so_that_a_change_of_layout_is_a_change_of_version(self):
        table, _, _ = self.table_of("keys")
        expected = [("pot.potgo", 2)] + [(name, size) for _ in range(4) for name, size in (("pot.cap", 2), ("pot.dat", 1), ("pot.dat_act", 1))]
        for _ in range(2):
            expected += [("joy.button", 1), ("joy.dir", 1), ("joy.left", 1), ("joy.right", 1), ("joy.top", 1), ("joy.bottom", 1)] + [("mouse.delta", 2)] * 4 + \
                [("mouse.noreset", 1)] * 4 + [("mouse.x", 2), ("mouse.y", 2), ("mouse.frame_x", 2), ("mouse.frame_y", 2)]
        expected += [("input.frame", 4), ("input.prev_frame", 4), ("input.prev_vpos", 4)]
        for _ in range(2):
            expected += [("edge.joy_buttons", 1)] + [("edge.joy_state", 1)] * 18
        expected += [("edge.mouse_buttons", 1)] + [("kb.keys", 1)] * 16 + [("kb.reset_mode", 1), ("kb.reset_seq", 4), ("kb.ring_count", 4), ("kb.ring", 5)]
        expected += [("applied.keys", 4)] * 4 + [("applied.type", 1)] * 2 + [("applied.buttons", 1)] * 2 + [("applied.axis", 1)] * 4
        expected += [("parallel.type", 1)] * 2 + [("parallel.buttons", 1)] * 2
        self.assertEqual(expected, [(base(label), table.size(label)) for label in table.order])
        offsets = [table.where[label][0] for label in table.order]
        self.assertEqual(sorted(offsets), offsets)
        self.assertEqual(len(parse_chunks(table.blob)[INPUT][2]), sum(size for _, size in expected))

    # -- the state of the input comes back ------------------------------------------------------------------------------------------------------

    def test_the_chunk_puts_the_input_back_to_the_unit_it_was_taken_in_in_the_same_process(self):
        for name, (rom, n, k, args) in SCENARIOS.items():
            with self.subTest(scenario=name):
                path = self.dir / f"{name}.state"
                run = self.probe(rom, "--units", str(n + k), "--every", "1", *args, "--save-at", str(n), str(path), "--load-at", str(n + k), str(path), *PART)
                self.assertEqual([(n + k, 0, "")], run.loads)
                self.assertNotEqual(run.digests[(n, "input")], run.digests[(n + k, "input")], "the input did not move: nothing was proven")
                self.assertEqual(run.digests[(n, "input")], run.after[0]["input"])
                others = [s for s in SUBSYSTEMS if s not in ("scheduler", "chipset", "rtc", "cia", "input")]
                self.assertEqual({s: run.digests[(n + k, s)] for s in others}, {s: run.after[0][s] for s in others}, "only the chunks asked for were applied")

    def test_a_fresh_image_gets_the_input_of_the_unit_it_was_taken_in_after_the_reset_head_has_run(self):
        for name, (rom, n, k, args) in SCENARIOS.items():
            with self.subTest(scenario=name):
                path, again = self.dir / f"{name}.state", self.dir / f"{name}.again"
                source = self.probe(rom, "--units", str(n), "--every", "1", *args, "--save-at", str(n), str(path))
                fresh = self.probe(rom, "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), *FULL)
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(source.digests[(n, "input")], fresh.after[0]["input"])
                a, b = parse_chunks(path.read_bytes()), parse_chunks(again.read_bytes())
                self.assertEqual([a[i] for i in (0, 1, 2, 3, 7, INPUT)], [b[i] for i in (0, 1, 2, 3, 7, INPUT)])

    # -- what the host still holds is still held, and is released ---------------------------------------------------------------------------------------

    def after_load(self, scenario, label, *packet, state=None):
        """The input chunk after a state of `scenario` is loaded into a fresh image and one unit runs with the packet `packet` (events before the first unit)."""
        rom, n, _, args = SCENARIOS[scenario]
        path = state or self.dir / f"{scenario}-{n}.input-state"
        if not path.exists():
            self.probe(rom, "--units", str(n), *args, "--save-at", str(n), str(path))
        out = self.dir / f"{scenario}-{label}.out"
        run = self.probe(rom, "--units", "1", "--load-at", "0", str(path), *FULL, *packet, "--save-fields-at", "1", str(out))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual([], run.save_errors)
        return FieldTable(out.read_bytes(), INPUT, run.fields)

    def test_a_key_that_was_held_is_not_sent_again_and_is_released_by_the_next_packet_in_a_new_process(self):
        held = ["--key", "0", "0x21", "down", "--key", "0", "0x40", "down", "--key", "0", "0x45", "down"]
        before, _, _ = self.table_of("keys")
        same = self.after_load("keys", "held", *held)
        self.assertEqual(self.ring_of(before), self.ring_of(same), "a key that was held went down again")
        self.assertEqual([(1, 1)] * 3, [self.key_state(same, code) for code in (0x21, 0x40, 0x45)])
        released = self.after_load("keys", "released", "--key", "0", "0x40", "down", "--key", "0", "0x45", "down")
        self.assertEqual((0, 0), self.key_state(released, 0x21), "the key is stuck: the bridge's memory was not restored")
        self.assertEqual(self.ring_of(before) + [0x43], self.ring_of(released), "the break code of key 0x21 is the only new thing in the ring")
        self.assertEqual([(1, 1)] * 2, [self.key_state(released, code) for code in (0x40, 0x45)])

    def test_a_button_and_a_direction_that_were_held_are_released_by_the_next_packet_in_a_new_process(self):
        holding = self.after_load("joystick", "held", "--port-at", "0", "0:1:9:0:0", "--port-at", "0", "1:1:22:0:0")
        self.assertEqual((1, 4, 6, 8), (holding.get("joy.button[0]"), holding.get("joy.dir[0]"), holding.get("joy.button[1]"), holding.get("joy.dir[1]")))
        released = self.after_load("joystick", "released", "--port-at", "0", "0:1:0:0:0", "--port-at", "0", "1:1:0:0:0")
        self.assertEqual([0] * 4, [released.get("joy.button[0]"), released.get("joy.dir[0]"), released.get("joy.button[1]"), released.get("joy.dir[1]")],
                         "a button or a direction is stuck: the edge memory was not restored")
        self.assertEqual([0, 0], [released.get("edge.joy_buttons[0]"), released.get("edge.joy_buttons[1]")])
        self.assertEqual([0, 0, 0, 0], [released.get(f"applied.axis[{i}]", signed=True) for i in range(4)])

    def test_a_mouse_button_that_was_held_is_released_by_the_next_packet_in_a_new_process(self):
        holding = self.after_load("mouse-pending", "held", "--port-at", "0", "0:2:1:0:0", "--port-at", "0", "1:1:65:0:0")
        self.assertEqual((1, 1), (holding.get("joy.button[0]"), holding.get("edge.mouse_buttons")))
        released = self.after_load("mouse-pending", "released", "--port-at", "0", "0:2:0:0:0", "--port-at", "0", "1:1:65:0:0")
        self.assertEqual((0, 0, 0), (released.get("joy.button[0]"), released.get("edge.mouse_buttons"), released.get("applied.buttons[0]")))

    def test_the_reset_chord_that_was_held_when_the_state_was_taken_completes_when_it_is_released_in_a_new_process(self):
        for scenario in ("chord", "chord-hard"):
            with self.subTest(scenario=scenario):
                held = ["--key", "0", "0x63", "down", "--key", "0", "0x66", "down", "--key", "0", "0x67", "down"]
                if scenario == "chord-hard":
                    held += ["--key", "0", "0x64", "down"]
                still = self.after_load(scenario, "held", *held)
                self.assertEqual(1 if scenario == "chord" else 2, still.get("kb.reset_mode"), "the chord is still being held")
                done = self.after_load(scenario, "released")
                self.assertEqual(0, done.get("kb.reset_mode"), "the chord did not complete")
                self.assertEqual([(0, 0)] * 4, [self.key_state(done, code) for code in (0x63, 0x64, 0x66, 0x67)])

    # -- every field is applied ---------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def another(label, value, size):
        low, high = RANGES[base(label)]
        signed = base(label) in SIGNED
        candidates = [value + 1, value - 1, high, low, value ^ 1]
        if base(label) == "pot.potgo":
            candidates = [value ^ 0x0100, value ^ 0x8000, value ^ 0x5500]
        for candidate in candidates:
            if low <= candidate <= high and candidate != value and representable(candidate, size, signed):
                return candidate
        raise AssertionError(f"no other value for {label}")

    def variants(self, table):
        """[(labels it changes, new bytes of the state)]: one per field, and pairs for the fields that the engine's memory and the bridge's must agree on."""
        out = []
        port_type = [table.get(f"applied.type[{p}]") for p in range(2)]

        def change(labels, **ring):
            values = {}
            for label, value in labels.items():
                values[label] = value
            return (set(labels), table.with_values(values))

        for label in table.order:
            b = base(label)
            if b in ("kb.ring_count", "kb.ring", "kb.keys", "edge.joy_buttons", "edge.mouse_buttons", "applied.keys", "applied.type", "applied.buttons", "applied.axis", "parallel.buttons"):
                continue
            if b == "edge.joy_state" and index(label) % 9 == 8:
                continue
            value = table.get(label, signed=b in SIGNED)
            out.append(change({label: self.another(label, value, table.size(label))}))
        for p in range(2):
            out.append(change({f"parallel.type[{p}]": 1, f"parallel.buttons[{p}]": 1}))
        for i, code in enumerate(self.ring_of(table)):                       # the ring: one code at a time
            offset, _ = table.where["kb.ring"]
            out.append(({f"kb.ring#{i}"}, with_bytes(table.blob, INPUT, offset + i, bytes([code ^ 0x10]))))
        for i in range(13):                                                  # a key: a bit of the engine's table and the same bit of the bridge's
            word, shift = i // 4, 8 * (i % 4)
            out.append(change({f"kb.keys[{i}]": table.get(f"kb.keys[{i}]") ^ 1, f"applied.keys[{word}]": table.get(f"applied.keys[{word}]") ^ (1 << shift)}))
        for p in range(2):
            if port_type[p] == 1:                                            # a joystick: its buttons and the last value of each axis
                out.append(change({f"edge.joy_buttons[{p}]": table.get(f"edge.joy_buttons[{p}]") ^ 1, f"applied.buttons[{p}]": table.get(f"applied.buttons[{p}]") ^ 1}))
                for ax in range(2):
                    now = table.get(f"applied.axis[{p * 2 + ax}]", signed=True)
                    new = 0 if now else 1
                    out.append(change({f"edge.joy_state[{p * 18 + ax * 9 + 8}]": new, f"applied.axis[{p * 2 + ax}]": new}))
        if port_type[0] == 2:                                                # the mouse on port 0
            out.append(change({"edge.mouse_buttons": table.get("edge.mouse_buttons") ^ 1, "applied.buttons[0]": table.get("applied.buttons[0]") ^ 1}))
        for p in range(2):                                                   # the device on a port, when nothing of it is down
            idle = table.get(f"applied.buttons[{p}]") == 0 and table.get(f"applied.axis[{p * 2}]") == 0 and table.get(f"applied.axis[{p * 2 + 1}]") == 0
            if idle and (p == 1 or table.get("edge.mouse_buttons") == 0):
                for new in ((0, 2) if p == 0 else (0,)) if port_type[p] == 1 else ():
                    out.append(change({f"applied.type[{p}]": new}))
        return out

    def test_every_field_of_the_body_is_applied_and_saved_again_unchanged(self):
        covered = set()
        everything = set()
        for scenario in ("keys", "mouse-pending"):
            table, _, base_path = self.table_of(scenario, 6 if scenario == "keys" else 5)
            rom, n, _, args = SCENARIOS[scenario]
            everything |= {label for label in table.order if base(label) not in ("kb.ring_count", "kb.ring")}
            everything |= {f"kb.ring#{i}" for i in range(len(self.ring_of(table)))}
            variants = self.variants(table)
            base_out = self.dir / "flip-base.out"
            arguments = ["--units", str(n), *args, *FULL, "--load-at", str(n), str(base_path), "--save-at", str(n), str(base_out)]
            files = [base_out]
            for k, (labels, blob) in enumerate(variants):
                path, out = self.dir / f"flip-{k}.state", self.dir / f"flip-{k}.out"
                path.write_bytes(blob)
                files += [path, out]
                arguments += ["--load-at", str(n), str(path), "--save-at", str(n), str(out)]
            try:
                run = self.probe(rom, *arguments)
                self.assertEqual([(n, 0, "")] * (1 + len(variants)), run.loads)
                self.assertEqual([], run.save_errors)
                reference = run.after[0]["input"]
                self.assertEqual(parse_chunks(table.blob)[INPUT][2], parse_chunks(base_out.read_bytes())[INPUT][2], "the state taken in is not the state put back")
                for k, ((labels, blob), after) in enumerate(zip(variants, run.after[1:])):
                    with self.subTest(scenario=scenario, fields=sorted(labels)):
                        body = parse_chunks((self.dir / f"flip-{k}.out").read_bytes())[INPUT][2]
                        self.assertEqual(parse_chunks(blob)[INPUT][2], body, f"{sorted(labels)}: written to the body and not applied, or applied and saved as something else")
                        self.assertNotEqual(reference, after["input"], f"{sorted(labels)}: the digest of the machine does not see the field")
                        covered |= labels
            finally:
                for path in files:
                    if path.exists():
                        path.unlink()
        # kb.keys[13..15] must be clear and kb.ring_count changes the length of the body: they have tests of their own below.
        missing = {label for label in everything - covered if not (label.startswith("kb.keys[") and index(label) >= 13)}
        self.assertEqual(set(), missing, "a field that no flip changed")

    # -- Validate and Apply ---------------------------------------------------------------------------------------------------------------------------

    def test_a_body_that_validate_accepts_never_fails_to_apply_and_a_refusal_never_changes_the_machine(self):
        _, _, path = self.table_of("keys")
        verdicts = set()
        for unit in (7, 9):
            run = self.probe("vsync", "--units", str(unit), *SCENARIOS["keys"][3], *PART, "--mutate-at", str(unit), "input", str(path))
            self.assertEqual([], run.mutate_errors, "the good body was refused by the machine it was loaded into")
            (mutated,) = run.mutate
            self.assertEqual(len(parse_chunks(path.read_bytes())[INPUT][2]), mutated["bytes"])
            self.assertEqual(0, mutated["faulted"], "Validate accepted a body that Apply could not apply")
            self.assertEqual(0, mutated["changed"], "a refused state changed the machine")
            self.assertEqual(mutated["attempts"], mutated["accepted"] + mutated["refused"])
            self.assertGreater(mutated["accepted"], 100)
            self.assertGreater(mutated["refused"], 100)
            verdicts.add((mutated["attempts"], mutated["accepted"], mutated["refused"], mutated["verdicts"]))
        self.assertEqual(1, len(verdicts), "the verdict on a body depends on the machine it is loaded into")

    def test_a_bad_body_is_refused_with_its_reason_and_leaves_the_machine_alone(self):
        table, _, _ = self.table_of("keys")
        mouse, _, _ = self.table_of("mouse-pending")
        attempts = []
        for label in table.order:
            if base(label) not in RANGES:
                raise AssertionError(f"{label} has no range in the test")
            low, high = RANGES[base(label)]
            size, signed = table.size(label), base(label) in SIGNED
            if base(label) == "kb.ring":
                continue
            for what, value in (("below", low - 1), ("above", high + 1)):
                if representable(value, size, signed) and not (base(label) == "kb.keys" and index(label) >= 13 and what == "above"):
                    attempts.append((f"{label} {what} its range", table.with_values({label: value}), 9, RANGE_MESSAGE if base(label) != "kb.ring_count" else "a list is longer than its bound"))
        attempts += [
            ("a POTGO bit that the register does not keep", table.with_values({"pot.potgo": table.get("pot.potgo") | 1}), 9, "pot.potgo: a bit POTGO does not keep"),
            ("a key above 0x67 in the engine's table (0x68)", table.with_values({"kb.keys[13]": 1}), 9, "kb.keys: a key above 0x67 is down"),
            ("a key above 0x67 in the engine's table (0x77)", table.with_values({"kb.keys[14]": 0x80}), 9, "kb.keys: a key above 0x67 is down"),
            ("a key above 0x67 in the engine's table (0x7f)", table.with_values({"kb.keys[15]": 0x80}), 9, "kb.keys: a key above 0x67 is down"),
            ("a key above 0x67 in the bridge's memory", table.with_values({"applied.keys[3]": table.get("applied.keys[3]") | 0x100}), 9, "applied.keys: a key above 0x67 is down"),
            ("a key down in the engine and not in the bridge", table.with_values({"kb.keys[4]": table.get("kb.keys[4]") ^ 1}), 9, "the engine's key table and the bridge's disagree"),
            ("a key down in the bridge and not in the engine", table.with_values({"applied.keys[1]": table.get("applied.keys[1]") ^ 1}), 9, "the engine's key table and the bridge's disagree"),
            ("second mouse buttons disagree", table.with_values({"applied.type[1]": 2, "joy.button[1]": 1, "applied.buttons[1]": 0}), 9, "the second mouse buttons and the bridge disagree"),
            ("a joystick button that the bridge does not know", table.with_values({"edge.joy_buttons[1]": 1}), 9, "the engine's joystick buttons and the bridge's disagree"),
            ("a joystick button that the engine does not know", table.with_values({"applied.buttons[0]": 1}), 9, "the engine's joystick buttons and the bridge's disagree"),
            ("a direction that the bridge does not know", table.with_values({"edge.joy_state[26]": 1}), 9, "the engine's joystick directions and the bridge's disagree"),
            ("a direction that the engine does not know", table.with_values({"applied.axis[1]": 1}), 9, "the engine's joystick directions and the bridge's disagree"),
            ("a mouse button without a mouse", table.with_values({"edge.mouse_buttons": 1}), 9, "the engine's mouse buttons and the bridge's disagree"),
            ("a joystick on port 0 and a mouse button down as well", table.with_values({"edge.joy_buttons[0]": 1, "applied.buttons[0]": 1, "edge.mouse_buttons": 1}), 9,
             "the engine's mouse buttons and the bridge's disagree"),
            ("buttons on a port without a device", table.with_values({"applied.type[1]": 0, "applied.buttons[1]": 1}), 9, "applied: a port without a device has buttons or a direction down"),
            ("a mouse with a direction", mouse.with_values({"applied.axis[0]": 1}), 9, "applied: a mouse has a direction down"),
            ("a mouse button that the bridge does not know", mouse.with_values({"edge.mouse_buttons": 0}), 9, "the engine's mouse buttons and the bridge's disagree"),
            ("the body one byte short", reforge(table.blob, lambda c: c[INPUT].__setitem__(2, c[INPUT][2][:-1])), 9, "chunk input: the body ends before its last field"),
            ("the body one byte long", reforge(table.blob, lambda c: c[INPUT].__setitem__(2, c[INPUT][2] + b"\0")), 9, "bytes after the last field of the body"),   # within the bound: the visitor's
        ]
        self.assertGreater(len(attempts), 130)
        # The mouse body is bound to a machine in which the mouse is in flight: they are all loaded into one that has the keys' input but refuse before touching it.
        rom, n, _, args = SCENARIOS["keys"]
        run = self.refusals(attempts, rom=rom, extra=(*args, *FULL), unit=n, units=n + 3)
        self.assertEqual(len(attempts), len(run.after))

    # -- a state is taken from an input that fields can describe --------------------------------------------------------------------------------------------

    def test_a_save_refuses_what_the_fields_cannot_describe_and_writes_nothing(self):
        cases = (("keyinject", "a text is being typed into the keyboard ring", False), ("ignorerelease", "the next key release is to be ignored", False),
                 ("turbo", "turbo emulation or a mouse speed other than 100", True), ("mousespeed", "turbo emulation or a mouse speed other than 100", True),
                 ("resetwarning", "a keyboard mode or a reset warning that is not the A500's", True),
                 ("engine-key", "the engine's key table and the bridge's disagree", True), ("engine-button", "the engine's joystick buttons and the bridge's disagree", True),
                 ("engine-axis", "the engine's joystick directions and the bridge's disagree", True), ("engine-mouse", "the engine's mouse buttons and the bridge's disagree", True))
        rom, n, _, args = SCENARIOS["keys"]
        for poke, text, can_clear in cases:
            with self.subTest(poke=poke):
                refused, accepted = self.dir / f"{poke}.state", self.dir / f"{poke}-clear.state"
                clear = ["--poke-at", str(n), "clear", "--save-at", str(n), str(accepted)] if can_clear else []
                run = self.probe(rom, "--units", str(n), *args, "--poke-at", str(n), poke, "--save-at", str(n), str(refused), *clear)
                self.assertEqual([(n, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
                self.assertIn("could not be saved: " + text, run.save_errors[0][2])
                self.assertFalse(refused.exists())
                if can_clear:
                    self.assertEqual(1, len(run.saves), "with the input back it is saved")
                    self.assertTrue(accepted.exists())

    def test_a_save_after_the_engine_cleared_its_key_table_says_that_no_key_is_applied(self):
        # A reset of the keyboard clears the engine's table and the bridge presses again every key the host holds with its next packet: the state is the same one as if
        # that had not happened yet, with no key applied, so that after a load the held keys go down again.
        rom, n, _, args = SCENARIOS["keys"]
        path = self.dir / "cleared.state"
        run = self.probe(rom, "--units", str(n), *args, "--poke-at", str(n), "released", "--save-fields-at", str(n), str(path))
        self.assertEqual([], run.save_errors)
        table = FieldTable(path.read_bytes(), INPUT, run.fields)
        self.assertEqual(([0] * 16, [0] * 4), ([table.get(f"kb.keys[{i}]") for i in range(16)], [table.get(f"applied.keys[{i}]") for i in range(4)]))
        again = self.after_load("keys", "cleared", "--key", "0", "0x21", "down", "--key", "0", "0x40", "down", "--key", "0", "0x45", "down", state=path)
        self.assertEqual(self.ring_of(table) + [0x42, 0x80, 0x8A], self.ring_of(again), "the keys the host holds went down again, once")

    # -- a load after a session that held input is a clean load ----------------------------------------------------------------------------------------------

    def test_a_load_after_a_session_that_ended_with_input_in_flight_is_a_clean_load(self):
        # Unit 0 is the machine before its reset head has run: not compared. A session that ended with keys down, a mouse that moves or the pot lines counting must leave
        # nothing for the next load: keys in the ring, the key table, the chord, the counters of the mouse, the pot lines and the host's memory.
        for name, (rom, n, k, args) in SCENARIOS.items():
            with self.subTest(scenario=name):
                clean = self.passes(rom, "--units", str(n), *args)[0]
                loads = self.passes(rom, "--units", str(n), "--loads", "3", *args)
                self.assertEqual({1, 2, 3}, set(loads))
                for number in (1, 2, 3):
                    different = sorted(key for key in clean if key[0] >= 1 and clean[key] != loads[number][key])
                    self.assertEqual([], different, f"load {number} differs from a clean process")

    # -- the owner's Kickstart -------------------------------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_puts_its_input_back_at_boundaries_with_keys_and_a_mouse_in_flight(self):
        # 45: a key held (CIA idle); 75: the mouse moving; 126: a key just sent with the keyboard handshake in flight; 140: the steady state with the mouse in motion.
        for taken in (45, 75, 126, 140):
            with self.subTest(taken=taken):
                path, again = self.dir / f"kickstart-{taken}.state", self.dir / f"kickstart-{taken}.again"
                run = self.probe("kickstart", "--units", str(taken + 3), "--every", "1", *KICKSTART_INPUT, "--save-at", str(taken), str(path), "--load-at", str(taken + 3), str(path), *PART)
                self.assertEqual([(taken + 3, 0, "")], run.loads)
                self.assertEqual(run.digests[(taken, "input")], run.after[0]["input"])
                fresh = self.probe("kickstart", "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), *FULL)
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(run.digests[(taken, "input")], fresh.after[0]["input"])
                self.assertEqual(parse_chunks(path.read_bytes())[INPUT], parse_chunks(again.read_bytes())[INPUT])


if __name__ == "__main__":
    unittest.main()
