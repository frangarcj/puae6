"""T15b: the closure of the cpu and scheduler chunks.

T15b1 and T15b2 prove each chunk by itself. What the closure adds is what the card asks of the pair: every causal component of the two subsystems has
its serializer and a test that exists, what is rebuilt after a restore is rebuilt (a restored CPU executes), and no pointer or address is in a chunk.
The futures of a whole machine (DET02) are the closure of T15, when every chunk exists: until then what is restored is the CPU, the scheduler and RAM,
and the chipset, the CIA and the rest are those of a reset head.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.state.harness import ProbeCase, SCENARIOS, TRACE, USER  # noqa: E402

INVENTORY = json.loads((ROOT / "manifests/state-inventory.json").read_text())["savestate"]
CLOSED = {"cpu": {"P6CP": "T15b1"}, "scheduler": {"P6SC": "T15b2"}}      # subsystem -> chunk and the task that wrote it
EXTENDED = {"cpu-020plus-caches-and-fallback": "T24c", "cpu-fpu-chunk": "T54"}          # components a later task added to the chunk (the blocks of the 68020 and of the FPU in P6CP)
CAUSAL = {"serialize", "serialize_extended", "extension"}


class InventoryClosureTests(unittest.TestCase):
    def test_every_causal_component_of_cpu_and_scheduler_has_its_serializer_and_a_test_that_exists(self):
        found = 0
        for component in INVENTORY["components"]:
            if component["subsystem"] not in CLOSED or not component["in_profile"]:
                continue
            with self.subTest(component=component["id"]):
                found += 1
                if component["policy"] in CAUSAL:
                    native = component.get("native_state")
                    self.assertIsNotNone(native, "a causal component without the chunk that carries it")
                    (chunk, task), = CLOSED[component["subsystem"]].items()
                    self.assertEqual({chunk: EXTENDED.get(component["id"], task)}, {native["chunk"]: native["task"]})
                    path = native["proves"].split(":")[0]
                    self.assertTrue((ROOT / path).is_file(), f"the test {path} does not exist")
                    self.assertEqual("implemented", component["probe"]["status"], "a probe digest that sees what the chunk carries")
                elif component["policy"] == "rebuild":
                    self.assertIn("T15b", component["rebuild"], "a derived component says what rebuilds it after a restore")
                else:
                    self.assertEqual("noncausal", component["policy"])
        self.assertEqual(27, found)         # 25, since T24c the 68020 state of the A1200 and, since T54, the FPU


class ClosureTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "stop": ("stop", None, None), "input_all": ("input_all", None, None), "trace": (TRACE, None, None),
            "user": (USER, None, None)}

    def test_the_state_is_the_same_bytes_in_every_process_with_the_heap_filled_differently_so_no_address_is_in_it(self):
        scribble = {"MallocScribble": "1", "MallocPreScribble": "1"} if sys.platform == "darwin" else {"MALLOC_PERTURB_": "165"}
        for rom in self.ROMS:
            n, _ = SCENARIOS.get(rom, (3, 4))
            with self.subTest(rom=rom):
                states = []
                for index, env in enumerate(({}, {}, scribble, {"TZ": "Pacific/Auckland", "LC_ALL": "tr_TR.UTF-8"})):
                    path = self.dir / f"{rom}-{index}.state"
                    result = subprocess.run([str(self.exe), str(self.roms[rom]), "--units", str(n), "--save-at", str(n), str(path)],
                                            capture_output=True, text=True, timeout=600, env={**os.environ, **env})
                    self.assertEqual(0, result.returncode, result.stderr[-300:])
                    states.append(path.read_bytes())
                self.assertEqual(1, len(set(states)), "two processes of one program made different states: an address, padding or the host is in it")

    def test_a_restored_cpu_executes_so_what_is_rebuilt_after_the_restore_is_there(self):
        # The dispatch tables, the handlers of the scheduler and the host pointers of the PC come from the reset head and from m68k_setpc_normal, not from the
        # state: an image that never ran gets the cpu, the scheduler and RAM of a later unit and then runs units without faulting, and its cpu moves.
        for rom in ("vsync", "input_all", "trace", "user"):
            n, _ = SCENARIOS.get(rom, (3, 4))
            with self.subTest(rom=rom):
                path = self.dir / f"{rom}-exec.state"
                self.probe(rom, "--units", str(n), "--save-at", str(n), str(path))
                run = self.probe(rom, "--units", "3", "--every", "1", "--load-at", "0", str(path), "--only", "cpu", "--only", "scheduler", "--only", "ram")
                self.assertEqual([(0, 0, "")], run.loads)
                moved = {run.digests[(unit, "cpu")] for unit in (1, 2, 3)} | {run.after[0]["cpu"]}
                self.assertEqual(4, len(moved), "the restored cpu did not execute")


if __name__ == "__main__":
    unittest.main()
