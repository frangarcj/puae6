"""T29d: a hard file mounted, written and restored inside the real Waterbox sandbox (WB10 for hard files, WB04, the budget of its heap).

The guest is the micro-ROM of tests/hdf/hdf_rom.py (loop_program): it boots the filesystem ROM of the UAE controller and then, at every vertical blank, writes two blocks across a chunk
boundary of the hard file, reads them back, reads block 0 and takes one step of the EXTER interrupt. The hard file is given to puae6.wbx as a P6_MEDIA_HDF (it is not copied into the sealed
heap: p6_load keeps it in the blob store, the mmap heap, with the room for the overlay of its writes). What is checked, always against p6_headless (the native engine) with the same ROM and image:

  * every unit (ticks, picture, audio) is the native one, and the hard file exported through the guest after the last unit is, byte for byte, the one the native engine exports;
  * a snapshot of unit N restored in a new process (a new sandbox) continues with the units of the straight run and the hard file of unit N, then of the last unit;
  * a snapshot loaded back in the same sandbox after more writes takes the writes back (the hard file is the one of unit N again) and the units that run again are the first run's;
  * the heap budget: the hard file is not in the sealed heap, the mmap heap of the sandbox is the base one plus twice what the image takes (HardFileHeapKiB in the smoke, the counterpart of
    MacHawk's HardDiskHeapKB), and what is committed is under half of it; the snapshot carries the dirty pages of the overlay, not the image.

Needs the runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom); skips without them.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_waterbox  # noqa: E402
from test_puae6 import MANIFEST, UNIT, microrom, runtime_available  # noqa: E402
from tests.hdf import hdf_image, hdf_rom  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe  # noqa: E402

UNITS = 40
EXPORT = re.compile(r"^export (\S+) (\d+) result=(-?\d+) bytes=(\d+) sha=(\S+)$", re.M)


def heap_kib(size):
    """HardFileHeapKiB of the smoke: the base and the room for the overlay, the dirty bitmap and 64 KiB of the allocator's own."""
    return 2 * ((size + 1023) // 1024) + (size // 4096 + 7) // 8 // 1024 + 64


def sha(data):
    return hashlib.sha256(data).hexdigest()


def exports(lines):
    return {m[1]: (int(m[3]), int(m[4]), m[5]) for m in EXPORT.finditer("\n".join(lines))}


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WaterboxHardFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wb-hdf-")
        cls.dir = Path(cls.tmp.name)
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.rom = cls.dir / "loop.rom"
        cls.rom.write_bytes(hdf_rom.loop_rom())
        cls.image = hdf_image.rdb_image(cylinders=64)               # 2 MiB: 512 chunks
        cls.hdf = cls.dir / "d.hdf"
        cls.hdf.write_bytes(cls.image)
        cls.manifest = json.loads(MANIFEST.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def native(self, units):
        out = self.dir / f"native-{units}.bin"
        result = subprocess.run([str(self.headless), "--run", str(self.rom), "--units", str(units), "--hdf", str(self.hdf), "--export-hdf", str(out)],
                                capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stderr)
        return UNIT.findall(result.stdout), out.read_bytes()

    def waterbox(self, units, **kwargs):
        directory = self.dir / f"export-{len(list(self.dir.iterdir()))}"
        directory.mkdir()
        lines, result = build_waterbox.run_puae6(self.rom, units, hard_files=(self.hdf,), export_dir=directory, **kwargs)
        return lines, result, directory

    def test_every_unit_and_the_hard_file_after_the_guest_wrote_it_are_the_native_ones(self):
        native_units, native_image = self.native(UNITS)
        lines, result, directory = self.waterbox(UNITS, export_at=(UNITS,))
        self.assertEqual(native_units, UNIT.findall("\n".join(lines)))
        self.assertEqual(UNITS, len(native_units))
        wrote = exports(result["export_lines"])[str(UNITS)]
        self.assertEqual((0, len(self.image), sha(native_image)), wrote)
        self.assertEqual(native_image, (directory / f"hdf0-{UNITS}.bin").read_bytes())
        self.assertNotEqual(self.image, native_image, "the guest wrote the hard file")
        changed = [c for c in range(len(self.image) // 4096) if self.image[c * 4096:(c + 1) * 4096] != native_image[c * 4096:(c + 1) * 4096]]
        self.assertGreater(len(changed), 20)
        self.assertEqual(list(range(changed[0], changed[0] + len(changed))), changed, "one chunk further on every unit, from the first the loop writes")

    def test_a_snapshot_restored_in_a_new_process_continues_with_the_hard_file_of_its_unit(self):
        state = self.dir / "wb.state"
        lines, straight, _ = self.waterbox(UNITS, save_at=20, state=state, export_at=(20, UNITS))
        mid, end = exports(straight["export_lines"])["20"], exports(straight["export_lines"])[str(UNITS)]
        self.assertNotEqual(mid[2], end[2], "the guest wrote between the two units")
        self.assertNotEqual(mid[2], sha(self.image))
        again, restored, _ = self.waterbox(UNITS, restore=state, restore_from=20, export_at=(20, UNITS))
        got = exports(restored["export_lines"])
        self.assertEqual(mid, got["20"], "right after the restore the hard file is the one of the snapshot")
        self.assertEqual(end, got[str(UNITS)], "and after the same units it is the one of the straight run")
        self.assertEqual(UNIT.findall("\n".join(lines))[20:], UNIT.findall("\n".join(again)), "the units after the snapshot are the straight run's")
        self.assertEqual(straight["state_digests"]["final"], restored["state_digests"]["final"], "the whole sandbox ends in the same state")

    def test_a_snapshot_loaded_back_in_the_same_sandbox_takes_the_later_writes_back(self):
        lines, result, _ = self.waterbox(UNITS, rewind_to=12, rewind_from=30, export_at=(12,))
        got = {m[1]: m[5] for m in EXPORT.finditer("\n".join(result["export_lines"]))}
        self.assertEqual(got["12"], got["12-after-rewind"], "the hard file is the one of unit 12 again")
        self.assertNotEqual(got["30-before-rewind"], got["12-after-rewind"], "and not the one the later writes made")
        units = UNIT.findall("\n".join(lines))
        first, second = units[:30], units[30:]
        self.assertEqual(first[12:], second[:len(first) - 12], "the units that run again are the first run's")
        self.assertEqual(UNITS - 12, len(second))

    def test_the_hard_file_is_not_in_the_sealed_heap_and_the_budget_is_twice_what_it_takes(self):
        lines, result, _ = self.waterbox(UNITS)
        base = self.manifest["layout"]["mmap"]
        self.assertEqual(base + 2 * heap_kib(len(self.image)) * 1024, result["layout"]["mmap"], "the base mmap heap plus twice the heap of the hard file")
        committed = result["after_last_unit"]["mmap"]["committed_bytes"]
        self.assertLessEqual(2 * committed, result["layout"]["mmap"], "what is committed fits in half of the budget")
        self.assertLess(result["after_last_unit"]["sealed"]["committed_bytes"], len(self.image), "the image is not in the sealed heap")
        # the blob store keeps the base and the room for the overlay: two images
        plain = self.manifest.get("measured", {}).get("after_last_unit", {}).get("mmap", {}).get("committed_bytes", 0)
        self.assertGreaterEqual(committed - plain, 2 * len(self.image) - 65536)
        # the snapshot carries the dirty pages of the overlay, not the image: a program that does no I/O, with the same hard file, is the baseline of the machine
        idle = self.dir / "boot.rom"
        idle.write_bytes(microrom("boot.s"))
        _, quiet = build_waterbox.run_puae6(idle, UNITS, hard_files=(self.hdf,))
        written = result["snapshot_bytes"]["after_last_unit"] - quiet["snapshot_bytes"]["after_last_unit"]
        self.assertLess(written, len(self.image) // 4, "the snapshot grew by the chunks the guest wrote (%d bytes), not by the image (%d)" % (written, len(self.image)))
        self.assertLess(quiet["snapshot_bytes"]["after_last_unit"], 12 << 20)

    def test_what_was_recorded_for_the_hard_files_fits_in_half_of_the_budget_the_rule_gives(self):
        budget = self.manifest["memory_budget"]
        keys = [key for key in self.manifest if key.startswith("measured_a500_hdf")]
        self.assertGreaterEqual(len(keys), 2, "run tools/build_waterbox.py --puae6-run ... --record-memory --hdf IMAGE")
        for key in keys:
            with self.subTest(key):
                recorded = self.manifest[key]
                sizes = recorded["fixture"]["hard_file_bytes"]
                extra = sum(heap_kib(size) for size in sizes)
                self.assertEqual(sum(sizes) // 1024, int(key.removeprefix("measured_a500_hdf")))
                mmap = self.manifest["layout"]["mmap"] + budget["mmap_extra_factor"] * extra * 1024
                for phase in ("after_seal", "after_first_unit", "after_last_unit"):
                    self.assertLessEqual(2 * recorded[phase]["mmap"]["committed_bytes"], mmap, f"{phase}: the mmap heap")
                    self.assertLessEqual(2 * recorded[phase]["sbrk"]["committed_bytes"], self.manifest["layout"]["sbrk"], f"{phase}: the sbrk heap")
                    self.assertLess(recorded[phase]["sealed"]["committed_bytes"], 1 << 20, f"{phase}: no image in the sealed heap")
                # the snapshot does not follow the size of the image
                self.assertLess(recorded["snapshot_bytes"]["after_last_unit"], 12 << 20)
        self.assertEqual(2, budget["hard_file_copies"])


if __name__ == "__main__":
    unittest.main()
