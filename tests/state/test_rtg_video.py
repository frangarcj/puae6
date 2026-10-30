"""T85p: seleccion RTG/chipset sobre un unico lienzo fijo y centrado."""
import hashlib
import re
import subprocess
import unittest

from tests.state.harness import ProbeCase


CANVAS = re.compile(
    r"^canvas (\d+) (\d+)x(\d+) active=(\d+),(\d+),(\d+),(\d+) "
    r"stride=(\d+) outside=(\d+) ([0-9a-f]{64})$", re.MULTILINE)
PICTURE = re.compile(r"^picture (\d+) (\d+)x(\d+) p\d+ \d+/\d+ ([0-9a-f]{64})$", re.MULTILINE)
FLAGS = ("--cpu-compatibility", "normal", "--rtg-internal")


def fixed_canvas(width, height, pixel):
    row = bytearray(1024 * 4)
    left = (1024 - width) // 2
    row[left * 4:(left + width) * 4] = pixel.to_bytes(4, "little") * width
    blank = bytes(1024 * 4)
    top = (768 - height) // 2
    return hashlib.sha256(blank * top + bytes(row) * height + blank * (768 - top - height)).hexdigest()


class RtgVideoTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}
    MACHINE = "a4030"

    def test_fixed_canvas_centres_rtg_and_chipset_without_dirty_padding(self):
        result = subprocess.run([
            str(self.exe), str(self.roms["vsync"]), "--machine", self.MACHINE,
            "--units", "4", "--poke-at", "1", "rtg-seed",
            "--poke-at", "3", "rtg-chipset", *FLAGS,
        ], capture_output=True, text=True, timeout=1800)
        self.assertEqual(result.returncode, 0, result.stderr[-1200:])
        canvas = {int(match[0]): match[1:] for match in CANVAS.findall(result.stdout)}
        self.assertEqual({1, 2, 3, 4}, set(canvas))
        for unit in canvas:
            self.assertEqual(("1024", "768", "4096", "0"),
                             (canvas[unit][0], canvas[unit][1], canvas[unit][6], canvas[unit][7]))

        native = ("134", "97", "756", "574")
        self.assertEqual(native, canvas[1][2:6])
        self.assertEqual(("192", "144", "640", "480"), canvas[2][2:6])
        self.assertEqual(("192", "144", "640", "480"), canvas[3][2:6])
        self.assertEqual(native, canvas[4][2:6])
        self.assertEqual(fixed_canvas(640, 480, 0x00123456), canvas[2][8])
        self.assertEqual(canvas[2][8], canvas[3][8])

        pictures = {int(unit): (width, height, digest)
                    for unit, width, height, digest in PICTURE.findall(result.stdout)}
        active = hashlib.sha256((0x00123456).to_bytes(4, "little") * (640 * 480)).hexdigest()
        self.assertEqual(("640", "480", active), pictures[2])
        self.assertEqual(pictures[2], pictures[3])


if __name__ == "__main__":
    unittest.main()
