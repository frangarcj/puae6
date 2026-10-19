"""T55b first core vertical: real A4000 motherboard, normal 68030, no PMMU.

The UI and real Waterbox acceptance remain separate gates; this is a directed core test.
"""
import struct
import subprocess
import unittest

from tests.state import test_det02 as d
from tests.state import test_cpu_modes as cpu
from tests.state import test_det02_fpu as fpu
from tests.state.harness import FieldTable

SETUP = """    MOVE.B #$00,$E8004A.L
    MOVE.B #$20,$E80048.L       ; configure the 8 MiB Zorro II board
    MOVE.L #$12345678,$200000.L
    MOVE.L #$9ABCDEF0,$9FFFFC.L
    MOVE.L #$13572468,$2008.L
    MOVE.L #$FFFFFFFF,$01002008.L ; 32-bit map: must not alias Chip RAM
    MOVE.B #$96,$DE0003.L       ; Ramsey config
    MOVE.B #0,$DE0002.L         ; clear Gary cold boot
    MOVE.B #0,$DE0000.L         ; timeout disabled
    MOVE.B #0,$DE0001.L         ; bus error disabled
    MOVE.B #2,$DC0035.L         ; Ricoh battery RAM low bank
    MOVE.B #11,$DC0001.L
    MOVE.B #3,$DC0035.L         ; Ricoh battery RAM high bank
    MOVE.B #10,$DC0001.L
    MOVE.L #$3113,D0
    .word $4E7B,$0002           ; MOVEC D0,CACR (030 bits unavailable on 020)
    .word $1039,$00DE,$0043     ; MOVE.B Ramsey revision,D0
    .word $13C0,$0000,$2010
    .word $1039,$00DE,$0003     ; Ramsey config
    .word $13C0,$0000,$2011
    .word $1039,$00DE,$0002     ; Gary cold boot
    .word $13C0,$0000,$2012
    .word $1039,$00DC,$0001     ; Ricoh high nibble
    .word $13C0,$0000,$2013
"""
PROGRAM = cpu.PMMU.replace("    MOVE.L #$4000,D0", SETUP + "    MOVE.L #$4000,D0", 1)


class A4030Tests(d.Det02Case):
    MACHINE = "a4030"
    ROMS = {"a4030": (PROGRAM, None, None), "fpu-arith": fpu.ROMS["fpu-arith"]}
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"

    @classmethod
    def probe_on(cls, machine, rom, *arguments):
        return super().probe_on(machine, rom, *arguments, "--cpu-compatibility", "normal")

    def test_real_motherboard_cpu_and_public_restore(self):
        path = self.dir / "a4030.state"
        ram = self.dir / "a4030.ram"
        run = self.probe("a4030", "--units", "3", "--save-fields-at", "3", str(path),
                         "--ramout-at", "3", "0x2000", "20", str(ram))
        self.assertFalse(run.save_errors)
        data = ram.read_bytes()
        resumed, fline, alias = struct.unpack_from(">III", data)
        self.assertGreater(min(resumed, fline), 0)
        self.assertLessEqual(abs(resumed - fline), 1, "PMMU must enter vector 11 and return after the extension")
        self.assertEqual(0x13572468, alias, "A4000 has a 32-bit address map")
        self.assertEqual(bytes([15, 0x96, 0x7f, 10]), data[16:20])
        table = FieldTable(path.read_bytes(), 0, run.fields)
        self.assertEqual(0x3113, table.get("cpu.cacr"))
        table = FieldTable(path.read_bytes(), 2, run.fields)
        self.assertEqual((8192 * 1024, 0x200000), (table.get("ram.fast_size"), table.get("ram.fast_start")))
        self.assertEqual(0x96, table.get("mbres.ramsey_config"))
        self.assertEqual(0, table.get("mbres.gary_coldboot"))
        for mode in (d.SAME, d.NEW):
            with self.subTest(restore=mode):
                self.assertIsNone(self.first_divergence("a4030", [], 3, mode))

    def test_no_fast_ram_still_saves_motherboard_and_empty_ide(self):
        for mode in (d.SAME, d.NEW):
            with self.subTest(restore=mode):
                self.assertIsNone(self.first_divergence("a4030", ["--fast", "4294967295"], 3, mode))

    def test_corrupt_motherboard_register_is_refused_without_mutation(self):
        path = self.dir / "source.state"
        run = self.probe("a4030", "--units", "3", "--save-fields-at", "3", str(path))
        self.assertFalse(run.save_errors)
        table = FieldTable(path.read_bytes(), 2, run.fields)
        bad = self.dir / "bad.state"
        bad.write_bytes(table.with_values({"mbres.gary_coldboot": 2}))
        control = self.probe("a4030", "--units", "7", "--every", "1")
        refused = self.probe("a4030", "--units", "7", "--every", "1", "--public-load-at", "6", str(bad))
        self.assertEqual(1, len(refused.loads))
        self.assertEqual(9, refused.loads[0][1])
        self.assertIn("chunk ram: a field is out of its range", refused.loads[0][2])
        self.assertEqual(control.digests, refused.digests)
        self.assertEqual(control.pcm, refused.pcm)
        self.assertEqual(control.pictures, refused.pictures)

    def test_optional_68882_arithmetic_restores_on_the_68030(self):
        for mode in (d.SAME, d.NEW):
            with self.subTest(restore=mode):
                self.assertIsNone(self.first_divergence("fpu-arith", ["--fpu", "68882"], 3, mode))

    def test_cpu_accuracy_levels_are_rejected_explicitly(self):
        for machine, level in (("a4030", "exact"), ("a4030", "compatible")):
            result = subprocess.run([str(self.exe), str(self.roms["a4030"]), "--machine", machine,
                                     "--cpu-compatibility", level, "--units", "1"], capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("requires normal CPU accuracy", result.stderr)


if __name__ == "__main__":
    unittest.main()
