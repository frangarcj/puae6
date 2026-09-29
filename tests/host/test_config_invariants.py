"""The config-invariants manifest must stay tied to real patches and be complete."""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ConfigInvariantTests(unittest.TestCase):
    def test_every_invariant_names_existing_patches(self):
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())
        numbers = {p["id"].split("-", 1)[0] for p in series["patches"]}
        self.assertEqual(manifest["profile"], "deterministic-a500")
        self.assertGreaterEqual(len(manifest["invariants"]), 15)
        for item in manifest["invariants"]:
            with self.subTest(option=item["option"]):
                self.assertTrue(item["require"] and len(item["why"]) > 20)
                self.assertTrue(item["patches"])
                self.assertLessEqual(set(item["patches"]), numbers)

    def test_every_patch_that_needs_an_invariant_is_covered(self):
        # Patches that make an option a silent no-op must be referenced by an invariant.
        manifest = json.loads((ROOT / "manifests/config-invariants.json").read_text())
        covered = {p for item in manifest["invariants"] for p in item["patches"]}
        self.assertLessEqual({"0046", "0047", "0048", "0049", "0050", "0051", "0052", "0053"}, covered)


if __name__ == "__main__":
    unittest.main()
