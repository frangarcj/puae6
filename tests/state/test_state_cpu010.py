"""T52: the native state of a machine with a 68010 and the line between it and the machine with a 68000.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure):
  * a machine with a 68010 has the chunks of the 68000 machine, in the same order; P6CP carries one more block, after the body of the 68000: the vector base register, SFC, DFC and the loop mode
    (bridge/state_cpu.cpp visit_010, 10 bytes). The machine with its own 68000 has the state it had.
  * the CPU is part of the configuration hash when it is not the machine's own (a derived hash, domain p6/config/cpu/v1): a state of one is INCOMPATIBLE in the other by the hash, and with
    the hash forged the chunk refuses it by its length. A config that names the machine's own CPU (68000 on an A500) is the profile's and hashes as it always did. The bound of the
    machine with a 68010 is the bound of the 68000 machine plus the block, to the byte.
  * every field of the block is validated against its range before anything is applied, and every field decides the future: a poke that moves it in the live machine changes what the machine does
    afterwards (DET02 in tests/state/test_det02_cpu010.py shows that the state puts each one back).
"""
import unittest

from tests.state import envelope as e
from tests.state import test_det02_cpu010 as c10
from tests.state import test_state_a600 as a600
from tests.state.harness import FieldTable, ProbeCase, SUBSYSTEMS, parse_chunks, reforge

CPU = 0
INCOMPATIBLE, CORRUPT = 10, 9
BLOCK_BYTES = 10            # the 68010 block in P6CP, see bridge/state_cpu.cpp
FIELDS = ["cpu.vbr", "cpu.sfc", "cpu.dfc", "cpu.loop_mode"]


class Cpu010ProbeCase(ProbeCase):
    """A ProbeCase whose machine is the A500 with a 68010, unless a test asks for another CPU."""

    ROMS = {**c10.ROMS, "vsync": ("vsync", None, None)}

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        extra = [] if "--cpu" in arguments else ["--cpu", "68010"]
        return super().probe_on(machine, rom, *arguments, *extra)


class LayoutTests(Cpu010ProbeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.states = {}
        cls.runs = {}
        for name, option in (("68000", "68000"), ("68010", "68010")):
            path = cls.dir / f"{name}.state"
            cls.runs[name] = cls.probe("vsync", "--units", "3", "--save-fields-at", "3", str(path), "--cpu", option)
            cls.states[name] = path.read_bytes()

    def test_the_machine_with_a_68010_has_the_chunks_of_the_68000_machine_and_one_more_block_in_p6cp(self):
        old, new = parse_chunks(self.states["68000"]), parse_chunks(self.states["68010"])
        self.assertEqual([(tag, version) for tag, version, _ in old], [(tag, version) for tag, version, _ in new])
        self.assertEqual(len(old[CPU][2]) + BLOCK_BYTES, len(new[CPU][2]))
        for index in range(1, len(old)):
            self.assertEqual(len(old[index][2]), len(new[index][2]), old[index][0])

    def test_the_block_is_the_registers_of_the_manual_in_order_after_the_body_of_the_68000(self):
        fields = [(name, size) for chunk, name, offset, size in self.runs["68010"].fields if chunk == CPU]
        base = [(name, size) for chunk, name, offset, size in self.runs["68000"].fields if chunk == CPU]
        self.assertEqual(base, fields[:len(base)], "the body of the 68000 is a prefix of the one of the 68010")
        block = fields[len(base):]
        self.assertEqual(BLOCK_BYTES, sum(size for _, size in block))
        self.assertEqual(FIELDS, [name for name, _ in block])

    def test_the_bound_grows_by_the_block_and_by_nothing_else(self):
        bounds = {}
        for model in ("68000", "68010"):
            run = self.probe("vsync", "--units", "3", "--public-at", "3", "--cpu", model)
            (_, bound, written, _), = run.public_size
            self.assertLessEqual(written, bound, model)
            bounds[model] = bound
        self.assertEqual(bounds["68000"] + BLOCK_BYTES, bounds["68010"])

    def test_a_state_taken_with_a_68010_is_saved_again_byte_for_byte_by_a_new_process(self):
        for rom in ("c10-loop", "c10-vbr", "c10-address"):
            with self.subTest(rom=rom):
                source, again = self.dir / "own.state", self.dir / "again.state"
                self.probe(rom, "--units", "4", "--save-at", "4", str(source))
                run = self.probe(rom, "--units", "0", "--load-at", "0", str(source), "--save-at", "0", str(again))
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(source.read_bytes(), again.read_bytes())

    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        for rom in ("c10-loop", "c10-vbr", "c10-address"):
            with self.subTest(rom=rom):
                self.probe(rom, "--units", "6", "--save-at", "3", str(self.dir / "g.state"), "--save-at", "4", str(self.dir / "natural.state"),
                           "--load-at", "5", str(self.dir / "g.state"), "--save-at", "6", str(self.dir / "rewound.state"))
                natural, rewound = (parse_chunks((self.dir / f"{name}.state").read_bytes()) for name in ("natural", "rewound"))
                differences = [(tag, [i for i in range(len(body)) if body[i] != rewound[k][2][i]][:4]) for k, (tag, _, body) in enumerate(natural) if body != rewound[k][2]]
                self.assertEqual([], differences)


class IdentityTests(Cpu010ProbeCase):
    """The CPU is in the identity: the two machines refuse each other's states."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.states = {}
        for model in ("68000", "68010"):
            path = cls.dir / f"{model}.state"
            cls.probe("vsync", "--units", "3", "--save-at", "3", str(path), "--cpu", model)
            cls.states[model] = path.read_bytes()

    def run_in(self, model, state):
        path = self.dir / "foreign.state"
        path.write_bytes(state)
        return self.probe("vsync", "--units", "4", "--load-at", "3", str(path), "--cpu", model)

    def test_the_states_of_the_other_cpu_are_incompatible(self):
        for mine, theirs in (("68000", "68010"), ("68010", "68000")):
            with self.subTest(machine=mine, state=theirs):
                run = self.run_in(mine, self.states[theirs])
                self.assertEqual([(3, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])
                self.assertIn("config_hash: differs from the loaded machine", run.loads[0][2])

    def test_with_the_hash_forged_the_length_of_the_cpu_body_refuses_it(self):
        for mine, theirs in (("68010", "68000"), ("68000", "68010")):
            with self.subTest(machine=mine, state=theirs):
                forged = reforge(self.states[theirs], header={a600.CONFIG_HASH.start: self.states[mine][a600.CONFIG_HASH]})
                run = self.run_in(mine, forged)
                self.assertEqual([(3, CORRUPT)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_machines_own_cpu_has_the_hash_it_had_and_the_68010_derives_it(self):
        # The A500 with p6_config.cpu_model = 1 (a 68000) is the machine of T04: the hash is the one of the flags word and nothing else (machine 1, PAL, 48000 Hz, the probe's seed 1 and its
        # epoch of 2000). The 68010 derives it from that hash and the resolved model (1), made here from the envelope's definition (tests/state/envelope.py) and not read from the probe.
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(base, self.states["68000"][a600.CONFIG_HASH])
        self.assertEqual(e.config_hash_cpu(base, 1), self.states["68010"][a600.CONFIG_HASH])
        self.assertNotEqual(base, e.config_hash_cpu(base, 1))

    def test_the_cpu_and_the_other_options_derive_one_after_the_other(self):
        # The audio word first (T73), the CPU on top of it: a 68010 with another stereo separation is neither of the two machines it is a part of.
        path = self.dir / "both.state"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(path), "--cpu", "68010", "--sound-sep", "11")
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        audio = e.config_hash_audio(base, 10, 1, 1, 0)
        self.assertEqual(e.config_hash_cpu(audio, 1), path.read_bytes()[a600.CONFIG_HASH])


class ValidationTests(Cpu010ProbeCase):
    """Every field of the block is refused outside its range before anything is applied."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = cls.dir / "good.state"
        run = cls.probe("c10-vbr", "--units", "4", "--save-fields-at", "4", str(path))
        cls.good = path.read_bytes()
        cls.table = FieldTable(cls.good, CPU, run.fields)

    def test_a_field_of_the_68010_block_outside_its_range_is_refused_and_the_machine_is_left_alone(self):
        changes = {
            "SFC with a fourth bit": ("cpu.sfc", 8), "DFC with a fourth bit": ("cpu.dfc", 0x10),
            "a loop mode that pays more than a pass can": ("cpu.loop_mode", 0x11), "extra cycles of the exit beyond a pass": ("cpu.loop_mode", 0x110000),
        }
        attempts = [(name, self.table.with_values({label: value}), CORRUPT, "chunk cpu") for name, (label, value) in changes.items()]
        self.refusals(attempts, rom="c10-vbr", unit=4, units=8)

    def test_a_vector_base_is_never_refused(self):
        # Any longword is a value VBR can hold (an odd one halts the CPU at its first exception, as upstream does): no range, only the length.
        for value in (0, 0x4000, 0xFFFFFFFF, 0x80000001):
            with self.subTest(value=value):
                path = self.dir / "vbr.state"
                path.write_bytes(self.table.with_values({"cpu.vbr": value}))
                run = self.probe("c10-vbr", "--units", "6", "--load-at", "4", str(path))
                self.assertEqual([(4, 0, "")], run.loads)


class NecessityTests(Cpu010ProbeCase):
    """Every field of the block decides the future of the program it matters in: a poke that moves it in the live machine, and nothing else, changes what the machine does later (a
    digest or the samples handed over). Each row is the field, the program, the units at which the poke is made."""

    POKES = {"vbr": ("c10-vbr", (2, 3, 4)), "sfc": ("c10-vbr", (2, 3, 4)), "dfc": ("c10-vbr", (2, 3, 4)), "loop": ("c10-loop", (2, 3, 4))}

    def effect(self, field, rom, unit):
        after = 4
        clean = self.probe(rom, "--units", str(unit + after), "--every", "1")
        poked = self.probe(rom, "--units", str(unit + after), "--every", "1", "--poke-at", str(unit), f"cpu010-{field}")
        return [(u, s) for u in range(unit + 1, unit + after + 1) for s in SUBSYSTEMS if clean.digests[(u, s)] != poked.digests[(u, s)]]

    def test_each_field_changes_the_future_of_the_program_it_matters_in(self):
        for field, (rom, units) in self.POKES.items():
            with self.subTest(field=field):
                effects = [self.effect(field, rom, unit) for unit in units]
                self.assertTrue(any(effects), f"{field}: nothing moved in any of the units {units} of {rom}")

    def test_the_loop_program_is_in_loop_mode_at_the_boundaries(self):
        # DET02 c10-loop is only a test of the loop mode if the state falls inside the loop: the field is set at most of the boundaries.
        inside = 0
        for unit in range(1, 8):
            path = self.dir / f"l{unit}.state"
            run = self.probe("c10-loop", "--units", str(unit), "--save-fields-at", str(unit), str(path))
            table = FieldTable(path.read_bytes(), CPU, run.fields)
            inside += 1 if table.get("cpu.loop_mode") else 0
        self.assertGreaterEqual(inside, 4, "the loop mode is set at only %d of 7 boundaries" % inside)


if __name__ == "__main__":
    unittest.main()
