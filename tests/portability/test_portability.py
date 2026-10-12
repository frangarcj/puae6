"""T25: the portability matrix. Two parts.

Always (no Docker, no build): reports/platform-matrix.md, plan/runners.json, plan/toolchains.json, the toolchain files and
tests/portability/evidence.json say the same thing. A row is called supported only if the evidence recorded a run of it that agreed with the
reference row; rows out of the declared scope have no evidence and no support; nothing says an x86_64 row ran on x86_64 hardware.

P6_TEST_PORTABILITY=1: runs the rows this machine can run (the native row on macOS arm64, the Docker rows when the daemon answers) at -O0:
builds the probe, runs the corpus in both variants and compares every hash with the evidence, so that a change that moves a hash on one ISA or
compiler fails here. A declared row that cannot run here is a skipped subtest, so tools/check.py (DET04, DET06 for T25) reports BLOCKED, not PASS,
until all five rows have run. The first run builds the images (tests/portability/Dockerfile) and every tree; it takes a long time.
    P6_TEST_PORTABILITY=1 python3 -m unittest tests.portability.test_portability
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.portability import corpus, render  # noqa: E402
from tests.portability.rows import OPT_ORDER, OPTS, OUT_OF_SCOPE, REFERENCE, ROWS, SCOPE_DECISION, WATERBOX_ROW  # noqa: E402

REPORT = ROOT / "reports/platform-matrix.md"
EVIDENCE = json.loads((ROOT / "tests/portability/evidence.json").read_text(encoding="utf-8"))
WATERBOX = json.loads((ROOT / "tests/portability/waterbox-evidence.json").read_text(encoding="utf-8"))
RUNNERS = {r["id"]: r for r in json.loads((ROOT / "plan/runners.json").read_text(encoding="utf-8"))["runners"]}
TOOLCHAINS = json.loads((ROOT / "plan/toolchains.json").read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MatrixCoherenceTests(unittest.TestCase):
    def test_the_generated_part_of_the_report_is_what_the_evidence_renders(self):
        text = REPORT.read_text(encoding="utf-8")
        self.assertEqual(render.current_block(text), render.render(EVIDENCE, WATERBOX))

    def test_toolchain_files_exist_and_are_the_ones_the_lock_records(self):
        recorded = TOOLCHAINS["portability_matrix"]["toolchain_files"]
        used = {spec["toolchain"] for spec in ROWS.values()}
        self.assertEqual(set(recorded), used)
        for path, digest in recorded.items():
            self.assertEqual(sha256(ROOT / path), digest, path)
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("CMAKE_SYSTEM_NAME", text.replace("no CMAKE_SYSTEM_NAME", ""), "the project only supports native builds")
            self.assertNotIn("march", text.replace("no -march=native", "").replace("no -march", ""), path)

    def test_the_lock_lists_the_rows_with_the_runner_and_the_isa_they_use(self):
        rows = TOOLCHAINS["portability_matrix"]["rows"]
        self.assertEqual(set(rows), set(ROWS))
        for name, spec in ROWS.items():
            self.assertEqual(rows[name]["runner"], spec["runner"])
            self.assertEqual(rows[name]["isa"], spec["isa"])
            self.assertEqual(rows[name]["native_hardware"], spec["native_hardware"])
        self.assertEqual(set(TOOLCHAINS["portability_matrix"]["out_of_scope"]), set(OUT_OF_SCOPE))

    def test_the_runners_say_what_the_evidence_verified_and_nothing_more(self):
        by_runner = {}
        for name, spec in ROWS.items():
            by_runner.setdefault(spec["runner"], []).append(name)
        for runner_id, names in by_runner.items():
            runner = RUNNERS[runner_id]
            verified = all(render.row_status(EVIDENCE, n)[1] for n in names)
            built = all(EVIDENCE["executions"].get(f"{n}/{o}", {}).get("build", {}).get("exit") == 0 for n in names for o in OPT_ORDER)
            self.assertEqual(runner["build_verified"], built, runner_id)
            self.assertEqual(runner["emulator_execution_verified"], verified, runner_id)
            self.assertEqual(runner["verified_date"], "2026-10-10")
        self.assertEqual(RUNNERS["linux-x86_64"]["native_hardware"], False)
        self.assertIn("translated", RUNNERS["linux-x86_64"]["execution_environment"])
        for name, spec in OUT_OF_SCOPE.items():
            runner = RUNNERS[spec["runner"]]
            if spec["runner"] in by_runner:
                continue                  # a runner that other rows use (linux-x86_64) is described by those rows
            self.assertEqual(runner["status"], "out_of_scope", name)
            self.assertFalse(runner["build_verified"] or runner["emulator_execution_verified"], name)
            self.assertEqual(runner["scope_decision"], SCOPE_DECISION)
        self.assertTrue(set(OUT_OF_SCOPE) <= set(TOOLCHAINS["portability_matrix"]["out_of_scope"]))

    def test_no_row_is_supported_without_an_execution_that_agreed_with_the_reference(self):
        self.assertEqual(set(ROWS) & set(OUT_OF_SCOPE), set())
        for name in OUT_OF_SCOPE:
            self.assertFalse([k for k in EVIDENCE["executions"] if k.startswith(name + "/")], name)
        text = REPORT.read_text(encoding="utf-8")
        for name in ROWS:
            status, supported = render.row_status(EVIDENCE, name)
            self.assertIn(f"**{status}**", text, name)
            if supported:
                for opt in OPT_ORDER:
                    e = EVIDENCE["executions"][f"{name}/{opt}"]
                    self.assertEqual(e["baseline"]["errors"], {})
                    self.assertEqual(e["baseline"]["hardware_equal"], e["baseline"]["cases"])
                    self.assertEqual(e["baseline"]["av_equal"], e["baseline"]["cases"])
                    self.assertEqual(e["det06"]["hardware_equal"], e["det06"]["cases"])
                    self.assertEqual(e["det06"]["av_equal"], e["det06"]["cases"])
                    self.assertTrue(e["det06"]["clock_shifted"], "DET06 without a shifted clock does not vary the time")
        for name, spec in ROWS.items():
            if not spec["native_hardware"]:
                self.assertIn("traducido", text)
                self.assertFalse(EVIDENCE["executions"][f"{name}/O0"]["native_hardware"])

    def test_the_hashes_are_per_case_and_hardware_is_never_mixed_with_av(self):
        for case_id, case in EVIDENCE["cases"].items():
            self.assertEqual(len(case["hardware"]), 64, case_id)
            self.assertEqual(len(case["av"]), 64, case_id)
            self.assertNotEqual(case["hardware"], case["av"], case_id)
        self.assertEqual(len(EVIDENCE["cases"]), EVIDENCE["corpus"]["cases"])
        # A comparison that cannot tell cases apart proves nothing: the corpus has to produce many different machines and many different pictures.
        self.assertGreater(len({c["hardware"] for c in EVIDENCE["cases"].values()}), 80)
        self.assertGreater(len({c["av"] for c in EVIDENCE["cases"].values()}), 50)

    def test_the_corpus_is_the_one_the_evidence_was_taken_with(self):
        with tempfile.TemporaryDirectory(prefix="p6-corpus-") as directory:
            listing = corpus.write_corpus(directory)
            digest = hashlib.sha256()
            for path in sorted(Path(directory).iterdir()):
                digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
        self.assertEqual({c["id"] for c in listing}, set(EVIDENCE["cases"]))
        self.assertEqual(digest.hexdigest(), EVIDENCE["corpus"]["sha256"])

    def test_the_waterbox_row_is_recorded_with_the_checks_of_the_task(self):
        self.assertEqual(WATERBOX["row"], WATERBOX_ROW)
        self.assertEqual(set(WATERBOX["checks"]), {"WB02", "WB13", "WB14", "WB15"})
        for check, entry in WATERBOX["checks"].items():
            self.assertTrue(entry["passed"], check)
            self.assertNotRegex(entry["result"].lower(), r"[1-9]\d* skip", check)

    def test_state_compatibility_is_never_deduced_from_the_abi(self):
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn("no prometido", text.lower())
        self.assertIn("states", EVIDENCE)
        self.assertFalse(EVIDENCE["states"]["promised"])


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


@unittest.skipUnless(os.environ.get("P6_TEST_PORTABILITY") == "1", "opt-in: P6_TEST_PORTABILITY=1 builds and runs every row this machine can run")
class RowExecutionTests(unittest.TestCase):
    def test_every_runnable_row_reproduces_the_recorded_hashes_at_O0(self):
        from tests.portability import run_matrix
        rows = runnable_rows()
        self.assertTrue(rows, "no row can run on this machine")
        for name in ROWS:
            with self.subTest(row=name):
                # A row this machine cannot run is a SKIP, not a pass: a matrix with a missing row has not agreed (tools/check.py reports it BLOCKED).
                if name not in rows:
                    self.skipTest(f"{name} cannot run here ({ROWS[name]['kind']} row" + ("; Docker is not answering" if ROWS[name]["kind"] == "docker" else "") + ")")
                kickstart = os.environ.get("P6_KICKSTART_ROM")       # optional authorized Kickstart: an extra case whose result is compared across rows by report
                self.assertEqual(run_matrix.exec_row(name, "O0", kickstart=kickstart if kickstart and Path(kickstart).is_file() else None), 0, name)
                base = json.loads((run_matrix.BUILD / name / "O0-baseline.json").read_text(encoding="utf-8"))
                host = json.loads((run_matrix.BUILD / name / "O0-host.json").read_text(encoding="utf-8"))
                self.assertEqual(base["errors"], {})
                for case_id, expected in EVIDENCE["cases"].items():
                    got = base["cases"][case_id]
                    self.assertEqual((got["hardware"], got["av"]), (expected["hardware"], expected["av"]), f"{name} {case_id}")
                    self.assertEqual((host["cases"][case_id]["hardware"], host["cases"][case_id]["av"]), (got["hardware"], got["av"]),
                                     f"DET06 {name} {case_id}")


DIFFERENTIAL = ROOT / "tests/portability/evidence-differential.json"


class DifferentialMatrixTests(unittest.TestCase):
    """T32c: the 28 scenarios of tests/scenarios/corpus run through the same rows (DET04 across ISA and compiler, DET06 across host environments, the sanitizer
    builds) with their own evidence, tests/portability/evidence-differential.json. A file that is missing or incomplete fails these tests: a matrix that was not run
    is not a matrix that agreed."""

    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(DIFFERENTIAL.read_text(encoding="utf-8"))

    def test_the_corpus_is_the_one_the_evidence_was_taken_with(self):
        with tempfile.TemporaryDirectory(prefix="p6-differential-") as directory:
            listing = corpus.write_differential(directory)
            digest = hashlib.sha256()
            for path in sorted(Path(directory).iterdir()):
                digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
        self.assertEqual({c["id"] for c in listing}, set(self.evidence["cases"]))
        self.assertEqual(len(listing), self.evidence["corpus"]["cases"])
        self.assertEqual(digest.hexdigest(), self.evidence["corpus"]["sha256"])
        from tests.scenarios.corpus import corpus as differential
        self.assertEqual({e.id for e in differential.ENTRIES}, set(self.evidence["cases"]))

    def test_every_declared_row_agreed_with_the_reference_at_O0_and_O2_hardware_and_av_apart(self):
        self.assertEqual(self.evidence["reference"], {"row": REFERENCE[0], "opt": REFERENCE[1]})
        for name, spec in ROWS.items():
            for opt in OPT_ORDER:
                with self.subTest(row=name, opt=opt):
                    e = self.evidence["executions"][f"{name}/{opt}"]
                    self.assertEqual(e["build"]["exit"], 0)
                    self.assertEqual(e["baseline"]["errors"], {})
                    self.assertEqual(e["baseline"]["differences"], [])
                    self.assertEqual(e["baseline"]["hardware_equal"], e["baseline"]["cases"])
                    self.assertEqual(e["baseline"]["av_equal"], e["baseline"]["cases"])
                    self.assertEqual(e["baseline"]["cases"], self.evidence["corpus"]["cases"])
                    self.assertEqual(e["native_hardware"], spec["native_hardware"])

    def test_the_host_environment_changes_nothing_in_any_row(self):
        for name in ROWS:
            for opt in OPT_ORDER:
                with self.subTest(row=name, opt=opt):
                    d = self.evidence["executions"][f"{name}/{opt}"]["det06"]
                    self.assertEqual(d["errors"], {})
                    self.assertEqual(d["differences"], [])
                    self.assertEqual(d["hardware_equal"], d["cases"])
                    self.assertEqual(d["av_equal"], d["cases"])
                    self.assertTrue(d["clock_shifted"], "DET06 without a shifted clock does not vary the time")

    def test_the_sanitizer_builds_ran_the_corpus_in_every_row_and_gave_the_same_hashes(self):
        for name in ROWS:
            with self.subTest(row=name):
                e = self.evidence["executions"][f"{name}/san"]
                self.assertEqual(e["build"]["exit"], 0)
                self.assertEqual(e["baseline"]["errors"], {})
                self.assertEqual(e["baseline"]["hardware_equal"], e["baseline"]["cases"])
                self.assertEqual(e["baseline"]["av_equal"], e["baseline"]["cases"])

    def test_the_hashes_tell_the_scenarios_apart(self):
        cases = self.evidence["cases"]
        for case_id, case in cases.items():
            self.assertEqual((len(case["hardware"]), len(case["av"])), (64, 64), case_id)
            self.assertNotEqual(case["hardware"], case["av"], case_id)
        self.assertGreaterEqual(len({c["hardware"] for c in cases.values()}), len(cases) - 1)
        self.assertGreaterEqual(len({c["av"] for c in cases.values()}), len(cases) - 8)

    def test_no_row_outside_the_declared_scope_has_evidence(self):
        for name in OUT_OF_SCOPE:
            self.assertFalse([k for k in self.evidence["executions"] if k.startswith(name + "/")], name)


@unittest.skipUnless(os.environ.get("P6_TEST_PORTABILITY") == "1", "opt-in: P6_TEST_PORTABILITY=1 builds and runs every row this machine can run")
class DifferentialRowExecutionTests(unittest.TestCase):
    def test_every_runnable_row_reproduces_the_recorded_hashes_of_the_differential_corpus_at_O0(self):
        evidence = json.loads(DIFFERENTIAL.read_text(encoding="utf-8"))
        rows = runnable_rows()
        self.assertTrue(rows, "no row can run on this machine")
        build = ROOT / "build/portability"
        for name in ROWS:
            with self.subTest(row=name):
                if name not in rows:
                    self.skipTest(f"{name} cannot run here ({ROWS[name]['kind']} row" + ("; Docker is not answering" if ROWS[name]["kind"] == "docker" else "") + ")")
                result = subprocess.run([sys.executable, str(ROOT / "tests/portability/run_matrix.py"), "exec", name, "O0", "--drop-tree"], cwd=ROOT, env={**os.environ, "P6_MATRIX_SET": "differential"},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout[-800:] + result.stderr[-800:])
                base = json.loads((build / name / "O0-baseline-differential.json").read_text(encoding="utf-8"))
                host = json.loads((build / name / "O0-host-differential.json").read_text(encoding="utf-8"))
                self.assertEqual(base["errors"], {})
                for case_id, expected in evidence["cases"].items():
                    got = base["cases"][case_id]
                    self.assertEqual((got["hardware"], got["av"]), (expected["hardware"], expected["av"]), f"{name} {case_id}")
                    self.assertEqual((host["cases"][case_id]["hardware"], host["cases"][case_id]["av"]), (got["hardware"], got["av"]), f"DET06 {name} {case_id}")


if __name__ == "__main__":
    unittest.main()
