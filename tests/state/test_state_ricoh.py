"""T55a: real RF5C01A handlers and semantic state, before exposing an A4000 preset.

The diagnostic probe switches the chip after its reset head. This deliberately is not
an A4000 machine claim. The guest writes every reachable alarm/RAM register each IRQ
and polls a battery nibble between IRQs; restore must preserve the chip and its future.
"""
import unittest

from tests.state.harness import FieldTable, ProbeCase

MASK = (0, 0, 15, 7, 15, 3, 7, 15, 3, 0, 1, 3, 0, 0, 0, 0)
RTC = 3


def write(reg, value):
    if value.startswith("D"):
        return f"    .word ${0x13C0 + int(value[1:]):04X},$00DC,${1 + 4 * reg:04X} ; MOVE.B {value},RTC register {reg}\n"
    return f"    MOVE.B {value},${0xDC0001 + 4 * reg:X}.L\n"


PROGRAM = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #irq,$6C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    MOVE.B $DC0001.L,D3
    MOVE.L D3,$2000.L
    BRA.S loop
irq:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D0
    .word $2200                  ; MOVE.L D0,D1
    ADDQ.L #1,D1
""" + write(13, "#1") + "".join(write(i, "#15") for i in range(13)) + write(13, "#2") + "".join(write(i, "D0") for i in range(13)) + write(13, "#3") + "".join(write(i, "D1") for i in range(13)) + write(14, "#11") + write(15, "#5") + write(13, "#2") + "    RTE\n"


class RicohChunkTests(ProbeCase):
    ROMS = {"ricoh": (PROGRAM, None, None)}

    @classmethod
    def probe(cls, rom, *arguments):
        return super().probe(rom, "--poke-at", "1", "rtc-ricoh", *arguments)

    def table(self):
        path = self.dir / "ricoh.state"
        run = self.probe("ricoh", "--units", "3", "--save-fields-at", "3", str(path))
        self.assertFalse(run.save_errors)
        self.assertEqual(1, len(run.saves))
        return FieldTable(path.read_bytes(), RTC, run.fields), path, run

    def test_real_guest_writes_controls_alarms_and_both_ram_nibbles(self):
        table, _, _ = self.table()
        self.assertEqual((2, 11, 5), tuple(table.get("rtc.ricoh_" + n) for n in ("d", "e", "f")))
        self.assertEqual(list(MASK), [table.get(f"rtc.ricoh_alarm[{i}]") for i in range(16)])
        ram = [table.get(f"rtc.ricoh_ram[{i}]") for i in range(16)]
        self.assertGreater(ram[0], 15, "the high battery bank was written")
        self.assertEqual([ram[0]] * 13 + [0, 0, 0], ram)

    def test_restore_same_and_new_process_preserves_all_subsystems_and_future(self):
        _, path, _ = self.table()
        control = self.probe("ricoh", "--units", "8", "--every", "1")
        for loaded in (2, 6):
            with self.subTest(load_at=loaded):
                run = self.probe("ricoh", "--units", str(loaded + 5), "--every", "1", "--load-at", str(loaded), str(path))
                self.assertEqual([(loaded, 0, "")], run.loads)
                self.assertEqual(control.unit(3), run.after[0])
                for j in range(1, 6):
                    self.assertEqual(control.unit(3 + j), run.unit(loaded + j), f"future unit {j}")
                    self.assertEqual(control.pcm[3 + j], run.pcm[loaded + j])
                    self.assertEqual(control.pictures[3 + j], run.pictures[loaded + j])

    def test_every_reachable_register_is_applied(self):
        table, _, _ = self.table()
        values = {"rtc.ricoh_d": 15, "rtc.ricoh_e": 3, "rtc.ricoh_f": 12}
        values.update({f"rtc.ricoh_alarm[{i}]": 0 for i in range(16)})
        values.update({f"rtc.ricoh_ram[{i}]": 255 - table.get(f"rtc.ricoh_ram[{i}]") for i in range(13)})
        altered = self.dir / "altered.state"
        altered.write_bytes(table.with_values(values))
        saved = self.dir / "applied.state"
        run = self.probe("ricoh", "--units", "6", "--load-at", "6", str(altered), "--save-fields-at", "6", str(saved))
        self.assertEqual([(6, 0, "")], run.loads)
        applied = FieldTable(saved.read_bytes(), RTC, run.fields)
        for field, value in values.items():
            self.assertEqual(value, applied.get(field), field)

    def test_corrupt_registers_are_refused_without_mutation_or_future_change(self):
        table, _, _ = self.table()
        invalid = {"rtc.ricoh_d": 16, "rtc.ricoh_e": 16, "rtc.ricoh_f": 16,
                   "rtc.ricoh_alarm[0]": 1, "rtc.ricoh_alarm[3]": 8, "rtc.ricoh_alarm[10]": 2,
                   "rtc.ricoh_alarm[15]": 1, "rtc.ricoh_ram[13]": 1}
        attempts = [(field, table.with_values({field: value}), 9, "state refused: chunk rtc:") for field, value in invalid.items()]
        self.refusals(attempts, rom="ricoh", unit=6, units=8)


if __name__ == "__main__":
    unittest.main()
