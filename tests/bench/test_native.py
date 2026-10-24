"""PERF01 (T33), the native and libretro probes: what the benchmark programs measure, and that measuring does not touch the machine.

tests/bench/bench_probe.cpp (p6_bench_probe) and the --bench mode of the libretro frontend (tests/libretro-host/host.cpp) are built in the optimized tree the state tests use
(build/state-opt, -O2 -g, asserts on) and run on the vsync micro-ROM for a few dozen units. The timings themselves are not asserted (they depend on the machine that runs the
test); what is asserted is the structure of what comes back and the properties that make a benchmark honest:

  * every unit of the window has one wall sample and one CPU sample, every state round a save, a load and the first unit after the load;
  * the clock is read outside the engine and does not feed it: the same total number of units ends on the same unit index and the same master tick whether the first
    ones were warmup or measured, with or without picture and audio (render flags change delivery only);
  * the steady state does not call the allocator: the allocator's logger is checked on a known malloc first, so that its zero means something;
  * the libretro core measured by the frontend's --bench mode reaches the same master tick as the same core run by the frontend in its checking mode.

    python3 -m unittest tests.bench.test_native
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.build_support import build_targets  # noqa: E402
from tests.host.test_bridge_lifecycle import builder  # noqa: E402

TOOLS = all(shutil.which(tool) for tool in ("cmake", "ninja", "clang", "clang++"))
TARGETS = ("p6_bench_probe", "puae6_libretro", "p6_libretro_host")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BenchProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-bench-")
        cls.rom = Path(cls.directory.name) / "vsync.rom"
        cls.rom.write_bytes(builder.build((ROOT / "tests/fixtures/microrom/vsync.s").read_text(encoding="utf-8")))
        cls.exes = build_targets(ROOT / "build/state-opt", TARGETS, optimized=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def probe(self, *args):
        result = subprocess.run([str(self.exes["p6_bench_probe"]), str(self.rom), *map(str, args)], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_unit_and_every_state_operation_has_its_sample(self):
        data = self.probe("--warmup", 10, "--units", 40, "--state-rounds", 3, "--alloc-units", 20)
        self.assertEqual((len(data["unit_wall_ns"]), len(data["unit_cpu_ns"])), (40, 40))
        for key in ("state_save_ns", "state_load_ns", "first_unit_after_load_ns"):
            self.assertEqual(len(data[key]), 3, key)
        for key in ("unit_wall_ns", "unit_cpu_ns", "state_save_ns", "state_load_ns", "first_unit_after_load_ns"):
            self.assertTrue(all(v > 0 for v in data[key]), key)
        self.assertEqual(data["unit_index_at_window_end"], 50)
        self.assertEqual(data["window_wall_ns"] // 1000 > 0, True)
        self.assertGreater(data["warmup_median_ns"], 0)
        self.assertTrue(data["state"]["supported"])
        self.assertGreater(data["state"]["bytes"], 0)
        self.assertLessEqual(data["state"]["bytes"], data["state"]["bound_bytes"])
        self.assertIsNone(data["state"]["dirty_pages"], "a native process has no dirty tracking: the counter is not invented")
        self.assertGreater(data["cadence"][0], 0)
        self.assertGreater(data["memory"]["resident_after"], 0)
        self.assertGreaterEqual(data["memory"]["peak_resident"], data["memory"]["resident_after"] // 2)

    def test_the_clock_does_not_feed_the_engine_and_render_flags_change_delivery_only(self):
        runs = [self.probe("--warmup", 10, "--units", 40, "--state-rounds", 0, "--alloc-units", 0),
                self.probe("--warmup", 0, "--units", 50, "--render", 0, "--state-rounds", 0, "--alloc-units", 0),
                self.probe("--warmup", 25, "--units", 25, "--render", 1, "--state-rounds", 0, "--alloc-units", 0),
                self.probe("--warmup", 49, "--units", 1, "--render", 2, "--state-rounds", 0, "--alloc-units", 0)]
        self.assertEqual({r["unit_index_at_window_end"] for r in runs}, {50})
        self.assertEqual(len({r["master_ticks_at_window_end"] for r in runs}), 1, "the machine is the same however it was measured")
        self.assertGreater(runs[0]["master_ticks_at_window_end"], 0)

    def test_the_steady_state_does_not_call_the_allocator(self):
        data = self.probe("--warmup", 20, "--units", 10, "--state-rounds", 0, "--alloc-units", 40)
        alloc = data["alloc"]
        if not alloc["supported"]:
            self.skipTest("no allocator logger on this platform")
        self.assertEqual(alloc["logger_works"], 1, "the logger saw a known malloc and free, so its zeros are real")
        self.assertEqual((alloc["allocs"], alloc["frees"], alloc["bytes"]), (0, 0, 0))

    def test_the_malloc_zone_does_not_grow_over_the_window(self):
        data = self.probe("--warmup", 50, "--units", 200, "--state-rounds", 0, "--alloc-units", 0)
        memory = data["memory"]
        self.assertLessEqual(memory["malloc_in_use_after"], memory["malloc_in_use_before"] + 64 * 1024)

    def test_the_libretro_frontend_in_bench_mode_measures_the_machine_the_checking_mode_runs(self):
        system = Path(self.directory.name) / "system"
        system.mkdir(exist_ok=True)
        shutil.copy2(self.rom, system / "kick34005.A500")
        out = Path(self.directory.name) / "libretro.json"
        host = [str(self.exes["p6_libretro_host"]), str(self.exes["puae6_libretro"]), "--system", str(system), "--runs", "60"]
        timed = subprocess.run([*host, "--bench", str(out), "--bench-state", "2"], capture_output=True, text=True, timeout=300)
        self.assertEqual(timed.returncode, 0, timed.stdout[-600:] + timed.stderr[-300:])
        checked = subprocess.run(host, capture_output=True, text=True, timeout=300)
        self.assertEqual(checked.returncode, 0, checked.stdout[-600:])
        data = json.loads(out.read_text())
        self.assertEqual((len(data["run_wall_ns"]), len(data["run_cpu_ns"])), (60, 60))
        for key in ("state_save_ns", "state_load_ns", "first_run_after_load_ns"):
            self.assertEqual(len(data[key]), 2, key)
        self.assertTrue(all(v > 0 for v in data["run_wall_ns"]))
        self.assertGreater(data["state_bytes"], 0)

        def last_run(text):
            lines = [line for line in text.splitlines() if line.startswith("run 60 ")]
            self.assertEqual(len(lines), 1, text[-400:])
            fields = dict(item.split("=", 1) for item in lines[0].split() if "=" in item)
            return fields["unit"], fields["ticks"]

        self.assertEqual(last_run(timed.stdout), last_run(checked.stdout))
        self.assertNotEqual(last_run(timed.stdout)[1], "0")


if __name__ == "__main__":
    unittest.main()
