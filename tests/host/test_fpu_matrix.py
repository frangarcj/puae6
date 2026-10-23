"""FPU corpus goldens and opt-in executions across every declared compiler/ISA."""
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from tests.host import fpu_matrix
from tests.portability import row_driver, run_matrix
from tests.portability.rows import ROWS
from tests.portability.test_portability import runnable_rows

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests/host/fpu_matrix.golden.json").read_text())


class FpuMatrixTests(unittest.TestCase):
    def test_corpus_matches_the_regression_fixture(self):
        with tempfile.TemporaryDirectory(prefix="p6-fpu-corpus-", dir=ROOT / "build") as directory:
            cases, digest = fpu_matrix.write_corpus(directory)
        self.assertEqual({"cases": len(cases), "sha256": digest}, GOLDEN["corpus"])
        self.assertEqual({case["id"] for case in fpu_matrix.listing()}, set(GOLDEN["cases"]))
        self.assertEqual(GOLDEN["schema_version"], 2)
        self.assertEqual(GOLDEN["probe_schema"], row_driver.PROBE_SCHEMA)

    def test_container_command_keeps_fpu_corpus_offline(self):
        # Infrastructure observation only: no container or emulated CPU runs here.
        with tempfile.TemporaryDirectory(prefix="p6-fpu-command-", dir=ROOT / "build") as directory:
            for name, spec in ROWS.items():
                if spec["kind"] != "docker":
                    continue
                row = fpu_matrix.FpuRow(name)
                row.host_dir = Path(directory) / name
                with patch.object(fpu_matrix.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    code, _ = row.run("fixture command", row.host_dir / "run.log", 30)
                self.assertEqual(code, 0)
                argv = run.call_args.args[0]
                for flag in ("--rm", "--pull=never", "--network=none"):
                    self.assertEqual(argv.count(flag), 1)
                self.assertEqual(argv[argv.index("--platform") + 1], spec["platform"])
                for mount in (f"{ROOT}:/src:ro", f"{row.host_dir}:/src/build",
                              f"{fpu_matrix.CORPUS}:/corpus:ro"):
                    self.assertIn(mount, argv)
                self.assertEqual(argv[-4:], [spec["image"], "bash", "-c", "fixture command"])
                self.assertEqual(run.call_args.kwargs["timeout"], 30)

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
                self.assertEqual(again["probe_schema"], GOLDEN["probe_schema"])
                self.assertEqual(again["errors"], {})
                self.assertEqual(set(again["cases"]), set(GOLDEN["cases"]))
                for case, expected in GOLDEN["cases"].items():
                    for field in ("hardware", "av", "units"):
                        self.assertEqual(expected[field], again["cases"][case][field], f"{row} {case} {field}")


if __name__ == "__main__":
    unittest.main()
