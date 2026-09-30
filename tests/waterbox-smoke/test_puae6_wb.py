"""T19c2: WB13, WB14 and WB15 with the real guest, puae6.wbx, in Waterbox.

WB13  a host callback crosses the guest, before units of a running machine, with canaries in every callee-saved register; x18, x28, musl's
      thread-specific data and errno come back unchanged, also from a guest function entered while the host is inside the callback, and the
      units that follow are the ones of a run without the callbacks.
WB14  a group of four 4 KiB logical pages in one 16 KiB host page, in memory of the running machine (a group nothing has written since the seal, and
      a group of Chip RAM): the first write dirties the group, a restore brings all four pages back, and the machine then goes on as if
      nothing had happened; a write into the word a fixture program shows on screen changes the picture.
WB15  a snapshot saved by a process is loaded by another, new process that only initialized and sealed the same guest with the same firmware,
      and the units that follow are the same; a guest that is not the one of the receipt, or a damaged one, is refused.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom) and skips without them.
    python3 -m unittest discover -s tests/waterbox-smoke -p "test_puae6_wb.py"
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe, builder  # noqa: E402
from tests.scenarios.a500.display import Display, rom_source  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_puae6 import runtime_available  # noqa: E402

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
LACE = Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)
GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class RealGuestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = build_probe(BUILD, False, "p6_headless")
        build_waterbox.build_managed()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wb-real-")
        cls.roms = {}
        for name, source in (("lace", rom_source(LACE)), ("chipcell", (Path(__file__).parent / "chipcell.s").read_text(encoding="utf-8")),
                             ("input_all", (FIXTURES / "input_all.s").read_text(encoding="utf-8"))):
            cls.roms[name] = Path(cls.tmp.name) / f"{name}.rom"
            cls.roms[name].write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def wb(self, rom, units, **options):
        return build_waterbox.run_puae6(self.roms[rom], units, **options)

    def native(self, rom, units):
        result = subprocess.run([str(self.headless), "--run", str(self.roms[rom]), "--units", str(units)], capture_output=True, text=True, timeout=3600)
        self.assertEqual(0, result.returncode, result.stderr)
        return [line for line in result.stdout.splitlines() if line.startswith("unit ")]

    # --- WB13 ---------------------------------------------------------------------------------------------------------------------------

    def test_host_callbacks_cross_the_guest_between_units_without_disturbing_x18_x28_the_thread_state_or_the_machine(self):
        lines, result = self.wb("lace", 40, abi_at=(5, 20, 21))
        self.assertEqual(self.native("lace", 40), lines, "after a callback with canaries in every register the machine is the one of a run without it")
        self.assertEqual([5, 20, 21], [entry["unit"] for entry in result["abi"]])
        contexts = {entry["x28"] for entry in result["abi"]}
        self.assertEqual(1, len(contexts), "one sandbox, one x28 context, before every unit that was probed")
        self.assertNotEqual(0, contexts.pop())
        self.assertTrue(all(entry["nested_x28"] == entry["x28"] for entry in result["abi"]), "a guest function entered from inside a callback sees the same context")
        self.assertTrue(all(entry["x18"] == result["abi"][0]["x18"] for entry in result["abi"]))

    # --- WB14 ---------------------------------------------------------------------------------------------------------------------------

    def test_a_16_kib_group_of_the_running_machine_dirties_as_a_whole_restores_whole_and_the_machine_goes_on(self):
        lines, result = self.wb("chipcell", 30, pages_at=15)
        self.assertEqual(self.native("chipcell", 30), lines, "the writes, restores and control units left the machine exactly where the snapshot was")
        pages = result["pages"]
        self.assertEqual(0, pages["clean_group"] % 16384)
        self.assertEqual(0, pages["chip_group"] % 16384)
        self.assertGreaterEqual(pages["chip_group"], pages["chip_ram"])
        for name, starts_clean in (("clean_group_pages", True), ("chip_group_pages", False)):
            self.assertEqual([0, 1, 2, 3], [entry["page"] for entry in pages[name]])
            for entry in pages[name]:
                self.assertEqual([True] * 4, entry["dirty_after_write"], f"{name}: one write dirties the four logical pages")
                self.assertEqual(1, len(set(entry["dirty_after_restore"])), f"{name}: the four logical pages agree after a restore")
                if starts_clean:
                    self.assertEqual([False] * 4, entry["dirty_before"], "a group nothing wrote since the seal starts clean")
                    self.assertEqual([False] * 4, entry["dirty_after_restore"], "and is clean again after restoring a snapshot taken while it was")

    # --- WB15 ---------------------------------------------------------------------------------------------------------------------------

    def restore_case(self, rom, units, at, **options):
        state = Path(self.tmp.name) / f"{rom}-{at}.state"
        (lines, saved), = [self.wb(rom, units, save_at=at, state=state, digest_every=units, **options)]
        self.assertGreater(state.stat().st_size, 1 << 20)
        with ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(self.wb, rom, units, restore=state, restore_from=at, digest_every=units, **options) for _ in range(2)]
            restored = [job.result() for job in jobs]
        for tail, result in restored:
            self.assertEqual(lines[at:], tail, "a new process, given the snapshot, continues with the units the original ran")
            self.assertEqual(units - at, len(tail))
            self.assertEqual(saved["state_digests"]["final"], result["state_digests"]["final"], "and ends in the same whole-machine state")
            self.assertEqual(at, result["restored_from"])
            self.assertEqual({n: h for n, h in saved["pcm"].items() if n > at}, result["pcm"], "with the same PCM, unit by unit")
        return lines

    def test_a_new_process_restores_a_snapshot_of_the_interlaced_machine_and_continues_exactly(self):
        lines = self.restore_case("lace", 90, 45)
        self.assertEqual(self.native("lace", 90), lines)

    def test_a_new_process_restores_a_snapshot_taken_with_buttons_held_and_continues_exactly(self):
        self.restore_case("input_all", 70, 30, ports=("0:1:1:0:0", "1:1:73:0:0"))

    def test_the_abi_probe_also_works_in_a_sandbox_that_was_restored(self):
        state = Path(self.tmp.name) / "lace-abi.state"
        lines, saved = self.wb("lace", 60, save_at=30, state=state)
        tail, result = self.wb("lace", 60, restore=state, restore_from=30, abi_at=(33, 40))
        self.assertEqual(lines[30:], tail)
        self.assertEqual([33, 40], [entry["unit"] for entry in result["abi"]])

    def test_a_guest_that_is_damaged_or_not_the_one_of_the_receipt_is_refused_before_it_can_run(self):
        image = GUEST.read_bytes()
        phoff, phentsize, phnum = struct.unpack_from("<Q", image, 32)[0], struct.unpack_from("<H", image, 54)[0], struct.unpack_from("<H", image, 56)[0]
        loads = [phoff + n * phentsize for n in range(phnum) if struct.unpack_from("<I", image, phoff + n * phentsize)[0] == 1]
        flags = lambda at: struct.unpack_from("<I", image, at + 4)[0]       # noqa: E731
        rx = next(at for at in loads if flags(at) == 5)
        rw = next(at for at in loads if flags(at) == 6)
        variants = {"wrong machine (x86-64)": bytearray(image), "bad magic": bytearray(image), "truncated": bytearray(image[:100]),
                    "a RW page inside the last RX group": bytearray(image)}
        struct.pack_into("<H", variants["wrong machine (x86-64)"], 18, 62)
        variants["bad magic"][0] = 0
        mixed = variants["a RW page inside the last RX group"]
        vaddr, filesz, memsz = struct.unpack_from("<QQQ", image, rx + 16)[0], struct.unpack_from("<Q", image, rx + 32)[0], struct.unpack_from("<Q", image, rx + 40)[0]
        tail = vaddr + memsz - 0x1000
        struct.pack_into("<Q", mixed, rx + 32, filesz - 0x1000)
        struct.pack_into("<Q", mixed, rx + 40, memsz - 0x1000)
        struct.pack_into("<QQQQ", mixed, rw + 16, tail, tail, 0x1000, 0x1000)
        for name, damaged in variants.items():
            path = Path(self.tmp.name) / "damaged.wbx"
            path.write_bytes(bytes(damaged))
            with self.subTest(name), self.assertRaises(build_waterbox.BuildError):
                build_waterbox.validate_elf(path)
        build_waterbox.validate_elf(GUEST)              # and the real one passes the same check
        self.assertEqual("PASS", build_waterbox.check_no_compiler_tls(GUEST)["wb16"])


if __name__ == "__main__":
    unittest.main()
