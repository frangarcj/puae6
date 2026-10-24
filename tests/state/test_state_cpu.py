"""T15b1: chunk P6CP, the state of the 68000 (bridge/state_cpu.cpp), on the real engine.

What is proven here is what the chunk puts back, not that a machine can be restored (that is the closure of T15): the digest of the cpu
subsystem and the bytes of the chunk come back to what they were in the unit the state was taken in, in the same process and in a new one
(where the reset head has to run first), every field of the body is applied, a body that Validate accepts never fails to apply, a bad body is
refused with the machine untouched, and a machine that is not at an instruction boundary is not saved.
"""
import os
import struct
import unittest

from tests.state import envelope as e
from tests.state.harness import ProbeCase, SCENARIOS, SUBSYSTEMS, TRACE, USER, parse_chunks, reforge, with_bytes

CPU = 0                 # index of the chunk in the state
SIGNED = {"go.quit_program"}
def alter(name, original):
    """The bytes that can replace a field of the body so that the body is still valid and the field is different: (label, bytes) for each change
    worth trying. The status register is a set of independent bits that are applied one by one, so each bit is tried on its own."""
    size = len(original)
    signed = name in SIGNED or size == 8
    n = int.from_bytes(original, "little", signed=signed)

    def pack(value):
        return int(value).to_bytes(size, "little", signed=signed)
    if name == "cpu.sr":
        bits = {"T1": 0x8000, "S": 0x2000, "mask2": 0x0400, "mask1": 0x0200, "mask0": 0x0100, "X": 0x10, "N": 0x08, "Z": 0x04, "V": 0x02, "C": 0x01}
        return [(f"{name}.{bit}", pack(n ^ mask)) for bit, mask in bits.items()]
    if name == "go.quit_program":
        new = -2 if n == 0 else 0
    elif name == "cpu.pc":
        new = n + 2
    elif name == "cpu.halted":
        new = 1 if n == 0 else 0
    elif name == "cpu.spcflags":
        return [(f"{name}.{flag}", pack(n ^ mask)) for flag, mask in (("BLTNASTY", 0x200), ("TRACE", 0x40), ("DOTRACE", 0x80), ("CPUINRESET", 0x2))]
    elif name in ("cpu.ipl_evt", "cpu.ipl_evt_pre", "cpu.ipl_pin_change_evt", "cpu.ipl_pin_change_evt_p"):
        new = n + 1
    elif name == "rng.xorshift":
        new = n ^ 2 or n ^ 4
    else:
        new = n ^ 1
    return [(name, pack(new))]


class CpuChunkTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None), "stop": ("stop", None, None), "halt": ("halt", None, None), "input_all": ("input_all", None, None),
            "long_ops": ("long_ops", {"PAD": "3"}, None), "copper_irq": ("copper_irq", {"HP": "2"}, None), "trace": (TRACE, None, None),
            "user": (USER, None, None)}

    def scenarios(self):
        names = dict(SCENARIOS)
        if "kickstart" in self.roms:
            names["kickstart"] = (60, 10)
        return names

    def fields_of(self, rom, unit):
        path = self.dir / f"{rom}-{unit}.fields"
        run = self.probe(rom, "--units", str(unit), "--save-fields-at", str(unit), str(path))
        blob = path.read_bytes()
        return blob, [(name, offset, size) for chunk, name, offset, size in run.fields if chunk == CPU]

    # -- the chunk puts the cpu back -----------------------------------------------------------------------------------------------------

    def test_no_state_is_taken_before_the_first_unit_and_the_bound_does_not_care(self):
        run = self.probe("vsync", "--units", "1", "--save-at", "0", str(self.dir / "never.state"), "--save-at", "1", str(self.dir / "ok.state"))
        self.assertEqual([(0, 2)], [(unit, code) for unit, code, _ in run.save_errors])      # P6_ERROR_BAD_STATE
        self.assertIn("run p6_run_frame once", run.save_errors[0][2])
        self.assertFalse((self.dir / "never.state").exists())
        self.assertEqual(1, len(run.saves))

    def test_the_chunk_puts_the_cpu_digest_of_the_unit_it_was_taken_in_back_and_moves_nothing_else(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path = self.dir / f"{rom}.state"
                run = self.probe(rom, "--units", str(n + k), "--every", "1", "--save-at", str(n), str(path), "--load-at", str(n + k), str(path),
                                 "--only", "cpu")
                self.assertEqual([(n + k, 0, "")], run.loads)
                self.assertEqual(run.digests[(n, "cpu")], run.after[0]["cpu"])
                self.assertEqual({s: run.digests[(n + k, s)] for s in SUBSYSTEMS if s != "cpu"}, {s: run.after[0][s] for s in SUBSYSTEMS if s != "cpu"},
                                 "only the cpu chunk was applied")

    def test_the_programs_actually_move_the_cpu_between_the_two_units(self):
        moving = {rom for rom in SCENARIOS if rom not in ("stop", "halt")}
        for rom in sorted(moving):
            n, k = SCENARIOS[rom]
            run = self.probe(rom, "--units", str(n + k), "--every", "1")
            self.assertNotEqual(run.digests[(n, "cpu")], run.digests[(n + k, "cpu")], f"{rom}: the restore of the cpu would prove nothing")

    def test_a_fresh_image_gets_the_cpu_and_the_ram_of_the_unit_it_was_taken_in_after_the_reset_head_has_run(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                path, again = self.dir / f"{rom}.state", self.dir / f"{rom}.again"
                source = self.probe(rom, "--units", str(n), "--every", "1", "--save-at", str(n), str(path))
                # A new process that has not run a single unit: the load runs the head, then the chunks overwrite what it left.
                fresh = self.probe(rom, "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), "--only", "cpu", "--only", "ram")
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(source.digests[(n, "cpu")], fresh.after[0]["cpu"])
                self.assertEqual(source.digests[(n, "ram")], fresh.after[0]["ram"])
                a, b = parse_chunks(path.read_bytes()), parse_chunks(again.read_bytes())
                self.assertEqual((a[CPU], a[2]), (b[CPU], b[2]), "the state taken again after the restore has the bytes of the one that was loaded")

    def test_a_reset_head_that_fails_before_the_state_is_applied_faults_the_image_and_unload_recovers_it(self):
        path = self.dir / "head.state"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(path))
        run = self.probe("vsync", "--units", "0", "--load-fault-head-at", "0", str(path))
        (unit, code, message), = run.loads
        self.assertEqual((0, 11), (unit, code))                           # P6_ERROR_INTERNAL
        self.assertIn("reset head", message)
        self.assertIn("faulted", message)
        self.assertEqual([("run-after-fault", 2, "the image faulted; unload it"), ("unload", 0, ""), ("reload", 0, "")], run.fault)
        self.assertEqual(self.probe("vsync", "--units", "0").digests[(0, "ram")], run.after[0]["ram"], "nothing of the state was applied")

    def test_the_state_taken_again_after_a_restore_in_the_same_process_is_the_same_bytes(self):
        for rom, (n, k) in self.scenarios().items():
            with self.subTest(rom=rom):
                first, second = self.dir / f"{rom}.first", self.dir / f"{rom}.second"
                self.probe(rom, "--units", str(n + k), "--save-at", str(n), str(first), "--load-at", str(n + k), str(first), "--save-at", str(n + k),
                           str(second), "--only", "cpu", "--only", "ram")
                a, b = parse_chunks(first.read_bytes()), parse_chunks(second.read_bytes())
                self.assertEqual(a[CPU], b[CPU])
                self.assertEqual(a[2], b[2])

    # -- every field is applied ----------------------------------------------------------------------------------------------------------

    def test_every_field_of_the_body_is_applied_a_different_value_gives_a_different_cpu(self):
        blob, fields = self.fields_of("vsync", 3)
        self.assertEqual(44, len(fields))
        names = [name for name, _, _ in fields]
        self.assertEqual(len(names), len(set(names)))
        changed, files = [], []
        for name, offset, size in fields:
            for label, new in alter(name, blob[offset:offset + size]):
                self.assertNotEqual(new, blob[offset:offset + size], label)
                path = self.dir / f"flip-{len(files)}.state"
                path.write_bytes(with_bytes(blob, CPU, offset, new))
                files.append(path)
                changed.append(label)
        arguments = ["--units", "9", "--every", "1", "--only", "cpu", "--load-at", "9", str(self.dir / "vsync-3.fields")]
        for path in files:
            arguments += ["--load-at", "9", str(path)]
        try:
            run = self.probe("vsync", *arguments)
        finally:
            for path in files:
                path.unlink()
        self.assertEqual([(9, 0, "")] * (1 + len(files)), run.loads)
        base = run.after[0]["cpu"]
        for name, after in zip(changed, run.after[1:]):
            self.assertNotEqual(base, after["cpu"], f"{name}: the field is written to the body and not applied to the machine")
            self.assertEqual({s: v for s, v in run.after[0].items() if s != "cpu"}, {s: v for s, v in after.items() if s != "cpu"})

    def test_the_special_flags_of_the_state_replace_those_the_machine_has_not_add_to_them(self):
        path = self.dir / "flags.state"
        run = self.probe("vsync", "--units", "3", "--every", "1", "--save-at", "3", str(path))
        wanted = run.digests[(3, "cpu")]
        for flag in ("bltnasty", "trace", "dotrace"):
            with self.subTest(flag=flag):
                run = self.probe("vsync", "--units", "9", "--only", "cpu", "--poke-at", "9", flag, "--load-at", "9", str(path))
                self.assertEqual(wanted, run.after[0]["cpu"], f"{flag} was pending in the machine, and not in the state, and still is")

    # -- Validate and Apply ---------------------------------------------------------------------------------------------------------------

    def test_a_body_that_validate_accepts_never_fails_to_apply_and_a_refusal_never_changes_the_machine(self):
        path = self.dir / "mutate.state"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(path))
        verdicts = set()
        for units in (4, 9):                    # two machines, two different moments: the verdict is about the body alone
            run = self.probe("vsync", "--units", str(units), "--only", "cpu", "--mutate-at", str(units), "cpu", str(path))
            self.assertEqual([], run.mutate_errors, "the good body was refused by the machine it was loaded into")
            (mutated,) = run.mutate
            self.assertEqual(155, mutated["bytes"])
            self.assertEqual(0, mutated["faulted"], "Validate accepted a body that Apply could not apply")
            self.assertEqual(0, mutated["changed"], "a refused state changed the machine")
            self.assertEqual(mutated["attempts"], mutated["accepted"] + mutated["refused"])
            self.assertGreater(mutated["accepted"], 300)
            self.assertGreater(mutated["refused"], 30)
            verdicts.add((mutated["attempts"], mutated["accepted"], mutated["refused"], mutated["verdicts"]))
        self.assertEqual(1, len(verdicts), "the verdict on a body depends on the machine it is loaded into")

    def test_a_bad_body_is_refused_with_its_reason_and_leaves_the_machine_alone(self):
        blob, fields = self.fields_of("vsync", 3)
        at = {name: (offset, size) for name, offset, size in fields}

        def value(name):
            offset, size = at[name]
            return int.from_bytes(blob[offset:offset + size], "little")

        def forge(name, new, size=None):
            offset, width = at[name]
            return with_bytes(blob, CPU, offset, int(new).to_bytes(size or width, "little"))
        rng_range = "a field is out of its range"
        attempts = [
            ("a request to quit", forge("go.quit_program", 0xFFFFFFFF), 9, "go.quit_program: a state cannot be a request to quit"),
            ("a quit_program above zero", forge("go.quit_program", 1), 9, rng_range),
            ("a quit_program below the resets", forge("go.quit_program", (-5) & 0xFFFFFFFF), 9, rng_range),
            ("a T0 bit in SR", forge("cpu.sr", value("cpu.sr") | 0x4000), 9, "cpu.sr: a bit a 68000 does not have"),
            ("an M bit in SR", forge("cpu.sr", value("cpu.sr") | 0x1000), 9, "cpu.sr: a bit a 68000 does not have"),
            ("a stopped state of 2", forge("cpu.stopped", 2), 9, rng_range),
            ("the accelerator fallback halt", forge("cpu.halted", 4), 9, "cpu.halted: the accelerator fallback"),
            ("a halt reason above the list", forge("cpu.halted", 14), 9, rng_range),
            ("UNIT_END pending", forge("cpu.spcflags", value("cpu.spcflags") | 1), 9, "cpu.spcflags: a flag that cannot be pending"),
            ("INT pending", forge("cpu.spcflags", value("cpu.spcflags") | 8), 9, "cpu.spcflags: a flag that cannot be pending"),
            ("an interrupt level of 8", forge("cpu.ipl0", 8), 9, rng_range),
            ("an interrupt pin of 8", forge("cpu.ipl_pin", 8), 9, rng_range),
            ("a pre mode of 2", forge("cpu.ipl_evt_pre_mode", 2), 9, rng_range),
            ("a negative interrupt time", forge("cpu.ipl_evt", (1 << 64) - 1), 9, rng_range),
            ("a generator at zero", forge("rng.xorshift", 0), 9, rng_range),
            ("the body one byte short", reforge(blob, lambda c: c[CPU].__setitem__(2, c[CPU][2][:-1])), 9, "chunk cpu: the body ends before its last field"),
        ]
        run = self.refusals(attempts, rom="vsync", extra=("--only", "cpu"))
        self.assertEqual(len(attempts), len(run.after))

    # -- a state is taken at an instruction boundary --------------------------------------------------------------------------------------

    def test_a_cpu_that_is_not_at_an_instruction_boundary_is_not_saved_and_nothing_is_written(self):
        names = [("exception", "an exception is in flight"), ("spc-int", "a special flag is pending"),
                 ("lastipl", "a register or mode that the CPU of this machine does not have is set")]
        arguments = []
        for index, (poke, _) in enumerate(names):
            arguments += ["--poke-at", "4", poke, "--save-at", "4", str(self.dir / f"poked{index}.state"), "--poke-at", "4", "clear"]
        arguments += ["--save-at", "4", str(self.dir / "clear.state")]
        run = self.probe("vsync", "--units", "4", *arguments)
        self.assertEqual([(4, 2)] * 3, [(unit, code) for unit, code, _ in run.save_errors])
        for (_, text), (_, _, message) in zip(names, run.save_errors):
            self.assertIn(text, message)
            self.assertIn("chunk cpu could not be saved", message)
        for index in range(3):
            self.assertFalse((self.dir / f"poked{index}.state").exists())
        self.assertEqual(1, len(run.saves), "once the machine is back at a boundary it is saved")

    # -- a real Kickstart ---------------------------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_the_owners_kickstart_gets_its_cpu_back_at_several_boundaries_in_the_same_process_and_in_a_new_one(self):
        for n, k in ((25, 10), (140, 6), (290, 5)):
            with self.subTest(unit=n):
                path = self.dir / f"kickstart-{n}.state"
                run = self.probe("kickstart", "--units", str(n + k), "--every", "1", "--save-at", str(n), str(path), "--load-at", str(n + k), str(path),
                                 "--only", "cpu")
                self.assertEqual([(n + k, 0, "")], run.loads)
                self.assertEqual(run.digests[(n, "cpu")], run.after[0]["cpu"])
                fresh = self.probe("kickstart", "--units", "0", "--load-at", "0", str(path), "--only", "cpu", "--only", "ram")
                self.assertEqual([(0, 0, "")], fresh.loads)
                self.assertEqual(run.digests[(n, "cpu")], fresh.after[0]["cpu"])
                self.assertEqual(run.digests[(n, "ram")], fresh.after[0]["ram"])


if __name__ == "__main__":
    unittest.main()
