"""T53 harness tests use synthetic records; CpuCampaignMeasurements alone consumes real measurements."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import bench as b
from tests.bench.test_statistics import record


class CpuCampaignHarnessTests(unittest.TestCase):
    def test_matrix_requires_every_production_runner_and_uses_the_real_protocol(self):
        rows = b.cpu_campaign_matrix(False)
        self.assertEqual(27, len(rows))
        self.assertEqual({"68000", "68010", "68020"}, {s.cpu_model for s, _, _, _ in rows})
        self.assertEqual({"exact", "compatible", "normal"}, {s.cpu_compatibility for s, _, _, _ in rows})
        self.assertTrue(all(w == 300 and u == (10000 if s.primary else 3000) for s, _, u, w in rows))
        with patch.object(b.NativeRunner, "available", return_value=False), self.assertRaisesRegex(ValueError, "unavailable"):
            b.cpu_campaign_matrix()

    def test_comparison_rejects_quick_subsets_and_protocol_overrides_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(b, "BASELINES", Path(tmp)):
            defaults = dict(root=tmp, id="unused", authorized_downloads=False, compare_cpu_levels=True,
                            quick=False, reps=5, state_rounds=20, scenario=[], runner=[], cpu="auto", cpu_compatibility="exact")
            for change in (dict(quick=True), dict(reps=4), dict(state_rounds=1), dict(runner=["native"]), dict(cpu="68010")):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, "protocol override"):
                    b.run_command(b.argparse.Namespace(**{**defaults, **change}))

    def test_identity_binds_artifacts_guest_receipt_and_sources_without_git_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("p6_bench_probe", "p6_libretro_host", "puae6_libretro.dylib", "Ffi.dll", "guest", "runtime"):
                (root / name).write_bytes(name.encode())
            (root / "CMakeCache.txt").write_text("CMAKE_BUILD_TYPE:STRING=Release\nP6_SANITIZERS:BOOL=OFF\nCMAKE_CXX_FLAGS_RELEASE:STRING=-O2\nCMAKE_C_FLAGS_RELEASE:STRING=-O2\n")
            guest = root / "guest.json"; runtime = root / "runtime.json"
            guest.write_text(json.dumps({"guest": {"path": str(root / "guest"), "sha256": b.sha256_of(root / "guest")},
                                          "sources": {"tools/bench.py": b.sha256_of(b.ROOT / "tools/bench.py")}}))
            runtime.write_text(json.dumps({"runtime_dylib": {"path": str(root / "runtime"), "sha256": b.sha256_of(root / "runtime")}}))
            with patch.object(b, "NATIVE_TREE", root), patch.object(b, "FFI_RELEASE", root), patch.object(b, "GUEST_RECEIPT", guest), \
                 patch.object(b, "RELEASE_RUNTIME_RECEIPT", runtime), patch.object(b.Scenario, "rom", return_value=root / "guest"):
                original = b.cpu_campaign_identity()
                (root / "Ffi.dll").write_bytes(b"another artifact")
                self.assertNotEqual(original, b.cpu_campaign_identity())
                (root / "guest").write_bytes(b"stale receipt")
                with self.assertRaisesRegex(ValueError, "receipt-verified"): b.cpu_campaign_identity()

    def test_validator_rejects_missing_stale_mislabeled_and_unmeasured_rows(self):
        identity, records = {"synthetic": "harness only"}, {}
        real_read = b.read_raw
        with tempfile.TemporaryDirectory() as tmp, patch.object(b, "cpu_campaign_identity", return_value=identity), \
             patch.object(b, "read_raw", side_effect=lambda path: records[Path(path).name]):
            directory = Path(tmp); (directory / "raw").mkdir()
            (directory / "environment.json").write_text(json.dumps({"cpu_campaign": identity}))
            for rep in range(1, 6):
                for pos, (s, runner, units, warmup) in enumerate(b.cpu_campaign_matrix(False)):
                    sequence = (rep - 1) * 27 + pos
                    data = record(scenario=s.id, scenario_base=s.base_id, runner=runner.id, layer=runner.layer, rep=rep,
                                  cpu_model=s.cpu_model, cpu_compatibility=s.cpu_compatibility, machine=s.machine, standard=s.standard,
                                  units=units, index=units + warmup, ticks=100 + pos // 3, warmup=warmup, state_rounds=20,
                                  campaign_identity=b.campaign_hash(identity), campaign_sequence=sequence, started_ns=sequence * 10 + 1, ended_ns=sequence * 10 + 2)
                    if runner.id == "waterbox-core-release": data["facts"].update(managed_build="Release", evict_blocks_forced_off=True)
                    for key in ("unit_wall_ns", "unit_cpu_ns"): data[key] = [1000] * units
                    for key in ("state_save_ns", "state_load_ns", "first_unit_after_load_ns"): data[key] = [2000] * 20
                    name = f"{s.id}__{runner.id}__rep{rep}.json.gz"; records[name] = data
                    (directory / "raw" / name).touch()
            rows = b.validate_cpu_campaign(directory)
            self.assertEqual(27, len(rows))
            self.assertTrue(all(row["state_cost_ns"]["state_save_ns"]["p95"] == 2000 and row["peak_rss_bytes"] > 0 for row in rows.values()))
            self.assertTrue(all(row["stable"] and row["p50_vs_exact"] == 1 for row in rows.values()))
            name = next(iter(records)); good = records[name]
            for key, bad in (("campaign_identity", "stale"), ("cpu_model", "auto"), ("warmup", 1), ("state_rounds", 1),
                             ("started_ns", 0), ("campaign_sequence", 1), ("unit_wall_ns", [1]), ("state_load_ns", []), ("master_ticks_at_window_end", 999)):
                records[name] = {**good, key: bad}
                with self.subTest(key=key), self.assertRaises(ValueError): b.validate_cpu_campaign(directory)
            records[name] = good
            (directory / "raw" / name).unlink()
            with self.assertRaisesRegex(ValueError, "coverage"): b.validate_cpu_campaign(directory)
            (directory / "raw" / name).touch()
            with patch.object(b, "cpu_campaign_identity", return_value={"different": "artifact"}), self.assertRaisesRegex(ValueError, "identity"):
                b.validate_cpu_campaign(directory)
            self.assertIsNone(b.validate_cpu_campaign(directory, partial=True))  # Even a full prefix is not PASS.
            first, second = list(records)[:2]
            for name in list(records)[2:]: (directory / "raw" / name).unlink()
            self.assertIsNone(b.validate_cpu_campaign(directory, partial=True))
            (directory / "raw" / first).unlink()
            with self.assertRaisesRegex(ValueError, "coverage"): b.validate_cpu_campaign(directory, partial=True)
            (directory / "raw" / first).touch()
            records[first] = {**records[first], "campaign_identity": "stale"}
            with self.assertRaisesRegex(ValueError, "identity"): b.validate_cpu_campaign(directory, partial=True)
            records[first] = good
            with patch.object(b, "read_raw", side_effect=real_read):
                b.write_raw(directory / "raw" / first, records[first])
                (directory / "raw" / second).write_bytes(b"\x1f\x8b")  # Interrupted gzip write.
                with self.assertRaises(EOFError): b.validate_cpu_campaign(directory, partial=True)


class CpuCampaignMeasurements(unittest.TestCase):
    def test_complete_current_cpu_campaign(self):
        directory = Path(os.environ.get("P6_CPU_PERF_DIR", b.BASELINES / "t53-cpu"))
        if os.environ.get("P6_RUN_CPU_PERF") == "1":
            subprocess.run([sys.executable, str(b.ROOT / "tools/bench.py"), "run", "--id", directory.name,
                            "--root", str(directory.parent), "--compare-cpu-levels"], check=True, timeout=4 * 3600)
        if not (directory / "environment.json").is_file():
            self.skipTest("no real CPU campaign; run tools/bench.py run --id t53-cpu --compare-cpu-levels")
        rows = b.validate_cpu_campaign(directory)
        self.assertEqual(27, len(rows))
        for key, row in sorted(rows.items()):
            print(key, "p50_ms", b.ms(row["median_of_run_medians_ns"]), "p95_ms", b.ms(row["pooled"]["p95"]),
                  "p50/p95_vs_exact", row["p50_vs_exact"], row["p95_vs_exact"], "stable", row["stable"],
                  "state_cost_ns", row["state_cost_ns"], "peak_rss_bytes", row["peak_rss_bytes"])
        # Goals remain advisory (no dedicated runner); no threshold is promoted here.
