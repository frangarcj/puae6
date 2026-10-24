"""T54: the native state of an A1200 with a 68881 or a 68882 (SoftFloat) and the line between it and the machine without one.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure):
  * a machine with an FPU has the chunks of the A1200, in the same order; P6CP carries one more block, after the block of the 68020: FP0-FP7 as 80-bit patterns, FPCR, FPSR, FPIAR,
    the FPU state, the posted exception and the FSAVE data of the last exception (bridge/state_cpu.cpp visit_fpu, 110 bytes). The machine without an FPU has the state it had.
  * the model of the FPU is part of the configuration hash: a state of one of {none, 68881, 68882} is INCOMPATIBLE in another by the hash, and with the hash forged the chunk refuses
    it by its length. The bound of the machine with an FPU is the bound of the A1200 plus the block, to the byte.
  * every field of the block is validated against its range before anything is applied, and every field decides the future: a poke that moves it in the live machine changes what the
    machine does afterwards (DET02 in tests/state/test_det02_fpu.py shows that the state puts each one back).
"""
import unittest

from tests.state import envelope as e
from tests.state import test_det02_fpu as fpu
from tests.state import test_state_a600 as a600
from tests.state.harness import FieldTable, ProbeCase, SUBSYSTEMS, parse_chunks, reforge

CPU = 0
INCOMPATIBLE, CORRUPT = 10, 9
FPU_BYTES = 110             # the block of the FPU in P6CP, see bridge/state_cpu.cpp
FIELDS = ["fpu.fp.high", "fpu.fp.low", "fpu.fpcr", "fpu.fpsr", "fpu.fpiar", "fpu.state", "fpu.exp_pend", "fpu.fsave.ccr", "fpu.fsave.eo"]


class FpuProbeCase(ProbeCase):
    """A ProbeCase whose machine is the A1200 with `FPU`, unless a test asks for another model."""

    MACHINE = "a1200"
    FPU = "68882"
    ROMS = {**fpu.ROMS, "vsync": ("vsync", None, None)}

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        extra = [] if "--fpu" in arguments or not cls.FPU else ["--fpu", cls.FPU]
        return super().probe_on(machine, rom, *arguments, *extra)


class FpuLayoutTests(FpuProbeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.states = {}
        cls.runs = {}
        for model, option in (("none", "0"), ("68881", "68881"), ("68882", "68882")):
            path = cls.dir / f"{model}.state"
            cls.runs[model] = cls.probe("vsync", "--units", "3", "--save-fields-at", "3", str(path), "--fpu", option)
            cls.states[model] = path.read_bytes()

    def test_the_machine_with_an_fpu_has_the_chunks_of_the_a1200_and_one_more_block_in_p6cp(self):
        none, with_fpu = parse_chunks(self.states["none"]), parse_chunks(self.states["68882"])
        self.assertEqual([(tag, version) for tag, version, _ in none], [(tag, version) for tag, version, _ in with_fpu])
        self.assertEqual(len(none[CPU][2]) + FPU_BYTES, len(with_fpu[CPU][2]))
        for index in range(1, len(none)):
            self.assertEqual(len(none[index][2]), len(with_fpu[index][2]), none[index][0])

    def test_the_block_is_the_fields_of_the_manual_in_order_after_the_block_of_the_68020(self):
        fields = [(name, size) for chunk, name, offset, size in self.runs["68882"].fields if chunk == CPU]
        base = [(name, size) for chunk, name, offset, size in self.runs["none"].fields if chunk == CPU]
        self.assertEqual(base, fields[:len(base)], "the body without an FPU is a prefix of the one with it")
        block = fields[len(base):]
        self.assertEqual(FPU_BYTES, sum(size for _, size in block))
        order = []
        for name, _ in block:
            if name not in order:
                order.append(name)
        self.assertEqual(FIELDS, order)
        self.assertEqual(8, sum(1 for name, _ in block if name == "fpu.fp.high"))

    def test_the_bound_grows_by_the_block_and_by_nothing_else(self):
        bounds = {}
        for model in ("none", "68881", "68882"):
            run = self.probe("vsync", "--units", "3", "--public-at", "3", "--fpu", "0" if model == "none" else model)
            (_, bound, written, _), = run.public_size
            self.assertLessEqual(written, bound, model)
            bounds[model] = bound
        self.assertEqual(bounds["none"] + FPU_BYTES, bounds["68881"])
        self.assertEqual(bounds["68881"], bounds["68882"])

    def test_a_state_taken_with_an_fpu_is_saved_again_byte_for_byte_by_a_new_process(self):
        for rom in ("fpu-arith", "fpu-trap", "fpu-modes", "fpu-frames"):
            with self.subTest(rom=rom):
                source, again = self.dir / "own.state", self.dir / "again.state"
                self.probe(rom, "--units", "4", "--save-at", "4", str(source))
                run = self.probe(rom, "--units", "0", "--load-at", "0", str(source), "--save-at", "0", str(again))
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(source.read_bytes(), again.read_bytes())

    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        for rom in ("fpu-arith", "fpu-trap", "fpu-frames"):
            with self.subTest(rom=rom):
                self.probe(rom, "--units", "6", "--save-at", "3", str(self.dir / "g.state"), "--save-at", "4", str(self.dir / "natural.state"),
                           "--load-at", "5", str(self.dir / "g.state"), "--save-at", "6", str(self.dir / "rewound.state"))
                natural, rewound = (parse_chunks((self.dir / f"{name}.state").read_bytes()) for name in ("natural", "rewound"))
                differences = [(tag, [i for i in range(len(body)) if body[i] != rewound[k][2][i]][:4]) for k, (tag, _, body) in enumerate(natural) if body != rewound[k][2]]
                self.assertEqual([], differences)


class FpuIdentityTests(FpuProbeCase):
    """The model of the FPU is in the identity: the three machines refuse each other's states."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.states = {}
        for model in ("0", "68881", "68882"):
            path = cls.dir / f"{model}.state"
            cls.probe_on("a1200", "vsync", "--units", "3", "--save-at", "3", str(path), "--fpu", model)
            cls.states[model] = path.read_bytes()

    def run_in(self, model, state):
        path = self.dir / "foreign.state"
        path.write_bytes(state)
        return self.probe_on("a1200", "vsync", "--units", "4", "--load-at", "3", str(path), "--fpu", model)

    def test_the_states_of_the_other_two_machines_are_incompatible(self):
        for mine in self.states:
            for theirs in self.states:
                if mine == theirs:
                    continue
                with self.subTest(machine=mine, state=theirs):
                    run = self.run_in(mine, self.states[theirs])
                    self.assertEqual([(3, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])
                    self.assertIn("config_hash: differs from the loaded machine", run.loads[0][2])

    def test_with_the_hash_forged_the_length_of_the_cpu_body_refuses_it(self):
        for mine, theirs in (("68882", "0"), ("0", "68882"), ("68881", "0")):
            with self.subTest(machine=mine, state=theirs):
                forged = reforge(self.states[theirs], header={a600.CONFIG_HASH.start: self.states[mine][a600.CONFIG_HASH]})
                run = self.run_in(mine, forged)
                self.assertEqual([(3, CORRUPT)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_machine_without_an_fpu_has_the_hash_it_had_and_each_fpu_adds_one_bit_pattern_to_the_flags(self):
        # An A1200 with p6_config.fpu_model = 0 is the machine of T24 and T49: its configuration hash is the one the series has always had, so no bit of the FPU is in the flags
        # word it hashes; the FPU is 1 (68881) or 2 (68882) in bits 23 and 24 (bridge/config.cpp identity_flags), which no flag and no memory size reaches. The expected hashes
        # are made here from the envelope's definition (tests/state/envelope.py), not read from the probe: machine 3, PAL, 48000 Hz, the probe's seed 1 and its epoch of 2000.
        for model, flags in (("0", 0), ("68881", 1 << 23), ("68882", 2 << 23)):
            with self.subTest(model=model):
                self.assertEqual(e.config_hash(3, 1, 48000, flags, 1, 946684800), self.states[model][a600.CONFIG_HASH])


class FpuValidationTests(FpuProbeCase):
    """Every field of the block is refused outside its range before anything is applied."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = cls.dir / "good.state"
        run = cls.probe("fpu-trap", "--units", "4", "--save-fields-at", "4", str(path))
        cls.good = path.read_bytes()
        cls.table = FieldTable(cls.good, CPU, run.fields)

    def test_a_field_of_the_fpu_block_outside_its_range_is_refused_and_the_machine_is_left_alone(self):
        changes = {
            "FPCR with a bit the 6888x does not have": ("fpu.fpcr", 0x0001), "FPCR with a bit in the reserved byte": ("fpu.fpcr", 0x1_0000),
            "FPSR with a bit of the reserved nibble": ("fpu.fpsr", 0x10000000), "FPSR with a bit of the accrued byte that is zero": ("fpu.fpsr", 0x00000001),
            "FPU state beyond idle": ("fpu.state", 2), "a posted exception that is not an FPU vector": ("fpu.exp_pend", 12), "a posted exception beyond the FPU vectors": ("fpu.exp_pend", 56),
        }
        attempts = [(name, self.table.with_values({label: value}), CORRUPT, "chunk cpu") for name, (label, value) in changes.items()]
        self.refusals(attempts, rom="fpu-trap", unit=4, units=8)

    def test_a_register_pattern_is_never_refused(self):
        # Every 80-bit pattern is a value the registers can hold (FMOVEM and FRESTORE put whatever the program has): no range, only the length.
        for label, value in (("fpu.fp.high[3]", 0xFFFF), ("fpu.fp.low[5]", 0), ("fpu.fp.high[7]", 0x7FFF), ("fpu.fp.low[0]", 0x7FFFFFFFFFFFFFFF)):
            with self.subTest(label=label):
                path = self.dir / "pattern.state"
                path.write_bytes(self.table.with_values({label: value}))
                run = self.probe("fpu-trap", "--units", "6", "--load-at", "4", str(path))
                self.assertEqual([(4, 0, "")], run.loads, label)


class FpuNecessityTests(FpuProbeCase):
    """Every field of the block decides the future of the program it matters in: a poke that moves it in the live machine, and nothing else, changes what the machine does later
    (a digest or the samples handed over). Each row is the field, the program, the units at which the poke is made."""

    POKES = {
        "reg": ("fpu-arith", (3, 4)), "fpcr": ("fpu-arith", (3, 4)), "fpiar": ("fpu-arith", (3, 4)), "fsave": ("fpu-arith", (3, 4)),
        # the trap program spends most of its time in an integer loop, so what the FPU holds at the boundary is still there when the handler of the unit logs it
        "fpsr": ("fpu-trap", (2, 3, 4)), "state": ("fpu-trap", (2, 3, 4)), "exp-pend": ("fpu-trap", (2, 3, 4, 5)),
    }

    def effect(self, field, rom, unit):
        after = 4
        clean = self.probe(rom, "--units", str(unit + after), "--every", "1")
        poked = self.probe(rom, "--units", str(unit + after), "--every", "1", "--poke-at", str(unit), f"fpu-{field}")
        moved = [(u, s) for u in range(unit + 1, unit + after + 1) for s in SUBSYSTEMS if clean.digests[(u, s)] != poked.digests[(u, s)]]
        return moved

    def test_each_field_changes_the_future_of_the_program_it_matters_in(self):
        for field, (rom, units) in self.POKES.items():
            with self.subTest(field=field):
                effects = [self.effect(field, rom, unit) for unit in units]
                self.assertTrue(any(effects), f"{field}: nothing moved in any of the units {units} of {rom}")

    def test_the_trap_program_has_an_exception_posted_at_some_boundaries(self):
        # DET02 fpu-trap is only a test of the posted exception if a state falls between the divide and the instruction that takes it.
        pending = 0
        for unit in range(1, 8):
            run = self.probe("fpu-trap", "--units", str(unit), "--save-fields-at", str(unit), str(self.dir / f"t{unit}.state"))
            table = FieldTable((self.dir / f"t{unit}.state").read_bytes(), CPU, run.fields)
            pending += 1 if table.get("fpu.exp_pend") else 0
        self.assertGreater(pending, 0, "no unit of fpu-trap has an exception posted: the window is too short")


class FpuOffTests(ProbeCase):
    """Without an FPU the coprocessor instructions are line F, as they were (tests/host/test_cpu_020.py), and the state has no FPU fields."""

    MACHINE = "a1200"
    ROMS = {"vsync": ("vsync", None, None)}

    def test_the_a1200_without_an_fpu_has_no_fpu_fields_in_its_cpu_chunk(self):
        path = self.dir / "none.state"
        run = self.probe("vsync", "--units", "3", "--save-fields-at", "3", str(path))
        names = {name for chunk, name, offset, size in run.fields if chunk == CPU}
        self.assertFalse(any(name.startswith("fpu.") for name in names))


if __name__ == "__main__":
    unittest.main()
