"""T77a: CLXDAT levels observed by a guest, native state and unchanged Full aliases."""
import struct
import unittest

from tests.scenarios.aga import aga
from tests.state import test_det02 as d
from tests.state import test_state_a600 as a600
from tests.state import envelope as e

DISPLAY = aga.AgaDisplay("collisions", planes=2, indices=tuple([0, 3] * 160),
                         palette=tuple((i, i * 0x010101, "twelve") for i in range(32)),
                         sprites=(aga.Sprite(0, 0x90, 80), aga.Sprite(2, 0x90, 80, seed=2)))
PROGRAM = aga.rom_source(DISPLAY).replace("loop:\n", """    MOVE.W #$0FC3,$DFF098.L  ; match planes 1/2; sprites 0 and 2 overlap
    MOVE.L #irq,$6C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
""", 1) + """irq:
    .word $3239,$00DF,$F00E    ; MOVE.W CLXDAT,D1: reading clears the latch
    MOVE.W D1,$1000.L
    MOVE.W #$0020,$DFF09C.L
    RTE
"""
LEVELS = {"none": 0, "sprites": 1, "playfields": 2, "full": 3}
EXPECTED = {"none": 0, "sprites": 0x200, "playfields": 0x266, "full": 0x267}


class CollisionTests(d.Det02Case):
    ROMS = {"collisions": (PROGRAM, None, None)}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    def observed(self, level):
        path = self.dir / f"{level}.ram"
        run = self.probe("collisions", "--units", "4", "--collisions", level,
                         "--ramout-at", "4", "0x1000", "2", str(path))
        return struct.unpack(">H", path.read_bytes())[0] & 0x7fff, run

    def test_none_disables_every_collision_bit(self):
        self.assertEqual(0, self.observed("none")[0], "None must suppress bitplane collisions as well as sprites")

    def test_levels_are_observable_and_each_restores_in_same_and_new_process(self):
        for level in LEVELS:
            with self.subTest(level=level):
                self.assertEqual(EXPECTED[level], self.observed(level)[0])
                for mode in (d.SAME, d.NEW):
                    self.assertIsNone(self.first_divergence("collisions", ["--collisions", level], 3, mode))

    def test_auto_and_full_preserve_default_hash_and_execution_and_reduced_states_are_incompatible(self):
        states = {}
        for level in ("auto", *LEVELS):
            path = self.dir / f"{level}.state"
            run = self.probe("collisions", "--units", "3", "--every", "1", "--collisions", level,
                             self.SAVE_OP, "3", str(path))
            self.assertFalse(run.save_errors)
            states[level] = (path, path.read_bytes(), run)
        base = e.config_hash(3 if self.MACHINE == "a1200" else 1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(base, states["auto"][1][a600.CONFIG_HASH])
        self.assertEqual(states["auto"][1], states["full"][1])
        self.assertEqual(states["auto"][2].digests, states["full"][2].digests)
        for level, number in LEVELS.items():
            expected = base if number == 3 else e.domain_hash("p6/config/collisions/v1", base, bytes([number]))
            self.assertEqual(expected, states[level][1][a600.CONFIG_HASH])
        control = self.probe("collisions", "--units", "6", "--every", "1", "--collisions", "full")
        refused = self.probe("collisions", "--units", "6", "--every", "1", "--collisions", "full",
                             self.LOAD_OP, "5", str(states["none"][0]))
        self.assertEqual(10, refused.loads[0][1])
        self.assertEqual(control.digests, refused.digests)
        self.assertEqual(control.pcm, refused.pcm)
        self.assertEqual(control.pictures, refused.pictures)


class AgaCollisionTests(CollisionTests):
    MACHINE = "a1200"


if __name__ == "__main__":
    unittest.main()
