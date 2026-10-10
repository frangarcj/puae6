"""T25: the portability matrix. Two parts.

Always (no Docker, no build): reports/platform-matrix.md, plan/runners.json, plan/toolchains.json, the toolchain files and
tests/portability/evidence.json say the same thing. A row is called supported only if the evidence recorded a run of it that agreed with the
reference row; rows out of the declared scope have no evidence and no support; nothing says an x86_64 row ran on x86_64 hardware.

P6_TEST_PORTABILITY=1: runs the rows this machine can run (the native row on macOS arm64, the Docker rows when the daemon answers) at -O0:
builds the probe, runs the corpus in both variants and compares every hash with the evidence, so that a change that moves a hash on one ISA or
compiler fails here. The first run builds the images (tests/portability/Dockerfile) and every tree; it takes a long time.
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
        for name in rows:
            with self.subTest(row=name):
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


if __name__ == "__main__":
    unittest.main()
