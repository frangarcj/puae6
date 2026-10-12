"""The differential corpus (tests/scenarios/corpus): that it has what docs/04-validation.md asks for and nothing it claims is false, that its scenario files are what the generator
makes, and REF01 over it (the product against the reference of tests/reference/patch-classes.json, boundary by boundary). DET02 and DET03 over the same entries are
tests/reference/test_corpus_det02.py and test_corpus_det03.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import compare_runs as cr  # noqa: E402
import scenario_schema as schema  # noqa: E402
from tests.reference import builds, corpus_run  # noqa: E402
from tests.scenarios.corpus import corpus  # noqa: E402

CLASSES = json.loads((ROOT / "tests/reference/patch-classes.json").read_text())
DISPLAY_PROGRAMS = ("a500-", "ecs-", "aga-")


def backed(entry, category):
    """Whether the program and the arguments of the entry do what its category says (the label alone proves nothing)."""
    args = " ".join(entry.args)
    return {
        "ocs": entry.machine == "a500", "ecs": entry.machine == "a600", "aga": entry.machine == "a1200",
        "pal": entry.standard == "pal" and "--ntsc" not in entry.args, "ntsc": entry.standard == "ntsc" and "--ntsc" in entry.args,
        "audio": entry.program.startswith(("audio-", "tone")), "dma": entry.program in ("audio-tone_all", "tone-mod", "busy", "hdfloop") or entry.extra == "swap",
        "mouse": ":2:" in args and "--port" in args, "joystick": ":1:" in args and "--port" in args or entry.program == "potgo",
        "keyboard": "--key" in entry.args, "reset": "--reset-at" in entry.args, "swap": entry.extra == "swap",
        "write": entry.extra in ("floppy-write", "hdf"), "restore": bool(entry.points),
        "ring": entry.program.startswith("ring-"), "copper": entry.program in ("copper_irq", "ring-copper", "ring-copper-ecs", "aga-bars24-pal"),
        "blitter": entry.program == "busy", "interlace": "lace" in entry.program, "irq": entry.program in ("vsync", "copper_irq", "tone-vol"),
        "cpu": entry.program in ("vsync", "halt"),
    }[category]


class CoverageTests(unittest.TestCase):
    def test_the_scenario_files_are_what_the_generator_makes(self):
        documents = corpus.documents()
        self.assertEqual(sorted(documents), sorted(p.stem for p in corpus.SCENARIO_DIR.glob("*.json") if p.stem != "index"))
        for scenario_id, document in documents.items():
            with self.subTest(scenario=scenario_id):
                self.assertEqual(document, schema.load_json(corpus.SCENARIO_DIR / f"{scenario_id}.json"))
                schema.validate_scenario(document)
        self.assertEqual(corpus.index(), schema.load_json(corpus.SCENARIO_DIR / "index.json"))

    def test_the_fixture_manifest_is_what_the_generator_makes_and_lists_no_firmware(self):
        committed = schema.load_json(ROOT / "manifests/fixtures.json")
        self.assertEqual(corpus.fixtures(), committed, "a program changed: update manifests/fixtures.json knowingly (python3 tests/scenarios/corpus/corpus.py)")
        self.assertEqual({entry.program for entry in corpus.ENTRIES}, {row["program"] for row in committed["programs"]})
        text = json.dumps(committed).lower()
        for word in ("kickstart", "workbench"):
            for row in committed["programs"]:
                self.assertNotIn(word, row["source"].lower())
        self.assertEqual(sorted(committed["opt_in_environment"]), sorted(corpus.OPT_IN))
        self.assertNotIn("sha256", json.dumps(committed["opt_in_environment"]).lower(), "the firmware that is not here is not hashed here")

    def test_identifiers_are_unique_and_every_program_exists(self):
        ids = [entry.id for entry in corpus.ENTRIES]
        self.assertEqual(len(ids), len(set(ids)))
        texts = corpus.programs()
        for entry in corpus.ENTRIES:
            self.assertIn(entry.program, texts, entry.id)
            self.assertTrue(set(entry.categories) <= set(corpus.CATEGORIES), entry.id)
            self.assertTrue(entry.extra in corpus.MEDIA or isinstance(entry.extra, (list, tuple)), entry.id)

    def test_at_least_twenty_scenarios_with_every_category_the_validation_document_asks_for(self):
        self.assertGreaterEqual(len(corpus.ENTRIES), 20)
        counts = corpus.category_counts()
        for category, least in corpus.REQUIRED.items():
            self.assertGreaterEqual(counts[category], least, f"category {category}: {counts[category]} of the {least} required")

    def test_every_category_an_entry_claims_is_backed_by_its_program_and_arguments(self):
        for entry in corpus.ENTRIES:
            for category in entry.categories:
                with self.subTest(entry=entry.id, category=category):
                    self.assertTrue(backed(entry, category), f"{entry.id} claims {category}")
        # and the other way round for the machine and the standard, which every entry states
        for entry in corpus.ENTRIES:
            self.assertEqual({"a500": "ocs", "a600": "ecs", "a1200": "aga"}[entry.machine] in entry.categories, True, entry.id)
            self.assertIn(entry.standard, entry.categories, entry.id)

    def test_the_corpus_is_not_made_of_boot_screens(self):
        # The stop condition of the card: pictures alone do not cover. Count the entries whose program is not a display generator.
        microtests = [entry for entry in corpus.ENTRIES if not entry.program.startswith(DISPLAY_PROGRAMS)]
        displays = [entry for entry in corpus.ENTRIES if entry.program.startswith(DISPLAY_PROGRAMS)]
        self.assertGreater(len(microtests), len(displays))
        for category in ("audio", "dma", "reset", "swap", "write", "keyboard", "mouse", "joystick", "ring"):
            self.assertTrue(any(category in e.categories and not e.program.startswith(DISPLAY_PROGRAMS) for e in corpus.ENTRIES), category)

    def test_the_inputs_of_the_scenario_files_are_the_ones_of_the_probe_arguments(self):
        import compare_runs
        for entry in corpus.ENTRIES:
            with self.subTest(entry=entry.id):
                document = schema.load_json(corpus.SCENARIO_DIR / f"{entry.id}.json")
                from_file = [(item["unit"], item["kind"]) for item in compare_runs.inputs_from_scenario(document) if item["kind"] in ("reset",)]
                from_args = [(item["unit"], "reset") for item in compare_runs.inputs_from_probe_args(entry.args) if item["kind"] == "reset-at"]
                self.assertEqual(from_args, from_file)
                keys = sum(1 for a in entry.args if a == "--key")
                ports = sum(1 for a in entry.args if a in ("--port", "--port-at"))
                changes = {p["unit"] for p in document["inputs"]}
                self.assertGreaterEqual(len(changes), 1)
                if keys + ports:
                    self.assertGreaterEqual(len(document["inputs"]), 1)
                self.assertEqual(entry.machine, {"A500": "a500", "A600": "a600", "A1200": "a1200"}[document["config"]["machine"]])
                self.assertEqual(entry.standard.upper(), document["config"]["video_standard"])


def expected_difference(entry_id):
    expected = set()
    for patch in CLASSES["ablate"].values():
        if patch["effect"] == "differs" and entry_id in patch["scenarios"]:
            expected |= set(patch["subsystems"])
    return expected


class Ref01CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-corpus-ref01-")
        directory = Path(cls.directory.name)
        cls.assets = corpus_run.Assets(directory)
        cls.roms = {}
        for name, text in corpus.programs().items():
            path = directory / f"{name}.rom"
            path.write_bytes(corpus.build_rom(text))
            cls.roms[name] = path
        builds.product_probe()
        builds.reference_probe()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_every_scenario_matches_the_reference_except_where_a_patch_says_otherwise(self):
        differing = []
        for entry in corpus.ENTRIES:
            with self.subTest(entry=entry.id):
                arguments = corpus_run.probe_arguments(entry, self.assets)
                runs = {}
                for tree in ("product", "reference"):
                    code, out, err = builds.probe(tree, self.roms[entry.program], *arguments, "--every", "1")
                    self.assertEqual(0, code, f"{tree}: {err[-300:]}")
                    runs[tree] = cr.parse_probe(out)
                report = cr.compare(runs["product"], runs["reference"])
                expected = expected_difference(entry.id)
                inputs = cr.inputs_from_probe_args(arguments)
                if expected:
                    self.assertFalse(report["equal"], f"{entry.id}: a patch is declared to change this scenario and nothing changed")
                    self.assertEqual(sorted(expected), sorted(report["differing_subsystems"]), cr.render(report, inputs))
                    differing.append(entry.id)
                else:
                    self.assertTrue(report["equal"], cr.render(report, cr.previous_inputs(inputs, report.get("first_divergent_boundary", 0), 8)))
        declared = {s for patch in CLASSES["ablate"].values() if patch["effect"] == "differs" for s in patch["scenarios"]} & {e.id for e in corpus.ENTRIES}
        self.assertEqual(declared, set(differing))


if __name__ == "__main__":
    unittest.main()
