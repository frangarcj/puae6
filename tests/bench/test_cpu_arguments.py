"""T53 wiring only: no emulator, benchmark samples or PERF01 certification."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import bench as b


class CpuArgumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b.BUILD.mkdir(exist_ok=True)

    def test_scenarios_have_distinct_immutable_cpu_identities_and_validate_choices(self):
        base = b.scenario_by_id("a500-ocs-busy-pal")
        variants = [base.with_cpu(cpu, level) for cpu in ("auto", "68000", "68010") for level in ("exact", "compatible", "normal")]
        self.assertEqual(9, len({s.id for s in variants}))
        self.assertEqual(("auto", "exact"), (base.cpu_model, base.cpu_compatibility))
        for cpu, level in (("68030", "normal"), ("auto", "fast"), ("68020", "exact")):
            with self.assertRaises(ValueError):
                base.with_cpu(cpu, level)
        self.assertEqual("68020", b.scenario_by_id("a1200-aga-planes8-pal").with_cpu("68020", "normal").cpu_model)

    def test_native_and_frontend_commands_share_cpu_selection_without_running(self):
        data = dict(cadence=[50, 1], unit_wall_ns=[], unit_cpu_ns=[], state_save_ns=[], state_load_ns=[], first_unit_after_load_ns=[],
                    run_wall_ns=[], run_cpu_ns=[], first_run_after_load_ns=[], state_bytes=0, pictures=0, audio_frames=0,
                    master_ticks_at_window_end=1, unit_index_at_window_end=2)
        with tempfile.TemporaryDirectory(dir=b.BUILD) as tmp, patch.object(b, "WORK", Path(tmp)), patch.object(b.Scenario, "rom", return_value=Path(tmp)/"public.rom"), \
             patch.object(b, "sha256_of", return_value="artifact"), patch.object(b, "json_result", side_effect=lambda path: json.loads(runtime.read_text()) if Path(path) == runtime else json.loads(json.dumps(data))), \
             patch.object(b, "run_process", return_value=(0, json.dumps(data), 1)) as run:
            # Supply only host metadata; no firmware or runtime is opened/copied.
            runtime = Path(tmp)/"runtime.json"
            runtime.write_text(json.dumps({"runtime_dylib": {"path": "unused"}, "guest": {"path": "unused", "sha256": "artifact"}}))
            host = Path(tmp)/"ffi-wb"; host.mkdir(); (host/"Ffi.dll").touch()
            with patch.object(b, "GUEST_RECEIPT", runtime):
                runners = [b.NativeRunner("native", 3, ""), b.LibretroRunner(), b.WaterboxCoreRunner("wb", Path(tmp), lambda: runtime, True, "")]
                for level in ("exact", "compatible", "normal"):
                    s = b.scenario_by_id("a500-ocs-busy-pal").with_cpu("68010", level)
                    for runner in runners:
                        runner.run(s, 1, 2, 0, 0)
                        command = list(map(str, run.call_args.args[0]))
                        if runner.layer == "libretro":
                            self.assertIn("puae6_cpu_model=68010", command)
                            self.assertIn(f"puae6_cpu_compatibility={level}", command)
                        else:
                            self.assertEqual("68010", command[command.index("--cpu") + 1])
                            self.assertEqual(level, command[command.index("--cpu-compatibility") + 1])
            with self.assertRaisesRegex(ValueError, "raw guest CPU selection"):
                b.RUNNERS[4].run(s, 1, 2, 0, 0)

    def test_summary_keeps_modes_apart_and_rejects_mislabeled_metadata(self):
        from tests.bench.test_statistics import make_baseline, record
        base = b.scenario_by_id("a500-ocs-busy-pal")
        variants = [base.with_cpu("68010", level) for level in ("exact", "normal")]
        records = [record(scenario=s.id, ticks=i+1, scenario_base=s.base_id, cpu_model=s.cpu_model, cpu_compatibility=s.cpu_compatibility) for i, s in enumerate(variants)]
        with tempfile.TemporaryDirectory(dir=b.BUILD) as tmp, patch.object(b, "BASELINES", Path(tmp)):
            directory = Path(tmp)/"cpu"; make_baseline(directory, records)
            summary = b.summarize("cpu")
            self.assertEqual({s.id for s in variants}, set(summary["scenarios"]))
            self.assertTrue(all(s["ticks_equal_across_runners"] for s in summary["scenarios"].values()))
            records[0]["cpu_compatibility"] = "normal"
            b.write_raw(directory/"raw"/"00.json.gz", records[0])
            with self.assertRaisesRegex(ValueError, "identity differs"):
                b.summarize("cpu")

    def test_native_parser_accepts_only_named_cpu_and_accuracy_values(self):
        source = r'''#include "cpu_options.h"
#include <assert.h>
int main() {
    uint8_t cpu, mode;
    const char *models[] = {"auto", "68000", "68010", "68020"};
    const char *modes[] = {"exact", "compatible", "normal"};
    for (int c=0;c<4;c++) for (int m=0;m<3;m++) {
        assert(bench_cpu_options(models[c],modes[m],&cpu,&mode) && cpu==c && mode==m);
    }
    assert(!bench_cpu_options("68030","normal",&cpu,&mode));
    assert(!bench_cpu_options("auto","2",&cpu,&mode));
    assert(!bench_cpu_options("","exact",&cpu,&mode));
}'''
        with tempfile.TemporaryDirectory(dir=b.BUILD) as tmp:
            path = Path(tmp)/"parse.cpp"; path.write_text(source)
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I"+str(b.ROOT/"tests/bench"), str(path), "-o", str(Path(tmp)/"parse")], check=True, capture_output=True)
            subprocess.run([str(Path(tmp)/"parse")], check=True)
