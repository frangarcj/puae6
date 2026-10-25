"""Soft reset executes the upstream reset head without the power-on RAM clear."""
from tests.state.harness import FieldTable, ProbeCase

# Original MIT fixture. ADDQ.L #1,(A0) (5290) counts entries into the ROM in RAM.
# $900 is a zero-filled OCS power-on bank (memory.cpp fillpattern); $800 is FFFFFFFF.
ROM = """
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    LEA $900.W,A0
    .word $5290
loop:
    MOVE.W #$0F00,$DFF180.L
    BRA.S loop
"""


class SoftResetTests(ProbeCase):
    ROMS = {"reset": (ROM, None, None)}

    def counter(self, *options):
        path = self.dir / 'counter.bin'
        run = self.probe('reset', '--units', '6', '--every', '1', *options,
                         '--ramout-at', '6', '0x900', '4', str(path))
        return int.from_bytes(path.read_bytes(), 'big'), run

    def test_soft_keeps_ram_hard_clears_it_and_hard_takes_priority(self):
        for options, expected in (
            ((), 1), (('--soft-reset-at', '0'), 1),
            (('--reset-at', '2'), 1), (('--soft-reset-at', '2'), 2),
            (('--soft-reset-at', '2', '--soft-reset-at', '3'), 3),
            (('--reset-at', '2', '--soft-reset-at', '2'), 1),
            (('--soft-reset-at', '2', '--reset-at', '2'), 1),
        ):
            with self.subTest(options=options):
                self.assertEqual(expected, self.counter(*options)[0])

    def test_pending_soft_reset_roundtrips_and_replays_in_a_new_process(self):
        path = self.dir / 'pending.state'
        first = self.probe('reset', '--units', '6', '--every', '1', '--soft-reset-at', '2',
                           '--save-fields-at', '2', str(path), '--public-save-at', '2', str(path))
        self.assertEqual(2, FieldTable(path.read_bytes(), 10, first.fields).get('bridge.reset_queued'))
        restored = self.probe('reset', '--units', '6', '--every', '1', '--public-load-at', '2', str(path))
        self.assertEqual([(2, 0, '')], restored.loads)
        for unit in range(3, 7):
            self.assertEqual(first.unit(unit), restored.unit(unit))
            self.assertEqual(first.pcm[unit], restored.pcm[unit])
            self.assertEqual(first.pictures[unit], restored.pictures[unit])
        repeated = self.probe('reset', '--units', '6', '--every', '1', '--soft-reset-at', '2')
        self.assertEqual(first.digests, repeated.digests)
        self.assertEqual(first.pcm, repeated.pcm)
        self.assertEqual(first.pictures, repeated.pictures)
