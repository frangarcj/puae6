"""T70d: four frontend players share the real frame boundary and native restore."""
from pathlib import Path
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from test_input import packets
from tests.build_support import build_targets
from tests.input.test_parallel import ROM


class ParallelMappingTests(unittest.TestCase):
    def test_extra_players_use_the_v2_tail_and_only_the_adapter_controls(self):
        source = r'''
#include "input_map.h"
#include <cassert>
using namespace p6_libretro;
static_assert(P6_ABI_VERSION == 1 && sizeof(p6_input) == 72 && sizeof(p6_input_v2) == 108, "ABI versions");
static unsigned masks[4];
int16_t read(unsigned port, unsigned device, unsigned, unsigned id) {
    return device == RETRO_DEVICE_JOYPAD && (masks[port] & (1u << id));
}
int main() {
    const unsigned devices[] = {kDeviceJoystickOnly, RETRO_DEVICE_JOYPAD, RETRO_DEVICE_JOYPAD, RETRO_DEVICE_JOYPAD};
    masks[0] = 1u << RETRO_DEVICE_ID_JOYPAD_UP; masks[1] = 1u << RETRO_DEVICE_ID_JOYPAD_DOWN;
    masks[2] = (1u << RETRO_DEVICE_ID_JOYPAD_B) | (1u << RETRO_DEVICE_ID_JOYPAD_LEFT) | (1u << RETRO_DEVICE_ID_JOYPAD_A);
    masks[3] = (1u << RETRO_DEVICE_ID_JOYPAD_RIGHT) | (1u << RETRO_DEVICE_ID_JOYPAD_X);
    InputMemory memory; InputConfig config;
    auto in = read_input_v2(read, devices, config, &memory);
    assert(in.base.abi_version == 2 && in.base.struct_size == 108 && !in.commands);
    assert(in.base.ports[0].buttons == P6_BUTTON_DOWN && in.base.ports[1].buttons == P6_BUTTON_UP);
    assert(in.parallel[0].buttons == (P6_BUTTON_1 | P6_BUTTON_LEFT));
    assert(in.parallel[1].buttons == P6_BUTTON_RIGHT);
    assert(device_allowed(2, RETRO_DEVICE_JOYPAD) && device_allowed(3, RETRO_DEVICE_NONE));
    assert(!device_allowed(2, kDeviceCd32) && !device_allowed(3, RETRO_DEVICE_MOUSE) && !device_allowed(4, 1));
    const unsigned off[] = {kDeviceJoystickOnly, RETRO_DEVICE_JOYPAD, RETRO_DEVICE_NONE, RETRO_DEVICE_NONE};
    in = read_input_v2(read, off, config, &memory);
    assert(in.base.abi_version == 1 && in.base.struct_size == 72 && !in.parallel[0].type && !in.parallel[1].type);
}
'''
        with tempfile.TemporaryDirectory(prefix="parallel-map-", dir=lr.ROOT / "build") as directory:
            path = Path(directory); (path / "probe.cpp").write_text(source)
            result = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                *["-I" + str(lr.ROOT / p) for p in ("adapters/libretro", "bridge/include", "references/libretro-api/include")], str(path / "probe.cpp"), "-o", str(path / "probe")], text=True, capture_output=True, timeout=120)
            self.assertEqual(0, result.returncode, result.stderr)
            result = subprocess.run([str(path / "probe")], text=True, capture_output=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(0, result.returncode, result.stderr)


class ParallelFrontendTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        products = build_targets(lr.ROOT / "build/cd32", ["p6_libretro_host", "puae6_libretro"], optimized=True)
        cls.host, cls.core = products["p6_libretro_host"], products["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="parallel-lr-", dir=lr.ROOT / "build")
        path = Path(cls.tmp.name) / "parallel.rom"; path.write_bytes(lr.builder.build(ROM))
        cls.roms, cls.counter = {"parallel": path}, 0

    def test_four_players_pins_and_restore_are_identical_in_pal_and_ntsc(self):
        for standard in ("PAL", "NTSC"):
            flags = ["--device", "0:257", "--device", "1:1", "--device", "2:1", "--device", "3:1",
                     "--joy", "0:51", "--joy", "1:a1", "--joy", "2:51", "--joy", "3:a1", "--set", f"puae6_video_standard={standard}"]
            plain = self.frontend("parallel", "--runs", 11, *flags, "--do", "11:peek:0x1000:120").stdout
            replay = self.frontend("parallel", "--runs", 19, *flags, "--do", "3:save:s", "--do", "11:load:s", "--do", "19:peek:0x1000:120").stdout
            p = packets(plain); self.assertEqual(11, len(p))
            self.assertTrue(all(v[:8] == (1, 81, 0, 0, 1, 41, 0, 0) for v in p))
            self.assertEqual(11, len(re.findall(r"parallel unit=\d+ ports=1:29,1:51", plain)))
            first, again = self.runs(plain), self.runs(replay)
            view = lambda r: (r["unit"], r["ticks"], r["hash"], r["ahash"], r["audio"], r["polls"])
            self.assertEqual([view(r) for r in first], [view(r) for r in again[:11]])
            self.assertEqual([view(r) for r in first[3:]], [view(r) for r in again[11:]])
            ram = re.search(r"^peek 0x1000 120 (\w+)$", plain, re.M)[1]
            self.assertEqual(ram, re.search(r"^peek 0x1000 120 (\w+)$", replay, re.M)[1])
            words = struct.unpack(">60H", bytes.fromhex(ram))[-5:]
            self.assertEqual((0x5a, 2), (words[3] & 255, words[4] & 7))
            self.assertTrue(all(r["polls"] == 1 for r in again))
        refused = self.frontend("parallel", "--runs", 2, "--device", "2:4", "--device", "3:2").stdout
        self.assertIn("device 4 is not available in port 2", refused); self.assertIn("device 2 is not available in port 3", refused)


if __name__ == "__main__": unittest.main()
