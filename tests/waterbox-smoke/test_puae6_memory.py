"""T20a: the memory the wrapper exposes, reached through the guest of puae6.wbx in a real Waterbox sandbox.

The guest reports its memory areas (adapters/waterbox/p6w.h, p6w_memory_areas) in the layout BizHawk's LibWaterboxCore.MemoryArea has, as function
hooks that run inside the sandbox. The managed harness (integration/bizhawk/smoke/Puae6.cs, --memory-at) checks the areas (Chip RAM primary,
Slow RAM, ROM; big endian, 16-bit words), reads what the 68000 stored, writes through the guest also into pages nothing has written since the seal
(a managed store there is fatal, T19c2), has the ROM and every out-of-range access refused and counted, undoes the writes with a restore and shows
that a write into the colour word of the fixture changes the next picture. The units that follow must be the ones of a run without any of that.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom) and skips without them.
    python3 -m unittest discover -s tests/waterbox-smoke -p "test_puae6_memory.py"
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe, builder  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_puae6 import runtime_available  # noqa: E402


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class GuestMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        build_waterbox.build_managed()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wb-memory-")
        cls.rom = Path(cls.tmp.name) / "chipcell.rom"
        cls.rom.write_bytes(builder.build((Path(__file__).parent / "chipcell.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_and_compare(self, units, memory_at, machine="A500"):
        lines, result = build_waterbox.run_puae6(self.rom, units, memory_at=memory_at, machine=machine)
        native = subprocess.run([str(self.headless), "--run", str(self.rom), "--units", str(units), "--machine", machine.lower()], capture_output=True, text=True, timeout=3600)
        self.assertEqual(0, native.returncode, native.stderr)
        self.assertEqual([line for line in native.stdout.splitlines() if line.startswith("unit ")], lines,
                         "reads, writes, refusals and restores left the machine exactly where the snapshot was")
        return result["memory"][0]

    def check_areas(self, memory):
        areas = memory["areas"]
        self.assertEqual(["Chip RAM", "Slow RAM", "ROM"], [area["Name"] for area in areas])
        self.assertEqual([524288, 524288], [area["Size"] for area in areas[:2]])
        # BizHawk's MemoryDomainFlags: Writable 1, Primary 8, YugeEndian 16, WordSize2 64, FunctionHook 1024.
        self.assertEqual([1024 | 64 | 16 | 8 | 1, 1024 | 64 | 16 | 1, 1024 | 64 | 16], [area["Flags"] for area in areas])

    def test_right_after_the_seal_a_write_through_the_guest_lands_in_a_clean_page_and_is_tracked(self):
        memory = self.run_and_compare(30, 0)
        self.check_areas(memory)
        self.assertGreaterEqual(memory["dirty_pages_after_writes"], memory["dirty_pages_before_writes"] + 12)

    def test_after_a_unit_the_marker_reads_big_endian_and_a_write_changes_the_next_picture(self):
        self.check_areas(self.run_and_compare(30, 15))

    def check_a600_areas(self, memory):
        areas = memory["areas"]
        # The A600 has 1 MiB of Chip RAM and the ROM; there is no Slow RAM area at all (not an empty one).
        self.assertEqual(["Chip RAM", "ROM"], [area["Name"] for area in areas])
        self.assertEqual(1048576, areas[0]["Size"])
        self.assertEqual([1024 | 64 | 16 | 8 | 1, 1024 | 64 | 16], [area["Flags"] for area in areas])

    def test_on_the_a600_the_areas_are_one_mebibyte_of_chip_ram_and_the_rom_and_writes_land_in_clean_pages(self):
        memory = self.run_and_compare(30, 0, "A600")
        self.check_a600_areas(memory)
        self.assertGreaterEqual(memory["dirty_pages_after_writes"], memory["dirty_pages_before_writes"] + 12)

    def test_on_the_a600_after_a_unit_the_marker_reads_big_endian_and_a_write_changes_the_next_picture(self):
        self.check_a600_areas(self.run_and_compare(30, 15, "A600"))

    def check_a1200_areas(self, memory):
        areas = memory["areas"]
        # T24f: the A1200 has 2 MiB of Chip RAM and the ROM; like the A600 it has no Slow RAM area at all (not an empty one).
        self.assertEqual(["Chip RAM", "ROM"], [area["Name"] for area in areas])
        self.assertEqual(2097152, areas[0]["Size"])
        self.assertEqual([1024 | 64 | 16 | 8 | 1, 1024 | 64 | 16], [area["Flags"] for area in areas])

    def test_on_the_a1200_the_areas_are_two_mebibytes_of_chip_ram_and_the_rom_and_writes_land_in_clean_pages(self):
        memory = self.run_and_compare(30, 0, "A1200")
        self.check_a1200_areas(memory)
        self.assertGreaterEqual(memory["dirty_pages_after_writes"], memory["dirty_pages_before_writes"] + 12)

    def test_on_the_a1200_after_a_unit_the_marker_reads_big_endian_and_a_write_changes_the_next_picture(self):
        self.check_a1200_areas(self.run_and_compare(30, 15, "A1200"))


if __name__ == "__main__":
    unittest.main()
