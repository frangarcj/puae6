"""T70d: WinUAE's real four-player parallel adapter, ABI1/2 and causal restore."""
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

from tests.build_support import ROOT, build_targets
from tests.state.harness import FieldTable, parse_chunks, reforge
from tests.state.test_det02 import Det02Case, SAME, NEW
from tests.step.test_step import builder

ROM = """; Original PUAE6 micro-ROM, MIT. CIA-A PRB directions and CIA-B PRA fire.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #0,$BFE301.L
    MOVE.B #0,$BFD200.L
    MOVE.L #vb,$6C.L
    LEA $1000.W,A0
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
vb:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W $DFF00A.L,(A0)+
    MOVE.W $DFF00C.L,(A0)+
    MOVE.B $BFE001.L,D1
    MOVE.W D1,(A0)+
    MOVE.B $BFE101.L,D1
    MOVE.W D1,(A0)+
    MOVE.B $BFD000.L,D1
    MOVE.W D1,(A0)+
    RTE
"""


def extra_ports(a, b):
    return ["--port", f"2:1:{a}:0:0", "--port", f"3:1:{b}:0:0"]


def pins(buttons):
    # Hardware adapter: nibble up/down/left/right. Opposites are neutral.
    up, down, left, right = (bool(buttons & bit) for bit in (8, 16, 32, 64))
    return 15 ^ ((1 if up and not down else 2 if down and not up else 0) | (4 if left and not right else 8 if right and not left else 0))


class ParallelTests(Det02Case):
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def setUpClass(cls):
        cls.exe = build_targets(ROOT / "build/cd32", ["p6_state_probe"], optimized=True)["p6_state_probe"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="parallel-", dir=ROOT / "build"); cls.dir = Path(cls.tmp.name)
        path = cls.dir / "parallel.rom"; path.write_bytes(builder.build(ROM))
        cls.roms = {"parallel": path, "vsync": path}; cls.cache = {}

    def test_independent_parallel_direction_and_fire_pins_pal_ntsc_and_v1_disconnect(self):
        for standard in ([], ["--ntsc"]):
            for a, b in ((9, 80), (33, 65), (121, 0), (0, 1)):
                path = self.dir / "pins.bin"
                run = self.probe("parallel", "--units", "7", *standard, *extra_ports(a, b), "--ramout-at", "7", "0x1000", "80", str(path))
                self.assertEqual([], run.save_errors)
                words = struct.unpack(">40H", path.read_bytes())[-5:]
                self.assertEqual(pins(a) | (pins(b) << 4), words[3] & 255)
                self.assertEqual((0 if a & 1 else 4) | (0 if b & 1 else 1) | 2, words[4] & 7)
        state = self.dir / "released.state"; ram = self.dir / "released.bin"
        run = self.probe("parallel", "--units", "7", *extra_ports(9, 65), "--input-v1-at", "4", "--save-fields-at", "7", str(state),
                         "--ramout-at", "7", "0x1000", "80", str(ram), "--input-check-at", "7")
        self.assertEqual([], run.save_errors)
        table = FieldTable(state.read_bytes(), 9, run.fields)
        self.assertEqual([0, 0, 0, 0], [table.get(f"parallel.{field}[{p}]") for field in ("type", "buttons") for p in range(2)])
        words = struct.unpack(">40H", ram.read_bytes())[-5:]
        self.assertEqual((255, 7), (words[3] & 255, words[4] & 7))
        raw = subprocess.run([str(self.exe), str(self.roms["parallel"]), "--units", "2", *extra_ports(9, 65), "--input-check-at", "2"], text=True, capture_output=True, check=True).stdout
        self.assertEqual(10, len(re.findall(r"inputcheck \d+ result=1 unchanged=1 canary=1", raw)))
        polls = re.findall(r"parallel-poll \d+ mask=(\d+) lag=(\d+)", raw)
        self.assertEqual(2, len(polls))
        self.assertTrue(all(int(mask) & 384 == 384 and lag == "0" for mask, lag in polls))

    def test_parallel_levels_and_device_changes_restore_in_same_and_new_process(self):
        for args in (extra_ports(9, 65), [*extra_ports(33, 17), "--port-at", "4", "2:0:0:0:0", "--port-at", "6", "3:0:0:0:0"]):
            for mode in (SAME, NEW): self.assertIsNone(self.first_divergence("parallel", args, 5, mode))

    def test_truncated_parallel_tail_and_invalid_types_or_buttons_are_transactionally_refused(self):
        state = self.dir / "parallel.state"
        run = self.probe("parallel", "--units", "5", *extra_ports(9, 65), "--save-fields-at", "5", str(state))
        self.assertEqual([], run.save_errors)
        blob = state.read_bytes(); table = FieldTable(blob, 9, run.fields)
        cut = reforge(blob, lambda chunks: chunks[9].__setitem__(2, chunks[9][2][:-1]))
        self.refusals([("cut parallel tail", cut, 9, "the body ends before its last field"),
                       ("parallel mouse", table.with_values({"parallel.type[0]": 2}), 9, "a field is out of its range"),
                       ("parallel second button", table.with_values({"parallel.buttons[0]": 2}), 9, "parallel port has unknown buttons"),
                       ("absent parallel device", table.with_values({"parallel.type[0]": 0}), 9, "parallel port has unknown buttons")],
                      rom="parallel", extra=extra_ports(9, 65), unit=4, units=5)


if __name__ == "__main__": unittest.main()
