"""T15c: the closure of the rtc, cia and input chunks.

T15c1, T15c2 and T15c3 prove each chunk by itself. What the closure adds is what the card asks of the three: every causal component of rtc, cia and input has its serializer and
a test that exists; no pointer or address is in a chunk; and the future. A restored machine executes: the units that follow a load run, and they are the original's.

The future (DET02) of a whole machine needs every chunk, and the chipset, the disk, the audio and the picture have none yet. What can be shown today is this: when the machine a state
is loaded into is, in every subsystem that the probe digests, the machine the state was taken from (the chipset, which the programs below never change, is in the same state at
every boundary, and the counters it shares with the scheduler come back with it), the units that follow are the original's, digest by digest, in the cpu, the scheduler, RAM, the clock,
the CIAs, the chipset, the audio and the input. That is what these tests check, for the keyboard in the middle of a handshake, the mouse in flight, the reset chord held, the pot lines
measuring and both CIAs running timers, TOD and alarms. The picture and the bridge's session counters (video, bridge) are not compared: they belong to T15g and T15h.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.state import test_state_cia as cia_tests  # noqa: E402
from tests.state import test_state_input as input_tests  # noqa: E402
from tests.state.harness import FieldTable, ProbeCase  # noqa: E402

INVENTORY = json.loads((ROOT / "manifests/state-inventory.json").read_text())["savestate"]
CAUSAL = {"serialize", "serialize_extended", "extension"}
CHUNK_TASKS = {"P6RT": "T15c1", "P6CI": "T15c2", "P6IN": "T15c3", "P6SC": "T15b2"}       # the chunk that carries a component, and the task that wrote it
FULL = ("--only", "cpu", "--only", "scheduler", "--only", "ram", "--only", "rtc", "--only", "cia", "--only", "input")
COMPARED = ("cpu", "scheduler", "ram", "rtc", "cia", "chipset", "audio", "input")
UNITS_AFTER = 5
CIA_CHUNK, INPUT_CHUNK = 7, 9


class InventoryClosureTests(unittest.TestCase):
    def test_every_causal_component_of_rtc_cia_and_input_has_its_serializer_and_a_test_that_exists(self):
        found = 0
        for component in INVENTORY["components"]:
            if component["subsystem"] not in ("rtc", "cia", "input") or not component["in_profile"]:
                continue
            with self.subTest(component=component["id"]):
                found += 1
                if component["policy"] in CAUSAL:
                    native = component.get("native_state")
                    self.assertIsNotNone(native, "a causal component without the chunk that carries it")
                    self.assertEqual(CHUNK_TASKS[native["chunk"]], native["task"])
                    self.assertTrue((ROOT / native["proves"].split(":")[0]).is_file(), "the test that proves it does not exist")
                    self.assertIn(component["probe"]["status"], ("implemented", "partial"), "a probe digest that sees what the chunk carries")
                    if component["probe"]["status"] == "partial":
                        self.assertTrue(component["probe"]["why"].strip())
                elif component["policy"] == "rebuild":
                    self.assertIn("T15c", component["rebuild"], "a derived component says what rebuilds it after a restore, or why nothing has to")
                else:
                    self.assertEqual("noncausal", component["policy"])
        self.assertEqual(24, found)          # 4 of the clock, 13 of the CIAs and 7 of the input that are in the profile
        applied = next(c for c in INVENTORY["components"] if c["id"] == "bridge-input-applied-edges")
        self.assertEqual(("P6IN", "T15c3"), (applied["native_state"]["chunk"], applied["native_state"]["task"]))


# name -> (program, probe arguments, unit the state is taken in)
FUTURES = {
    "cias": ("cias", [], 8),
    "cias-noack": ("cias-noack", [], 8),
    "cias-led": ("cias-led", [], 8),
    "mouse": ("input_all", input_tests.SCENARIOS["mouse"][3], 8),
    "mouse-pending": ("vsync", input_tests.SCENARIOS["mouse-pending"][3], 8),
    "keys": ("vsync", input_tests.SCENARIOS["keys"][3], 6),
    "chord": ("vsync", input_tests.SCENARIOS["chord"][3], 7),
    "chord-hard": ("vsync", input_tests.SCENARIOS["chord-hard"][3], 7),
    "pot-measuring": ("potgo-once", input_tests.SCENARIOS["pot-measuring"][3], 6),
    "pot-discharging": ("potgo-every", input_tests.SCENARIOS["pot-discharging"][3], 7),
    "types": ("input_all", input_tests.SCENARIOS["types"][3], 7),
    "joystick": ("vsync", input_tests.SCENARIOS["joystick"][3], 8),
}
# The host changes what it holds in the units right after the saved one: the machine the state is loaded into (the same one, a few units later) already has the new packet, and
# the state does not. What the state says the bridge and the engine remember is what makes the next packet press or release what it has to, as it did in the original.
FUTURES["keys-after"] = ("vsync", input_tests.SCENARIOS["keys"][3] + ["--key", "7", "0x50", "down", "--key", "8", "0x21", "up"], 6)
FUTURES["joystick-after"] = ("vsync", input_tests.SCENARIOS["joystick"][3] + ["--port-at", "9", "0:1:0:0:0", "--port-at", "10", "1:1:0:0:0"], 8)
FUTURES["mouse-after"] = ("vsync", input_tests.SCENARIOS["mouse-pending"][3] + ["--port-at", "9", "0:2:0:0:0"], 8)
FUTURES["chord-released-after"] = ("vsync", input_tests.SCENARIOS["chord"][3] + ["--key", "8", "0x63", "up", "--key", "8", "0x66", "up", "--key", "8", "0x67", "up"], 7)
# A field changed in the state before it is loaded changes what happens next: (scenario, {field of the chunk: new value}, chunk, what it shows)
SENSITIVE = [
    ("cias", {"cia.imask[0]": 0}, CIA_CHUNK, "the interrupts of CIA-A are masked: its handler no longer runs"),
    ("cias", {"cia.tod[1]": 0x60}, CIA_CHUNK, "the line counter of CIA-B reaches its alarm: an interrupt that did not happen"),
    ("cias", {"cia.inputpipe[0]": 0}, CIA_CHUNK, "the interrupt pipeline of timer A"),
    ("cias", {"cia.cycle_accurate": 0}, CIA_CHUNK, "the first frame's inexact CIA paths"),
    ("cias-noack", {"kb.lostsync": 1}, CIA_CHUNK, "the keyboard's wait is over: its next byte arrives"),
    ("mouse", {"mouse.x[0]": 3}, INPUT_CHUNK, "the quadrature counter that JOY0DAT reads"),
    ("keys", {"kb.keys[4]": 3, "applied.keys[1]": 3}, INPUT_CHUNK, "key 0x20 down in both memories: the next packet has to release it"),
    ("pot-measuring", {"pot.dat_act[2]": 0}, INPUT_CHUNK, "the pot line of port 1 stops counting"),
]


class FutureTests(ProbeCase):
    ROMS = {"cias": (cia_tests.CIAS, {"PRA": "2", "KBD": "$41"}, None), "cias-noack": (cia_tests.CIAS, {"PRA": "2", "KBD": "$01"}, None),
            "cias-led": (cia_tests.CIAS, {"PRA": "0", "KBD": "$41"}, None), "input_all": ("input_all", None, None), "vsync": ("vsync", None, None),
            "potgo-once": (input_tests.POTGO, {"NEXT": "again"}, None), "potgo-every": (input_tests.POTGO, {"NEXT": "first"}, None)}

    def original(self, name):
        """The program run straight through (digests of every unit), and the state taken at the saved unit with the offsets of its fields."""
        rom, args, n = FUTURES[name]
        path = self.dir / f"{name}.state"
        run = self.probe(rom, "--units", str(n + UNITS_AFTER), "--every", "1", *args, "--save-fields-at", str(n), str(path))
        return run, path, run.fields

    @staticmethod
    def shifted(args, n, k, history=False):
        """The packet of the machine that is rewound: what the host sends after the saved unit `n` it sends after the load, k units later. With `history` the host held key 0x30 in the
        meantime (it goes up in the first unit after the load), so the ring, the key table and the bridge's memory of the machine that is loaded into are not the state's."""
        out, i = [], 0
        while i < len(args):
            if args[i] in ("--key", "--port-at"):
                width = 4 if args[i] == "--key" else 3
                unit = int(args[i + 1])
                out += [args[i], str(unit + k if unit > n else unit), *args[i + 2:i + width]]
                i += width
            else:
                out.append(args[i])
                i += 1
        if history:
            out += ["--key", str(n + 1), "0x30", "down", "--key", str(n + k + 1), "0x30", "up"]
        return out

    def distance(self, name, original, path):
        """How many units back the state is loaded so that the machine is the original's at the saved unit in every subsystem compared."""
        rom, args, n = FUTURES[name]
        for k in range(1, 13):
            loaded = self.probe(rom, "--units", str(n + k), "--every", "1", *self.shifted(args, n, k), "--load-at", str(n + k), str(path), *FULL)
            if loaded.loads == [(n + k, 0, "")] and all(loaded.after[0][s] == original.digests[(n, s)] for s in COMPARED):
                return k
        return None

    def future(self, name, k, path, history=False):
        rom, args, n = FUTURES[name]
        run = self.probe(rom, "--units", str(n + k + UNITS_AFTER), "--every", "1", *self.shifted(args, n, k, history), "--load-at", str(n + k), str(path), *FULL)
        self.assertEqual([(n + k, 0, "")], run.loads)
        return run

    # -- no address is in a chunk ---------------------------------------------------------------------------------------------------------------

    def test_the_state_is_the_same_bytes_in_every_process_with_the_heap_filled_differently_so_no_address_is_in_it(self):
        scribble = {"MallocScribble": "1", "MallocPreScribble": "1"} if sys.platform == "darwin" else {"MALLOC_PERTURB_": "165"}
        for name, (rom, args, n) in FUTURES.items():
            with self.subTest(scenario=name):
                states = []
                for index, env in enumerate(({}, {}, scribble, {"TZ": "Pacific/Auckland", "LC_ALL": "tr_TR.UTF-8"})):
                    path = self.dir / f"{name}-{index}.state"
                    result = subprocess.run([str(self.exe), str(self.roms[rom]), "--units", str(n), *args, "--save-at", str(n), str(path)],
                                            capture_output=True, text=True, timeout=600, env={**os.environ, **env})
                    self.assertEqual(0, result.returncode, result.stderr[-300:])
                    states.append(path.read_bytes())
                self.assertEqual(1, len(set(states)), "two processes of one program made different states: an address, padding or the host is in it")

    # -- the future -----------------------------------------------------------------------------------------------------------------------------------

    def test_a_machine_restored_by_every_chunk_there_is_continues_as_the_original_did(self):
        for name, (rom, args, n) in FUTURES.items():
            with self.subTest(scenario=name):
                original, path, _ = self.original(name)
                k = self.distance(name, original, path)
                self.assertIsNotNone(k, "no unit in 1..12 at which the machine, after the load, is the original at the saved unit in every subsystem compared")
                for history in (False, True):
                    run = self.future(name, k, path, history)
                    for j in range(1, UNITS_AFTER + 1):
                        different = [s for s in COMPARED if original.digests[(n + j, s)] != run.digests[(n + k + j, s)]]
                        self.assertEqual([], different, f"{j} units after the load (history={history}), the machine is not the original's in {different}")

    def test_changing_a_field_in_the_state_before_it_is_loaded_changes_the_units_that_follow(self):
        for name, changes, chunk, shows in SENSITIVE:
            with self.subTest(scenario=name, fields=sorted(changes)):
                rom, args, n = FUTURES[name]
                original, path, fields = self.original(name)
                k = self.distance(name, original, path)
                self.assertIsNotNone(k)
                table = FieldTable(path.read_bytes(), chunk, fields)
                changed = self.dir / f"{name}-changed.state"
                changed.write_bytes(table.with_values(changes))
                run = self.future(name, k, changed)
                later = [s for s in ("cpu", "scheduler", "ram", "cia", "input")
                         if original.digests[(n + UNITS_AFTER, s)] != run.digests[(n + k + UNITS_AFTER, s)]]
                self.assertNotEqual([], later, f"{shows}: nothing differs {UNITS_AFTER} units later")

    # -- the owner's Kickstart ---------------------------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_restored_by_every_chunk_there_is_keeps_running_at_three_boundaries(self):
        # 60: the CIAs untouched; 107: the keyboard handshake in flight; 140: the steady state with the LED lit and the TOD counting. A state of an earlier unit is loaded
        # into the machine three units later, and three more units run: the chunks that exist come back and the machine does not fault.
        for taken in (60, 107, 140):
            with self.subTest(taken=taken):
                path = self.dir / f"kickstart-{taken}.state"
                run = self.probe("kickstart", "--units", str(taken + 6), "--every", "1", *input_tests.KICKSTART_INPUT, "--save-at", str(taken), str(path),
                                 "--load-at", str(taken + 3), str(path), *FULL)
                self.assertEqual([(taken + 3, 0, "")], run.loads)
                for subsystem in ("cpu", "scheduler", "ram", "rtc", "cia", "input"):
                    self.assertEqual(run.digests[(taken, subsystem)], run.after[0][subsystem], subsystem)
                self.assertIn((taken + 6, "cia"), run.digests, "the machine went on running three units after the load")


if __name__ == "__main__":
    unittest.main()
