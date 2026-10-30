"""T85r/T85s: fixed BizHawk RTG canvas and disk-independent uaegfx resident."""
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.scenarios.a500.display import rom_source  # noqa: E402
from tests.step.test_display import CASES  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
RESIDENT_MARK = 0x6000
RESIDENT_OK = b"P6R3"


def resident_probe_adf():
    """Free boot block: OpenLibrary proves the disk-independent uaegfx.card 3.4 resident."""
    code, labels, fixups = bytearray(), {}, []

    def words(*values):
        code.extend(struct.pack(">" + "H" * len(values), *(value & 0xffff for value in values)))

    def relative(opcode, label):
        words(opcode)
        fixups.append((len(code), label))
        words(0)

    words(0x2c78, 4)                       # movea.l 4.w,a6 (ExecBase)
    relative(0x43fa, "name")              # lea uaegfx.card(pc),a1
    words(0x7003, 0x4eae, -552, 0x4a80)   # OpenLibrary v3; tst.l d0
    relative(0x6700, "fail")
    words(0x2040)                          # movea.l d0,a0
    words(0x0c68, 3, 20)                   # cmpi.w #3,lib_Version(a0)
    relative(0x6600, "fail")
    words(0x0c68, 4, 22)                   # cmpi.w #4,lib_Revision(a0)
    relative(0x6600, "fail")
    words(0x23fc)
    code.extend(RESIDENT_OK + struct.pack(">I", RESIDENT_MARK))
    relative(0x6000, "idle")
    labels["fail"] = len(code)
    words(0x23fc)
    code.extend(b"FAIL" + struct.pack(">I", RESIDENT_MARK))
    labels["idle"] = len(code)
    words(0x60fe)                          # leave the result visible in Chip RAM
    labels["name"] = len(code)
    code.extend(b"uaegfx.card\0")
    code.extend(b"\0" * (len(code) % 2))
    for at, label in fixups:
        struct.pack_into(">h", code, at, labels[label] - at)

    block = bytearray(1024)
    block[:4] = b"DOS\0"
    struct.pack_into(">I", block, 8, 880)
    block[12:12 + len(code)] = code
    checksum = 0
    for value in struct.unpack(">256I", block):
        checksum += value
        checksum = (checksum & 0xffffffff) + (checksum >> 32)
    struct.pack_into(">I", block, 4, checksum ^ 0xffffffff)
    return bytes(block) + bytes(901120 - len(block))


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6)")
class WrapperRtgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wrapper-rtg-")
        cls.directory = Path(cls.tmp.name)
        cls.rom = cls.directory / "bars.rom"
        cls.rom.write_bytes(builder.build(rom_source(CASES["hires-bars"])))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, expect=0):
        WrapperRtgTests.count += 1
        program = self.directory / f"program-{WrapperRtgTests.count}.txt"
        program.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi(
            "session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha,
            "--firmware", str(self.rom), "--program", str(program))
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout + result.stderr

    @staticmethod
    def core(name, rtg=True, resolution="Hires"):
        return f"core {name} machine=a4030 accuracy=Normal vres={resolution} rtg={'MB4' if rtg else 'None'}"

    @staticmethod
    def units(output, name):
        return [m.groups() for m in map(UNIT.match, output.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(output, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", output, re.M)}

    def test_sync_setting_adds_only_its_documented_mmap_budget(self):
        manifest = json.loads((ROOT / "manifests/waterbox-memory.json").read_text())
        heaps = []
        for rtg in ("None", "MB4"):
            result = build_waterbox.run_ffi("heaps", "--machine", "A4030", "--rtg", rtg)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            heaps.append([int(value) for value in re.search(r"^heaps (.*)$", result.stdout, re.M)[1].split()])
        self.assertEqual(heaps[0][:4], heaps[1][:4])
        self.assertEqual(manifest["memory_budget"]["rtg_mmap_kib"], heaps[1][4] - heaps[0][4])

    def test_incompatible_machine_accuracy_and_raster_fail_before_a_unit(self):
        cases = (
            ("core A machine=a500 rtg=MB4", "A4000/030"),
            ("core A machine=a4030 accuracy=Exact rtg=MB4", "Normal CPU accuracy"),
            (self.core("A", resolution="SuperHires"), "fixed 1024x768 canvas"),
        )
        for line, reason in cases:
            with self.subTest(line=line):
                output = self.session([line], expect=1)
                self.assertIn(reason, output)
                self.assertNotIn("u A ", output)

    def test_canvas_is_fixed_black_padded_and_restores_in_another_sandbox(self):
        picture = self.directory / "rtg-canvas.bin"
        output = self.session([
            self.core("A"), "buffer A", "run A 4", "buffer A", f"picture A {picture}", "save A s",
            "run A 5", "digest A first", "load A s", "run A 5", "digest A second",
            self.core("B"), "load B s", "run B 5", "digest B third",
        ])
        self.assertEqual([("A", "1024x768"), ("A", "1024x768")], re.findall(r"^buffer (\S+) (\S+)$", output, re.M))
        a = self.units(output, "A")
        b = self.units(output, "B")
        self.assertEqual(a[4:9], a[9:14])
        self.assertEqual([unit[1:] for unit in a[4:9]], [unit[1:] for unit in b])
        digests = self.digests(output, "A")
        self.assertEqual(digests["first"], digests["second"])
        self.assertEqual(digests["first"], self.digests(output, "B")["third"])

        data = picture.read_bytes()
        width, height, bytes_per_pixel, frame = struct.unpack_from("<4i", data)
        self.assertEqual((1024, 768, 4, 4), (width, height, bytes_per_pixel, frame))
        pixels = struct.unpack_from(f"<{width * height}I", data, 16)
        left, top, active_width, active_height = 134, 97, 756, 574
        outside = []
        for y in range(height):
            row = pixels[y * width:(y + 1) * width]
            if y < top or y >= top + active_height:
                outside.extend(row)
            else:
                outside.extend(row[:left]); outside.extend(row[left + active_width:])
        self.assertTrue(all(pixel == 0 for pixel in outside), "the native black padding must survive the managed XRGB copy")
        centre = [pixels[y * width + x] for y in range(top, top + active_height) for x in range(left, left + active_width)]
        self.assertTrue(any(pixel != 0 for pixel in centre), "the centered chipset picture must not be mistaken for padding")

    def test_rtg_state_is_refused_by_the_same_machine_without_the_board(self):
        output = self.session([
            self.core("R"), "run R 3", "save R s",
            self.core("N", rtg=False), "run N 3", "digest N before", "load N s", "digest N after",
        ])
        self.assertRegex(output, re.compile(r"^rejected N s reason=OtherHeaps ", re.M))
        digests = self.digests(output, "N")
        self.assertEqual(digests["before"], digests["after"])

    @unittest.skipUnless(os.environ.get("PUAE6_RTG_P96_FIRMWARE") and os.environ.get("PUAE6_RTG_P96_HDF"),
                         "set PUAE6_RTG_P96_FIRMWARE and PUAE6_RTG_P96_HDF to run the authorized Picasso96 reproducer")
    def test_authorized_picasso96_selects_a_real_rtg_mode(self):
        """Picasso96 opens uaegfx.card and replaces the chipset rectangle with 640x480 RTG."""
        firmware = self.directory / "rtg-firmware.rom"
        hard_file = self.directory / "rtg-picasso96.hdf"
        picture = self.directory / "rtg-picasso96.bin"
        firmware.write_bytes(Path(os.environ["PUAE6_RTG_P96_FIRMWARE"]).read_bytes())
        hard_file.write_bytes(Path(os.environ["PUAE6_RTG_P96_HDF"]).read_bytes())
        self.session([
            f"core P firmware={firmware} hdf={hard_file} hdfc=Ide machine=a4030 accuracy=Normal drives=0 fast=MB8 rtg=MB4",
            "run P 1200", f"picture P {picture}",
        ])
        data = picture.read_bytes()
        width, height, bytes_per_pixel, frame = struct.unpack_from("<4i", data)
        self.assertEqual((1024, 768, 4, 1200), (width, height, bytes_per_pixel, frame))
        pixels = struct.unpack_from(f"<{width * height}I", data, 16)
        left, top, active_width, active_height = 192, 144, 640, 480
        outside = []
        for y in range(height):
            row = pixels[y * width:(y + 1) * width]
            if y < top or y >= top + active_height:
                outside.extend(row)
            else:
                outside.extend(row[:left]); outside.extend(row[left + active_width:])
        self.assertTrue(all(pixel == 0 for pixel in outside),
                        "Picasso96 did not select centered 640x480")

    @unittest.skipUnless(os.environ.get("PUAE6_RTG_P96_FIRMWARE"),
                         "set PUAE6_RTG_P96_FIRMWARE to probe the resident without a hardfile")
    def test_resident_exists_without_a_hardfile_and_after_power_reset(self):
        firmware = self.directory / "resident-firmware.rom"
        disk = self.directory / "resident-probe.adf"
        firmware.write_bytes(Path(os.environ["PUAE6_RTG_P96_FIRMWARE"]).read_bytes())
        disk.write_bytes(resident_probe_adf())
        output = self.session([
            f"core N firmware={firmware} adf={disk} machine=a4030 accuracy=Normal drives=1 fast=MB8 rtg=MB4",
            "run N 300", f"peek N Chip_RAM {RESIDENT_MARK:x} 4",
            "run N 300 press=1:Power", f"peek N Chip_RAM {RESIDENT_MARK:x} 4",
        ])
        expected = RESIDENT_OK.hex()
        self.assertEqual([expected, expected],
                         re.findall(rf"^peek N Chip_RAM {RESIDENT_MARK:x} (\w+)$", output, re.M))


if __name__ == "__main__":
    unittest.main()
