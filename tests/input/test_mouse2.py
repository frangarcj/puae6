"""T70b: two independent mice on the real engine, with unchanged ABI/P6IN layout."""
from pathlib import Path
import struct
import tempfile
import unittest

from tests.build_support import ROOT, build_targets
from tests.state.harness import FieldTable, parse_chunks
from tests.state.test_det02 import Det02Case, SAME, NEW
from tests.step.test_step import builder

ROM = """; Original PUAE6 micro-ROM, MIT. Both mouse counters and all button pins.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.W #0,$DFF034.L
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
    MOVE.W $DFF016.L,(A0)+
    MOVEQ #0,D2
    MOVE.B $BFE001.L,D2
    MOVE.W D2,(A0)+
    RTE
"""


class Mouse2Tests(Det02Case):
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def setUpClass(cls):
        cls.exe = build_targets(ROOT / "build/cd32", ["p6_state_probe"], optimized=True)["p6_state_probe"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="mouse2-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        path = cls.dir / "mice.rom"
        path.write_bytes(builder.build(ROM))
        cls.roms, cls.cache = {"mice": path, "vsync": path}, {}

    def mice(self):
        return ("--port", "0:2:1:5:-3", "--port", "1:2:6:-7:2")

    def test_independent_motion_and_three_buttons_reach_both_ports_pal_and_ntsc(self):
        for standard in ((), ("--ntsc",)):
            path = self.dir / "registers.bin"
            self.probe("mice", "--units", "8", *standard, "--port", "0:2:0:0:0", "--port", "1:2:0:0:0",
                       "--port-at", "4", "0:2:1:5:-3", "--port-at", "4", "1:2:6:-7:2", "--ramout-at", "8", "0x1000", "56", str(path))
            words = struct.unpack(">28H", path.read_bytes())
            for u in range(4, 9):
                joy0, joy1, pots, fire = words[(u - 2) * 4:(u - 1) * 4]
                n = u - 3
                self.assertEqual((((-3 * n) & 255) << 8) | ((5 * n) & 255), joy0)
                self.assertEqual((((2 * n) & 255) << 8) | ((-7 * n) & 255), joy1)
                self.assertEqual(0x0500, pots & 0x5500)
                self.assertEqual(0x80, fire & 0xc0)

    def test_motion_in_flight_restores_in_same_and_new_process(self):
        for mode in (SAME, NEW):
            self.assertIsNone(self.first_divergence("mice", [*self.mice(), "--key", "3", "0x20", "down"], 5, mode))

    def test_second_mouse_buttons_are_checked_and_device_changes_release_pending_motion(self):
        states = []
        for tag, extra in (("mice", ()), ("released", ("--port-at", "4", "1:0:0:0:0"))):
            path = self.dir / f"{tag}.state"
            run = self.probe("mice", "--units", "5", *self.mice(), *extra, "--save-fields-at", "5", str(path))
            self.assertEqual([], run.save_errors)
            states.append((path.read_bytes(), FieldTable(path.read_bytes(), 9, run.fields)))
        self.assertEqual(len(parse_chunks(states[0][0])[9][2]), len(parse_chunks(states[1][0])[9][2]))
        attached, released = states[0][1], states[1][1]
        self.assertEqual((2, 6, 6), tuple(attached.get(n) for n in ("applied.type[1]", "applied.buttons[1]", "joy.button[1]")))
        self.assertEqual((0, 0, 0, 0), tuple(released.get(n) for n in ("applied.type[1]", "joy.button[1]", "mouse.delta[4]", "mouse.delta[5]")))
        self.refusals([("buttons disagree", attached.with_values({"joy.button[1]": 0}), 9, "the second mouse buttons and the bridge disagree"),
                       ("direction on a mouse", attached.with_values({"joy.dir[1]": 1}), 9, "the second mouse has a joystick direction")],
                      rom="mice", extra=self.mice(), unit=5, units=6)


if __name__ == "__main__":
    unittest.main()
