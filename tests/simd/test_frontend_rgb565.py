"""T35c scoped gates; no matrix or measurements means BLOCKED, never PASS."""
import json
import os
import unittest

from tests.simd import frontend_matrix as matrix
from tests.simd import frontend_rgb565 as frontend
from tests.simd.frontend_statistics import compare


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare the T35c frontend rows and set P6_TEST_RGB565_FRONTEND=1")
class EquivalenceTests(unittest.TestCase):
    def test_actual_rgb565_and_same_and_new_process_restore_in_all_declared_rows(self):
        results = matrix.verify()
        for row, result in results.items():
            self.assertEqual(len(result["cases"]), 27, row)
            self.assertEqual(set(result["build"]["binaries"]), {"current", "candidate", "host"})
            for case in result["cases"].values():
                self.assertEqual(len(case["runs"]), 14)
                self.assertTrue(case["same_process_restore"] and case["new_process_restore"])


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare T35c sanitizer rows")
class SafetyTests(unittest.TestCase):
    def test_full_rgb565_frontend_and_restore_under_sanitizers_in_every_row(self):
        self.assertEqual(set(matrix.verify(True)), set(matrix.ROWS))
        for row in matrix.ROWS:
            commands = json.loads((matrix.directory(row) / "san/compile_commands.json").read_text())
            tested = [c["command"] for c in commands if any(target in c["command"] for target in
                      ("p6_engine.dir/", "puae6_libretro.dir/", "puae6_libretro_rgb565_candidate.dir/"))]
            self.assertTrue(tested, row)
            for command in tested:
                self.assertIn("-fsanitize=address,undefined", command, row)
                self.assertIn("-fno-sanitize-recover=all", command, row)


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "run the T35c measurements first")
class PerformanceTests(unittest.TestCase):
    def test_each_measured_row_uses_both_real_libraries_and_the_full_protocol(self):
        for row in matrix.MEASURED:
            r = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
            self.assertEqual(r["verification_sha256"], frontend.digest(matrix.directory(row) / "opt/verification.json"))
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
            self.assertEqual(result["verification_sha256"], frontend.digest(matrix.directory(row) / "opt/verification.json"))


if __name__ == "__main__":
    unittest.main()
