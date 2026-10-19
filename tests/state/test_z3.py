"""T55c2: a real 16 MiB Z3 board on the 32-bit A4030, without commercial firmware."""
import struct
import unittest

from tests.state import test_det02 as d
from tests.state.test_state_fast import PRELUDE, delay
from tests.state.harness import FieldTable

Z3 = ["--z3", "16"]
NONE = ["--fast", "4294967295"]


def program(z2):
    text = PRELUDE
    if z2:
        text += "    MOVE.B #$00,$E8004A.L\n    MOVE.B #$20,$E80048.L\n"
    steps = [
        "    .word $1039,$FF00,$0000\n    .word $13C0,$0000,$0210\n",  # Z3 type, through the real Z3 window
        "    MOVE.B #0,$FF000048.L\n",  # low base byte, retained between calls
        "    MOVE.W #$2200,$FF000044.L\n",  # choose $22000000, not the reset's suggested address
        """    MOVE.L #$11223344,$22000000.L
    MOVE.L #$55667788,$22FFFFFC.L
    MOVE.L #$13572468,$2020.L
    MOVE.L #$DEADBEEF,$22002020.L
    .word $2039,$2200,$0000
    .word $23C0,$0000,$2000
    .word $2039,$22FF,$FFFC
    .word $23C0,$0000,$2004
    .word $2039,$0000,$2020
    .word $23C0,$0000,$2008
""",
    ]
    for n, step in enumerate(steps):
        text += delay(n, 20000) + step
    return text + "loop:\n    ADDQ.L #1,D1\n    BRA.S loop\n"


class Z3Tests(d.Det02Case):
    MACHINE = "a4030"
    ROMS = {"z3": (program(True), None, None), "z3-only": (program(False), None, None)}
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def probe_on(cls, machine, rom, *args):
        return super().probe_on(machine, rom, *args, "--cpu-compatibility", "normal")

    def test_bus_mapping_bound_and_restore_with_and_without_z2(self):
        for rom, extra in (("z3", Z3), ("z3-only", Z3 + NONE)):
            with self.subTest(rom=rom):
                state, early, ram = (self.dir / (rom + suffix) for suffix in (".state", ".early", ".ram"))
                run = self.probe(rom, "--units", "12", "--save-at", "1", str(early),
                                 "--save-fields-at", "12", str(state), "--ramout-at", "12", "0x2000", "12", str(ram),
                                 "--public-at", "0", "--public-at", "3", "--public-at", "12", *extra)
                self.assertFalse(run.save_errors)
                before = FieldTable(early.read_bytes(), 2, run.fields)
                after = FieldTable(state.read_bytes(), 2, run.fields)
                self.assertEqual(0, before.get("ram.z3_mapped"))
                self.assertEqual((1, 0x22000000), (after.get("ram.z3_mapped"), after.get("ram.z3_start")))
                self.assertEqual((0x11223344, 0x55667788, 0x13572468), struct.unpack(">III", ram.read_bytes()))
                self.assertEqual(1, len({bound for _, bound, _, _ in run.public_size}))
                for _, bound, written, _ in run.public_size:
                    self.assertLessEqual(written, bound)
                for n in (1, 3, 8):
                    for mode in (d.SAME, d.NEW):
                        self.assertIsNone(self.first_divergence(rom, extra, n, mode), (rom, n, mode))

    def test_bad_mapping_and_incompatible_config_leave_the_machine_unchanged(self):
        state, bad = self.dir / "good.state", self.dir / "bad.state"
        run = self.probe("z3", "--units", "12", "--save-fields-at", "12", str(state), *Z3)
        table = FieldTable(state.read_bytes(), 2, run.fields)
        bad.write_bytes(table.with_values({"ram.z3_start": 0x0f000000}))
        control = self.probe("z3", "--units", "14", "--every", "1", *Z3)
        refused = self.probe("z3", "--units", "14", "--every", "1", "--public-load-at", "13", str(bad), *Z3)
        self.assertEqual(9, refused.loads[0][1])
        self.assertIn("Z3: mapped base outside the bus", refused.loads[0][2])
        self.assertEqual((control.digests, control.pcm, control.pictures), (refused.digests, refused.pcm, refused.pictures))
        self.probe("z3", "--units", "1", "--public-save-at", "1", str(bad))
        refused = self.probe("z3", "--units", "14", "--every", "1", "--public-load-at", "13", str(bad), *Z3)
        self.assertEqual(10, refused.loads[0][1])
        self.assertEqual(control.digests, refused.digests)


if __name__ == "__main__":
    unittest.main()
