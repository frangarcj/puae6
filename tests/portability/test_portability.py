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
from unittest import mock

from tests.portability import corpus
from tests.portability.row_driver import PROBE_SCHEMA
from tests.portability.rows import OPT_ORDER, OUT_OF_SCOPE, ROWS
from tools.ci.matrix_checks import check_observations

ROOT = Path(__file__).resolve().parents[2]
TOOLCHAINS = json.loads((ROOT / "plan/toolchains.json").read_text())
_MATRIX_RUN = None


def golden(name):
    return json.loads((ROOT / f"tests/portability/{name}.golden.json").read_text())


def check_corpus(test, expected, writer):
    test.assertIs(type(expected["probe_schema"]), int)
    test.assertEqual(expected["probe_schema"], PROBE_SCHEMA)
    with tempfile.TemporaryDirectory(prefix="p6-corpus-") as directory:
        listing = writer(directory)
        digest = hashlib.sha256()
        for path in sorted(Path(directory).iterdir()):
            digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    test.assertEqual({c["id"] for c in listing}, set(expected["cases"]))
    test.assertEqual(len(listing), expected["corpus"]["cases"])
    test.assertEqual(digest.hexdigest(), expected["corpus"]["sha256"])
    for case in expected["cases"].values():
        test.assertIs(type(case["probe_schema"]), int)
        test.assertEqual(case["probe_schema"], PROBE_SCHEMA)
        test.assertEqual((len(case["hardware"]), len(case["av"])), (64, 64))
        test.assertNotEqual(case["hardware"], case["av"])


class MatrixCoherenceTests(unittest.TestCase):
    def test_toolchain_files_match_the_lock(self):
        recorded = TOOLCHAINS["portability_matrix"]["toolchain_files"]
        self.assertEqual(set(recorded), {s["toolchain"] for s in ROWS.values()})
        for path, digest in recorded.items():
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest, path)

    def test_container_pins_match_the_declared_platforms(self):
        from tests.simd.matrix import IMAGES
        platforms = {s["platform"] for s in ROWS.values() if s["kind"] == "docker"}
        self.assertEqual(set(IMAGES), platforms)
        self.assertEqual(set(TOOLCHAINS["portability_matrix"]["images"]), platforms)
        for image in IMAGES.values():
            self.assertRegex(image, r"^sha256:[0-9a-f]{64}$")

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


def matrix_identity():
    from tools.ci.run import identity
    return json.dumps(identity(), sort_keys=True)


def check_matrix_result(test, dataset, name, opt):
    expected = golden("differential" if dataset else "corpus")
    suffix = "-differential" if dataset else ""
    folder = ROOT / "build/portability" / name
    base = json.loads((folder / f"{opt}-baseline{suffix}.json").read_text())
    host = json.loads((folder / f"{opt}-host{suffix}.json").read_text())
    record = json.loads((folder / f"{opt}-record{suffix}.json").read_text())
    test.assertTrue(record["faketime"]["shifted"], "DET06 requires another host clock")
    check_observations(expected, base, host, f"{name}/{opt}")


def execute_matrix(test, dataset):
    global _MATRIX_RUN
    previous, _MATRIX_RUN = _MATRIX_RUN, None  # Errors, including subTest failures, cannot preserve success.
    identity = matrix_identity()
    available = runnable_rows()
    test.assertTrue(available, "no runnable row")
    cells = len(ROWS) * (len(OPT_ORDER) + 1)
    complete = previous == identity
    if not complete:
        executed = 0
        for name in ROWS:
            for opt in (*OPT_ORDER, "san"):
                # Both normal builds check the same tree; only the second removes it.
                for current in ("", "differential"):
                    with test.subTest(dataset=current, row=name, opt=opt):
                        if name not in available:
                            test.skipTest(f"{name} cannot execute here")
                        command = [sys.executable, str(ROOT / "tests/portability/run_matrix.py"), "exec", name, opt]
                        if current: command.append("--drop-tree")
                        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "P6_MATRIX_SET": current}, capture_output=True, text=True)
                        test.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-1500:])
                        check_matrix_result(test, current, name, opt)
                        test.assertEqual(identity, matrix_identity(), "matrix inputs changed during execution")
                        executed += 1
        complete = executed == 2 * cells
    checked = 0
    # Each public test re-reads its own JSON and current goldens, never a saved PASS.
    for name in ROWS:
        for opt in (*OPT_ORDER, "san"):
            with test.subTest(row=name, opt=opt):
                if name not in available:
                    test.skipTest(f"{name} cannot execute here")
                check_matrix_result(test, dataset, name, opt)
                test.assertEqual(identity, matrix_identity(), "matrix inputs changed before result validation")
                checked += 1
    if complete and checked == cells:
        _MATRIX_RUN = identity


class MatrixBuildReuseTests(unittest.TestCase):
    """Own JSON fixtures and mocked subprocesses; no compiler, Docker or emulator."""

    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix="matrix-reuse-", dir=ROOT / "build")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.module = sys.modules[__name__]
        self.enterContext(mock.patch.object(self.module, "ROOT", self.root))
        self.enterContext(mock.patch.object(self.module, "_MATRIX_RUN", None))
        self.identity = self.enterContext(mock.patch.object(self.module, "matrix_identity", return_value="own inputs A"))
        self.enterContext(mock.patch.object(self.module, "runnable_rows", return_value=list(ROWS)))
        self.expected = {name: {"probe_schema": PROBE_SCHEMA, "cases": {"own-" + name: {
            "probe_schema": PROBE_SCHEMA, "hardware": h * 64, "av": a * 64}}}
            for name, h, a in (("corpus", "a", "b"), ("differential", "c", "d"))}
        self.enterContext(mock.patch.object(self.module, "golden", side_effect=lambda name: self.expected[name]))
        self.process = self.enterContext(mock.patch.object(subprocess, "run", side_effect=self.run_cell))

    def run_cell(self, command, *, env, **kwargs):
        name, opt = command[3:5]
        dataset = env["P6_MATRIX_SET"]
        folder = self.root / "build/portability" / name
        tree = folder / opt
        if dataset:
            self.assertTrue(tree.is_dir(), "the first build survives for the second corpus")
        else:
            tree.mkdir(parents=True, exist_ok=True)
        expected = self.expected[dataset or "corpus"]
        result = {**expected, "errors": {}}
        suffix = "-differential" if dataset else ""
        for variant, data in (("baseline", result), ("host", result), ("record", {"faketime": {"shifted": True}})):
            (folder / f"{opt}-{variant}{suffix}.json").write_text(json.dumps(data))
        if "--drop-tree" in command: shutil.rmtree(tree)
        return subprocess.CompletedProcess(command, 0, "", "")

    def invoke(self, dataset):
        case = unittest.FunctionTestCase(lambda: execute_matrix(case, dataset))
        result = unittest.TestResult()
        case.run(result)  # Capture genuine subTest failures, not just raised exceptions.
        return result

    def assert_success(self, result):
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_both_methods_keep_every_cell_and_host_check_with_only_second_corpus_dropping_tree(self):
        self.assert_success(self.invoke("differential"))
        self.assert_success(self.invoke(""))
        calls = self.process.call_args_list
        self.assertEqual(len(ROWS) * 3 * 2, len(calls))
        expected = [(name, opt, dataset) for name in ROWS for opt in (*OPT_ORDER, "san") for dataset in ("", "differential")]
        self.assertEqual(expected, [(c.args[0][3], c.args[0][4], c.kwargs["env"]["P6_MATRIX_SET"]) for c in calls])
        for call in calls:
            command = call.args[0]
            self.assertNotIn("--skip-build", command)
            self.assertNotIn("--skip-host", command)
            self.assertEqual(bool(call.kwargs["env"]["P6_MATRIX_SET"]), "--drop-tree" in command)
        self.assertFalse(any((self.root / "build/portability" / name / opt).exists()
                             for name in ROWS for opt in (*OPT_ORDER, "san")))

    def test_second_method_rejects_corrupt_json_and_invalidates_success(self):
        self.assert_success(self.invoke("differential"))
        path = self.root / "build/portability" / next(iter(ROWS)) / "O0-host.json"
        data = json.loads(path.read_text()); data["cases"]["own-corpus"]["hardware"] = "f" * 64
        path.write_text(json.dumps(data))
        self.assertFalse(self.invoke("").wasSuccessful())
        self.assertIsNone(self.module._MATRIX_RUN)
        self.assertEqual(30, self.process.call_count)
        self.assert_success(self.invoke(""))
        self.assertEqual(60, self.process.call_count)

    def test_new_identity_reexecutes_and_inputs_changed_mid_execution_never_cache_success(self):
        self.assert_success(self.invoke(""))
        self.identity.return_value = "own inputs B"
        self.assert_success(self.invoke("differential"))
        self.assertEqual(60, self.process.call_count)
        self.identity.return_value = "own inputs C"
        def drift(*args, **kwargs):
            result = self.run_cell(*args, **kwargs)
            self.identity.return_value = "own inputs D"
            return result
        self.process.side_effect = drift
        self.assertFalse(self.invoke("").wasSuccessful())
        self.assertIsNone(self.module._MATRIX_RUN)

    def test_partial_process_failure_never_authorizes_existing_json(self):
        self.assert_success(self.invoke(""))
        self.identity.return_value = "own inputs B"
        self.process.side_effect = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "own failure")
        self.assertFalse(self.invoke("").wasSuccessful())
        self.assertIsNone(self.module._MATRIX_RUN)
        self.process.reset_mock(side_effect=True)
        self.process.side_effect = self.run_cell
        self.assert_success(self.invoke("differential"))
        self.assertEqual(30, self.process.call_count)


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
