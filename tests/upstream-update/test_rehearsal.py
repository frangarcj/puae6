"""T38: the record of the update rehearsal (reports/update-rehearsal) is coherent and does not move the pins of the product.

The rehearsal itself (a real update to WinUAE 6.1.0 beta 17, its builds and its tests) ran in a work area under build/ and cannot be repeated by a unit test: it needs the
candidate commit from the network and hours of builds. What a test can keep is that what was kept is internally consistent and that the product still pins what the rehearsal
started from. Nothing here reads the network or builds an engine.
"""
import hashlib
import json
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RECORD = ROOT / "reports/update-rehearsal"
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class CandidateRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = load(RECORD / "candidate.json")
        cls.lock = load(ROOT / "plan/upstream.lock.json")
        cls.pin = next(s for s in cls.lock["sources"] if s["id"] == "winuae")

    def test_the_candidate_is_a_full_commit_that_is_not_the_pin_and_is_not_a_branch(self):
        commit = self.candidate["commit"]
        self.assertRegex(commit, SHA40)
        self.assertRegex(self.candidate["old_pin"]["commit"], SHA40)
        self.assertNotEqual(commit, self.candidate["old_pin"]["commit"])
        self.assertIsNone(self.candidate["tag"], "there is no published tag after 6030: the candidate is a pinned commit, not a tag")
        self.assertNotIn(commit, json.dumps(self.lock), "the rehearsal candidate must not leak into plan/upstream.lock.json")

    def test_the_scope_says_what_the_update_is(self):
        scope = self.candidate["scope_old_to_new"]
        self.assertGreater(scope["commits"], 0)
        self.assertEqual(1134, scope["snapshot_files_old_to_new"]["old"])
        self.assertTrue(scope["notes_that_matter_to_the_profile"])
        self.assertTrue(self.candidate["untrusted_input"])

    def test_the_record_starts_from_the_pin_the_product_has_or_the_product_moved_on(self):
        if self.pin["commit"] != self.candidate["old_pin"]["commit"]:
            self.skipTest("the product pins another commit now: the rehearsal record is history")
        self.assertEqual(self.candidate["old_pin"]["tag"], self.pin["tag"])
        returned = load(RECORD / "return-to-pins.json")
        self.assertEqual(self.pin["commit"], returned["pin"])
        files = returned["unchanged_files"]
        for path, digest in files.items():
            self.assertEqual(digest, sha256((ROOT / path).read_bytes()), f"{path} changed after the rehearsal: the product must return to the same pins")


class CandidateSeriesTests(unittest.TestCase):
    """The rebased queue that the rehearsal produced: every patch file is the one the series names and the hash chain starts at the candidate snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.directory = RECORD / "candidate-series"
        cls.series = load(cls.directory / "patches/winuae/series.json")
        cls.bases = load(RECORD / "candidate-bases.json")
        cls.old = load(ROOT / "patches/winuae/series.json") if (ROOT / "patches/winuae/series.json").is_file() else None

    def test_series_is_pinned_to_the_candidate_commit(self):
        self.assertEqual(load(RECORD / "candidate.json")["commit"], self.series["upstream_commit"])
        self.assertEqual(self.bases["commit"], self.series["upstream_commit"])

    def test_every_patch_file_matches_its_hash_and_touches_what_the_series_says(self):
        ids = set()
        for patch in self.series["patches"]:
            self.assertNotIn(patch["id"], ids)
            ids.add(patch["id"])
            data = (self.directory / patch["file"]).read_bytes()
            self.assertEqual(patch["sha256"], sha256(data), patch["id"])
            touched = set(re.findall(rb"^diff --git a/(\S+) b/\1$", data, re.M))
            self.assertEqual({f["path"].encode() for f in patch["files"]}, touched, patch["id"])
            for item in patch["files"]:
                self.assertNotEqual(item["before_sha256"], item["after_sha256"], patch["id"])

    def test_hash_chain_is_closed_from_the_candidate_snapshot(self):
        last = dict(self.bases["patched_files"])
        for patch in self.series["patches"]:
            for item in patch["files"]:
                self.assertIn(item["path"], last, f"{patch['id']} patches a file the candidate snapshot does not have")
                self.assertEqual(last[item["path"]], item["before_sha256"], f"{patch['id']}: {item['path']}")
                last[item["path"]] = item["after_sha256"]

    def test_dropped_and_new_patches_are_declared_and_nothing_else_changed_identity(self):
        report = load(self.directory / "rebase-report.json")
        ids = [p["id"] for p in self.series["patches"]]
        for dropped in report["dropped"]:
            self.assertNotIn(dropped, ids)
        for added in report["added"]:
            self.assertIn(added, ids)
        described = {p["id"] for p in load(RECORD / "new-patches.json")}
        self.assertEqual(described, set(report["added"]))
        if self.old is not None and load(ROOT / "plan/upstream.lock.json") and self.old["upstream_commit"] == load(RECORD / "candidate.json")["old_pin"]["commit"]:
            old_ids = [p["id"] for p in self.old["patches"]]
            self.assertEqual(sorted(set(old_ids) - set(ids)), sorted(report["dropped"]), "the patches missing from the candidate series are exactly the declared ones")
            self.assertEqual(len(old_ids) - len(report["dropped"]) + len(report["added"]), len(ids))

    def test_every_new_patch_is_described_like_the_rest_of_the_series(self):
        by_id = {p["id"]: p for p in self.series["patches"]}
        for new in load(RECORD / "new-patches.json"):
            entry = by_id[new["id"]]
            for key in ("title", "reason", "category", "symbols", "test", "upstream_candidate", "removal_version"):
                self.assertEqual(new[key], entry[key], f"{new['id']}.{key}")
            self.assertGreater(entry["order"], max(p["order"] for p in self.series["patches"] if p["id"] not in {n["id"] for n in load(RECORD / "new-patches.json")}))


class ResultsTests(unittest.TestCase):
    def test_the_results_file_names_every_check_of_the_card_with_a_status_and_a_reason(self):
        results = load(RECORD / "results.json")
        expected = {"UPD01", "REF01", "GEN01", "WB03", "WB04", "PERF01", "ADR01"}
        self.assertEqual(expected, set(results["checks"]))
        for name, entry in results["checks"].items():
            self.assertIn(entry["status"], ("PASS", "BLOCKED", "PARTIAL"), name)
            self.assertTrue(entry.get("evidence") or entry.get("reason"), name)
            if entry["status"] != "PASS":
                self.assertTrue(entry.get("reason"), f"{name}: a check that is not PASS says why")
        self.assertFalse(results["maintenance_demonstrated_by_counting_patch_lines"])

    def test_the_open_differences_are_all_classified(self):
        results = load(RECORD / "results.json")
        for item in results["differences_against_the_pin"]:
            self.assertIn(item["class"], ("upstream-intended", "upstream-attributed-by-release-note", "port-adaptation", "open"), item["id"])
            self.assertTrue(item["evidence"], item["id"])
        self.assertFalse(results["state_compatibility"]["old_states_load_in_new_layout"])

    def test_the_adr_has_what_ADR01_asks_for(self):
        """ADR01 (docs/04-validation.md): context, alternatives, evidence, cost, risks and concrete validation steps."""
        text = (ROOT / "docs/adr/0005-update-rehearsal.md").read_text(encoding="utf-8")
        for heading in ("## Contexto", "## Decision", "## Alternativas descartadas", "## Evidencia", "## Costes y riesgos", "## Como se valida"):
            self.assertIn(heading, text)
        self.assertIn(load(RECORD / "candidate.json")["commit"], text)

    def test_the_rehearsal_tooling_writes_only_under_build(self):
        """make_project.py refuses a destination outside build/, and the other helpers write only the directory they are given."""
        text = (RECORD / "make_project.py").read_text()
        self.assertIn('sys.exit("DEST must be a new directory under build/")', text)
        self.assertNotIn("shutil.rmtree", text)
        for script in ("rebase_series.py", "make_project.py", "cross_version.py", "series_metrics.py"):
            self.assertNotIn("rm -rf", (RECORD / script).read_text())


if __name__ == "__main__":
    unittest.main()
