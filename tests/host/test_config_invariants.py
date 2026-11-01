"""The config-invariants manifest must stay tied to real patches and be complete."""
from collections import defaultdict
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ConfigInvariantTests(unittest.TestCase):
    def test_every_invariant_names_existing_patches(self):
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())
        patch_files = list((ROOT / "patches/winuae").glob("*.patch"))
        by_number = defaultdict(list)
        for path in patch_files:
            by_number[path.name.split("-", 1)[0]].append(path)
        numbers = set(by_number)
        # beta21's imported base already contains the historical portability
        # patches. series.json lists only patches still applied on top of it,
        # while invariant provenance continues to name the preserved files.
        active = {p["id"].split("-", 1)[0] for p in series["patches"]}
        self.assertLessEqual(active, numbers)
        for patch in series["patches"]:
            with self.subTest(active_patch=patch["id"]):
                self.assertTrue((ROOT / patch["file"]).is_file())
                self.assertEqual(patch["id"].split("-", 1)[0],
                                 Path(patch["file"]).name.split("-", 1)[0])
        self.assertEqual(manifest["profile"], "deterministic-a500")
        self.assertGreaterEqual(len(manifest["invariants"]), 29)
        for item in manifest["invariants"]:
            with self.subTest(option=item["option"]):
                self.assertTrue(item["require"] and len(item["why"]) > 20)
                self.assertTrue(item["patches"])
                self.assertLessEqual(set(item["patches"]), numbers)
                for patch_id in item["patches"]:
                    self.assertEqual(len(by_number[patch_id]), 1,
                                     f"invariant patch {patch_id} must be unambiguous")

    def test_every_patch_that_needs_an_invariant_is_covered(self):
        # Patches that make an option a silent no-op must be referenced by an invariant.
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        covered = {p for item in manifest["invariants"] for p in item["patches"]}
        self.assertLessEqual({"0046", "0047", "0048", "0049", "0050", "0051", "0052", "0053", "0055", "0056", "0060"}, covered)

    def test_invariants_marked_as_held_by_the_preset_match_the_profile_file(self):
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        profile = json.loads((ROOT / "config/profiles/a500-ocs.json").read_text())
        values = {f["name"]: f["value"] for f in profile["fields"]}
        held = [item for item in manifest["invariants"] if "profile" in item]
        self.assertGreaterEqual(len(held), 9)
        for item in held:
            for name, value in item["profile"].items():
                with self.subTest(option=item["option"], field=name):
                    self.assertEqual(values[name], value)


if __name__ == "__main__":
    unittest.main()
