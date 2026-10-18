"""Portability configuration, immutable regression hashes and opt-in real executions.

P6_TEST_PORTABILITY=1 executes every declared row at O0, O2 and under sanitizers.
Historical PASS records are not inputs to a gate. Missing runners cause a skip/BLOCKED.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.portability import corpus
from tests.portability.rows import OPT_ORDER, OUT_OF_SCOPE, ROWS

ROOT = Path(__file__).resolve().parents[2]
TOOLCHAINS = json.loads((ROOT / "plan/toolchains.json").read_text())


def golden(name):
    return json.loads((ROOT / f"tests/portability/{name}.golden.json").read_text())


def check_corpus(test, expected, writer):
    with tempfile.TemporaryDirectory(prefix="p6-corpus-") as directory:
        listing = writer(directory)
        digest = hashlib.sha256()
        for path in sorted(Path(directory).iterdir()):
            digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    test.assertEqual({c["id"] for c in listing}, set(expected["cases"]))
    test.assertEqual(len(listing), expected["corpus"]["cases"])
    test.assertEqual(digest.hexdigest(), expected["corpus"]["sha256"])
    for case in expected["cases"].values():
        test.assertEqual((len(case["hardware"]), len(case["av"])), (64, 64))
        test.assertNotEqual(case["hardware"], case["av"])


class MatrixCoherenceTests(unittest.TestCase):
    def test_toolchain_files_match_the_lock(self):
        recorded = TOOLCHAINS["portability_matrix"]["toolchain_files"]
        self.assertEqual(set(recorded), {s["toolchain"] for s in ROWS.values()})
        for path, digest in recorded.items():
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest, path)

    def test_declared_rows_and_scope_match_the_lock(self):
        self.assertFalse(set(ROWS) & set(OUT_OF_SCOPE))
        self.assertEqual(set(TOOLCHAINS["portability_matrix"]["rows"]), set(ROWS))
        self.assertEqual(set(TOOLCHAINS["portability_matrix"]["out_of_scope"]), set(OUT_OF_SCOPE))
        for name, spec in ROWS.items():
            for field in ("runner", "isa", "native_hardware"):
                self.assertEqual(TOOLCHAINS["portability_matrix"]["rows"][name][field], spec[field])
        self.assertFalse(ROWS["linux-x86_64-clang"]["native_hardware"])

    def test_public_corpus_matches_the_regression_fixture(self):
        expected = golden("corpus")
        check_corpus(self, expected, corpus.write_corpus)
        self.assertGreater(len({c["hardware"] for c in expected["cases"].values()}), 80)
        self.assertGreater(len({c["av"] for c in expected["cases"].values()}), 50)


class DifferentialMatrixTests(unittest.TestCase):
    def test_scenarios_match_the_regression_fixture(self):
        expected = golden("differential")
        check_corpus(self, expected, corpus.write_differential)
        from tests.scenarios.corpus import corpus as differential
        self.assertEqual({e.id for e in differential.ENTRIES}, set(expected["cases"]))


def docker_ready():
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def runnable_rows():
    native = sys.platform == "darwin" and os.uname().machine == "arm64"
    docker = docker_ready()
    return [n for n, s in ROWS.items() if (s["kind"] == "native" and native) or (s["kind"] == "docker" and docker)]


def execute_matrix(test, dataset):
    expected = golden("differential" if dataset else "corpus")
    available = runnable_rows()
    test.assertTrue(available, "no runnable row")
    suffix = "-differential" if dataset else ""
    for name in ROWS:
        for opt in (*OPT_ORDER, "san"):
            with test.subTest(row=name, opt=opt):
                if name not in available:
                    test.skipTest(f"{name} cannot execute here")
                result = subprocess.run([sys.executable, str(ROOT / "tests/portability/run_matrix.py"), "exec", name, opt, "--drop-tree"],
                                        cwd=ROOT, env={**os.environ, "P6_MATRIX_SET": dataset}, capture_output=True, text=True)
                test.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-1500:])
                folder = ROOT / "build/portability" / name
                base = json.loads((folder / f"{opt}-baseline{suffix}.json").read_text())
                host = json.loads((folder / f"{opt}-host{suffix}.json").read_text())
                record = json.loads((folder / f"{opt}-record{suffix}.json").read_text())
                test.assertTrue(record["faketime"]["shifted"], "DET06 requires another host clock")
                test.assertEqual(base["errors"], {})
                test.assertEqual(host["errors"], {})
                test.assertEqual(set(base["cases"]), set(expected["cases"]))
                for case_id, wanted in expected["cases"].items():
                    got, shifted = base["cases"][case_id], host["cases"][case_id]
                    for field in ("hardware", "av"):
                        test.assertEqual(got[field], wanted[field], f"{name}/{opt} {case_id} {field}")
                        test.assertEqual(shifted[field], got[field], f"DET06 {name}/{opt} {case_id} {field}")


@unittest.skipUnless(os.environ.get("P6_TEST_PORTABILITY") == "1", "P6_TEST_PORTABILITY=1 executes the actual matrix")
class RowExecutionTests(unittest.TestCase):
    def test_all_variants_against_goldens_and_a_different_host_environment(self):
        execute_matrix(self, "")


@unittest.skipUnless(os.environ.get("P6_TEST_PORTABILITY") == "1", "P6_TEST_PORTABILITY=1 executes the actual matrix")
class DifferentialRowExecutionTests(unittest.TestCase):
    def test_all_variants_against_goldens_and_a_different_host_environment(self):
        execute_matrix(self, "differential")


if __name__ == "__main__":
    unittest.main()
