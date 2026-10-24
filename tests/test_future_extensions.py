"""T44 study consistency; these checks do not execute or enable an MMU."""
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check
import generate_options


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
