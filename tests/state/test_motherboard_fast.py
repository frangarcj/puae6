"""T105d: A4000 Ramsey motherboard Fast RAM is a fixed bank, not a Zorro board."""
import unittest
import subprocess

from tests.state import test_det02 as d
from tests.state.harness import FieldTable


PROGRAM = """; A4000 motherboard RAM at 1 MiB ends immediately below $08000000.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$96,$DE0003.L
    MOVE.B #0,$DE0002.L
    MOVE.B #0,$DE0000.L
    MOVE.B #0,$DE0001.L
    MOVE.L #$11223344,$07F00000.L
    MOVE.L #$55667788,$07FFFFFC.L
loop:
    .word $2039,$07F0,$0000
    ADDQ.L #1,D0
    .word $23C0,$07F0,$0000
    .word $23C0,$0000,$0220
    .word $2039,$07FF,$FFFC
    .word $23C0,$0000,$0224
    BRA.S loop
"""


class MotherboardFastTests(d.Det02Case):
    MACHINE = "a4030"
    ROMS = {"motherboard-fast": (PROGRAM, None, None)}
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def probe_on(cls, machine, rom, *args):
        return super().probe_on(machine, rom, *args, "--cpu-compatibility", "normal")

    def test_fixed_bank_bytes_ramsey_latches_identity_and_restore(self):
        fields = self.dir / "motherboard.fields"
        state = self.dir / "motherboard.state"
        run = self.probe("motherboard-fast", "--units", "3", "--save-fields-at", "3", str(fields),
                         "--public-save-at", "3", str(state), "--motherboard-fast", "1", "--fast", "4294967295")
        self.assertFalse(run.save_errors)
        ram = FieldTable(fields.read_bytes(), 2, run.fields)
        self.assertEqual(1 << 20, ram.get("ram.motherboard_fast_size"))
        self.assertEqual(0x96, ram.get("mbres.ramsey_config"))
        self.assertEqual(0, ram.get("mbres.gary_coldboot"))
        blob = state.read_bytes()
        plain = self.dir / "plain.state"
        self.probe("motherboard-fast", "--units", "3", "--public-save-at", "3", str(plain), "--fast", "4294967295")
        self.assertNotEqual(plain.read_bytes()[176:208], blob[176:208], "motherboard capacity enters config identity")
        for mode in (d.SAME, d.NEW):
            with self.subTest(restore=mode):
                self.assertIsNone(self.first_divergence("motherboard-fast", ["--motherboard-fast", "1", "--fast", "4294967295"], 3, mode))

    def test_motherboard_ram_coexists_with_z3_backing(self):
        for mode in (d.SAME, d.NEW):
            with self.subTest(restore=mode):
                self.assertIsNone(self.first_divergence("motherboard-fast", ["--motherboard-fast", "1", "--fast", "4294967295", "--z3", "1"], 3, mode))

    def test_all_capacities_have_distinct_identity_and_non_a4000_is_refused(self):
        hashes = set()
        for mib in (1, 2, 4, 8, 16, 32, 64):
            with self.subTest(mib=mib):
                path = self.dir / f"motherboard-{mib}.state"
                run = self.probe("motherboard-fast", "--units", "1", "--public-save-at", "1", str(path),
                                 "--motherboard-fast", str(mib), "--fast", "4294967295")
                self.assertFalse(run.save_errors)
                hashes.add(path.read_bytes()[176:208])
                path.unlink()
        self.assertEqual(7, len(hashes))
        refused = subprocess.run([str(self.exe), str(self.roms["motherboard-fast"]), "--machine", "a1200",
                                  "--motherboard-fast", "1", "--units", "1"], capture_output=True, text=True)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("on A4000 only", refused.stderr)


if __name__ == "__main__":
    unittest.main()
