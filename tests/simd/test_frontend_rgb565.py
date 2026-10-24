"""T35c scoped gates; no matrix or measurements means BLOCKED, never PASS."""
import json
import os
import unittest

from tests.simd import frontend_matrix as matrix
from tests.simd import frontend_rgb565 as frontend


def current_inputs(row):
    return matrix.current_inputs(row)


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare the T35c frontend rows and set P6_TEST_RGB565_FRONTEND=1")
class EquivalenceTests(unittest.TestCase):
    def test_actual_rgb565_and_same_and_new_process_restore_in_all_declared_rows(self):
        results = matrix.verify()
        matrix.compare_rows(results)


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "prepare T35c sanitizer rows")
class SafetyTests(unittest.TestCase):
    def test_full_rgb565_frontend_and_restore_under_sanitizers_in_every_row(self):
        results = matrix.verify(True)
        self.assertEqual(set(results), set(matrix.ROWS))
        for row, result in results.items():
            optimized = json.loads((matrix.directory(row) / "opt/verification.json").read_text())
            matrix.compare_variants(row, optimized, result)
            commands = matrix.directory(row) / 'san/compile_commands.json'
            matrix.sanitizer_commands(json.loads(commands.read_text()), frontend.digest(commands))


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "run the T35c measurements first")
class PerformanceTests(unittest.TestCase):
    def test_each_measured_row_uses_both_real_libraries_and_the_full_protocol(self):
        for row in matrix.MEASURED:
            r = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
            verified = json.loads((matrix.directory(row) / "opt/verification.json").read_text())
            matrix.validate_measurements(r, verified['inputs'], current_inputs(row))


@unittest.skipUnless(os.environ.get("P6_TEST_RGB565_FRONTEND") == "1", "T35c needs the measured platform decision")
class DecisionTests(unittest.TestCase):
    def test_the_decision_covers_each_measured_row_and_preserves_the_other_platforms(self):
        adr = (frontend.ROOT / "docs/adr/0004-native-optimizations.md").read_text()
        self.assertIn("## Decision de frontend T35c", adr, "T35c requires the measured platform decision")
        for row in matrix.MEASURED:
            result = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
            matrix.validate_decision(row, result['inputs'], current_inputs(row), adr)


if __name__ == "__main__":
    unittest.main()
