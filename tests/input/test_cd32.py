"""T70 CD32 vertical: real serial-pad protocol and native restore through the public boundary.

The MIT micro-ROM clocks the two pads once per VERTB. The expected nine bits are
blue/red/yellow/green/forward/rewind/play/one/zero, active low (CD32 pad protocol),
calculated from the input, never captured from the engine. No commercial assets.
"""
from pathlib import Path
import tempfile
import subprocess
import struct
import unittest

from tests.build_support import ROOT, build_targets
from tests.state.harness import FieldTable, parse_chunks, reforge
from tests.state.test_det02 import Det02Case, SAME, NEW
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


class Cd32Tests(Det02Case):
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"

    @classmethod
    def setUpClass(cls):
        # state_probe has no STEP01 trace observers; use its public RAM view, not a trace build shared with step_probe.
        cls.exe = build_targets(ROOT / "build/cd32", ["p6_state_probe"], optimized=True)["p6_state_probe"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="cd32-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)
        path = cls.dir / "cd32.rom"
        path.write_bytes(builder.build(ROM))
        cls.roms = {"cd32": path, "vsync": path}
        directions = cls.dir / "directions.rom"
        directions.write_bytes(builder.build(ROM.replace("MOVE.W $DFF016.L,(A0)+", "MOVE.W $DFF00A.L,(A0)+\n    MOVE.W $DFF00C.L,(A0)+")))
        cls.roms["directions"] = directions
        cls.cache = {}

    def ports(self, mask=ALL):
        return ("--port", f"0:3:{mask}:0:0", "--port", f"1:3:{mask}:0:0")

    def test_seven_buttons_and_identification_bits_in_both_ports_pal_and_ntsc(self):
        for standard in ("pal", "ntsc"):
            for mask in (0, ALL, *BUTTONS):
                path = self.dir / "wire.bin"
                masks = (mask, ALL ^ mask)  # opposite levels also detect crossed/aliased ports.
                flags = ("--ntsc",) if standard == "ntsc" else ()
                self.probe("cd32", "--units", "11", *flags, "--port", f"0:3:{masks[0]}:0:0", "--port", f"1:3:{masks[1]}:0:0",
                           "--ramout-at", "11", "0x1000", "18", str(path))
                words = struct.unpack(">9H", path.read_bytes())
                for p in (0, 1):
                    expected = [int(not (masks[p] & b)) for b in BUTTONS] + [1, 0]
                    self.assertEqual(expected, [(w >> (10 + 4 * p)) & 1 for w in words], (standard, mask, p))

    def saved(self, unit=3):
        path = self.dir / "source.state"
        run = self.probe("cd32", "--units", "7", "--every", "1", *self.ports(), "--save-fields-at", str(unit), str(path), "--public-save-at", str(unit), str(path))
        self.assertEqual([], run.save_errors)
        return path, run, FieldTable(path.read_bytes(), 9, run.fields)

    def test_dpad_quadrature_and_opposite_directions_are_independent_in_both_ports(self):
        def buttons(x, y):
            return {0: 0, -1: 32, 1: 64, 2: 96}[x] | {0: 0, -1: 8, 1: 16, 2: 24}[y]

        def quadrature(x, y):
            left, right, up, down = x == -1, x == 1, y == -1, y == 1
            return int(down ^ right) | (int(right) << 1) | (int(up ^ left) << 8) | (int(left) << 9)

        for x, y in [(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)] + [(2, 2)]:
            path = self.dir / "directions.bin"
            opposite = (2 if x == 2 else -x, 2 if y == 2 else -y)
            masks = (buttons(x, y), buttons(*opposite))
            self.probe("directions", "--units", "4", "--port", f"0:3:{masks[0]}:0:0", "--port", f"1:3:{masks[1]}:0:0", "--ramout-at", "4", "0x1000", "4", str(path))
            # JOYxDAT quadrature encoding from the Amiga hardware contract, not an engine capture.
            self.assertEqual((quadrature(x, y), quadrature(*opposite)), tuple(w & 0x0303 for w in struct.unpack(">2H", path.read_bytes())))

    def test_partly_read_pad_restores_in_same_and_new_process(self):
        _, _, table = self.saved()
        self.assertIn(table.get("cd32.shifter[0]"), range(1, 8))
        self.assertEqual(1, table.get("cd32.clock[0]"))
        # Existing DET02 oracle: custom_storage's last-write scratch is excluded, every causal group and A/V is compared.
        # This ROM's first scratch difference is POTGO ($034), canonical offset97: $2200 versus0; live potgo_value agrees.
        for mode in (SAME, NEW):
            with self.subTest(mode=mode):
                self.assertIsNone(self.first_divergence("cd32", list(self.ports()), 3, mode))

    def test_malformed_cd32_tail_is_refused_before_touching_the_machine(self):
        _, _, table = self.saved()
        cut = reforge(table.blob, lambda c: c[9].__setitem__(2, c[9][2][:-1]))
        self.refusals([
            ("cut tail", cut, 9, "the body ends before its last field"),
            ("shifter", table.with_values({"cd32.shifter[0]": 9}), 9, "a field is out of its range"),
            ("clock", table.with_values({"cd32.clock[1]": 2}), 9, "a field is out of its range"),
            ("buttons", table.with_values({"cd32.buttons_extra[0]": 16}), 9, "a field is out of its range"),
            ("ordinary button", table.with_values({"joy.button[0]": 1}), 9, "CD32 has no ordinary joystick button bits"),
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
