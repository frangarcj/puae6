"""FPU corpus goldens and opt-in executions across every declared compiler/ISA."""
import json
from pathlib import Path
import os
import tempfile
import unittest

from tests.host import fpu_matrix
from tests.portability import run_matrix
from tests.portability.rows import ROWS
from tests.portability.test_portability import runnable_rows

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests/host/fpu_matrix.golden.json").read_text())


class FpuMatrixTests(unittest.TestCase):
    def test_corpus_matches_the_regression_fixture(self):
        with tempfile.TemporaryDirectory(prefix="p6-fpu-corpus-") as directory:
            cases, digest = fpu_matrix.write_corpus(directory)
        self.assertEqual({"cases": len(cases), "sha256": digest}, GOLDEN["corpus"])
        self.assertEqual({case["id"] for case in fpu_matrix.listing()}, set(GOLDEN["cases"]))

    @unittest.skipUnless(os.environ.get("P6_TEST_FPU_MATRIX") == "1", "P6_TEST_FPU_MATRIX=1 executes all declared rows")
    def test_every_declared_row_reproduces_the_fpu_hashes(self):
        available = runnable_rows()
        self.assertTrue(available)
        for row in ROWS:
            with self.subTest(row=row):
                if row not in available:
                    self.skipTest(f"{row} cannot execute here")
                self.assertEqual(0, fpu_matrix.exec_row(row, "O0", skip_build=False))
                again = run_matrix.load(run_matrix.BUILD / row / "O0-fpu-baseline.json")
                self.assertEqual(again["errors"], {})
                self.assertEqual(set(again["cases"]), set(GOLDEN["cases"]))
                for case, expected in GOLDEN["cases"].items():
                    for field in ("hardware", "av"):
                        self.assertEqual(expected[field], again["cases"][case][field], f"{row} {case} {field}")


if __name__ == "__main__":
    unittest.main()
