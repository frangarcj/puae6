"""UPD01: the upstream update tool on a mini upstream with two versions (no network, no WinUAE needed).

Every case builds a Git repository with v1 (pinned by a throwaway project) and a v2 that breaks something
specific, then checks the verdict, the exit status and what the report says about it. The last class runs the
tool on the real pinned snapshot: OLD = NEW must be "no changes" and the series must come back byte for byte.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
import import_sources  # noqa: E402
import update_upstream  # noqa: E402
from fixture import BASE, Mini, make_patch, run, sha  # noqa: E402

HAVE_CLANG = bool(shutil.which("clang") and shutil.which("clang++"))


class MiniCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p6-upd-")
        self.addCleanup(self.temp.cleanup)
        self.mini = Mini(Path(self.temp.name))
        self.root = self.mini.root

    def update(self, new, name="t", *, generate=False, **kwargs):
        kwargs.setdefault("new_source", self.mini.repo)
        return update_upstream.update(self.root, self.mini.first, new, f"build/update/{name}",
                                      skip_generate=not generate, **kwargs)

    def workdir(self, name="t") -> Path:
        return self.root / "build/update" / name

    def statuses(self, report) -> dict:
        details = {p["id"]: p["status"] for p in report["patches"]["details"]}
        return {pid: details.get(pid, "clean") for pid in ("0001-core-a", "0002-core-z", "0003-util-two")}


class SnapshotTests(MiniCase):
    def test_same_commit_is_no_changes_and_returns_the_series_byte_for_byte(self):
        report = self.update(self.mini.first)
        self.assertEqual(("no-changes", 0), (report["verdict"], report["exit_code"]))
        self.assertEqual({"clean": 3}, report["patches"]["counts"])
        self.assertEqual({"added": 0, "removed": 0, "changed": 0}, report["snapshot"]["counts"])
        self.assertEqual((self.root / "patches/winuae/series.json").read_bytes(),
                         (self.workdir() / "candidate-series.json").read_bytes())

    def test_same_commit_read_from_git_equals_the_pinned_pristine(self):
        pinned = self.update(self.mini.first, "pinned", new_source=None)
        from_git = self.update(self.mini.first, "git")
        self.assertEqual(pinned["new"]["tree_sha256"], from_git["new"]["tree_sha256"])
        self.assertEqual("pinned-pristine", pinned["new"]["source"]["kind"])
        self.assertEqual("git", from_git["new"]["source"]["kind"])
        self.assertEqual("no-changes", from_git["verdict"])

    def test_upstream_change_far_from_the_patch_zones_rebases_hashes_exactly(self):
        core = BASE["core.cpp"].replace("return x * 6;", "return x * 66;")
        new = self.mini.commit("far", {"core.cpp": core, "README": "mini upstream, v2\n", "docs/notes.txt": "n\n"})
        report = self.update(new)
        self.assertEqual("candidate", report["verdict"])
        self.assertEqual({"clean": 1, "rebased": 2}, report["patches"]["counts"])
        self.assertEqual({"added": 1, "removed": 0, "changed": 2}, report["snapshot"]["counts"])
        changed = {e["path"]: e for e in report["snapshot"]["relevant"]["changed"]}
        self.assertEqual({"core.cpp"}, set(changed))
        self.assertEqual(1, changed["core.cpp"]["diffstat"]["hunks"])
        candidate = json.loads((self.workdir() / "candidate-series.json").read_text())
        self.assertEqual(new, candidate["upstream_commit"])
        first = candidate["patches"][0]["files"][0]
        self.assertEqual(sha(core.replace("return x + 1;", "return x + 2;").encode()), first["after_sha256"])
        self.assertEqual(sha(core.encode()), first["before_sha256"])
        self.assertTrue(any(p["path"] == "core.cpp" and p["changed"] for p in report["hashes"]["patched_files"]))

    def test_added_relevant_sources_and_licences_removed_units_and_changed_notices_are_flagged(self):
        new = self.mini.commit("triage", {
            "newdev.cpp": "int newdev(void) { return 1; }\n",           # unlisted source next to selected ones
            "COPYING.new": "Permission is hereby granted, free of charge\n",
            "COPYING": "GNU General Public License fixture, version 3\n",
            "extra.cpp": None,                                          # a selected unit disappears
            "od-win32/dialog.cpp": "int dialog(void);\n"})              # Windows UI: not a finding
        report = self.update(new)
        self.assertEqual(("triage-required", 1), (report["verdict"], report["exit_code"]))
        kinds = {(t["kind"], t["path"]) for t in report["triage"]}
        self.assertEqual({("added-source", "newdev.cpp"), ("added-license", "COPYING.new"),
                          ("license-notice-changed", "COPYING"), ("removed-relevant", "extra.cpp")}, kinds)
        self.assertEqual(["extra.cpp"], report["snapshot"]["manifest_references_missing_in_new"])
        self.assertIn("MIT", next(e for e in report["snapshot"]["relevant"]["added"] if e["path"] == "COPYING.new")["license_markers"])
        self.assertEqual({"added": 1, "removed": 0, "changed": 0}, report["snapshot"]["other_paths"]["unselected"])
        self.assertEqual(4, len(report["proposed_tasks"]))
        self.assertIn("COPYING", [n["path"].split("/", 2)[2] for n in report["snapshot"]["licenses"]["notice_files"]
                                  if n["status"] == "changed"])


class ConflictTests(MiniCase):
    def test_upstream_rewrites_the_patch_zone_is_a_semantic_conflict_and_blocks_only_dependants(self):
        new = self.mini.commit("zone", {"core.cpp": BASE["core.cpp"].replace("return x + 1;", "return x + 100;")})
        report = self.update(new)
        self.assertEqual(("conflicts", 1), (report["verdict"], report["exit_code"]))
        self.assertEqual({"0001-core-a": "semantic-conflict", "0002-core-z": "blocked", "0003-util-two": "clean"},
                         self.statuses(report))
        first = report["patches"]["details"][0]
        self.assertEqual("upstream-changed", first["files"][0]["hunks"][0]["state"])
        self.assertEqual(["0001-core-a"], report["patches"]["details"][1]["blocked_by"])
        self.assertEqual("skipped", report["generation"]["status"])
        self.assertFalse((self.workdir() / "candidate-series.json").exists())
        kinds = {t["id"].split("-", 2)[2]: t["kind"] for t in report["proposed_tasks"]}
        self.assertEqual({"0001-core-a": "semantic-conflict", "0002-core-z": "blocked"}, kinds)

    def test_context_changed_at_the_edge_is_a_conflict_never_a_fuzzy_match(self):
        new = self.mini.commit("edge", {"core.cpp": BASE["core.cpp"].replace("int core_b(int x)", "long core_b(int x)")})
        report = self.update(new)
        self.assertEqual("semantic-conflict", self.statuses(report)["0001-core-a"])

    def test_zone_that_only_moved_is_a_shifted_conflict_not_applied_with_an_offset(self):
        new = self.mini.commit("shift", {"core.cpp": "// five\n// new\n// header\n// lines\n// here\n" + BASE["core.cpp"]})
        report = self.update(new)
        self.assertEqual("conflicts", report["verdict"])
        self.assertEqual({"0001-core-a": "shifted-conflict", "0002-core-z": "blocked", "0003-util-two": "clean"},
                         self.statuses(report))
        hunk = report["patches"]["details"][0]["files"][0]["hunks"][0]
        self.assertEqual(("shifted", 5), (hunk["state"], hunk["delta"]))
        self.assertIn("re-exportar", report["proposed_tasks"][0]["action"])

    def test_removed_patched_file_is_a_semantic_conflict(self):
        new = self.mini.commit("gone", {"util.cpp": None})
        report = self.update(new)
        self.assertEqual("semantic-conflict", self.statuses(report)["0003-util-two"])
        self.assertTrue(any(t["kind"] == "removed-relevant" and t["path"] == "util.cpp" for t in report["triage"]))

    def test_a_patch_file_that_changed_is_a_port_failure_not_an_upstream_conflict(self):
        path = self.root / "patches/winuae/0001-core-a.patch"
        path.write_bytes(path.read_bytes() + b"\n")
        report = self.update(self.mini.first)           # even with nothing new upstream
        self.assertEqual("conflicts", report["verdict"])
        self.assertEqual({"0001-core-a": "port-failure", "0002-core-z": "blocked", "0003-util-two": "clean"},
                         self.statuses(report))
        self.assertIn("hash del archivo", report["patches"]["details"][0]["files"][0]["detail"])

    def test_a_patch_whose_context_never_existed_is_a_port_failure_in_the_baseline(self):
        specs = self.mini.default_patches()
        wrong = make_patch("core.cpp", specs[1]["old"].replace("return 0;", "return 9;"),
                           specs[1]["old"].replace("return 0;", "return 7;"))
        self.mini.write_series(specs, patch_bytes={"0002-core-z": wrong})
        new = self.mini.commit("harmless", {"README": "v2\n"})
        report = self.update(new)
        self.assertEqual("conflicts", report["verdict"])
        self.assertEqual({"0001-core-a": "clean", "0002-core-z": "port-failure", "0003-util-two": "clean"},
                         self.statuses(report))
        self.assertFalse(report["patches"]["baseline_ok"])
        self.assertEqual("port-failure", report["proposed_tasks"][0]["kind"])


@unittest.skipUnless(HAVE_CLANG, "needs the HOST compilers")
class GeneratorTests(MiniCase):
    def test_syntax_error_in_the_generator_fails_with_a_useful_diagnostic(self):
        new = self.mini.commit("broken generator", {"gen.cpp": "int main(void) { this is not C }\n"})
        report = self.update(new, generate=True)
        self.assertEqual(("generator-failed", 1), (report["verdict"], report["exit_code"]))
        generation = report["generation"]
        self.assertEqual("failed", generation["status"])
        self.assertIn("gen.cpp", generation["diagnostic"])
        self.assertIn("error", generation["diagnostic"])
        self.assertEqual(["gen.cpp"], generation["suspect_inputs"])
        self.assertNotIn(self.temp.name, (self.workdir() / "report.json").read_text())
        self.assertTrue(any(t["kind"] == "generator-failed" for t in report["proposed_tasks"]))

    def test_generator_that_stops_producing_its_output_fails(self):
        new = self.mini.commit("silent generator", {"gen.cpp": "int main(void) { return 0; }\n"})
        report = self.update(new, generate=True)
        self.assertEqual("generator-failed", report["verdict"])
        self.assertIn("no produjo tab.cpp", report["generation"]["diagnostic"])

    def test_generator_with_new_output_is_a_candidate_with_the_difference_listed(self):
        new = self.mini.commit("new table", {"gen.cpp": BASE["gen.cpp"].replace("{1, 2, 3}", "{4, 5, 6}")})
        report = self.update(new, generate=True)
        self.assertEqual("candidate", report["verdict"])
        generation = report["generation"]
        self.assertEqual(["tab.cpp"], generation["outputs"]["changed"])
        self.assertEqual("old-snapshot", generation["baseline"])
        self.assertIn("upstream/gen.cpp", generation["generators"]["mini"]["inputs_changed"])
        self.assertTrue(report["materialized"]["predicted_equal"])

    def test_unchanged_tree_regenerates_the_same_outputs_and_reproduces_the_materialized_tree(self):
        report = self.update(self.mini.first, generate=True)
        self.assertEqual("no-changes", report["verdict"])
        self.assertEqual({"unchanged": 1, "changed": [], "added": [], "removed": []}, report["generation"]["outputs"])
        self.assertTrue(report["materialized"]["predicted_equal"])


@unittest.skipUnless(HAVE_CLANG, "needs the HOST compilers")
class DisciplineTests(MiniCase):
    def benign(self):
        return self.mini.commit("far", {"core.cpp": BASE["core.cpp"].replace("return x * 6;", "return x * 66;")})

    def test_two_runs_give_the_same_report_in_the_same_or_another_workdir(self):
        new = self.benign()
        self.update(new, "a", generate=True)
        first = {name: (self.workdir("a") / name).read_bytes() for name in os.listdir(self.workdir("a"))}
        self.update(new, "a", generate=True)
        self.update(new, "b", generate=True)
        self.assertEqual({"candidate-series.json", "report.json", "report.md"}, set(first))
        for name, data in first.items():
            self.assertEqual(data, (self.workdir("a") / name).read_bytes(), name)
            self.assertEqual(data, (self.workdir("b") / name).read_bytes(), name)

    def test_conflict_reports_repeat_too(self):
        new = self.mini.commit("zone", {"core.cpp": BASE["core.cpp"].replace("return x + 1;", "return x + 100;")})
        self.update(new, "a")
        self.update(new, "b")
        self.assertEqual((self.workdir("a") / "report.json").read_bytes(), (self.workdir("b") / "report.json").read_bytes())

    def test_pristine_the_project_and_the_upstream_repository_are_untouched(self):
        new = self.benign()
        before = self.mini.listing("build/update")
        head = run(self.mini.repo, "rev-parse", "HEAD")
        pristine = import_sources.inventory(self.root / "upstream/winuae")
        self.update(new, generate=True)
        self.assertEqual(before, self.mini.listing("build/update"))
        self.assertEqual(pristine, import_sources.inventory(self.root / "upstream/winuae"))
        self.assertEqual(head, run(self.mini.repo, "rev-parse", "HEAD"))
        self.assertEqual("", run(self.mini.repo, "status", "--porcelain"))
        self.assertEqual({"update"}, {p.name for p in (self.root / "build").iterdir()})   # no build/upstream, no build/generated

    def test_nothing_is_downloaded(self):
        commands = []
        real = subprocess.run

        def spy(args, *a, **k):
            commands.append(list(args))
            return real(args, *a, **k)
        with mock.patch.object(subprocess, "run", spy):
            self.update(self.benign(), generate=True)
        git = [c for c in commands if c and c[0] == "git"]
        self.assertTrue(git)
        for word in ("fetch", "clone", "pull", "push", "ls-remote", "remote"):
            self.assertFalse([c for c in git if word in c], word)

    def test_keep_workspace_leaves_the_trees_and_refuses_to_overwrite_them(self):
        new = self.benign()
        self.update(new, "k", keep_workspace=True)
        self.assertTrue((self.workdir("k") / "workspace/patched-new/core.cpp").is_file())
        with self.assertRaises(update_upstream.UpdateError):
            self.update(new, "k", keep_workspace=True)

    def test_the_default_run_leaves_no_staging_directory_behind(self):
        self.update(self.benign(), "clean")
        self.assertEqual({"candidate-series.json", "report.json", "report.md"}, set(os.listdir(self.workdir("clean"))))

    def test_stale_candidate_is_removed_when_the_next_run_fails(self):
        self.update(self.benign(), "s")
        self.assertTrue((self.workdir("s") / "candidate-series.json").exists())
        zone = self.mini.commit("zone", {"core.cpp": BASE["core.cpp"].replace("return x + 1;", "return x + 100;")})
        self.update(zone, "s")
        self.assertFalse((self.workdir("s") / "candidate-series.json").exists())


class RefusalTests(MiniCase):
    def test_new_sha_that_is_not_in_the_local_repository_is_not_downloaded(self):
        with self.assertRaisesRegex(update_upstream.UpdateError, "no descarga"):
            self.update("1" * 40)

    def test_branch_names_and_short_ids_are_not_shas(self):
        for name in ("master", "latest", "HEAD", self.mini.first[:12]):
            with self.assertRaisesRegex(update_upstream.UpdateError, "40 hex"):
                self.update(name)

    def test_without_a_source_or_a_cache_nothing_is_fetched(self):
        new = self.mini.commit("x", {"README": "v2\n"})
        with self.assertRaisesRegex(update_upstream.UpdateError, "No se descarga"):
            self.update(new, new_source=None)

    def test_old_sha_must_be_the_pinned_commit(self):
        with self.assertRaisesRegex(update_upstream.UpdateError, "no es el commit fijado"):
            update_upstream.update(self.root, "2" * 40, self.mini.first, "build/update/t")

    def test_tampered_pristine_stops_the_run_before_anything_is_decided(self):
        (self.root / "upstream/winuae/README").write_bytes(b"edited by hand\n")
        with self.assertRaisesRegex(update_upstream.UpdateError, "SRC01"):
            self.update(self.mini.first)
        self.assertFalse((self.workdir() / "report.json").exists())

    def test_workdir_outside_build_is_refused(self):
        for bad in ("report", "../elsewhere/x", "/tmp/x", "build"):
            with self.assertRaises(ValueError, msg=bad):
                update_upstream.update(self.root, self.mini.first, self.mini.first, bad)

    def test_exit_status_of_the_command_line(self):
        zone = self.mini.commit("zone", {"core.cpp": BASE["core.cpp"].replace("return x + 1;", "return x + 100;")})
        args = lambda new, name: ["--root", str(self.root), "--old", self.mini.first, "--new", new, "--workdir",
                                  f"build/update/{name}", "--new-source", str(self.mini.repo), "--skip-generate"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, update_upstream.main(args(self.mini.first, "same")))
            self.assertEqual(1, update_upstream.main(args(zone, "zone")))
            self.assertEqual(2, update_upstream.main(args("3" * 40, "unknown")))
        self.assertIn("UPD01 conflicts", out.getvalue())
        self.assertIn("ERROR:", err.getvalue())


class DirectorySnapshotTests(MiniCase):
    def snapshot(self, sha_value, name="snap"):
        directory = Path(self.temp.name) / name
        import_sources.import_source  # noqa: B018 - the importer owns the blob reader used below
        for path, mode, content in import_sources.blobs(self.mini.repo, {"id": "winuae", "commit": sha_value}):
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        manifest = Path(self.temp.name) / f"{name}.json"
        manifest.write_text(json.dumps({"schema_version": 1, "source": {"commit": sha_value},
                                        "files": import_sources.inventory(directory)}))
        return directory, manifest

    def test_directory_with_its_manifest_gives_the_same_verdict_as_git(self):
        new = self.mini.commit("zone", {"core.cpp": BASE["core.cpp"].replace("return x + 1;", "return x + 100;")})
        directory, manifest = self.snapshot(new)
        via_dir = self.update(new, "dir", new_source=directory, new_manifest=manifest)
        via_git = self.update(new, "git")
        self.assertEqual(via_git["patches"], via_dir["patches"])
        self.assertEqual(via_git["new"]["tree_sha256"], via_dir["new"]["tree_sha256"])
        self.assertEqual("directory", via_dir["new"]["source"]["kind"])

    def test_directory_that_differs_from_its_manifest_or_names_another_commit_is_refused(self):
        new = self.mini.commit("x", {"README": "v2\n"})
        directory, manifest = self.snapshot(new)
        (directory / "README").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(update_upstream.UpdateError, "no verificado"):
            self.update(new, "dir", new_source=directory, new_manifest=manifest)
        directory, manifest = self.snapshot(new, "snap2")
        with self.assertRaisesRegex(update_upstream.UpdateError, "no verificado"):
            self.update(self.mini.first, "dir", new_source=directory, new_manifest=manifest)


class RealPinnedSnapshotTests(unittest.TestCase):
    """OLD = NEW = the pinned WinUAE commit: nothing changes and the series comes back byte for byte."""

    def test_pinned_commit_against_itself_is_no_changes(self):
        lock = json.loads((ROOT / "plan/upstream.lock.json").read_text())
        commit = next(s["commit"] for s in lock["sources"] if s["id"] == "winuae")
        pristine = import_sources.inventory(ROOT / "upstream/winuae")
        report = update_upstream.update(ROOT, commit, commit, "build/update/test-pinned", skip_generate=True)
        self.assertEqual("no-changes", report["verdict"])
        self.assertEqual(report["patches"]["total"], report["patches"]["counts"]["clean"])
        self.assertTrue(report["patches"]["baseline_ok"])
        self.assertEqual((ROOT / "patches/winuae/series.json").read_bytes(),
                         (ROOT / "build/update/test-pinned/candidate-series.json").read_bytes())
        self.assertEqual(pristine, import_sources.inventory(ROOT / "upstream/winuae"))


if __name__ == "__main__":
    unittest.main()
