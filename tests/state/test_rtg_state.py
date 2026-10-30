"""T85o: estado nativo del RTG acotado antes de exponer la opcion publica."""
import unittest

from tests.state import envelope as e
from tests.state import test_det02 as d
from tests.state.harness import FieldTable, ProbeCase, parse_chunks

RAM = 2
CORRUPT = 9
VRAM = 4 << 20
DEVICE = 1088
TRAPS = 24
RESIDENT = 8
# A4030 already owns its Fast RAM Autoconfig body. RTG inserts its own state
# before that body and extends the shared chain by only three words (9 bytes).
EXTRA = 4 + VRAM + DEVICE + TRAPS + RESIDENT + 9
FLAGS = ("--cpu-compatibility", "normal", "--rtg-internal")


def chunk_body_offset(blob, index):
    at = e.HEADER_SIZE
    for current, (_, _, body) in enumerate(parse_chunks(blob)):
        if current == index:
            return at + 16
        at += 16 + len(body)
    raise AssertionError(index)


class RtgStateTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}
    MACHINE = "a4030"

    def seeded(self, name="rtg", fields=False):
        path = self.dir / f"{name}.state"
        save = "--save-fields-at" if fields else "--save-at"
        run = self.probe("vsync", "--units", "2", "--poke-at", "1", "rtg-seed",
                         save, "2", str(path), *FLAGS)
        self.assertFalse(run.save_errors)
        return path.read_bytes(), run, path

    def test_layout_identity_and_rtg0_prefix(self):
        plain_path = self.dir / "plain.state"
        plain_run = self.probe("vsync", "--units", "2", "--save-fields-at", "2", str(plain_path),
                               "--cpu-compatibility", "normal")
        self.assertFalse(plain_run.save_errors)
        rtg, run, _ = self.seeded(fields=True)
        plain = plain_path.read_bytes()
        plain_ram, rtg_ram = parse_chunks(plain)[RAM][2], parse_chunks(rtg)[RAM][2]
        self.assertEqual(len(plain_ram) + EXTRA, len(rtg_ram))
        plain_table = FieldTable(plain, RAM, plain_run.fields)
        table = FieldTable(rtg, RAM, run.fields)
        self.assertNotIn("ram.rtg_size", plain_table.where)
        plain_expansion = plain_table.where["ram.fast_ecard"][0] - chunk_body_offset(plain, RAM)
        rtg_insert = table.where["ram.rtg_size"][0] - chunk_body_offset(rtg, RAM)
        self.assertEqual(plain_expansion, rtg_insert)
        self.assertEqual(plain_ram[:plain_expansion], rtg_ram[:rtg_insert])
        self.assertEqual((VRAM, VRAM), (table.get("ram.rtg_size"), table.size("ram.rtg_vram")))
        self.assertEqual((1, 640, 480, 1),
                         (table.get("ram.rtg_device_version"), table.get("ram.rtg_mode_width"),
                          table.get("ram.rtg_mode_height"), table.get("ram.rtg_active")))
        self.assertEqual((1, 640, 480, 8, 1, 1),
                         tuple(table.get(name) for name in ("ram.uaegfx_version", "ram.uaegfx_width", "ram.uaegfx_height",
                                                           "ram.uaegfx_depth", "ram.uaegfx_card_found", "ram.uaegfx_selected")))
        self.assertEqual((1, 0x1000), (table.get("ram.uaegfx_resident_version"), table.get("ram.uaegfx_library_base")))
        self.assertEqual((0x10000000, VRAM, 1),
                         (table.get("ram.rtg_start"), table.get("ram.rtg_allocated"), table.get("ram.rtg_mapped")))
        self.assertEqual(e.domain_hash("p6/config/rtg/v1", plain[176:208], b"\x01"), rtg[176:208])

    def test_corruption_is_transactional_and_valid_state_is_byte_exact(self):
        blob, run, path = self.seeded(fields=True)
        table = FieldTable(blob, RAM, run.fields)
        attempts = [
            ("future device codec", table.with_values({"ram.rtg_device_version": 2}), CORRUPT, "range"),
            ("misaligned mapped aperture", table.with_values({"ram.rtg_start": 0x10010000}), CORRUPT, "base and mapped"),
            ("invalid uaegfx geometry", table.with_values({"ram.uaegfx_width": 641}), CORRUPT, "trap state is invalid"),
            ("unaligned resident", table.with_values({"ram.uaegfx_library_base": 0x1001}), CORRUPT, "resident state is invalid"),
        ]
        refused = self.refusals(attempts, rom="vsync", extra=FLAGS, unit=2, units=4)
        self.assertEqual(len(attempts), len(refused.loads))

        again = self.dir / "again.state"
        restored = self.probe("vsync", "--units", "0", "--load-at", "0", str(path),
                              "--save-at", "0", str(again), *FLAGS)
        self.assertEqual([(0, 0, "")], restored.loads)
        self.assertEqual(blob, again.read_bytes())

    def test_future_is_identical_in_same_and_new_process(self):
        state = self.dir / "future.state"
        original = self.probe("vsync", "--units", "8", "--every", "1",
                              "--poke-at", "1", "rtg-seed", "--save-at", "2", str(state),
                              "--load-at", "5", str(state), *FLAGS)
        self.assertEqual([(5, 0, "")], original.loads)
        fresh = self.probe("vsync", "--units", "3", "--every", "1", "--load-at", "0", str(state), *FLAGS)
        self.assertEqual([(0, 0, "")], fresh.loads)
        for run, offset in ((original, 5), (fresh, 0)):
            for future in range(1, 4):
                self.assertEqual([], d.Det02Case.differing(self, original, run, 2, offset, future),
                                 (offset, future))


if __name__ == "__main__":
    unittest.main()
