"""T53: exact, prefetch-compatible and normal instruction timing.

The public state ABI must continue from the saved instruction in the same process and in a
fresh process. These directed cases exercise VERTB, CIA timers and active chipset/audio DMA.
The 68010 loop/VBR and 68020 cache/AGA paths retain their own directed restore cases.
Neither this module nor a successful build closes the full T53 gate.
"""
import unittest
import struct

from tests.state import envelope as e
from tests.state import test_det02 as d
from tests.state import test_state_a600 as a600
from tests.state import test_det02_cpu010 as c10
from tests.state import test_det02_a1200 as c20
from tests.state.harness import SUBSYSTEMS

MODES = {"exact": 0, "compatible": 1, "normal": 2}

PMMU = c20.PROLOGUE + """    MOVE.L #$4000,D0
    .word $4E7B,$0801           ; MOVEC D0,VBR
    MOVE.L #fline,$402C.L
    MOVE.L #$80000000,$3000.L
    LEA $3000.W,A0
    MOVE.L #0,D0
loop:
    .word $F010,$4000           ; PMOVE (A0),TC: no 68851 is present
    ADDQ.L #1,D0
    MOVE.L D0,$2000.L
    MOVE.L D1,$2004.L
    BRA.S loop
fline:
    ADDQ.L #1,D1
    .word $58AF,$0002           ; ADDQ.L #4,2(A7): resume after the PMOVE and its extension
    RTE
"""


class CpuModesTests(d.Det02Case):
    ROMS = {name: d.Det02Tests.ROMS[name] for name in ("vsync", "cias", "busy")}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    def original(self, rom, extra, n):
        run, path = super().original(rom, extra, n)
        self.assertFalse(run.save_errors, "the source state could not be saved")
        return run, path

    def state(self, mode, unit=3):
        path = self.dir / f"{mode}-{unit}.state"
        run = self.probe("vsync", "--units", str(unit), self.SAVE_OP, str(unit), str(path),
                         "--cpu-compatibility", mode)
        self.assertFalse(run.save_errors)
        self.assertTrue(path.is_file(), run.saves)
        return path, path.read_bytes(), run

    def test_restore_continues_through_interrupts_timers_and_dma(self):
        for level in MODES:
            for rom in self.ROMS:
                for mode in (d.SAME, d.NEW):
                    with self.subTest(level=level, rom=rom, restore=mode):
                        self.assertIsNone(self.first_divergence(rom, ["--cpu-compatibility", level], 3, mode))

    def test_default_exact_and_named_exact_keep_the_original_config_hash_and_execution(self):
        _, data, _ = self.state("exact")
        self.assertEqual(data[a600.CONFIG_HASH], e.config_hash(1, 1, 48000, 0, 1, 946684800))
        default = self.probe("vsync", "--units", "3", "--every", "1")
        explicit = self.probe("vsync", "--units", "3", "--every", "1", "--cpu-compatibility", "exact")
        self.assertEqual(default.digests, explicit.digests)
        self.assertEqual(default.pcm, explicit.pcm)
        self.assertEqual(default.pictures, explicit.pictures)
        # The option must select a different execution route, not just change an identity label.
        normal = self.probe("vsync", "--units", "3", "--every", "1", "--cpu-compatibility", "normal")
        self.assertNotEqual(default.digests[(3, "ram")], normal.digests[(3, "ram")])

    def test_each_nonexact_level_derives_its_own_hash_and_rejects_foreign_states(self):
        states = {mode: self.state(mode) for mode in MODES}
        base = states["exact"][1][a600.CONFIG_HASH]
        for mode, level in MODES.items():
            wanted = base if not level else e.domain_hash("p6/config/cpu-compatibility/v1", base, bytes([level]))
            self.assertEqual(states[mode][1][a600.CONFIG_HASH], wanted)
            for foreign in MODES:
                if foreign == mode:
                    continue
                with self.subTest(mode=mode, foreign=foreign):
                    run = self.probe("vsync", "--units", "4", self.LOAD_OP, "3", str(states[foreign][0]),
                                     "--cpu-compatibility", mode)
                    self.assertEqual([(3, 10)], [(n, code) for n, code, _ in run.loads])
                    self.assertIn("config_hash", run.loads[0][2])
                    control = self.probe("vsync", "--units", "4", "--cpu-compatibility", mode)
                    for subsystem in SUBSYSTEMS:
                        self.assertEqual(run.digests[(4, subsystem)], control.digests[(4, subsystem)])

    def test_bound_stays_constant_at_later_instruction_boundaries(self):
        for level in MODES:
            with self.subTest(level=level):
                _, first, a = self.state(level, 3)
                _, later, b = self.state(level, 6)
                self.assertEqual(a.saves[0][3], b.saves[0][3])
                self.assertLessEqual(len(first), a.saves[0][3])
                self.assertLessEqual(len(later), b.saves[0][3])


class Cpu010ModesTests(d.Det02Case):
    ROMS = {name: c10.ROMS[name] for name in ("c10-loop", "c10-vbr", "busy")}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        return super().probe_on(machine, rom, *arguments, "--cpu", "68010")

    def test_each_level_restores_loop_mode_vbr_and_dma(self):
        for level in MODES:
            for rom in self.ROMS:
                for restore in (d.SAME, d.NEW):
                    with self.subTest(level=level, rom=rom, restore=restore):
                        self.assertIsNone(self.first_divergence(rom, ["--cpu-compatibility", level], 3, restore))


class Cpu020ModesTests(d.Det02Case):
    MACHINE = "a1200"
    ROMS = {name: c20.ROMS[name] for name in ("cache020", "busy", "tone")}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    def test_each_level_restores_cache_dma_and_audio_phase(self):
        for level in MODES:
            for rom in self.ROMS:
                for restore in (d.SAME, d.NEW):
                    with self.subTest(level=level, rom=rom, restore=restore):
                        self.assertIsNone(self.first_divergence(rom, ["--cpu-compatibility", level], 3, restore))


class Cpu020NoMmuTests(d.Det02Case):
    MACHINE = "a1200"
    ROMS = {"pmmu": (PMMU, None, None)}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    def test_pmmu_enters_line_f_continues_and_restores_without_fake_mmu(self):
        for level in MODES:
            with self.subTest(level=level):
                flags = ["--cpu-compatibility", level]
                memory = self.dir / f"pmmu-{level}.ram"
                run = self.probe("pmmu", "--units", "3", "--ramout-at", "3", "0x2000", "8", str(memory), *flags)
                resumed, fline = struct.unpack(">II", memory.read_bytes())
                self.assertGreater(min(resumed, fline), 0, "PMMOVE must reach vector 11 and resume, not fake a working MMU")
                self.assertLessEqual(abs(resumed - fline), 1)
                self.assertFalse(run.save_errors)
                for mode in (d.SAME, d.NEW):
                    self.assertIsNone(self.first_divergence("pmmu", flags, 3, mode))


if __name__ == "__main__":
    unittest.main()
