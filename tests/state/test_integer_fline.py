"""T55c: integer F-line dispatch on 040/060 without an MMU (original MIT fixtures)."""
import struct
from tests.state import test_det02 as d
from tests.state.harness import FieldTable

PRELUDE = """entry:
    MOVE.W #$2700,SR
    LEA $7000.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #fline,$2C.L
    MOVEQ #0,D7
"""
# table68k: 1111 0100 pp{001,010,011,101,110,111}rrr, pp=3 (BC), rrr=0.
CACHE_OPS = (0xF4D8, 0xF4C8, 0xF4D0, 0xF4E8, 0xF4F0, 0xF4F8)
CACHE = PRELUDE + "    LEA $3000.W,A0\n" + "".join(f"    .word ${op:04X}\n" for op in CACHE_OPS) + """
    MOVE.L D7,$2000.L
    MOVE.L #$12345678,$2004.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
fline:
    ADDQ.L #1,D7
    .word $54AF,$0002           ; ADDQ.L #2,2(A7): skip one cache opcode
    RTE
"""
MOVE16 = PRELUDE + """    MOVE.L #$11223344,$3000.L
    MOVE.L #$55667788,$3004.L
    MOVE.L #$99AABBCC,$3008.L
    MOVE.L #$DDEEFF00,$300C.L
    LEA $3000.W,A0
    LEA $3100.W,A1
    .word $F620,$9000           ; MOVE16 (A0)+,(A1)+
    .word $23C8,$0000,$2000    ; MOVE.L A0,$2000
    .word $23C9,$0000,$2004    ; MOVE.L A1,$2004
    MOVE.L D7,$2008.L
loop:
    BRA.S loop
fline:
    ADDQ.L #1,D7
    .word $58AF,$0002           ; ADDQ.L #4,2(A7)
    RTE
"""
ABSENT = PRELUDE + """    LEA $3000.W,A0
    .word $F010,$4000           ; PMOVE (A0),TC: no PMMU
    .word $F518,$4E71           ; PFLUSHA ; NOP: no integrated MMU
    .word $F200,$4000           ; FMOVE.L D0,FP0: depends on the explicit FPU
    MOVE.L D7,$2000.L
loop:
    BRA.S loop
fline:
    ADDQ.L #1,D7
    .word $58AF,$0002           ; default skip four bytes
    RTE
"""
LPSTOP = PRELUDE + """    MOVE.L #irq,$6C.L
    MOVE.W #$C020,$DFF09A.L     ; real VERTB level-3 interrupt
loop:
    .word $F800,$01C0,$2000     ; LPSTOP #$2000, supervisor, interrupts enabled
    ADDQ.L #1,D0
    MOVE.L D0,$2004.L
    BRA.S loop
irq:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    MOVE.L D1,$2000.L
    RTE
fline:
    ADDQ.L #1,D7
    .word $5CAF,$0002           ; skip all six bytes if dispatch is broken
    RTE
"""


class IntegerFlineTests(d.Det02Case):
    MACHINE = 'a4040'
    SAVE_OP, LOAD_OP = '--public-save-at', '--public-load-at'
    ROMS = {name: (source, None, None) for name, source in
            (('cache', CACHE), ('move16', MOVE16), ('absent', ABSENT), ('lpstop', LPSTOP))}

    @classmethod
    def probe_on(cls, machine, rom, *args):
        return super().probe_on(machine, rom, *args, '--cpu-compatibility', 'normal')

    def test_cache_families_and_move16_execute_on_040_and_060(self):
        for cpu in ('68040', '68060'):
            with self.subTest(cpu=cpu):
                flags = ['--cpu', cpu]
                ram = self.dir / (cpu + '.ram')
                self.probe('cache', *flags, '--units', '3', '--ramout-at', '3', '0x2000', '8', str(ram))
                self.assertEqual((0, 0x12345678), struct.unpack('>II', ram.read_bytes()))
                self.probe('move16', *flags, '--units', '3', '--ramout-at', '3', '0x2000', '12', str(ram))
                self.assertEqual((0x3010, 0x3110, 0), struct.unpack('>III', ram.read_bytes()))
                self.probe('move16', *flags, '--units', '3', '--ramout-at', '3', '0x3100', '16', str(ram))
                self.assertEqual(bytes.fromhex('112233445566778899aabbccddeeff00'), ram.read_bytes())
                for rom in ('cache', 'move16'):
                    for mode in (d.SAME, d.NEW):
                        self.assertIsNone(self.first_divergence(rom, flags, 3, mode))

    def test_pmmu_and_absent_fpu_still_raise_fline(self):
        for cpu in ('68040', '68060'):
            for fpu in ('0', cpu):
                with self.subTest(cpu=cpu, fpu=fpu):
                    ram = self.dir / 'absent.ram'
                    self.probe('absent', '--cpu', cpu, '--fpu', fpu, '--units', '3', '--ramout-at', '3', '0x2000', '4', str(ram))
                    self.assertEqual(3 if fpu == '0' else 2, struct.unpack('>I', ram.read_bytes())[0])

    def test_lpstop060_wakes_from_vertb_and_restores_while_stopped(self):
        ram, fields = self.dir / 'lpstop.ram', self.dir / 'lpstop.fields'
        run = self.probe('lpstop', '--cpu', '68060', '--units', '4', '--ramout-at', '4', '0x2000', '8', str(ram),
                         '--save-fields-at', '4', str(fields))
        interrupts, resumed = struct.unpack('>II', ram.read_bytes())
        self.assertGreater(resumed, 0)
        self.assertLessEqual(abs(interrupts-resumed), 1)
        self.assertEqual(2, FieldTable(fields.read_bytes(), 0, run.fields).get('cpu.stopped'))
        for mode in (d.SAME, d.NEW):
            self.assertIsNone(self.first_divergence('lpstop', ['--cpu', '68060'], 4, mode))
