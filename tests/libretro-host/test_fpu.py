"""T54c: the FPU option through the libretro core, run by the minimal frontend (LR01).

puae6_fpu_model (None, M68881, M68882; a restart option) is p6_config.fpu_model. What is decided here:
  * the A1200 a person gets with an FPU is the machine headless runs with --fpu (unit by unit: ticks, audio and picture), for each model;
  * serialize_size grows by the 110 bytes of the FPU block of the native state and by nothing else;
  * an FPU on a machine without a 68020 fails the load with the reason, a value the schema does not have fails it naming the key, and a model changed while the machine runs is
    only reported.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_fpu.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

STATE = re.compile(r"^state size=(\d+)$", re.M)
FPU_BLOCK_BYTES = 110


def size_of(text):
    return int(STATE.search(text)[1])


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class FpuOptionTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless
    messages = staticmethod(lr.LibretroTests.messages)

    def a1200(self, rom="boot", *options, **keywords):
        return self.frontend(rom, *options, name=lr.KICKSTART_NAMES_A1200[0], **keywords)

    def test_the_a1200_with_an_fpu_is_the_machine_headless_runs_with_fpu(self):
        for model, number in (("M68881", "68881"), ("M68882", "68882")):
            for rom, units in (("boot", 20), ("identify", 8)):
                with self.subTest(model=model, rom=rom):
                    out = self.a1200(rom, "--runs", units, "--set", "puae6_machine=A1200", "--set", f"puae6_fpu_model={model}").stdout
                    self.compare_with_headless(out, rom, units, "--machine", "a1200", "--fpu", number)

    def test_the_serialization_size_grows_by_the_fpu_block_and_by_nothing_else(self):
        plain = size_of(self.a1200("boot", "--runs", 3, "--set", "puae6_machine=A1200").stdout)
        for model in ("M68881", "M68882"):
            with self.subTest(model=model):
                self.assertEqual(plain + FPU_BLOCK_BYTES, size_of(self.a1200("boot", "--runs", 3, "--set", "puae6_machine=A1200", "--set", f"puae6_fpu_model={model}").stdout))

    def test_an_fpu_on_a_68000_fails_the_load_with_the_reason_and_a_value_of_no_schema_fails_it_naming_the_key(self):
        out = self.frontend("boot", "--set", "puae6_fpu_model=M68882", name=lr.KICKSTART_NAMES[0], expect=3).stdout
        self.assertIn("an FPU needs a 68020: only the A1200 can have a 68881 or 68882", out)
        self.assertNotIn("run 1 ", out)
        out = self.frontend("boot", "--set", "puae6_fpu_model=68040", expect=3).stdout
        self.assertIn('the option puae6_fpu_model has the value "68040", which this core does not support (allowed: None, M68881, M68882)', out)

    def test_a_model_changed_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("identify", lr.KICKSTART_NAMES_A1200[0])
        base = self.frontend("identify", "--runs", 12, *lr.A1200, system=system).stdout
        changed = self.frontend("identify", "--runs", 12, *lr.A1200, "--set-at", "5:puae6_fpu_model=M68882", system=system).stdout
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_fpu_model is now M68882 but the machine runs with None: reload the content to apply it"])
        self.assertEqual(size_of(changed), size_of(base))


if __name__ == "__main__":
    unittest.main()
