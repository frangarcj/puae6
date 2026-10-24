"""T70b: independent frontend mice, causal adapter memory, and existing stick mouse."""
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
from tests.input.test_mouse2 import ROM


class Mouse2MappingTests(unittest.TestCase):
    def test_two_mice_and_stick_fraction_restore_and_validate_without_changing_abi(self):
        source = r'''
#include "state_frame.h"
#include <cassert>
using namespace p6_libretro;
static_assert(P6_ABI_VERSION == 1 && sizeof(p6_input) == 72, "versions");
static bool stick;
int16_t read(unsigned port, unsigned device, unsigned, unsigned id) {
    if (device == RETRO_DEVICE_ANALOG && !port && stick) return id ? 9000 : 12000;
    if (device != RETRO_DEVICE_MOUSE) return 0;
    switch(id) {
    case RETRO_DEVICE_ID_MOUSE_X: return port ? -3 : 1;
    case RETRO_DEVICE_ID_MOUSE_Y: return port ? 1 : 3;
    case RETRO_DEVICE_ID_MOUSE_LEFT: return !port;
    case RETRO_DEVICE_ID_MOUSE_RIGHT: case RETRO_DEVICE_ID_MOUSE_MIDDLE: return port;
    default: return 0;
    }
}
int main() {
    const unsigned devices[] = {RETRO_DEVICE_MOUSE, RETRO_DEVICE_MOUSE};
    InputConfig config; config.mouse_speed = 50;
    InputMemory memory;
    auto first = read_input(read, devices, config, &memory);
    assert(first.ports[0].type == P6_PORT_MOUSE && first.ports[1].type == P6_PORT_MOUSE);
    assert(first.ports[0].buttons == 1 && first.ports[1].buttons == 6);
    assert(first.ports[0].dx == 0 && first.ports[0].dy == 1 && first.ports[1].dx == -1 && first.ports[1].dy == 0);
    Section section; section.input = memory;
    std::vector<uint8_t> bytes; encode_section(section, &bytes);
    Section restored; const char *why = nullptr;
    assert(decode_section(bytes.data(), bytes.size(), 0, 760, 576, &restored, &why));
    assert(restored.input == memory);
    assert(!decode_section(bytes.data(), bytes.size() - 1, 0, 760, 576, &restored, &why));
    assert(decode_section(bytes.data(), bytes.size(), 0, 760, 576, &restored, &why));
    auto second = read_input(read, devices, config, &restored.input);
    assert(second.ports[0].dx == 1 && second.ports[0].dy == 2 && second.ports[1].dx == -2 && second.ports[1].dy == 1);
    for (int bad = 0; bad < 3; ++bad) {
        auto invalid = section;
        if (bad == 0) invalid.input.mouse2_rx = kMouseBacklog + 1;
        if (bad == 1) invalid.input.mouse2_ry = -kMouseBacklog - 1;
        if (bad == 2) invalid.input.host2_frac[1] = 100;
        encode_section(invalid, &bytes);
        assert(!decode_section(bytes.data(), bytes.size(), 0, 760, 576, &restored, &why));
    }
    config.analog_mouse = 1; stick = true;
    const unsigned with_stick[] = {RETRO_DEVICE_JOYPAD, RETRO_DEVICE_MOUSE};
    read_input(read, with_stick, config, &memory);
    assert(memory.stick_frac[0][0] || memory.stick_frac[0][1]);
    section.input = memory; encode_section(section, &bytes);
    assert(decode_section(bytes.data(), bytes.size(), 0, 760, 576, &restored, &why));
    for (int i = 0; i < 8; ++i) {
        auto a = read_input(read, with_stick, config, &memory);
        auto b = read_input(read, with_stick, config, &restored.input);
        for (int p = 0; p < 2; ++p) assert(a.ports[p].dx == b.ports[p].dx && a.ports[p].dy == b.ports[p].dy);
        assert(memory == restored.input);
    }
    const unsigned removed[] = {RETRO_DEVICE_MOUSE, RETRO_DEVICE_NONE};
    read_input(read, removed, config, &memory);
    assert(!memory.mouse2_rx && !memory.mouse2_ry && !memory.host2_frac[0] && !memory.host2_frac[1]);
}
'''
        (lr.ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mouse2-map-", dir=lr.ROOT / "build") as directory:
            path = Path(directory)
            (path / "probe.cpp").write_text(source)
            built = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                                    "-fno-sanitize-recover=all", *["-I" + str(lr.ROOT / name) for name in
                                    ("adapters/libretro", "bridge/include", "references/libretro-api/include", "ports/portable/media")],
                                    str(path / "probe.cpp"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, built.returncode, built.stderr)
            out = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(0, out.returncode, out.stderr)


class Mouse2FrontendTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        products = build_targets(lr.ROOT / "build/cd32", ["p6_libretro_host", "puae6_libretro"], optimized=True)
        cls.host, cls.core = products["p6_libretro_host"], products["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="mouse2-lr-", dir=lr.ROOT / "build")
        path = Path(cls.tmp.name) / "mice.rom"
        path.write_bytes(lr.builder.build(ROM))
        cls.roms, cls.counter = {"mice": path}, 0

    def test_two_mice_and_stick_replay_after_partial_motion_with_one_poll_per_run(self):
        for extra in ((), ("--set", "puae6_analog_mouse=left", "--analog", "0:12000,9000")):
            flags = ("--device", "0:1" if extra else "0:2", "--device", "1:2", "--mouse", "1,3,1", "--mouse2", "-3,1,6",
                     "--set", "puae6_mouse_speed=50", *extra)
            plain = self.frontend("mice", "--runs", 11, *flags, "--do", "11:peek:0x1000:80").stdout
            p = packets(plain)
            self.assertEqual(11, len(p))
            self.assertTrue(all((v[0], v[1], v[4], v[5]) == (2, 1, 2, 6) for v in p))
            self.assertEqual([-1, -2] * 5 + [-1], [v[6] for v in p])
            replay = self.frontend("mice", "--runs", 19, *flags, "--do", "3:save:s", "--do", "11:load:s", "--do", "19:peek:0x1000:80").stdout
            first, again = self.runs(plain), self.runs(replay)
            view = lambda r: (r["unit"], r["ticks"], r["hash"], r["ahash"], r["audio"], r["polls"])
            self.assertEqual([view(r) for r in first], [view(r) for r in again[:11]])
            self.assertEqual([view(r) for r in first[3:]], [view(r) for r in again[11:]])
            self.assertEqual([v[:8] for v in p[3:]], [v[:8] for v in packets(replay)[11:]])
            self.assertTrue(all(r["polls"] == 1 for r in again))
            self.assertEqual(re.search(r"^peek 0x1000 80 (\w+)$", plain, re.M)[1], re.search(r"^peek 0x1000 80 (\w+)$", replay, re.M)[1])
            if extra: self.assertIn("player 1's pad is not sent: player 2's mouse holds Amiga port 2", plain)


if __name__ == "__main__":
    unittest.main()
