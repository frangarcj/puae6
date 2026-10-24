"""WB12 (T33): p50/p95 of a unit and of a snapshot, bytes and pages mutated, and the occupation of the heaps, measured in a real Waterbox sandbox.

tools/bench.py drives integration/bizhawk/smoke/Puae6Bench.cs (the guest ABI, one unit split into Activate / p6w_frame_advance / copy / Deactivate) and tests/ffi/Bench.cs
(Puae6Core.FrameAdvance as BizHawk calls it). These tests run both for a few dozen units of the vsync micro-ROM and assert what makes the numbers mean what the report says:

  * the percentiles are ordered and the parts of a unit add up to its total;
  * the dirty unit of Darwin ARM64 is the 16 KiB host page: the runtime's page table has 4 KiB logical pages, a page dirty-flag is set for the four of a group together, so the dirty
    bytes are 16 KiB times the groups that are whole, plus the logical pages of one group that is not (measured: the single 4 KiB page of the ELF's `.invis` data after the seal);
  * the heaps: nothing is committed past its layout, a heap's dirty bytes are inside what it committed, and the dirty set only grows after the seal;
  * the snapshot of the harness is the one manifests/waterbox-memory.json recorded, and the same machine seen through the core ends on the same master tick as through the guest ABI;
  * the local runtime is a RELEASE build since T34a (what BizHawk ships, eviction off): entering the sandbox is orders of magnitude cheaper than in the Debug lane
    (P6_WATERBOX_RUNTIME=debug: a DEBUG build whose eviction cannot be switched off), which is the cost the T33 report records for the Debug runtime and the reason for the change.

Skips (BLOCKED in the gate) without the Waterbox runtime and the guest: python3 tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units 60.
    python3 -m unittest tests.bench.test_wb12
"""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402
import build_waterbox  # noqa: E402

AVAILABLE = bench.GUEST_RECEIPT.is_file() and bench.RELEASE_RUNTIME_RECEIPT.is_file()
UNITS, WARMUP, ROUNDS = 60, 20, 3
SCENARIO = bench.Scenario("wb12-vsync", "a500", "pal", "microrom", "WB12 fixture", "ocs-vsync-irq-pal", warmup=WARMUP, units=UNITS)


def runner(identifier):
    return next(r for r in bench.RUNNERS if r.id == identifier)


@unittest.skipUnless(AVAILABLE, "no Waterbox runtime and guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units 60)")
class Wb12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_waterbox.build_managed()                                    # Smoke.dll with the --puae6-bench mode
        cls.guest = runner("waterbox-guest-release-runtime").run(SCENARIO, 1, UNITS, WARMUP, ROUNDS)
        cls.facts = cls.guest["facts"]

    def test_a_unit_is_timed_in_its_four_parts_and_the_percentiles_are_ordered(self):
        self.assertEqual(len(self.guest["unit_wall_ns"]), UNITS)
        stats = bench.describe(self.guest["unit_wall_ns"])
        self.assertLessEqual(stats["p50"], stats["p95"])
        self.assertLessEqual(stats["p95"], stats["p99"])
        self.assertLessEqual(stats["p99"], stats["max"])
        self.assertGreater(stats["min"], 0)
        for name, values in self.guest["parts"].items():
            self.assertEqual(len(values), UNITS, name)
        total = sum(self.guest["unit_wall_ns"])
        parts = sum(sum(values) for values in self.guest["parts"].values())
        self.assertLessEqual(parts, total * 1.001)                        # the parts are read inside the total
        self.assertGreaterEqual(parts, total * 0.5)

    def test_the_snapshot_is_timed_and_is_the_size_that_was_recorded(self):
        for key in ("state_save_ns", "state_load_ns", "first_unit_after_load_ns"):
            self.assertEqual(len(self.guest[key]), ROUNDS, key)
            stats = bench.describe(self.guest[key])
            self.assertGreater(stats["p50"], 0, key)
            self.assertLessEqual(stats["p50"], stats["p95"], key)
        recorded = json.loads(bench.MEMORY_MANIFEST.read_text())["measured"]["snapshot_bytes"]["after_last_unit"]
        self.assertAlmostEqual(self.facts["snapshot_bytes"] / recorded, 1.0, delta=0.10, msg="the snapshot of the benchmark is the one WB02/WB03 recorded for this guest")

    def test_the_dirty_unit_of_darwin_arm64_is_the_16_kib_group_of_four_logical_pages(self):
        self.assertEqual((self.facts["host_page_bytes"], self.facts["logical_page_bytes"]), (16384, 4096))
        for stage in ("pages_after_seal", "pages_after_warmup", "pages_after_units", "pages_after_state_rounds"):
            pages = self.facts[stage]
            outside = pages["dirty_bytes_4k_pages"] - 16384 * pages["dirty_groups_16k_all_four_logical_pages"]
            self.assertGreaterEqual(outside, 0, stage)
            self.assertLess(outside, 16384, f"{stage}: the dirty bytes are whole groups of four logical pages, plus the pages of at most one group that is not whole")
            self.assertLessEqual(pages["dirty_groups_16k"] - pages["dirty_groups_16k_all_four_logical_pages"], 1, f"{stage}: one group is not whole")
        self.assertGreaterEqual(self.facts["pages_after_warmup"]["dirty_bytes_4k_pages"], self.facts["pages_after_seal"]["dirty_bytes_4k_pages"])
        self.assertGreater(self.facts["pages_after_units"]["dirty_bytes_4k_pages"], self.facts["pages_after_seal"]["dirty_bytes_4k_pages"], "running the machine dirties pages")

    def test_nothing_is_committed_past_its_heap_and_the_dirty_bytes_are_committed_bytes(self):
        heaps = self.facts["heaps_after_units"]
        self.assertEqual(sorted(heaps), sorted(["elf", "main_thread", "alt_thread", "sbrk", "sealed", "invisible", "plain", "mmap"]))
        for name, heap in heaps.items():
            self.assertGreaterEqual(heap["committed_bytes"], 0, name)
            if name in ("sbrk", "sealed", "mmap", "invisible", "plain"):
                self.assertLessEqual(heap["committed_bytes"], self.facts["layout"][name], f"{name} stays inside its layout")
        self.assertGreater(heaps["mmap"]["committed_bytes"], 0)
        self.assertGreater(heaps["mmap"]["dirty_bytes"], 0, "the machine's RAM lives in the mmap heap and the unit wrote to it")
        self.assertLessEqual(heaps["mmap"]["dirty_bytes"], heaps["mmap"]["committed_bytes"])

    def test_the_core_as_bizhawk_calls_it_ends_on_the_same_machine_as_the_guest_abi(self):
        if not (bench.FFI_DEBUG / "Ffi.dll").is_file():
            build_waterbox.build_core()
        host = bench.WaterboxCoreRunner("waterbox-core-lane", bench.FFI_DEBUG, bench.release_runtime, True, "the Debug managed host of the FFI tests on the default (Release) runtime")
        core = host.run(SCENARIO, 1, UNITS, WARMUP, ROUNDS)
        self.assertEqual(len(core["unit_wall_ns"]), UNITS)
        self.assertEqual(core["unit_index_at_window_end"], self.guest["unit_index_at_window_end"])
        self.assertEqual(core["master_ticks_at_window_end"], self.guest["master_ticks_at_window_end"])
        self.assertGreater(core["facts"]["state_bytes"], self.facts["snapshot_bytes"], "a core state is the envelope and the wrapper's fields around the snapshot")
        self.assertGreaterEqual(core["facts"]["managed_allocated_bytes_in_window"], 0)

    def test_the_debug_lane_of_the_runtime_costs_what_the_release_build_does_not(self):
        build_waterbox.ensure_runtime_lane("debug")                       # the Debug lane is built from the same sources when it is missing
        debug = runner("waterbox-guest-debug-runtime").run(SCENARIO, 1, UNITS, WARMUP, ROUNDS)
        self.assertEqual(debug["master_ticks_at_window_end"], self.guest["master_ticks_at_window_end"], "the same guest on the other runtime is the same machine")
        debug_enter = bench.describe(debug["parts"]["unit_enter_ns"])["p50"]
        release_enter = bench.describe(self.guest["parts"]["unit_enter_ns"])["p50"]
        self.assertGreater(debug_enter, 100 * max(release_enter, 1.0))


if __name__ == "__main__":
    unittest.main()
