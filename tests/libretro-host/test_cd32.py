"""T70a: the actual libretro core reads both CD32 pads and restores a partly clocked serial protocol."""
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from test_input import packets
from tests.build_support import build_targets
from tests.input.test_cd32 import ROM, BUTTONS


class Cd32FrontendTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        products = build_targets(lr.ROOT / "build/cd32", ["p6_libretro_host", "puae6_libretro"], optimized=True)
        cls.host, cls.core = products["p6_libretro_host"], products["puae6_libretro"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="cd32-lr-", dir=lr.ROOT / "build")
        path = Path(cls.tmp.name) / "cd32.rom"
        path.write_bytes(lr.builder.build(ROM))
        cls.roms, cls.counter = {"cd32": path}, 0

    def test_both_pads_reach_the_serial_pins_and_restore_with_one_poll_per_unit(self):
        # RetroPad B/L/Start -> red/rewind/play; A/X/Y/R -> blue/green/yellow/forward.
        # Player 1 drives Amiga port 2, player 2 port 1.
        flags = ("--device", "0:513", "--device", "1:513", "--joy", "0:409", "--joy", "1:b02")
        for standard in ("PAL", "NTSC"):
            with self.subTest(standard=standard):
                options = (*flags, "--set", f"puae6_video_standard={standard}")
                plain = self.frontend("cd32", "--runs", 11, *options, "--do", "11:peek:0x1000:18").stdout
                self.assertEqual([(3, 0x286, 0, 0, 3, 0x501, 0, 0)] * 11, [p[:8] for p in packets(plain)])
                words = struct.unpack(">9H", bytes.fromhex(re.search(r"^peek 0x1000 18 ([0-9a-f]+)$", plain, re.M)[1]))
                for port, mask in enumerate((0x286, 0x501)):
                    self.assertEqual([int(not (mask & b)) for b in BUTTONS] + [1, 0], [(w >> (10 + 4 * port)) & 1 for w in words])
                restored = self.frontend("cd32", "--runs", 19, *options, "--do", "3:save:s", "--do", "11:load:s", "--do", "19:peek:0x1000:18").stdout
                first, replay = self.runs(plain), self.runs(restored)
                view = lambda r: (r["unit"], r["ticks"], r["hash"], r["ahash"], r["audio"], r["polls"])
                self.assertEqual([view(r) for r in first], [view(r) for r in replay[:11]])
                self.assertEqual([view(r) for r in first[3:11]], [view(r) for r in replay[11:19]])
                self.assertTrue(all(r["polls"] == 1 for r in replay))
                self.assertEqual(re.search(r"^peek 0x1000 18 ([0-9a-f]+)$", plain, re.M)[1], re.search(r"^peek 0x1000 18 ([0-9a-f]+)$", restored, re.M)[1])


if __name__ == "__main__":
    unittest.main()
