"""T35c scoped gates; no matrix or measurements means BLOCKED, never PASS."""
import json
import os
import tempfile
import unittest

from tests.simd import frontend_matrix as matrix
from tests.simd import frontend_rgb565 as frontend
from tests.simd.frontend_statistics import compare


def current_inputs(row):
    directory = matrix.directory(row)
    binaries = frontend.core_paths(row, directory / "opt")
    with tempfile.TemporaryDirectory(prefix="p6-timing-inputs-") as work:
        frontend.assets.write_differential(work)
        return frontend.timing_inputs({**binaries, "host": directory / "opt/p6_libretro_host"}, work)


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare the T35c frontend rows and set P6_TEST_RGB565_FRONTEND=1")
class EquivalenceTests(unittest.TestCase):
    def test_actual_rgb565_and_same_and_new_process_restore_in_all_declared_rows(self):
        results = matrix.verify()
        for row, result in results.items():
            self.assertEqual(len(result["cases"]), 27, row)
            self.assertEqual(set(result["build"]["binaries"]), {"current", "candidate", "host"})
            variant = "candidate" if row in frontend.NEON_PRODUCT_ROWS else "current"
            self.assertEqual(result["build"]["product_variant"], variant)
            extension = ".dylib" if row.startswith("macos-") else ".so"
            self.assertEqual(result["build"]["binaries"][variant], frontend.digest(matrix.directory(row) / "opt" / ("puae6_libretro" + extension)))
            for case in result["cases"].values():
                self.assertEqual(len(case["runs"]), 14)
                self.assertTrue(case["same_process_restore"] and case["new_process_restore"])


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare T35c sanitizer rows")
class SafetyTests(unittest.TestCase):
    def test_full_rgb565_frontend_and_restore_under_sanitizers_in_every_row(self):
        results = matrix.verify(True)
        self.assertEqual(set(results), set(matrix.ROWS))
        for row, result in results.items():
            optimized = json.loads((matrix.directory(row) / "opt/verification.json").read_text())["cases"]
            self.assertEqual(set(result["cases"]), set(optimized), row)
            for name, case in result["cases"].items():
                self.assertEqual(case["runs"], optimized[name]["runs"], f"{row}: sanitizers change {name}")
            commands = json.loads((matrix.directory(row) / "san/compile_commands.json").read_text())
            tested = [c["command"] for c in commands if any(target in c["command"] for target in
                      ("p6_engine.dir/", "puae6_libretro.dir/", "puae6_libretro_rgb565_alternate.dir/"))]
            self.assertTrue(tested, row)
            for command in tested:
                self.assertIn("-fsanitize=address,undefined", command, row)
                self.assertIn("-fno-sanitize-recover=all", command, row)


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "run the T35c measurements first")
class PerformanceTests(unittest.TestCase):
    def test_each_measured_row_uses_both_real_libraries_and_the_full_protocol(self):
        for row in matrix.MEASURED:
            r = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
            verified = json.loads((matrix.directory(row) / "opt/verification.json").read_text())
            self.assertEqual(r["inputs"], verified["inputs"])
            self.assertEqual(r["inputs"], current_inputs(row))
            self.assertEqual(len(r["runs"]), 50)
            for scenario in frontend.WORKLOADS:
                facts = []
                for variant in ("current", "candidate"):
                    samples = [x for x in r["runs"] if x["scenario"] == scenario.id and x["variant"] == variant]
                    self.assertEqual({x["repeat"] for x in samples}, set(range(5)))
                    for sample in samples:
                        self.assertEqual(sample["warmup"], 300)
                        self.assertEqual(sample["units"], 10000 if scenario.primary else 3000)
                        raw = sample["raw"]
                        self.assertEqual(len(raw["run_cpu_ns"]), 300 + sample["units"])
                        self.assertTrue(all(n > 0 for n in raw["run_cpu_ns"]))
                        self.assertEqual(len(raw["state_load_ns"]), 20)
                        facts.append(tuple(raw[k] for k in ("state_bytes", "pictures", "audio_frames")) +
                                     (tuple(sample["last_run"]), tuple(sample["final_state"])))
                self.assertEqual(len(set(facts)), 1, scenario.id)
                comparison = compare([x for x in r["runs"] if x["scenario"] == scenario.id])
                self.assertEqual(len(comparison["paired_reductions_percent"]), 5)
                for variant in ("current", "candidate"):
                    self.assertEqual(comparison["variants"][variant]["median_cpu_ns"], r["summary"][scenario.id][variant]["median_cpu_ns"])


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "T35c needs the measured platform decision")
class DecisionTests(unittest.TestCase):
    def test_the_decision_covers_each_measured_row_and_preserves_the_other_platforms(self):
        adr = (frontend.ROOT / "docs/adr/0004-native-optimizations.md").read_text()
        self.assertIn("## Decision de frontend T35c", adr, "T35c requires the measured platform decision")
        for row in matrix.MEASURED:
            self.assertIn(row, adr)
            result = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
            self.assertEqual(result["inputs"], current_inputs(row))


if __name__ == "__main__":
    unittest.main()
