"""T70c: optical frontend coordinates, viewport history and public native restore."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from test_input import packets
from tests.build_support import build_targets
from tests.input.test_pointer import ROM


class PointerMappingTests(unittest.TestCase):
    def test_absolute_levels_crop_mapping_and_causal_history_round_trip_in_state_v8(self):
        source = r'''
#include "state_frame.h"
#include <cassert>
using namespace p6_libretro;
static_assert(P6_ABI_VERSION == 1 && sizeof(p6_input) == 72 && kStateVersion == 8, "versions");
static bool pressed = true, outside = false;
int16_t read(unsigned, unsigned device, unsigned, unsigned id) {
    if (device == RETRO_DEVICE_LIGHTGUN) {
        if (id == RETRO_DEVICE_ID_LIGHTGUN_SCREEN_X) return 0;
        if (id == RETRO_DEVICE_ID_LIGHTGUN_SCREEN_Y) return 0;
        if (id == RETRO_DEVICE_ID_LIGHTGUN_TRIGGER) return pressed;
        if (id == RETRO_DEVICE_ID_LIGHTGUN_IS_OFFSCREEN) return outside;
    }
    if (device == RETRO_DEVICE_POINTER && id == RETRO_DEVICE_ID_POINTER_PRESSED) return pressed;
    return 0;
}
int main() {
    bool out = false;
    assert(pointer_axis(-32767, 20, 720, 760, &out) == -31041 && !out);
    assert(pointer_axis(0, 20, 720, 760, &out) == 0 && !out);
    assert(pointer_axis(32767, 20, 720, 760, &out) == 31040 && !out);
    pointer_axis(-32767, -20, 720, 760, &out); assert(out);
    InputMemory memory; InputConfig config;
    const unsigned gun[] = {RETRO_DEVICE_LIGHTGUN, RETRO_DEVICE_JOYPAD};
    auto a = read_input(read, gun, config, &memory);
    assert(a.ports[1].type == P6_PORT_LIGHTGUN && a.ports[1].buttons == 1);
    outside = true; a = read_input(read, gun, config, &memory); assert(a.ports[1].buttons == 3);
    pressed = false; a = read_input(read, gun, config, &memory); assert(a.ports[1].buttons == 2);
    const unsigned pen[] = {RETRO_DEVICE_JOYPAD, RETRO_DEVICE_POINTER};
    outside = false; a = read_input(read, pen, config, &memory); assert(a.ports[0].type == P6_PORT_LIGHTPEN && a.ports[0].buttons == 2);
    pressed = true; a = read_input(read, pen, config, &memory); assert(a.ports[0].buttons == 1);
    Section s; s.input.pointer_view_x = 20; s.input.pointer_view_y = 30;
    s.input.pointer_view_w = 720; s.input.pointer_view_h = 500; s.input.pointer_raster_w = 760; s.input.pointer_raster_h = 576;
    s.pointer_crop.have = true; s.pointer_crop.stable = 17;
    s.pointer_crop.current = {10, 20, 100, 200}; s.pointer_crop.candidate = {11, 21, 101, 201};
    std::vector<uint8_t> bytes; encode_section(s, &bytes); Section back; const char *why = nullptr;
    assert(decode_section(bytes.data(), bytes.size(), 0, 760, 576, &back, &why));
    assert(back.input == s.input && back.pointer_crop.have && back.pointer_crop.stable == 17);
    assert(back.pointer_crop.current == s.pointer_crop.current && back.pointer_crop.candidate == s.pointer_crop.candidate);
    s.pointer_crop.stable = kCropDelayUnits; encode_section(s, &bytes);
    assert(!decode_section(bytes.data(), bytes.size(), 0, 760, 576, &back, &why));
    s.pointer_crop.stable = 0; s.input.pointer_view_w = 0; encode_section(s, &bytes);
    assert(!decode_section(bytes.data(), bytes.size(), 0, 760, 576, &back, &why));
}
'''
        (lr.ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pointer-map-", dir=lr.ROOT / "build") as directory:
            path = Path(directory); (path / "probe.cpp").write_text(source)
            built = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    *["-I" + str(lr.ROOT / name) for name in ("adapters/libretro", "bridge/include", "references/libretro-api/include", "ports/portable/media")],
                                    str(path / "probe.cpp"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, built.returncode, built.stderr)
            out = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(0, out.returncode, out.stderr)


class PointerFrontendTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        products = build_targets(lr.ROOT / "build/cd32", ["p6_libretro_host", "puae6_libretro"], optimized=True)
        cls.host, cls.core = products["p6_libretro_host"], products["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="pointer-lr-", dir=lr.ROOT / "build")
        path = Path(cls.tmp.name) / "pointer.rom"; path.write_bytes(lr.builder.build(ROM))
        cls.roms, cls.counter = {"pointer": path}, 0

    def test_gun_and_pen_with_cropped_viewport_restore_beam_results_and_one_poll_per_unit(self):
        for standard in ("PAL", "NTSC"):
            for port, device in ((0, 4), (1, 6)):
                with self.subTest(standard=standard, port=port):
                    flags = ("--device", f"{port}:{device}", "--pointer", f"{port}:8000,-6000,1", "--set", "puae6_video_crop=minimum",
                             "--set", f"puae6_video_standard={standard}")
                    plain = self.frontend("pointer", "--runs", 11, *flags, "--do", "11:peek:0x1000:60").stdout
                    p = packets(plain); self.assertEqual(11, len(p))
                    offset = 4 * (1 - port)
                    self.assertTrue(all((v[offset], v[offset + 1]) == (4 if device == 4 else 5, 1) for v in p))
                    self.assertNotEqual(p[0][offset + 2:offset + 4], p[-1][offset + 2:offset + 4], "crop transforms the frontend's viewport back into the emulated raster")
                    replay = self.frontend("pointer", "--runs", 19, *flags, "--do", "3:save:s", "--do", "11:load:s", "--do", "19:peek:0x1000:60").stdout
                    first, again = self.runs(plain), self.runs(replay)
                    view = lambda r: (r["unit"], r["ticks"], r["hash"], r["ahash"], r["audio"], r["polls"])
                    self.assertEqual([view(r) for r in first], [view(r) for r in again[:11]])
                    self.assertEqual([view(r) for r in first[3:]], [view(r) for r in again[11:]])
                    self.assertEqual([v[:8] for v in p[3:]], [v[:8] for v in packets(replay)[11:]])
                    self.assertEqual(re.search(r"^peek 0x1000 60 (\w+)$", plain, re.M)[1], re.search(r"^peek 0x1000 60 (\w+)$", replay, re.M)[1])
                    self.assertTrue(all(r["polls"] == 1 for r in again))
        refused = self.frontend("pointer", "--runs", 2, "--device", "0:4", "--device", "1:6").stdout
        self.assertIn("only one optical sensor is supported", refused)


if __name__ == "__main__":
    unittest.main()
