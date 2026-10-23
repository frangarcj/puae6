"""T49: the memory options and the PUAE5-like presets through the libretro core, run by the minimal frontend (LR01).

The core takes puae6_chip_size, puae6_slow_size and puae6_fast_size (restart options) and puae6_preset, which lays a bundle of the model and its memory over the others
(config/schema/options.json, "presets"). What is decided here:
  * the machine a person gets from the options is the one headless runs with the same flags (unit by unit: ticks, audio and picture), for the memory options and for each preset;
  * a preset is the same machine as its options chosen by hand (same ticks, pictures and state size), and it wins over the model and the memory options;
  * a size the machine cannot have fails the load with the reason, a value the schema does not have fails it naming the key; a size changed while the machine runs is only reported;
  * a state of a machine with Fast RAM saves and loads through the libretro frame (the 8 MiB of the board travel in it), and serialize_size is the one of the machine without the board
    plus the board (4 + size + 278 bytes of the native state), the same before and after the first unit.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_fast.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

A1200_FAST8 = ("--set", "puae6_machine=A1200", "--set", "puae6_fast_size=MB8")
A600_PUAE5 = ("--set", "puae6_machine=A600", "--set", "puae6_chip_size=MB2", "--set", "puae6_fast_size=MB8")
EXPANSION_BYTES = 22 + 256
STATE = re.compile(r"^state size=(\d+)$", re.M)


def size_of(text):
    return int(STATE.search(text)[1])


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MemoryOptionTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless
    messages = staticmethod(lr.LibretroTests.messages)

    def a1200(self, rom="boot", *options, **keywords):
        return self.frontend(rom, *options, name=lr.KICKSTART_NAMES_A1200[0], **keywords)

    def a600(self, rom="boot", *options, **keywords):
        return self.frontend(rom, *options, name=lr.KICKSTART_NAMES_A600[0], **keywords)

    def test_the_a1200_with_fast_ram_is_the_machine_headless_runs_with_fast_8192(self):
        for rom, units in (("boot", 20), ("long_frame", 14), ("identify", 8)):
            with self.subTest(rom=rom):
                out = self.a1200(rom, "--runs", units, *A1200_FAST8).stdout
                self.assertIn("machine A1200 (puae6_machine)", out)
                self.compare_with_headless(out, rom, units, "--machine", "a1200", "--fast", "8192")

    def test_the_a600_with_the_puae5_memory_is_the_machine_headless_runs_with_chip_2048_and_fast_8192(self):
        for rom, units in (("boot", 20), ("identify", 8)):
            with self.subTest(rom=rom):
                out = self.a600(rom, "--runs", units, *A600_PUAE5).stdout
                self.compare_with_headless(out, rom, units, "--machine", "a600", "--chip", "2048", "--fast", "8192")

    def test_the_memory_of_the_other_machines_is_what_headless_runs_with_too(self):
        cases = (("A500", ("--set", "puae6_chip_size=MB1"), ("--machine", "a500", "--chip", "1024"), lr.KICKSTART_NAMES[0]),
                 ("A500", ("--set", "puae6_slow_size=None"), ("--machine", "a500", "--slow", "none"), lr.KICKSTART_NAMES[0]),
                 ("A500", ("--set", "puae6_fast_size=MB2",), ("--machine", "a500", "--fast", "2048"), lr.KICKSTART_NAMES[0]),
                 ("A1200", ("--set", "puae6_slow_size=MB1_5",), ("--machine", "a1200", "--slow", "1536"), lr.KICKSTART_NAMES_A1200[0]),
                 ("A600", ("--set", "puae6_slow_size=MB1_75",), ("--machine", "a600", "--slow", "1792"), lr.KICKSTART_NAMES_A600[0]))
        for machine, options, flags, name in cases:
            with self.subTest(machine=machine, options=options):
                out = self.frontend("boot", "--runs", 12, "--set", f"puae6_machine={machine}", *options, name=name).stdout
                self.compare_with_headless(out, "boot", 12, *flags)

    def test_a_preset_is_the_machine_of_its_options_chosen_by_hand_and_wins_over_the_model_and_the_memory(self):
        by_hand = self.a1200("boot", "--runs", 16, *A1200_FAST8).stdout
        preset = self.a1200("boot", "--runs", 16, "--set", "puae6_preset=a1200_fast8").stdout
        self.assertEqual([(r["hash"], r["px"], r["ahash"], r["ticks"]) for r in self.runs(preset)], [(r["hash"], r["px"], r["ahash"], r["ticks"]) for r in self.runs(by_hand)])
        self.assertEqual(size_of(preset), size_of(by_hand))
        self.assertIn("machine A1200 (puae6_machine)", preset)
        # Over a model and a memory the person chose differently, the preset is what runs.
        over = self.a1200("boot", "--runs", 16, "--set", "puae6_preset=a1200_fast8", "--set", "puae6_machine=A500", "--set", "puae6_fast_size=MB1").stdout
        self.assertIn("machine A1200 (puae6_machine)", over)
        self.assertEqual(size_of(over), size_of(by_hand))
        puae5 = self.a600("boot", "--runs", 16, "--set", "puae6_preset=a600_puae5").stdout
        self.assertEqual([(r["hash"], r["px"], r["ahash"], r["ticks"]) for r in self.runs(puae5)],
                         [(r["hash"], r["px"], r["ahash"], r["ticks"]) for r in self.runs(self.a600("boot", "--runs", 16, *A600_PUAE5).stdout)])
        none = self.a1200("boot", "--runs", 6, "--set", "puae6_preset=none", "--set", "puae6_machine=A1200").stdout
        self.assertEqual(size_of(none) + 4 + (8 << 20) + EXPANSION_BYTES, size_of(preset))

    def test_the_serialization_size_follows_the_board_and_is_the_same_before_and_after_the_first_unit(self):
        plain = size_of(self.a1200("boot", "--runs", 3, "--set", "puae6_machine=A1200").stdout)
        for fast, kib in (("MB1", 1024), ("MB4", 4096), ("MB8", 8192)):
            with self.subTest(fast=fast):
                out = self.a1200("boot", "--runs", 3, "--cycles", 2, "--set", "puae6_machine=A1200", "--set", f"puae6_fast_size={fast}").stdout
                sizes = STATE.findall(out)
                self.assertEqual(len(sizes), 2)
                self.assertEqual(sizes[0], sizes[1])
                self.assertEqual(int(sizes[0]), plain + 4 + (kib << 10) + EXPANSION_BYTES)

    def test_a_size_the_machine_cannot_have_fails_the_load_with_the_reason_and_a_value_of_no_schema_fails_it_naming_the_key(self):
        for options, text in ((("--set", "puae6_chip_size=MB2"), "chip memory size is not supported for this machine"),                              # the A500: OCS Agnus
                              (("--set", "puae6_machine=A1200", "--set", "puae6_slow_size=MB1_75"), "slow memory size is not supported for this machine")):    # AGA maps 1.5 MiB
            with self.subTest(options=options):
                name = lr.KICKSTART_NAMES_A1200[0] if "A1200" in " ".join(options) else lr.KICKSTART_NAMES[0]
                out = self.frontend("boot", *options, name=name, expect=3).stdout
                self.assertIn(text, out)
                self.assertNotIn("run 1 ", out)
        for key, value, allowed in (("puae6_fast_size", "MB16", "None, MB1, MB2, MB4, MB8, Auto"), ("puae6_chip_size", "mb2", "Auto, KB512, MB1, MB2"),
                                    ("puae6_slow_size", "512", "Auto, None, KB512, MB1, MB1_5, MB1_75"), ("puae6_preset", "a4000", "none, a1200_fast8, a600_puae5, a4030_normal, cd32_fast8, a4040_normal, a4060_normal")):
            with self.subTest(key=key, value=value):
                out = self.frontend("boot", "--set", f"{key}={value}", expect=3).stdout
                self.assertIn(f'the option {key} has the value "{value}", which this core does not support (allowed: {allowed})', out)

    def test_a_size_changed_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("identify", lr.KICKSTART_NAMES_A1200[0])
        base = self.frontend("identify", "--runs", 12, *lr.A1200, system=system).stdout
        changed = self.frontend("identify", "--runs", 12, *lr.A1200, "--set-at", "5:puae6_fast_size=MB8", system=system).stdout
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_fast_size is now MB8 but the machine runs with Auto: reload the content to apply it"])
        self.assertEqual(size_of(changed), size_of(base))                                    # nothing of the running machine moved, serialize_size included
        cycles = self.frontend("identify", "--runs", 12, *lr.A1200, "--set-at", "5:puae6_fast_size=MB8", "--cycles", 2, system=system).stdout
        sizes = STATE.findall(cycles)
        self.assertEqual((int(sizes[1]) - int(sizes[0])), 4 + (8 << 20) + EXPANSION_BYTES)

    def test_a_state_of_a_machine_with_fast_ram_saves_and_loads_through_the_frame(self):
        out = self.a1200("boot", "--runs", 24, *A1200_FAST8, "--do", "8:save:a", "--do", "16:save:before", "--do", "16:load:a", "--do", "16:save:again").stdout
        saves = {m[1]: (int(m[3]), m[5]) for m in re.finditer(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", out, re.M)}
        self.assertEqual(saves["again"][1], saves["a"][1])                                  # the loaded machine is the saved one, byte for byte
        self.assertNotEqual(saves["before"][1], saves["a"][1])
        self.assertIn("ok=1", re.search(r"^state load a \S+ ok=\d size=\d+$", out, re.M)[0])
        runs = self.runs(out)
        seen = {}
        for run in runs:
            seen.setdefault(run["unit"], []).append((run["ticks"], run["audio"], run["ahash"], run["hash"], run["px"]))
        for unit in range(9, 17):
            self.assertEqual(len(seen[unit]), 2, unit)
            self.assertEqual(len(set(seen[unit])), 1, f"unit {unit} differs after the load")

    def test_a_state_file_of_the_board_is_refused_by_a_machine_without_it_and_the_machine_goes_on(self):
        path = self.work() / "fast.state"
        self.a1200("boot", "--runs", 10, *A1200_FAST8, "--do", f"6:savefile:{path}")
        out = self.a1200("boot", "--runs", 10, "--set", "puae6_machine=A1200", "--do", f"2:loadfile:{path}:pos=6").stdout
        self.assertRegex(out, r"state load \S+ \S+ ok=0")
        self.assertEqual(len(self.runs(out)), 10)                                           # the refusal left the machine usable


if __name__ == "__main__":
    unittest.main()
