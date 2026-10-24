"""Fresh executions, not a PASS inferred from saved evidence. Missing opt-in blocks the gate."""
import os
import unittest

from tests.portability.rows import ROWS
from tests.simd import matrix


@unittest.skipUnless(os.environ.get("P6_TEST_SIMD_MATRIX") == "1", "set P6_TEST_SIMD_MATRIX=1; Docker must be running")
class EquivalenceTests(unittest.TestCase):
    def test_actual_and_forced_scalar_routes_agree_in_every_declared_row(self):
        result = matrix.run(("opt", "fallback"))
        self.assertEqual(set(ROWS), set(result["rows"]))


@unittest.skipUnless(os.environ.get("P6_TEST_SIMD_MATRIX") == "1", "set P6_TEST_SIMD_MATRIX=1; Docker must be running")
class SafetyTests(unittest.TestCase):
    def test_sanitizers_guards_and_scalar_routes_in_every_declared_row(self):
        result = matrix.run(("san",))
        self.assertEqual(set(ROWS), set(result["rows"]))


if __name__ == "__main__":
    unittest.main()
