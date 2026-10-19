"""T52: the CPU options through the libretro core, run by the minimal frontend (LR01).

puae6_cpu_model (auto, 68000, 68010, 68020) and puae6_cpu_multiplier (auto, 1, 2, 4, 8, 10, 12, 16) are restart options: p6_config.cpu_model and cpu_multiplier. What is decided here:
  * the machine a person gets with a 68010 is the machine headless runs with --cpu 68010 (unit by unit: ticks, audio and picture), on the A500 and the A600, and the program that tells the CPUs apart
    (MOVE from CCR is illegal on a 68000) shows it in the colour of the screen: the option reaches the engine;
  * the machine's own CPU named by hand is the machine of the default (the same picture and state, the same size), and the multiplier the machine already has too;
  * serialize_size grows by the 10 bytes of the 68010 block of the native state and by nothing else; the multiplier does not change it;
  * a clock changes the cycles the program gets in a unit and nothing the frontend sees of the chipset: the audio frames delivered and the ticks of every unit are those of the default machine to the length of the instruction that ends a unit;
  * a CPU the machine cannot have (a 68020 in a 68000 machine, a 68000 or a 68010 in the A1200) fails the load with the reason, a value the schema does not have fails it naming the key, and an
    option changed while the machine runs is only reported.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_cpu.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

STATE = re.compile(r"^state size=(\d+)$", re.M)
CPU_BLOCK_BYTES = 10

# A program that tells a 68010 from a 68000: MOVE from CCR is new in the 68010 and an illegal instruction in a 68000, whose handler paints the screen red. A 68010 paints it green.
CPUID = """; PUAE6 test program (T52): the colour of the screen is the CPU.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and the power LED are outputs
    MOVE.B #2,$BFE001.L         ; OVL off
    MOVE.L #bad,$10.L           ; the illegal instruction vector
loop:
    .word $42C2                 ; move.w ccr, d2 (68010): illegal instruction on a 68000
    MOVE.W #$00F0,$DFF180.L     ; COLOR00: green
    BRA.S loop
bad:
    MOVE.W #$0F00,$DFF180.L     ; COLOR00: red
    BRA.S bad
"""


def size_of(text):
    return int(STATE.search(text)[1])


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class CpuOptionTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless
    messages = staticmethod(lr.LibretroTests.messages)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "cpuid.rom"
        rom.write_bytes(lr.builder.build(CPUID))
        cls.roms["cpuid"] = rom

    def a500(self, rom, *options, **keywords):
        return self.frontend(rom, *options, name=lr.KICKSTART_NAMES[0], **keywords)

    def picture(self, out):
        """The hash of the last picture the frontend got."""
        return self.runs(out)[-1]["hash"]

    def test_accuracy_is_a_restart_option_and_each_level_runs_the_headless_machine(self):
        for machine, flags, names, options in (("a500", (), lr.KICKSTART_NAMES[0], ()),
                                                ("a500", ("--cpu", "68010"), lr.KICKSTART_NAMES[0], ("--set", "puae6_cpu_model=68010")),
                                                ("a1200", ("--machine", "a1200"), lr.KICKSTART_NAMES_A1200[0], lr.A1200)):
            for level in ("exact", "compatible", "normal"):
                with self.subTest(machine=machine, flags=flags, level=level):
                    out = self.frontend("boot", "--runs", 6, *options, "--set", f"puae6_cpu_compatibility={level}", name=names).stdout
                    self.compare_with_headless(out, "boot", 6, *flags, "--cpu-compatibility", level)
        system = self.system_for("boot", lr.KICKSTART_NAMES[0])
        before = self.frontend("boot", "--runs", 8, system=system).stdout
        changed = self.frontend("boot", "--runs", 8, "--set-at", "3:puae6_cpu_compatibility=normal", system=system).stdout
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_cpu_compatibility is now normal but the machine runs with exact: reload the content to apply it"])
        self.assertEqual(self.runs(before), self.runs(changed))
        self.assertEqual(size_of(before), size_of(changed))

    def test_the_machine_with_a_68010_is_the_machine_headless_runs_with_cpu(self):
        for machine, flags, names, option in (("A500", (), lr.KICKSTART_NAMES[0], ()), ("A600", ("--machine", "a600"), lr.KICKSTART_NAMES_A600[0], lr.A600)):
            for rom, units in (("cpuid", 12), ("boot", 12), ("long_frame", 10)):
                with self.subTest(machine=machine, rom=rom):
                    out = self.frontend(rom, "--runs", units, *option, "--set", "puae6_cpu_model=68010", name=names).stdout
                    self.compare_with_headless(out, rom, units, *flags, "--cpu", "68010")

    def test_the_option_reaches_the_engine_the_colour_of_the_screen_is_the_cpu(self):
        green = self.picture(self.a500("cpuid", "--runs", 8, "--set", "puae6_cpu_model=68010").stdout)
        red = self.picture(self.a500("cpuid", "--runs", 8).stdout)
        self.assertNotEqual(green, red)
        self.assertEqual(red, self.picture(self.a500("cpuid", "--runs", 8, "--set", "puae6_cpu_model=68000").stdout))
        self.assertEqual(red, self.picture(self.a500("cpuid", "--runs", 8, "--set", "puae6_cpu_model=auto").stdout))

    def test_the_machines_own_cpu_and_clock_named_by_hand_are_the_default_machine(self):
        plain = self.a500("boot", "--runs", 6).stdout
        for options in (("--set", "puae6_cpu_model=68000"), ("--set", "puae6_cpu_multiplier=2")):
            with self.subTest(options=options):
                named = self.a500("boot", "--runs", 6, *options).stdout
                self.assertEqual((size_of(named), self.picture(named)), (size_of(plain), self.picture(plain)))
                self.compare_with_headless(named, "boot", 6)
        a1200 = self.frontend("boot", "--runs", 6, *lr.A1200, name=lr.KICKSTART_NAMES_A1200[0]).stdout
        for options in (("--set", "puae6_cpu_model=68020"), ("--set", "puae6_cpu_multiplier=4")):
            with self.subTest(machine="A1200", options=options):
                named = self.frontend("boot", "--runs", 6, *lr.A1200, *options, name=lr.KICKSTART_NAMES_A1200[0]).stdout
                self.assertEqual((size_of(named), self.picture(named)), (size_of(a1200), self.picture(a1200)))

    def test_the_serialization_size_grows_by_the_68010_block_and_by_nothing_else(self):
        plain = size_of(self.a500("boot", "--runs", 3).stdout)
        self.assertEqual(plain + CPU_BLOCK_BYTES, size_of(self.a500("boot", "--runs", 3, "--set", "puae6_cpu_model=68010").stdout))
        for multiplier in ("1", "4", "16"):
            with self.subTest(multiplier=multiplier):
                self.assertEqual(plain, size_of(self.a500("boot", "--runs", 3, "--set", f"puae6_cpu_multiplier={multiplier}").stdout))

    def test_a_clock_changes_the_cycles_of_a_unit_and_nothing_the_frontend_sees_of_the_chipset(self):
        for machine, option, name in (("a500", (), lr.KICKSTART_NAMES[0]), ("a1200", lr.A1200, lr.KICKSTART_NAMES_A1200[0])):
            base = self.headless_units("boot", 12, "--machine", machine)
            for multiplier in ("1", "8", "16"):
                with self.subTest(machine=machine, multiplier=multiplier):
                    out = self.frontend("boot", "--runs", 12, *option, "--set", f"puae6_cpu_multiplier={multiplier}", name=name).stdout
                    self.compare_with_headless(out, "boot", 12, "--machine", machine, "--cpu-multiplier", multiplier)
                    fast = self.headless_units("boot", 12, "--machine", machine, "--cpu-multiplier", multiplier)
                    # A unit ends at the first instruction boundary after the beam wraps, so the ticks of a unit differ by the length of that instruction (a few colour clocks of 512 ticks
                    # each: the boot program's instructions are 8 to 12 CPU clocks, 12 colour clocks at 3.55 MHz at the most) and the audio frames of every unit are the same.
                    # and the audio frames of a unit move by one frame between two units when that instruction moves the end across a sample, never more: the frames delivered so far agree to one.
                    delivered = {"fast": 0, "base": 0}
                    for n, unit in fast.items():
                        delivered["fast"] += unit["audio"]
                        delivered["base"] += base[n]["audio"]
                        self.assertLessEqual(abs(unit["ticks"] - base[n]["ticks"]), 16 * 512, f"unit {n}")
                        self.assertLessEqual(abs(delivered["fast"] - delivered["base"]), 1, f"unit {n}")

    def test_a_cpu_the_machine_cannot_have_fails_the_load_with_the_reason_and_a_value_of_no_schema_fails_it_naming_the_key(self):
        out = self.a500("boot", "--set", "puae6_cpu_model=68020", expect=3).stdout
        self.assertIn("the machines with a 68000 take a 68000 or a 68010; a 68020 is the A1200's", out)
        self.assertNotIn("run 1 ", out)
        for value in ("68010", "68000"):
            out = self.frontend("boot", *lr.A1200, "--set", f"puae6_cpu_model={value}", name=lr.KICKSTART_NAMES_A1200[0], expect=3).stdout
            self.assertIn("the A1200 has a soldered 68EC020: no other CPU is offered for it", out)
        out = self.frontend("boot", "--set", "puae6_cpu_model=68040", expect=3).stdout
        self.assertIn('the option puae6_cpu_model has the value "68040", which this core does not support (allowed: auto, 68000, 68010, 68020)', out)
        out = self.frontend("boot", "--set", "puae6_cpu_multiplier=3", expect=3).stdout
        self.assertIn('the option puae6_cpu_multiplier has the value "3", which this core does not support (allowed: auto, 1, 2, 4, 8, 10, 12, 16)', out)

    def test_an_option_changed_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("boot", lr.KICKSTART_NAMES[0])
        base = self.frontend("boot", "--runs", 12, system=system).stdout
        changed = self.frontend("boot", "--runs", 12, "--set-at", "5:puae6_cpu_model=68010", "--set-at", "6:puae6_cpu_multiplier=8", system=system).stdout
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_cpu_model is now 68010 but the machine runs with auto: reload the content to apply it",
                                                  "PUAE6: puae6_cpu_multiplier is now 8 but the machine runs with auto: reload the content to apply it"])
        self.assertEqual(size_of(changed), size_of(base))


if __name__ == "__main__":
    unittest.main()
