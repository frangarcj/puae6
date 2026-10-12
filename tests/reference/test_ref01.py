"""REF01: the product and the reference (the same WinUAE 6.0.3 snapshot without the patches that change what the engine computes, see
tests/reference/patch-classes.json) run the micro-ROM corpus of tests/portability/corpus.py through the same probe, bridge, port and inputs, and
tools/compare_runs.py compares them boundary by boundary: ticks and events (scheduler), CPU, RAM, chipset and its groups, devices, and the
audio and picture the host receives. Equal is the rule; a difference is allowed only where an ablated patch is declared to make one, in exactly
the subsystems it declares, and only there.

This is fidelity to the chosen upstream commit under the shared port, never evidence about a physical Amiga, and it cannot see what the product
and the reference share (docs/04-validation.md, "Referencia de exactitud")."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import compare_runs as cr  # noqa: E402
from tests.portability import corpus  # noqa: E402
from tests.reference import builds  # noqa: E402
from tests.scenarios.corpus import corpus as differential  # noqa: E402

CLASSES = json.loads((ROOT / "tests/reference/patch-classes.json").read_text())


def expected_difference(case_id):
    """The subsystems in which the reference is declared to differ from the product in this case (empty: it must be equal)."""
    expected = set()
    for entry in CLASSES["ablate"].values():
        if entry["effect"] == "differs" and case_id in entry["scenarios"]:
            expected |= set(entry["subsystems"])
    return expected


# Cases of REF01 that the portability corpus does not have: the machine restarted by p6_reset after unit 3 (the reference does not put the pot port back, see patch 0069).
EXTRA_CASES = [{"id": "a500-vsync-reset3-pal", "group": "reset", "rom": "vsync", "args": ["--units", "8", "--reset-at", "3"]}]


def run_both(rom, args):
    results = {}
    for tree in ("product", "reference"):
        code, out, err = builds.probe(tree, rom, *args, "--every", "1")
        if code:
            raise AssertionError(f"{tree} probe exited {code}: {err[-400:]}")
        results[tree] = cr.parse_probe(out)
    return results["product"], results["reference"]


class Ref01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-ref01-")
        cls.cases = corpus.write_corpus(cls.directory.name) + EXTRA_CASES
        builds.product_probe()
        builds.reference_probe()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def rom(self, case):
        return Path(self.directory.name) / f"{case['rom']}.rom"

    def test_the_reference_is_built_from_the_reference_tree_and_the_product_from_the_product_tree(self):
        reference = (builds.REFERENCE_TREE / "compile_commands.json").read_text()
        self.assertIn("build/reference/upstream/newcpu.cpp", reference)
        self.assertNotIn("build/upstream/newcpu.cpp", reference)
        cache = (builds.REFERENCE_TREE / "CMakeCache.txt").read_text()
        self.assertIn("P6_UPSTREAM_TREE:STRING=reference", cache)
        product = (builds.ROOT / "build/state-opt/compile_commands.json").read_text()
        self.assertIn("build/upstream/newcpu.cpp", product)
        self.assertNotIn("build/reference/upstream", product)

    def test_every_case_of_the_corpus_matches_the_reference_except_where_a_patch_says_otherwise(self):
        differing = []
        for case in self.cases:
            with self.subTest(case=case["id"]):
                product, reference = run_both(self.rom(case), case["args"])
                report = cr.compare(product, reference)
                expected = expected_difference(case["id"])
                if expected:
                    self.assertFalse(report["equal"], f"{case['id']}: a patch is declared to change this case and nothing changed")
                    self.assertEqual(sorted(expected), sorted(report["differing_subsystems"]), cr.render(report, cr.inputs_from_probe_args(case["args"])))
                    differing.append(case["id"])
                else:
                    self.assertTrue(report["equal"], cr.render(report, cr.previous_inputs(cr.inputs_from_probe_args(case["args"]), report.get("first_divergent_boundary", 0), 8)))
        known = {case["id"] for case in self.cases}
        declared = {scenario for entry in CLASSES["ablate"].values() if entry["effect"] == "differs" for scenario in entry["scenarios"]} & known
        self.assertEqual(declared, set(differing), "every case a patch is declared to change was compared and did change")

    def test_the_cases_that_a_neutral_patch_names_are_equal(self):
        names = [scenario for entry in CLASSES["ablate"].values() if entry["effect"] == "neutral" for scenario in entry["scenarios"]]
        by_id = {case["id"]: case for case in self.cases}
        self.assertGreaterEqual(len(names), 5)
        # A neutral patch may name scenarios of the differential corpus (T32b, e.g. patch 0262): those are compared, equal unless a patch says
        # otherwise, by tests/reference/test_corpus.py. Here only the cases of this corpus run, and every name must exist in one of the two.
        unknown = sorted(set(names) - set(by_id) - {entry.id for entry in differential.ENTRIES})
        self.assertEqual([], unknown, "a neutral patch names a scenario that no corpus has")
        for name in (name for name in names if name in by_id):
            with self.subTest(case=name):
                self.assertFalse(expected_difference(name), "a neutral patch and a patch expected to differ name the same case")
                product, reference = run_both(self.rom(by_id[name]), by_id[name]["args"])
                self.assertTrue(cr.compare(product, reference)["equal"])

    def test_the_comparison_finds_a_real_divergence_at_the_boundary_and_names_the_input_that_caused_it(self):
        case = next(case for case in self.cases if case["id"] == "a500-vsync-pal")
        args = case["args"] + ["--every", "1"]
        code, clean, _ = builds.probe("product", self.rom(case), *args)
        poked_args = args + ["--poke-at", "4", "beam:10:10:20"]
        code2, poked, _ = builds.probe("product", self.rom(case), *poked_args)
        self.assertEqual((0, 0), (code, code2))
        report = cr.compare(cr.parse_probe(clean), cr.parse_probe(poked))
        self.assertFalse(report["equal"])
        self.assertEqual(5, report["first_divergent_boundary"])
        self.assertEqual(4, report["last_equal_boundary"])
        self.assertIn("chipset", report["subsystems"])
        self.assertIn("chip.beam", [item["key"] for item in report["keys"]])
        shown = cr.previous_inputs(cr.inputs_from_probe_args(poked_args), 5, 8)
        self.assertEqual([("poke-at", 4)], [(item["kind"], item["unit"]) for item in shown])
        self.assertTrue(cr.compare(cr.parse_probe(clean), cr.parse_probe(clean))["equal"])


if __name__ == "__main__":
    unittest.main()
