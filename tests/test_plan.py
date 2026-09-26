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
        self.assertEqual(48, len(by_id))
        self.assertEqual(42, sum(task["required"] for task in by_id.values()))
        self.assertIn("docs/07-machawk.md", by_id["T09"]["read"])
        self.assertTrue({"WB13", "WB14", "WB15"}.issubset(by_id["T09"]["checks"]))
        self.assertEqual("M1", by_id["T16"]["milestone"])
        self.assertIn("STEP01", by_id["T16"]["checks"])
        self.assertIn("T16", by_id["T19"]["depends"])
        self.assertIn("STEP01", by_id["T19"]["checks"])
        self.assertTrue(any("m68k_go/frame_end" in step for step in by_id["T07"]["steps"]))
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
