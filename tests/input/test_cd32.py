"""T70 CD32 vertical: real serial-pad protocol and native restore through the public boundary.

The MIT micro-ROM clocks the two pads once per VERTB. The expected nine bits are
blue/red/yellow/green/forward/rewind/play/one/zero, active low (CD32 pad protocol),
calculated from the input, never captured from the engine. No commercial assets.
"""
from pathlib import Path
import tempfile
import subprocess
import unittest

from tests.build_support import ROOT, build_targets
from tests.input.test_input import Run as InputRun, line
from tests.state.harness import FieldTable, ProbeCase, parse_chunks, reforge
from tests.step.test_step import builder

CD32 = 3
BUTTONS = (2, 1, 128, 4, 512, 256, 1024)  # wire order, then fixed 1 and 0
ALL = sum(BUTTONS)
ROM = """; Original PUAE6 micro-ROM, MIT. Both pads: P5 selects serial mode, CIA fire is the clock.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #$C3,$BFE201.L
    MOVE.B #$C2,$BFE001.L
    MOVE.W #$2200,$DFF034.L
    MOVE.L #vb,$6C.L
    LEA $1000.W,A0
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
vb:
    MOVE.W #$0020,$DFF09C.L
    MOVE.W $DFF016.L,(A0)+
    MOVE.B #$02,$BFE001.L
    MOVE.B #$C2,$BFE001.L
    RTE
"""


class Cd32MappingTests(unittest.TestCase):
    def test_libretro_maps_all_seven_buttons_in_both_ports_without_changing_abi_v1(self):
        source = r'''
#include "input_map.h"
#include <cassert>
static_assert(sizeof(p6_input) == 72 && sizeof(p6_port_input) == 16 && P6_ABI_VERSION == 1, "ABI v1");
static unsigned held;
int16_t read(unsigned, unsigned device, unsigned, unsigned id) {
    return device == RETRO_DEVICE_JOYPAD && (held & (1u << id)) ? 1 : 0;
}
int main() {
    using namespace p6_libretro;
    const unsigned ids[] = {RETRO_DEVICE_ID_JOYPAD_B, RETRO_DEVICE_ID_JOYPAD_A, RETRO_DEVICE_ID_JOYPAD_X,
        RETRO_DEVICE_ID_JOYPAD_Y, RETRO_DEVICE_ID_JOYPAD_L, RETRO_DEVICE_ID_JOYPAD_R, RETRO_DEVICE_ID_JOYPAD_START};
    const uint32_t bits[] = {P6_CD32_RED, P6_CD32_BLUE, P6_CD32_GREEN, P6_CD32_YELLOW, P6_CD32_REWIND, P6_CD32_FORWARD, P6_CD32_PLAY};
    const unsigned devices[] = {kDeviceCd32, kDeviceCd32};
    for (unsigned i = 0; i < 7; ++i) {
        held = 1u << ids[i]; InputMemory memory{}; InputConfig config{};
        const auto in = read_input(read, devices, config, &memory);
        for (const auto &p : in.ports) assert(p.type == P6_PORT_CD32 && p.buttons == bits[i] && !p.dx && !p.dy);
    }
    assert(device_allowed(0, kDeviceCd32) && device_allowed(1, kDeviceCd32));
    const unsigned shadow[] = {RETRO_DEVICE_MOUSE, kDeviceCd32};
    assert(joystick_shadowed_by_mouse(shadow));
}
'''
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cd32-mapping-", dir=ROOT / "build") as directory:
            path = Path(directory)
            (path / "probe.cpp").write_text(source)
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "adapters/libretro"),
                            "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                            str(path / "probe.cpp"), "-o", str(path / "probe")], check=True, capture_output=True, text=True)
            subprocess.run([str(path / "probe")], check=True)


class Cd32Tests(ProbeCase):
    @classmethod
    def setUpClass(cls):
        products = build_targets(ROOT / "build/cd32", ["p6_step_probe", "p6_state_probe"], trace=True, optimized=True)
        cls.step, cls.exe = products["p6_step_probe"], products["p6_state_probe"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="cd32-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        path = cls.dir / "cd32.rom"
        path.write_bytes(builder.build(ROM))
        cls.roms = {"cd32": path, "vsync": path}

    def ports(self, mask=ALL):
        return ("--port", f"0:3:{mask}:0:0", "--port", f"1:3:{mask}:0:0")

    def test_seven_buttons_and_identification_bits_in_both_ports_pal_and_ntsc(self):
        for standard in ("pal", "ntsc"):
            for mask in (0, ALL, *BUTTONS):
                script = [line(p0=(CD32, mask, 0, 0), p1=(CD32, mask, 0, 0))] * 11
                run = InputRun(self.step, self.roms["cd32"], script, 11, standard=standard)
                words = [word for unit in sorted(run.logged) for word in run.unit_words(unit)]
                self.assertGreaterEqual(len(words), 9)
                expected = [int(not (mask & b)) for b in BUTTONS] + [1, 0]
                for p in (0, 1):
                    self.assertEqual(expected, [(w >> (10 + 4 * p)) & 1 for w in words[:9]], (standard, mask, p))

    def saved(self, unit=3):
        path = self.dir / "source.state"
        run = self.probe("cd32", "--units", "7", "--every", "1", *self.ports(), "--save-fields-at", str(unit), str(path), "--public-save-at", str(unit), str(path))
        self.assertEqual([], run.save_errors)
        return path, run, FieldTable(path.read_bytes(), 9, run.fields)

    def test_partly_read_pad_restores_in_same_and_new_process(self):
        path, control, table = self.saved()
        self.assertIn(table.get("cd32.shifter[0]"), range(1, 8))
        self.assertEqual(1, table.get("cd32.clock[0]"))
        same = self.probe("cd32", "--units", "9", "--every", "1", *self.ports(), "--public-load-at", "6", str(path))
        fresh = self.probe("cd32", "--units", "4", "--every", "1", *self.ports(), "--public-load-at", "0", str(path))
        for run, start in ((same, 6), (fresh, 0)):
            self.assertEqual([(start, 0, "")], run.loads)
            self.assertEqual(control.unit(3), run.after[0])
            for j in range(1, 4):
                self.assertEqual(control.unit(3 + j), run.unit(start + j))
                self.assertEqual(control.pcm[3 + j], run.pcm[start + j])
                self.assertEqual(control.pictures[3 + j], run.pictures[start + j])

    def test_malformed_cd32_tail_is_refused_before_touching_the_machine(self):
        _, _, table = self.saved()
        cut = reforge(table.blob, lambda c: c[9].__setitem__(2, c[9][2][:-1]))
        self.refusals([
            ("cut tail", cut, 9, "the body ends before its last field"),
            ("shifter", table.with_values({"cd32.shifter[0]": 9}), 9, "a field is out of its range"),
            ("clock", table.with_values({"cd32.clock[1]": 2}), 9, "a field is out of its range"),
            ("buttons", table.with_values({"cd32.buttons_extra[0]": 16}), 9, "a field is out of its range"),
        ], rom="cd32", extra=self.ports(), unit=4, units=5)

    def test_device_changes_release_cd32_and_bound_always_covers_both_pads(self):
        states, runs = [], []
        for name, args in (("pad", self.ports()), ("stick", (*self.ports(), "--port-at", "4", "0:1:0:0:0", "--port-at", "4", "1:0:0:0:0"))):
            path = self.dir / f"{name}.state"
            run = self.probe("cd32", "--units", "6", *args, "--save-fields-at", "6", str(path))
            self.assertEqual([], run.save_errors)
            runs.append(run); states.append(path.read_bytes())
        self.assertEqual(runs[0].saves[0][3], runs[1].saves[0][3])
        self.assertEqual(6, len(parse_chunks(states[0])[9][2]) - len(parse_chunks(states[1])[9][2]))
        table = FieldTable(states[1], 9, runs[1].fields)
        self.assertEqual((1, 0), (table.get("applied.type[0]"), table.get("applied.type[1]")))
        self.assertEqual((0, 0), (table.get("joy.button[0]"), table.get("joy.button[1]")))


if __name__ == "__main__":
    unittest.main()
