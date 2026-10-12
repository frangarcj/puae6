"""The classification that defines the REF01 reference (tests/reference/patch-classes.json): it covers the whole patch series, it only removes
what it says it removes, and the tree it produces differs from the product's in exactly the files of the removed patches.

Materializing the reference tree needs build/ (a copy of the verified snapshot and ~100 `git apply` calls, a few seconds); nothing is compiled here."""
import copy
import json
from pathlib import Path
import re
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_reference as br  # noqa: E402
import import_sources  # noqa: E402
import materialize_upstream as mu  # noqa: E402

SERIES = json.loads((ROOT / "patches/winuae/series.json").read_text())
CLASSES = br.load_classes(ROOT)
CODEGEN = json.loads((ROOT / "manifests/codegen.json").read_text())


def generator_inputs():
    names = set()
    for generator in CODEGEN["generators"]:
        for source in generator["sources"]:
            if source.startswith("upstream/"):
                names.add(source[len("upstream/"):])
        for run in generator["runs"]:
            if run.get("stdin", "").startswith("upstream/"):
                names.add(run["stdin"][len("upstream/"):])
    return names


def test_file(reference):
    """The first file a mention like `tests.host.test_pcm`, `tests/host/test_cpu_fpu.py` or `tests.fuzz.test_fuzz.HardFileFuzzTests` points at."""
    for token in re.findall(r"tests[./][\w./-]+", reference):
        parts = token.rstrip(".").replace(".py", "").replace("/", ".").split(".")
        while parts:
            if (ROOT / Path(*parts)).with_suffix(".py").is_file():
                return True
            parts.pop()
    return False


class ClassificationTests(unittest.TestCase):
    def test_the_classification_covers_the_whole_series(self):
        self.assertEqual([], br.validate(CLASSES, SERIES))
        self.assertEqual(len(SERIES["patches"]), len(CLASSES["patches"]))
        counts = {name: list(CLASSES["patches"].values()).count(name) for name in br.CLASS_NAMES}
        self.assertEqual(len(SERIES["patches"]), sum(counts.values()))
        self.assertGreater(counts["required"], counts["behavior"], "the reference must keep what the profile cannot run without")

    def test_a_patch_without_class_an_unknown_class_and_a_stale_entry_are_refused(self):
        broken = copy.deepcopy(CLASSES)
        first = SERIES["patches"][0]["id"]
        del broken["patches"][first]
        broken["patches"]["9999-no-such-patch"] = "required"
        broken["patches"][SERIES["patches"][1]["id"]] = "optional"
        errors = br.validate(broken, SERIES)
        self.assertIn(f"patch not classified: {first}", errors)
        self.assertIn("classified patch that is not in the series: 9999-no-such-patch", errors)
        self.assertTrue(any("unknown class 'optional'" in error for error in errors))

    def test_only_behavior_and_instrumentation_can_be_ablated_and_every_ablation_is_explained(self):
        broken = copy.deepcopy(CLASSES)
        required = next(ident for ident, name in CLASSES["patches"].items() if name == "required")
        broken["ablate"][required] = dict(effect="neutral", why="x", test="y", scenarios=[], subsystems=[])
        self.assertTrue(any("only behavior and instrumentation" in e for e in br.validate(broken, SERIES)))
        ident = next(iter(CLASSES["ablate"]))
        for mutation, text in ((lambda e: e.pop("test"), "exactly effect"), (lambda e: e.update(effect="maybe"), "effect must be one of"),
                               (lambda e: e.update(why=""), "non-empty text"), (lambda e: e.update(effect="differs", scenarios=[], subsystems=[]), "names the cases"),
                               (lambda e: e.update(subsystems=["pcm"]), "only a patch expected to differ")):
            broken = copy.deepcopy(CLASSES)
            mutation(broken["ablate"][ident])
            self.assertTrue(any(text in error for error in br.validate(broken, SERIES)), text)

    def test_a_behavior_patch_that_is_neither_ablated_nor_explained_is_refused(self):
        broken = copy.deepcopy(CLASSES)
        ident = next(iter(CLASSES["ablate"]))
        del broken["ablate"][ident]
        self.assertTrue(any("says why under 'keep'" in error for error in br.validate(broken, SERIES)))
        broken["keep"][ident] = "other code needs it"
        self.assertEqual([], br.validate(broken, SERIES))

    def test_every_ablation_names_a_test_that_exists(self):
        for ident, entry in CLASSES["ablate"].items():
            self.assertTrue(test_file(entry["test"]), f"{ident}: no test file found in {entry['test']!r}")

    def test_no_ablated_patch_touches_a_generator_input_or_a_header(self):
        inputs = generator_inputs()
        self.assertIn("gencpu.cpp", inputs)
        by_id = {p["id"]: p for p in SERIES["patches"]}
        for ident in CLASSES["ablate"]:
            for item in by_id[ident]["files"]:
                path = item["path"]
                self.assertNotIn(path, inputs, f"{ident} changes a generator input, so the generated sources would not be the product's")
                self.assertFalse(path.startswith("include/") or path.endswith(".h"), f"{ident} changes a header ({path}), which the generators and the bridge include")

    def test_the_scenarios_of_the_ablations_are_cases_of_the_corpus(self):
        from tests.portability import corpus
        from tests.reference.test_ref01 import EXTRA_CASES
        known = {case["id"] for case in corpus.cases(corpus.sources())} | {case["id"] for case in EXTRA_CASES}
        try:
            from tests.scenarios.corpus import corpus as differential
            known |= {entry.id for entry in differential.ENTRIES}
        except ImportError:          # the differential corpus is T32b
            pass
        for ident, entry in CLASSES["ablate"].items():
            for scenario in entry["scenarios"]:
                self.assertIn(scenario, known, f"{ident}")


class ReferenceTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt, cls.path = br.materialize_reference(ROOT)
        _, cls.product = mu.materialize(ROOT)

    def test_the_receipt_says_what_was_applied(self):
        ids = [p["id"] for p in SERIES["patches"]]
        self.assertEqual(sorted(ids), sorted(self.receipt["applied"] + self.receipt["ablated"]))
        self.assertEqual(sorted(CLASSES["ablate"]), sorted(self.receipt["ablated"]))
        self.assertEqual(mu._series(ROOT, SERIES["upstream_commit"])[1], self.receipt["series_sha256"])

    def test_the_reference_differs_from_the_product_only_in_the_files_of_the_ablated_patches(self):
        product = {item["path"]: item["sha256"] for item in import_sources.inventory(self.product)}
        reference = {item["path"]: item["sha256"] for item in import_sources.inventory(self.path)}
        self.assertEqual(set(product), set(reference))
        differing = {name for name in product if product[name] != reference[name]}
        by_id = {p["id"]: p for p in SERIES["patches"]}
        allowed = {item["path"] for ident in CLASSES["ablate"] for item in by_id[ident]["files"]}
        self.assertTrue(differing, "ablating patches must change the tree")
        self.assertLessEqual(differing, allowed)

    def test_a_second_materialization_reuses_the_tree_and_a_modified_tree_is_refused(self):
        self.assertEqual(self.receipt, br.materialize_reference(ROOT)[0])
        # A copy of its own: the shared tree is read by the builds and the comparisons that may be running next to this test.
        spare = "build/reference-tamper/upstream"
        receipt, path = br.materialize_reference(ROOT, spare, force=True)
        try:
            self.assertEqual(self.receipt["tree"], receipt["tree"])
            (path / "audio.cpp").write_bytes((path / "audio.cpp").read_bytes() + b"\n// tampered\n")
            with self.assertRaisesRegex(ValueError, "not what its receipt says"):
                br.materialize_reference(ROOT, spare)
        finally:
            shutil.rmtree(ROOT / "build/reference-tamper", ignore_errors=True)

    def test_a_tree_that_is_the_earlier_output_of_the_tool_is_replaced_when_the_classification_changes_and_a_modified_one_is_not(self):
        spare = "build/reference-replace/upstream"
        other = copy.deepcopy(CLASSES)
        victim = "0020-audio-signed-scaling"
        del other["ablate"][victim]
        other["patches"][victim] = "required"
        try:
            first, path = br.materialize_reference(ROOT, spare, force=True)
            second, _ = br.materialize_reference(ROOT, spare, classes=other)
            self.assertNotEqual(first["tree"], second["tree"])
            self.assertEqual(len(first["applied"]) + 1, len(second["applied"]))
            (path / "audio.cpp").write_bytes((path / "audio.cpp").read_bytes() + b"\n// edited by hand\n")
            with self.assertRaisesRegex(ValueError, "in the way; not overwritten"):       # its identity changed too: it is not the tool's output any more, it is somebody's edit
                br.materialize_reference(ROOT, spare, classes=CLASSES)
        finally:
            shutil.rmtree(ROOT / "build/reference-replace", ignore_errors=True)

    def test_ablating_a_patch_that_another_one_needs_stops_with_the_name_of_the_dependent(self):
        # 0078 changes lines that sit next to the ones 0070 changes: the reference cannot be built without 0070.
        broken = copy.deepcopy(CLASSES)
        ident = next(p["id"] for p in SERIES["patches"] if p["id"].startswith("0070-"))
        broken["patches"][ident] = "behavior"
        broken["ablate"][ident] = dict(effect="inert", why="x", test="tests.lifecycle.test_reload", scenarios=[], subsystems=[])
        try:
            with self.assertRaisesRegex(ValueError, r"0078-events-state-access does not apply to the reference tree"):
                br.materialize_reference(ROOT, "build/reference-conflict/upstream", force=True, classes=broken)
        finally:
            shutil.rmtree(ROOT / "build/reference-conflict", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
