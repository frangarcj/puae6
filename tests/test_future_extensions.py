"""Extension-plan consistency, including the implemented CD32 FMV subbacklog."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check
import generate_options


class PostReleaseStudyTests(unittest.TestCase):
    def validate_study(self, task_id, name, adr):
        study = json.loads((ROOT / f"plan/extensions/{name}.json").read_text())
        expected_scope = ("implemented_public_cd32_fmv_profile_via_subbacklog"
                          if task_id == "T43" else "study_only_no_profile_changes")
        self.assertEqual((1, task_id, expected_scope),
                         (study["schema_version"], study["task"], study["scope"]))
        tasks = json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]
        task = next(t for t in tasks if t["id"] == task_id)
        self.assertFalse(task["required"])
        self.assertEqual(["AV01", "DET02"] if task_id == "T43" else ["ADR01"], task["checks"])
        self.assertIn(adr, task["touch"])
        self.assertTrue((ROOT / adr).read_text().strip())
        sources = json.loads((ROOT / "plan/upstream.lock.json").read_text())["sources"]
        pin = study.get("upstream", study.get("pin"))
        source = next(s for s in sources if s["id"] == pin["source_id"])
        self.assertEqual(source["commit"], pin["commit"])
        self.assertEqual(40, len(pin["commit"]))
        self.assertTrue((ROOT / pin["snapshot_manifest"]).is_file())
        self.assertTrue(study["decision"])
        proposals = {p["id"]: p for p in study["subbacklog"]}
        self.assertEqual(len(proposals), len(study["subbacklog"]))
        self.assertTrue(proposals)
        paths = [p["path"] if isinstance(p, dict) else p for p in study["source_paths"]]
        task_ids = {t["id"] for t in tasks}
        by_id = {t["id"]: t for t in tasks}
        for item in proposals.values():
            if task_id == "T43" and item.get("task"):
                self.assertEqual(by_id[item["task"]]["status"], item["status"])
            elif task_id == "T43" and item.get("subtasks"):
                states = [by_id[subtask]["status"] for subtask in item["subtasks"]]
                expected = "done" if all(state == "done" for state in states) else (
                    "in_progress" if any(state in ("done", "in_progress") for state in states) else "proposed")
                self.assertEqual(expected, item["status"])
            else:
                self.assertEqual("proposed", item["status"])
            self.assertTrue(item["deliverable"].strip() and item["oracle"].strip())
            self.assertTrue(set(item["depends"]) <= proposals.keys() | task_ids)
            self.assertNotIn(item["id"], item["depends"])
            paths.extend(item.get("source_paths", []))
        for name in paths:
            path = (ROOT / name).resolve()
            self.assertTrue(path.is_relative_to(ROOT), name)
            self.assertTrue(path.exists(), name)
        reached = set()
        while True:
            ready = {key for key, p in proposals.items()
                     if (set(p["depends"]) & proposals.keys()) <= reached}
            if ready <= reached:
                break
            reached |= ready
        self.assertEqual(set(proposals), reached, "cyclic or unreachable proposal")
        selector = f"tests.test_future_extensions.PostReleaseStudyTests.{self._testMethodName}"
        if task_id == "T43":
            for gate, spec in study["current_checks"].items():
                self.assertTrue(spec["covers"].strip())
                self.assertEqual(set(spec["selectors"]) | {selector},
                                 set(check.TASK_SUITES[(gate, task_id)].values()))
                for item in spec["selectors"]:
                    loader = unittest.TestLoader()
                    suite = loader.loadTestsFromName(item)
                    self.assertFalse(loader.errors, item)
                    self.assertGreater(suite.countTestCases(), 0)
        else:
            self.assertEqual({"study_contract": selector}, check.TASK_SUITES[("ADR01", task_id)])
        return study

    def test_jit(self):
        study = self.validate_study("T42", "jit", "docs/adr/future-jit.md")
        self.assertFalse(study["support_announced"])
        self.assertFalse(study["measurement_basis"]["priority_cpu_hotspot_established"])
        self.assertEqual("ADVISORY", study["measurement_basis"]["enforcement"])
        self.assertEqual("ADVISORY", json.loads((ROOT / "plan/performance-budgets.json").read_text())["status"])
        arm64 = next(b for b in study["backends"] if b["isa"] == "AArch64")
        self.assertIsNone(arm64["source"])
        self.assertFalse(any(b["port_verified"] for b in study["backends"]))

    def test_rtg(self):
        study = self.validate_study("T45", "rtg", "docs/adr/future-rtg.md")
        self.assertEqual("not_implemented", study["decision"]["runtime_support"])
        memory = study["memory_proposal"]
        total = sum(memory[k] for k in ("vram_bytes", "conversion_buffer_bytes", "auxiliary_cap_bytes"))
        self.assertEqual(total, memory["mutable_increment_bytes"])
        self.assertEqual(total, memory["logical_4k_pages"] * 4096)
        self.assertEqual(total, memory["physical_16k_groups"] * 16384)
        self.assertEqual(total * 2, memory["initial_mmap_budget_increment_kib"] * 1024)
        self.assertEqual("not_run", memory["measurement_status"])

    def test_waterbox_platforms(self):
        study = self.validate_study("T46", "waterbox-platforms", "docs/adr/future-waterbox-platforms.md")
        self.assertFalse(study["support_announced"])
        self.assertEqual({f"WB{i:02d}" for i in range(1, 13)}, set(study["gates"]))
        self.assertEqual("passed", study["gates"]["WB01"]["status"])
        self.assertTrue(all(g["status"] == "blocked" for key, g in study["gates"].items() if key != "WB01"))
        execution = study["gates"]["WB01"]["execution"]
        self.assertEqual("WB01_MINICORE", execution["scope"])
        for name, expected in execution["source_sha256"].items():
            self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        self.assertEqual(execution["recipe_sha256"], hashlib.sha256((ROOT / "tools/ci/linux_waterbox_probe.py").read_bytes()).hexdigest())
        tasks = json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]
        self.assertTrue(all(next(t for t in tasks if t["id"] == key)["status"] == "done"
                            for key in ("T46a", "T46a1", "T46a2")))
        self.assertEqual(check.TASK_SUITES[("WB01", "T46a2")], check.TASK_SUITES[("WB01", "T46a")])
        self.assertIn("tests.ci.test_linux_waterbox_probe.LinuxWaterboxRuntimeTests", check.TASK_SUITES[("WB01", "T46a")].values())
        self.assertFalse(study["observed_unverified_artifact"]["accepted_as_wb01"])
        self.assertFalse(study["observed_unverified_artifact"]["executed"])
        sources = json.loads((ROOT / "plan/upstream.lock.json").read_text())["sources"]
        for source in study["sources"]:
            self.assertEqual(source["commit"], next(s["commit"] for s in sources if s["id"] == source["source_id"]))
            self.assertEqual(source["manifest_sha256"], hashlib.sha256((ROOT / source["snapshot_manifest"]).read_bytes()).hexdigest())
        for source in study["source_paths"]:
            self.assertEqual(source["sha256"], hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest())

    def test_multi_instance(self):
        study = self.validate_study("T47", "multi-instance", "docs/adr/future-multi-instance.md")
        self.assertIn("BUSY for nested calls", study["decision"]["preserve"])
        self.assertIn("native reentrancy", study["decision"]["not_claimed"])

    def test_fmv(self):
        study = self.validate_study("T43", "cd", "docs/adr/future-cd.md")
        self.assertEqual("implemented_public_cd32_fmv_profile_via_subbacklog", study["scope"])
        self.assertTrue(study["support_announced"])
        self.assertEqual({"AV01", "DET02"}, set(study["current_checks"]))
        self.assertEqual("exact_scalar_native_arm64_x86_64_sanitized_and_waterbox_linked_FMV-B2",
                         study["decoder_closure"]["status"])
        self.assertEqual("build/libmpeg2", study["decoder_closure"]["derived_tree"])
        self.assertTrue((ROOT / study["decoder_closure"]["patch_series"]).is_file())
        bounds = study["bounds_inventory"]
        self.assertEqual(bounds["known_mutable_payload_bytes"]
                         + bounds["libmpeg2_peak_live_requested_bytes"]
                         + bounds["kjmp2_context_bytes"],
                         bounds["measured_peak_allocations_plus_owned_payload_and_mp2_context_bytes"])
        self.assertEqual("complete_P6FM_bounded_and_transactional_FMV-D2",
                         bounds["state_bound_status"])
        self.assertEqual(17599236, bounds["max_native_state_bound_bytes"])
        self.assertLessEqual(bounds["observed_complete_state_bytes"],
                             bounds["max_native_state_bound_bytes"])
        fmv_c = next(item for item in study["subbacklog"] if item["id"] == "FMV-C")
        self.assertEqual("T43c", fmv_c["task"])
        self.assertEqual("done", fmv_c["status"])
        self.assertEqual({"AV02", "DET02"}, set(fmv_c["gates"]))
        self.assertEqual(4608, study["pcm_runtime"]["fmv_max_frames"])
        self.assertEqual("P6FM_value_codec_T43d2", study["pcm_runtime"]["state_codec"])
        fmv_d = next(item for item in study["subbacklog"] if item["id"] == "FMV-D")
        self.assertEqual(["T43d1", "T43d2"], fmv_d["subtasks"])
        self.assertEqual("done", fmv_d["status"])
        fmv_e = next(item for item in study["subbacklog"] if item["id"] == "FMV-E")
        self.assertEqual(["T43e1", "T43e2"], fmv_e["subtasks"])
        self.assertEqual("done", fmv_e["status"])
        fmv_f = next(item for item in study["subbacklog"] if item["id"] == "FMV-F")
        self.assertEqual(["T43f1", "T43f2"], fmv_f["subtasks"])
        self.assertEqual("done", fmv_f["status"])
        self.assertEqual("FMV-E2_exact_composed_handover_native_and_Waterbox_allocations",
                         study["compositor"]["status"])
        self.assertEqual([1, 2, 4], study["compositor"]["integer_scales"])
        self.assertEqual(0, study["compositor"]["allocation_per_pass"])
        self.assertEqual("single_XRGB8888_composed_frame_T43e2",
                         study["compositor"]["public_handover"])
        self.assertEqual(1320984, study["decoder_state"]["max_serialized_bytes"])
        self.assertEqual("P6FM_complete_T43d2", study["decoder_state"]["full_device_chunk"])
        self.assertEqual("FMV-F2_Cannon_Fodder_libretro_and_Waterbox_fresh_owner_acceptance_passed", study["frontend"]["status"])
        self.assertLessEqual(study["frontend"]["waterbox_fmv_committed_bytes"] * 2,
                             study["frontend"]["waterbox_fmv_mmap_budget_bytes"])
        source_policy = json.loads((ROOT / "manifests/source-manifest.json").read_text())[
            "exclusions"]["JIT/PPC/FPU/MMU/RTG/CD32/CDTV"]
        self.assertIn("la opcion FMV se expone publicamente desde T43f2", source_policy)
        self.assertNotIn("la opcion FMV sigue sin exponerse", source_policy)
        acceptance = study["runtime_acceptance"]
        self.assertEqual({
            "status": "passed",
            "implemented_by": "T43a_through_T43f2",
            "terminal_task": "T43f2",
            "gate_registry": "tools/check.py:TASK_SUITES",
            "root_task_note": acceptance["root_task_note"],
        }, acceptance)
        self.assertIn("real Cannon Fodder", acceptance["root_task_note"])
        tasks = {task["id"]: task for task in
                 json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]}
        runtime_tasks = set()
        for item in study["subbacklog"]:
            runtime_tasks.update(item.get("subtasks", []))
            if item.get("task"):
                runtime_tasks.add(item["task"])
        self.assertEqual({"T43a", "T43b1", "T43b2", "T43c", "T43d1", "T43d2",
                          "T43e1", "T43e2", "T43f1", "T43f2"}, runtime_tasks)
        for task_id in runtime_tasks:
            self.assertEqual("done", tasks[task_id]["status"], task_id)
            for gate in tasks[task_id]["checks"]:
                selectors = check.TASK_SUITES[(gate, task_id)]
                self.assertTrue(selectors, (task_id, gate))
                for selector in selectors.values():
                    loader = unittest.TestLoader()
                    suite = loader.loadTestsFromName(selector)
                    self.assertFalse(loader.errors, (task_id, gate, selector))
                    self.assertGreater(suite.countTestCases(), 0, (task_id, gate, selector))
        self.assertTrue(study["support_announced"])
        self.assertIn("!defined(P6_PORTABLE)",
                      (ROOT / "patches/winuae/0362-cd32-without-fmv.patch").read_text())


class MmuStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = json.loads((ROOT / "plan/extensions/fpu-mmu.json").read_text())

    def test_scope_and_upstream_are_explicit_and_pinned(self):
        study = self.study
        self.assertEqual((1, "T44", "study_only_no_profile_changes"),
                         (study["schema_version"], study["task"], study["scope"]))
        source = next(s for s in json.loads((ROOT / "plan/upstream.lock.json").read_text())["sources"]
                      if s["id"] == study["upstream"]["source_id"])
        self.assertEqual(source["commit"], study["upstream"]["commit"])
        self.assertEqual("6030", study["upstream"]["tag"])
        self.assertTrue((ROOT / study["upstream"]["snapshot_manifest"]).is_file())
        self.assertNotIn("mmu_model", generate_options.p6_config_fields())
        recipe = json.loads((ROOT / "manifests/codegen.json").read_text())
        cpu = next(g for g in recipe["generators"] if g["name"] == "gencpu")
        self.assertTrue(all("--no-mmu" in run["args"] for run in cpu["runs"]))
        self.assertFalse({"cpuemu_31.cpp", "cpuemu_32.cpp", "cpuemu_33.cpp"} & set(cpu["outputs"]))

    def test_matrix_separates_every_current_pair_from_its_unimplemented_extension(self):
        pairs = {(68030, 0), (68030, 68881), (68030, 68882),
                 (68040, 0), (68040, 68040), (68060, 0), (68060, 68060)}
        expected = {(cpu, fpu, mmu) for cpu, fpu in pairs for mmu in (0, cpu)}
        actual = set()
        for row in self.study["matrix"]:
            self.assertEqual({"cpu", "machine", "fpu", "mmu", "accuracy", "status"}, set(row))
            self.assertTrue(all(type(row[key]) is int for key in ("cpu", "fpu", "mmu")))
            key = row["cpu"], row["fpu"], row["mmu"]
            self.assertNotIn(key, actual)
            actual.add(key)
            self.assertEqual("A4030" if row["cpu"] == 68030 else "A4040", row["machine"])
            self.assertEqual("Normal", row["accuracy"])
            self.assertEqual("planned_unimplemented" if row["mmu"] else "supported_no_mmu", row["status"])
        self.assertEqual(expected, actual)
        self.assertTrue(self.study["invalid_combinations"])
        self.assertTrue(self.study["outside_scope"])

    def test_subbacklog_has_source_backed_oracles_and_an_acyclic_acceptance_path(self):
        proposals = self.study["subbacklog"]
        by_id = {p["id"]: p for p in proposals}
        self.assertEqual(len(proposals), len(by_id))
        self.assertTrue(by_id)
        for item in proposals:
            self.assertEqual("proposed", item["status"])
            self.assertIn(item["kind"], ("decision", "probe", "acceptance_gate"))
            self.assertTrue(set(item["cpus"]) <= {68030, 68040, 68060})
            self.assertTrue(item["cpus"] and item["source_paths"])
            self.assertTrue(item["deliverable"].strip() and item["oracle"].strip())
            self.assertTrue(set(item["depends"]) <= by_id.keys())
            for name in item["source_paths"]:
                path = (ROOT / name).resolve()
                self.assertTrue(path.is_relative_to(ROOT))
                self.assertTrue(path.exists(), name)
        reached = set()
        while True:
            ready = {key for key, item in by_id.items() if set(item["depends"]) <= reached}
            if ready <= reached:
                break
            reached |= ready
        self.assertEqual(set(by_id), reached, "cyclic or unreachable proposal")
        gates = [p for p in proposals if p["kind"] == "acceptance_gate"]
        self.assertTrue(gates)
        self.assertEqual({68030, 68040, 68060}, {cpu for gate in gates for cpu in gate["cpus"]})
        self.assertEqual({"GEN01", "DET02"}, set(self.study["future_gates"]))

    def test_current_checks_resolve_and_dispatch_does_not_claim_future_execution(self):
        self.assertEqual({"GEN01", "DET02"}, set(self.study["current_checks"]))
        self.assertEqual("python3 tools/generate_cpu.py --check", self.study["current_checks"]["GEN01"]["command"])
        for gate, spec in self.study["current_checks"].items():
            selectors = spec["selectors"]
            mapped = set(check.TASK_SUITES[(gate, "T44")].values())
            expected = set(selectors)
            if gate == "GEN01":
                expected.add("tests.test_future_extensions.MmuStudyTests")
            self.assertEqual(expected, mapped)
            self.assertEqual(len(mapped), len(check.TASK_SUITES[(gate, "T44")]))
            self.assertTrue(spec["covers"].strip())
            for selector in selectors:
                parts = selector.split(".")
                for split in range(len(parts), 0, -1):
                    path = ROOT.joinpath(*parts[:split]).with_suffix(".py")
                    if path.is_file():
                        nodes = ast.parse(path.read_text()).body
                        for name in parts[split:]:
                            node = next(n for n in nodes if getattr(n, "name", None) == name)
                            nodes = getattr(node, "body", [])
                        break
                else:
                    self.fail("missing current selector: " + selector)


if __name__ == "__main__":
    unittest.main()
