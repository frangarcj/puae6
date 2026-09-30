"""Tests of planning metadata only. Fixtures below are not emulator evidence."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p6_plan", ROOT / "tools/plan.py")
assert spec is not None and spec.loader is not None
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="p6-plan-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "README.md").write_text("Synthetic test fixture only.\n")
        task = {
            "id": "T00", "title": "Synthetic task", "milestone": "M0",
            "required": True, "status": "pending", "depends": [],
            "read": ["README.md"], "touch": ["src/*.cpp"],
            "steps": ["Check synthetic fixture"], "checks": ["PLAN01"],
            "acceptance": ["Synthetic check passes"], "stop": "Record a blocker",
            "evidence": None,
        }
        second = copy.deepcopy(task)
        second.update(id="T01", depends=["T00"])
        self.data = {"schema_version": 1, "check_ids": ["PLAN01"], "tasks": [task, second]}
        self.lock = {
            "schema_version": 1, "unresolved": [],
            "sources": [{"id": "synthetic", "repository": "https://example.invalid/repo.git",
                         "required": True, "commit": "1" * 40}],
        }

    def errors(self, release=False):
        return plan.validate_plan(self.root, self.data, self.lock, release=release)

    def assert_error(self, text, release=False):
        self.assertTrue(any(text in error for error in self.errors(release)), self.errors(release))

    def finish(self, task_id):
        task = next(task for task in self.data["tasks"] if task["id"] == task_id)
        task["status"] = "done"

    def add_note(self, task_id):
        task = next(task for task in self.data["tasks"] if task["id"] == task_id)
        path = self.root / "reports/tasks" / f"{task_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic task note for validator unit test.\n")
        task["evidence"] = path.relative_to(self.root).as_posix()

    def write_inputs(self):
        (self.root / "plan").mkdir(exist_ok=True)
        (self.root / "plan/tasks.json").write_text(json.dumps(self.data))
        (self.root / "plan/upstream.lock.json").write_text(json.dumps(self.lock))

    def test_valid_synthetic_plan(self):
        self.assertEqual([], self.errors())

    def test_current_project_is_valid(self):
        data = json.loads((ROOT / "plan/tasks.json").read_text())
        lock = json.loads((ROOT / "plan/upstream.lock.json").read_text())
        self.assertEqual([], plan.validate_plan(ROOT, data, lock))

    def test_current_project_gate_and_machawk_priority(self):
        data = json.loads((ROOT / "plan/tasks.json").read_text())
        by_id = {task["id"]: task for task in data["tasks"]}
        self.assertEqual(196, len(by_id))
        self.assertEqual(190, sum(task["required"] for task in by_id.values()))
        self.assertEqual(["T00"], by_id["T01a"]["depends"])
        self.assertEqual(["T01a"], by_id["T01b"]["depends"])
        self.assertEqual(["T01a", "T01b"], by_id["T01"]["depends"])
        self.assertEqual(["T01"], by_id["T02a"]["depends"])
        self.assertEqual(["T02a"], by_id["T02b"]["depends"])
        self.assertEqual(["T02a", "T02b"], by_id["T02"]["depends"])
        self.assertEqual(["T02"], by_id["T03a"]["depends"])
        self.assertEqual(["T03a"], by_id["T03b"]["depends"])
        self.assertEqual(["T03a", "T03b"], by_id["T03"]["depends"])
        self.assertEqual(["T04a", "T04b", "T04c"], by_id["T04"]["depends"])
        self.assertEqual(["T04a"], by_id["T04b"]["depends"])
        self.assertEqual(["T04b"], by_id["T04c"]["depends"])
        self.assertEqual(["T09a", "T09b", "T09c"], by_id["T09"]["depends"])
        self.assertEqual(["T04"], by_id["T09a"]["depends"])
        self.assertEqual(["T09a", "T09b1", "T09b2"], by_id["T09b"]["depends"])
        self.assertEqual(["T09a"], by_id["T09b1"]["depends"])
        self.assertEqual(["T09b1"], by_id["T09b2"]["depends"])
        self.assertEqual({"WB01", "WB15", "WB16"}, set(by_id["T09b"]["checks"]))
        self.assertIn("WB16", by_id["T09b2"]["checks"])
        self.assertIn("WB16", by_id["T09"]["checks"])
        self.assertEqual(["T09b"], by_id["T09c"]["depends"])
        self.assertEqual(["T05a", "T05b", "T05c", "T05d", "T05e"], by_id["T05"]["depends"])
        self.assertEqual(["T05b1", "T05b2", "T05b3"], by_id["T05b"]["depends"])
        self.assertEqual(["T05c1", "T05c2", "T05c3"], by_id["T05c"]["depends"])
        self.assertEqual(["T05c1"], by_id["T05c2"]["depends"])
        self.assertEqual(["T05c2"], by_id["T05c3"]["depends"])
        self.assertEqual(["T05d1", "T05d2"], by_id["T05d"]["depends"])
        self.assertEqual(["T05d1"], by_id["T05d2a"]["depends"])
        self.assertEqual(["T05d2a", "T05d2b"], by_id["T05d2"]["depends"])
        self.assertEqual(["T05d2b1", "T05d2b2"], by_id["T05d2b"]["depends"])
        self.assertEqual(["T05d2a"], by_id["T05d2b1"]["depends"])
        self.assertEqual(["T05d2b2v", "T05d2b2h"], by_id["T05d2b2"]["depends"])
        self.assertEqual(["T05d2b1"], by_id["T05d2b2v"]["depends"])
        self.assertEqual(["T05d2b2v"], by_id["T05d2b2h"]["depends"])
        self.assertEqual(["T03", "T05"], by_id["T06a"]["depends"])
        self.assertEqual(["T06a", "T06b", "T06c"], by_id["T06"]["depends"])
        self.assertEqual(["T06b1", "T06b2"], by_id["T06b"]["depends"])
        self.assertEqual(["T06a"], by_id["T06b1"]["depends"])
        self.assertEqual(["T06b2a", "T06b2b"], by_id["T06b2"]["depends"])
        self.assertEqual(["T06b1"], by_id["T06b2a"]["depends"])
        self.assertEqual(["T06b2c", "T06b2d"], by_id["T06b2b"]["depends"])
        self.assertEqual(["T06b2a"], by_id["T06b2c"]["depends"])
        self.assertEqual(["T06b2e", "T06b2f"], by_id["T06b2d"]["depends"])
        self.assertEqual(["T06b2c"], by_id["T06b2e"]["depends"])
        self.assertEqual(["T06b2g", "T06b2h"], by_id["T06b2f"]["depends"])
        self.assertEqual(["T06b2e"], by_id["T06b2g"]["depends"])
        self.assertEqual(["T06b2i", "T06b2j"], by_id["T06b2h"]["depends"])
        self.assertEqual(["T06b2g"], by_id["T06b2i"]["depends"])
        self.assertEqual(["T06b2k", "T06b2l"], by_id["T06b2j"]["depends"])
        self.assertEqual(["T06b2m", "T06b2n"], by_id["T06b2k"]["depends"])
        self.assertEqual(["T06b2i"], by_id["T06b2m"]["depends"])
        self.assertEqual(["T06b2m"], by_id["T06b2n"]["depends"])
        self.assertEqual(["T06b2o", "T06b2p"], by_id["T06b2l"]["depends"])
        self.assertEqual(["T06b2k"], by_id["T06b2o"]["depends"])
        self.assertEqual(["T06b2q", "T06b2r"], by_id["T06b2p"]["depends"])
        self.assertEqual(["T06b2o"], by_id["T06b2q"]["depends"])
        self.assertEqual(["T06b2s", "T06b2t"], by_id["T06b2r"]["depends"])
        self.assertEqual(["T06b2q"], by_id["T06b2s"]["depends"])
        self.assertEqual(["T06b2u", "T06b2v"], by_id["T06b2t"]["depends"])
        self.assertEqual(["T06b2s"], by_id["T06b2u"]["depends"])
        self.assertEqual(["T06b2w", "T06b2x"], by_id["T06b2v"]["depends"])
        self.assertEqual(["T06b2u"], by_id["T06b2w"]["depends"])
        self.assertEqual(["T06b2y", "T06b2z"], by_id["T06b2x"]["depends"])
        self.assertEqual(["T06b2w"], by_id["T06b2y"]["depends"])
        self.assertEqual(["T06b2aa", "T06b2ab"], by_id["T06b2z"]["depends"])
        self.assertEqual(["T06b2y"], by_id["T06b2aa"]["depends"])
        self.assertEqual(["T06b2ac", "T06b2ad"], by_id["T06b2ab"]["depends"])
        self.assertEqual(["T06b2aa"], by_id["T06b2ac"]["depends"])
        self.assertEqual(["T06b2ae", "T06b2af"], by_id["T06b2ad"]["depends"])
        self.assertEqual(["T06b2ac"], by_id["T06b2ae"]["depends"])
        self.assertEqual(["T06b2ag", "T06b2ah"], by_id["T06b2af"]["depends"])
        self.assertEqual(["T06b2ae"], by_id["T06b2ag"]["depends"])
        self.assertEqual(["T06b2ai", "T06b2aj"], by_id["T06b2ah"]["depends"])
        self.assertEqual(["T06b2ag"], by_id["T06b2ai"]["depends"])
        self.assertEqual(["T06b2ak", "T06b2al"], by_id["T06b2aj"]["depends"])
        self.assertEqual(["T06b2ai"], by_id["T06b2ak"]["depends"])
        self.assertEqual(["T06b2am", "T06b2an"], by_id["T06b2al"]["depends"])
        self.assertEqual(["T06b2ak"], by_id["T06b2am"]["depends"])
        self.assertEqual(["T06b2ao", "T06b2ap"], by_id["T06b2an"]["depends"])
        self.assertEqual(["T06b2am"], by_id["T06b2ao"]["depends"])
        self.assertEqual(["T06b2aq", "T06b2ar"], by_id["T06b2ap"]["depends"])
        self.assertEqual(["T06b2ao"], by_id["T06b2aq"]["depends"])
        self.assertEqual(["T06b2as", "T06b2at"], by_id["T06b2ar"]["depends"])
        self.assertEqual(["T06b2aq"], by_id["T06b2as"]["depends"])
        self.assertEqual(["T06b2au", "T06b2av"], by_id["T06b2at"]["depends"])
        self.assertEqual(["T06b2as"], by_id["T06b2au"]["depends"])
        self.assertEqual(["T06b2aw", "T06b2ax"], by_id["T06b2av"]["depends"])
        self.assertEqual(["T06b2au"], by_id["T06b2aw"]["depends"])
        self.assertEqual(["T06b2ay", "T06b2az"], by_id["T06b2ax"]["depends"])
        self.assertEqual(["T06b2aw"], by_id["T06b2ay"]["depends"])
        self.assertEqual(["T06b2ba", "T06b2bb"], by_id["T06b2az"]["depends"])
        self.assertEqual(["T06b2ay"], by_id["T06b2ba"]["depends"])
        self.assertEqual(["T06b2bc", "T06b2bd"], by_id["T06b2bb"]["depends"])
        self.assertEqual(["T06b2ba"], by_id["T06b2bc"]["depends"])
        self.assertEqual(["T06b2be", "T06b2bf"], by_id["T06b2bd"]["depends"])
        self.assertEqual(["T06b2bc"], by_id["T06b2be"]["depends"])
        self.assertEqual(["T06b2bg", "T06b2bh"], by_id["T06b2bf"]["depends"])
        self.assertEqual(["T06b2be"], by_id["T06b2bg"]["depends"])
        self.assertEqual(["T06b2bi", "T06b2bj"], by_id["T06b2bh"]["depends"])
        self.assertEqual(["T06b2bg"], by_id["T06b2bi"]["depends"])
        self.assertEqual(["T06b2bk", "T06b2bl"], by_id["T06b2bj"]["depends"])
        self.assertEqual(["T06b2bi"], by_id["T06b2bk"]["depends"])
        self.assertEqual(["T06b2bm", "T06b2bn"], by_id["T06b2bl"]["depends"])
        self.assertEqual(["T06b2bk"], by_id["T06b2bm"]["depends"])
        self.assertEqual(["T06b2bo", "T06b2bp"], by_id["T06b2bn"]["depends"])
        self.assertEqual(["T06b2bm"], by_id["T06b2bo"]["depends"])
        self.assertEqual(["T06b2bq", "T06b2br"], by_id["T06b2bp"]["depends"])
        self.assertEqual(["T06b2bo"], by_id["T06b2bq"]["depends"])
        self.assertEqual(["T06b2bs", "T06b2bt"], by_id["T06b2br"]["depends"])
        self.assertEqual(["T06b2bq"], by_id["T06b2bs"]["depends"])
        self.assertEqual(["T06b2bu", "T06b2bv"], by_id["T06b2bt"]["depends"])
        self.assertEqual(["T06b2bs"], by_id["T06b2bu"]["depends"])
        self.assertEqual(["T06b2bw", "T06b2bx"], by_id["T06b2bv"]["depends"])
        self.assertEqual(["T06b2bu"], by_id["T06b2bw"]["depends"])
        self.assertEqual(["T06b2by", "T06b2bz"], by_id["T06b2bx"]["depends"])
        self.assertEqual(["T06b2bw"], by_id["T06b2by"]["depends"])
        self.assertEqual(["T06b2ca", "T06b2cb"], by_id["T06b2bz"]["depends"])
        self.assertEqual(["T06b2by"], by_id["T06b2ca"]["depends"])
        self.assertEqual(["T06b2ca"], by_id["T06b2cc"]["depends"])
        self.assertEqual(["T06b2cc"], by_id["T06b2cd"]["depends"])
        self.assertEqual(["T06b2cd"], by_id["T06b2ce"]["depends"])
        self.assertEqual(["T06b2ce"], by_id["T06b2cf"]["depends"])
        self.assertEqual(["T06b2cf"], by_id["T06b2cg"]["depends"])
        self.assertEqual(["T06b2cg"], by_id["T06b2cb"]["depends"])
        self.assertEqual(["T06b", "T06ca", "T06cb", "T06cc", "T06cd", "T06ce"], by_id["T06c"]["depends"])
        self.assertEqual(["T06ca"], by_id["T06cb"]["depends"])
        self.assertEqual(["T06ca"], by_id["T06cc"]["depends"])
        self.assertEqual(["T06cb"], by_id["T06cd"]["depends"])
        self.assertEqual(["T06cb"], by_id["T06ce"]["depends"])
        self.assertEqual(["T06b2cb"], by_id["T06ca"]["depends"])
        self.assertIn("docs/07-machawk.md", by_id["T09"]["read"])
        self.assertTrue({"WB13", "WB14", "WB15"}.issubset(by_id["T09"]["checks"]))
        self.assertEqual(["T13", "T16a"], by_id["T16"]["depends"])
        self.assertEqual(["T13"], by_id["T16a"]["depends"])
        self.assertEqual(set(by_id["T16"]["checks"]), set(by_id["T16a"]["checks"]))
        self.assertEqual("M1", by_id["T16"]["milestone"])
        self.assertIn("STEP01", by_id["T16"]["checks"])
        self.assertIn("T16", by_id["T19"]["depends"])
        self.assertEqual(["T09", "T13", "T16"], by_id["T19a"]["depends"])
        self.assertEqual(["T19a"], by_id["T19b"]["depends"])
        self.assertEqual(["T19b"], by_id["T19c1"]["depends"])
        self.assertEqual(["T19c1"], by_id["T19c2"]["depends"])
        self.assertEqual(["T19b", "T19c1", "T19c2"], by_id["T19c"]["depends"])
        self.assertEqual(["T09", "T13", "T16", "T19a", "T19b", "T19c"], by_id["T19"]["depends"])
        self.assertIn("STEP01", by_id["T19"]["checks"])
        self.assertTrue(any("m68k_go/frame_end" in step for step in by_id["T07"]["steps"]))
        self.assertEqual(["T06", "T04", "T07a", "T07b", "T07c"], by_id["T07"]["depends"])
        self.assertEqual(["T06", "T04"], by_id["T07a"]["depends"])
        self.assertEqual(["T07a"], by_id["T07b"]["depends"])
        self.assertEqual(["T07b"], by_id["T07c"]["depends"])
        self.assertIn("STEP01", by_id["T07c"]["checks"])
        self.assertNotIn("STEP01", by_id["T07a"]["checks"] + by_id["T07b"]["checks"])
        self.assertEqual(["T07", "T08a", "T08b", "T08c"], by_id["T08"]["depends"])
        self.assertEqual(["T07"], by_id["T08a"]["depends"])
        self.assertEqual(["T08a"], by_id["T08b"]["depends"])
        self.assertEqual(["T08b"], by_id["T08c"]["depends"])
        self.assertIn("AV01", by_id["T08b"]["checks"] + by_id["T08c"]["checks"])
        self.assertTrue({"STEP01", "AV01"} <= set(by_id["T08c"]["checks"]))
        self.assertEqual(["T08", "T10a", "T10b"], by_id["T10"]["depends"])
        self.assertEqual(["T08"], by_id["T10a"]["depends"])
        self.assertEqual(["T10a"], by_id["T10b"]["depends"])
        self.assertIn("IN01", by_id["T10a"]["checks"] + by_id["T10b"]["checks"])
        self.assertEqual(["T08", "T12a", "T12b", "T12c"], by_id["T12"]["depends"])
        self.assertEqual(["T08"], by_id["T12a"]["depends"])
        self.assertEqual(["T12a"], by_id["T12b"]["depends"])
        self.assertEqual(["T12b"], by_id["T12c"]["depends"])
        self.assertIn("MED01", by_id["T12b"]["checks"] + by_id["T12c"]["checks"])
        self.assertEqual(["T10", "T11", "T12", "T26", "T13a", "T13b"], by_id["T13"]["depends"])
        self.assertEqual(["T10", "T11", "T12"], by_id["T13a"]["depends"])
        self.assertEqual(["T13a", "T26"], by_id["T13b"]["depends"])
        self.assertIn("LIFE01", by_id["T13a"]["checks"] + by_id["T13b"]["checks"])
        required = {task_id for task_id, task in by_id.items() if task["required"]}
        ancestors = set()
        queue = list(by_id["T41"]["depends"])
        while queue:
            item = queue.pop()
            if item not in ancestors:
                ancestors.add(item)
                queue.extend(by_id[item]["depends"])
        self.assertEqual(required - {"T41"}, ancestors)

    def test_duplicate_id_rejected(self):
        self.data["tasks"].append(copy.deepcopy(self.data["tasks"][0]))
        self.assert_error("ID duplicado")

    def test_unknown_dependency_rejected(self):
        self.data["tasks"][1]["depends"] = ["T99"]
        self.assert_error("dependencia desconocida")

    def test_self_dependency_rejected(self):
        self.data["tasks"][0]["depends"] = ["T00"]
        self.assert_error("dependencia de si misma")

    def test_cycle_rejected(self):
        self.data["tasks"][0]["depends"] = ["T01"]
        self.assert_error("Ciclo de dependencias")

    def test_invalid_status_rejected(self):
        self.data["tasks"][0]["status"] = "probably_finished"
        self.assert_error("estado invalido")

    def test_multiple_in_progress_rejected(self):
        for task in self.data["tasks"]:
            task["depends"] = []
            task["status"] = "in_progress"
        self.assert_error("Mas de una tarea")

    def test_done_without_evidence_accepted(self):
        self.data["tasks"][0]["status"] = "done"
        self.assertEqual([], self.errors())

    def test_done_with_pending_dependency_rejected(self):
        self.finish("T01")
        self.assert_error("dependencia T00 no terminada")

    def test_done_with_report_accepted(self):
        self.finish("T00")
        self.add_note("T00")
        self.assertEqual([], self.errors())

    def test_report_template_is_not_evidence(self):
        self.data["tasks"][0].update(status="done", evidence="README.md")
        self.assert_error("evidence debe ser reports/tasks/T00.md")

    def test_empty_evidence_rejected(self):
        self.finish("T00")
        self.add_note("T00")
        (self.root / "reports/tasks/T00.md").write_text(" \n")
        self.assert_error("inexistente o vacia")

    def test_blocked_requires_report(self):
        self.data["tasks"][0]["status"] = "blocked"
        self.assert_error("requiere nota")

    def test_unknown_check_rejected(self):
        self.data["tasks"][0]["checks"] = ["MADE_UP_SUCCESS"]
        self.assert_error("check desconocido")

    def test_duplicate_check_catalog_rejected(self):
        self.data["check_ids"].append("PLAN01")
        self.assert_error("check_ids contiene duplicados")

    def test_missing_read_rejected(self):
        self.data["tasks"][0]["read"] = ["does-not-exist.md"]
        self.assert_error("lectura inexistente")

    def test_path_traversal_rejected(self):
        self.data["tasks"][0]["touch"] = ["../MacHawk/overwrite.cs"]
        self.assert_error("ruta fuera del proyecto")

    def test_absolute_path_rejected(self):
        self.data["tasks"][0]["read"] = ["/etc/passwd"]
        self.assert_error("ruta fuera del proyecto")

    def test_windows_path_rejected(self):
        self.assertIsNone(plan.safe_path(self.root, "C:\\outside\\file.txt"))
        self.assertIsNone(plan.safe_path(self.root, "C:/outside/file.txt"))

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p6-outside-test-") as other:
            target = Path(other) / "not-project.txt"
            target.write_text("Synthetic outside fixture")
            try:
                (self.root / "escape.md").symlink_to(target)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Symlink creation unavailable: {error}")
            self.assertIsNone(plan.safe_path(self.root, "escape.md"))

    def test_nonboolean_required_rejected(self):
        self.data["tasks"][0]["required"] = "true"
        self.assert_error("required debe ser booleano")

    def test_malformed_commit_rejected(self):
        self.lock["sources"][0]["commit"] = "main"
        self.assert_error("SHA completo")

    def test_pending_pin_allowed_only_for_planning(self):
        self.lock["sources"][0]["commit"] = None
        self.assertEqual([], self.errors())
        self.assert_error("pin obligatorio pendiente", release=True)

    def test_local_source_provenance_allowed(self):
        source = self.lock["sources"][0]
        source.update(repository=None, local_path="../MacHawk")
        self.assertEqual([], self.errors())

    def test_missing_reference_manifest_rejected(self):
        self.lock["sources"][0]["reference_manifest"] = "plan/missing.json"
        self.assert_error("reference_manifest inexistente")

    def test_release_rejects_pending_tasks(self):
        self.assert_error("tareas obligatorias sin terminar", release=True)

    def test_release_rejects_unresolved_lock(self):
        self.finish("T00")
        self.finish("T01")
        self.lock["unresolved"] = ["Synthetic unresolved input"]
        self.assert_error("pendientes explicitos", release=True)

    def test_complete_documentary_release_accepted(self):
        self.finish("T00")
        self.finish("T01")
        self.assertEqual([], self.errors(release=True))

    def test_next_respects_dependencies_and_array_priority(self):
        self.assertEqual(["T00"], [task["id"] for task in plan.ready_tasks(self.data)])
        self.finish("T00")
        third = copy.deepcopy(self.data["tasks"][1])
        third.update(id="T09", depends=["T00"])
        self.data["tasks"].insert(1, third)
        self.assertEqual(["T09", "T01"], [task["id"] for task in plan.ready_tasks(self.data)])

    def test_malformed_structures_fail_without_crash(self):
        self.data["tasks"].append("not a task")
        self.data["tasks"][0]["status"] = ["done"]
        self.data["tasks"][0]["checks"] = [{}]
        self.assertGreaterEqual(len(self.errors()), 3)

    def test_cli_validate_and_show_do_not_write(self):
        self.write_inputs()
        before = (self.root / "plan/tasks.json").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, plan.main(["validate"], self.root))
            self.assertEqual(0, plan.main(["next"], self.root))
            self.assertEqual(0, plan.main(["show", "T00"], self.root))
        self.assertEqual(before, (self.root / "plan/tasks.json").read_bytes())

    def test_cli_unknown_task_has_nonzero_exit(self):
        self.write_inputs()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, plan.main(["show", "T99"], self.root))

    def test_cli_malformed_json_has_nonzero_exit(self):
        self.write_inputs()
        (self.root / "plan/tasks.json").write_text("{broken json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, plan.main(["validate"], self.root))


if __name__ == "__main__":
    unittest.main()
