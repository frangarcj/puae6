"""DET04 for the FPU (T54): the evidence of tests/host/fpu_matrix.py says that every executed row of the platform matrix hashes the FPU corpus as the reference row does.

The rows are run by `python3 tests/host/fpu_matrix.py exec ROW OPT` (native on the Mac, in Docker for the Linux rows) and recorded by `fpu_matrix.py report --write`; this test reads
the recorded evidence and checks that it is complete and consistent with the corpus of the checkout, so that a change of the programs or of the case list that is not followed by a
new run of the matrix fails here. Executing the rows needs Docker and takes a long time: P6_TEST_FPU_MATRIX=1 runs the native row again and compares it with the evidence.
"""
import json
from pathlib import Path
import os
import unittest

from tests.host import fpu_matrix

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = json.loads((ROOT / "tests/host/fpu_matrix_evidence.json").read_text())
DECLARED_ROWS = {"macos-arm64-appleclang", "linux-arm64-clang", "linux-arm64-gcc", "linux-x86_64-clang", "linux-x86_64-gcc"}


class FpuMatrixEvidenceTests(unittest.TestCase):
    def test_the_cases_of_the_evidence_are_the_cases_of_the_corpus(self):
        self.assertEqual(sorted(case["id"] for case in fpu_matrix.listing()), sorted(EVIDENCE["cases"]))

    def test_the_corpus_that_was_run_is_the_one_in_the_checkout(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="p6-fpu-corpus-") as directory:
            cases, digest = fpu_matrix.write_corpus(directory)
        self.assertEqual({"cases": len(cases), "sha256": digest}, EVIDENCE["corpus"], "the ROMs or the case list changed since the matrix ran: run it again")

    def test_every_declared_row_ran_at_O0_and_hashes_like_the_reference(self):
        ran = {(e["row"], e["opt"]) for e in EVIDENCE["executions"]}
        self.assertEqual(DECLARED_ROWS, {row for row, opt in ran if opt == "O0"})
        self.assertTrue(EVIDENCE["all_rows_equal_to_the_reference"])
        for execution in EVIDENCE["executions"]:
            base = execution["baseline"]
            with self.subTest(row=execution["row"], opt=execution["opt"]):
                self.assertEqual({}, base["errors"])
                self.assertEqual(base["cases"], base["hardware_equal"], "the machine (CPU, FPU registers, RAM with the results) differs from the reference")
                self.assertEqual(base["cases"], base["av_equal"], "the picture or the samples differ from the reference")
                self.assertEqual([], base["differences"])

    def test_both_isas_and_both_compilers_are_in_the_evidence(self):
        isas = {(e["isa"], e["compiler"]) for e in EVIDENCE["executions"]}
        self.assertEqual({("arm64", "Apple Clang"), ("arm64", "Clang"), ("arm64", "GCC"), ("x86_64", "Clang"), ("x86_64", "GCC")}, isas)

    @unittest.skipUnless(os.environ.get("P6_TEST_FPU_MATRIX"), "opt-in: P6_TEST_FPU_MATRIX=1 runs the native row again")
    def test_the_native_row_again_gives_the_recorded_hashes(self):
        self.assertEqual(0, fpu_matrix.exec_row("macos-arm64-appleclang", "O0", skip_build=True))
        from tests.portability import run_matrix as rm
        again = rm.load(rm.BUILD / "macos-arm64-appleclang" / "O0-fpu-baseline.json")
        for case, expected in EVIDENCE["cases"].items():
            self.assertEqual((expected["hardware"], expected["av"]), (again["cases"][case]["hardware"], again["cases"][case]["av"]), case)


if __name__ == "__main__":
    unittest.main()
