"""T21b: manifests/state-inventory.json (key `wrapper`) classifies every instance field of the PUAE6 core, and the fields it says are saved are exactly the fields of the
state the core writes. The compiled class is asked by reflection (tests/ffi Program.cs, `inventory`), so a field that is added without a decision fails here.

    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_inventory.py"
"""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

MANIFEST = json.loads((ROOT / "manifests/state-inventory.json").read_text())["wrapper"]


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = build_waterbox.run_ffi("inventory")
        assert result.returncode == 0, result.stdout + result.stderr
        cls.fields = set(re.findall(r"^field (\S+)$", result.stdout, re.M))
        cls.saved = set(re.findall(r"^saved (\S+)$", result.stdout, re.M))

    def test_every_field_of_the_core_has_a_decision_and_every_decision_a_field(self):
        self.assertEqual(self.fields, set(MANIFEST["fields"]), "a field was added or removed without updating the inventory")

    def test_every_decision_is_one_of_the_policies_and_a_saved_field_names_its_proof_and_its_state(self):
        for name, entry in MANIFEST["fields"].items():
            with self.subTest(field=name):
                self.assertIn(entry["policy"], MANIFEST["policy_values"])
                self.assertTrue(entry["what"])
                if entry["policy"] == "saved":
                    self.assertTrue(entry["proof"] and entry["state"], "a saved field says which test proves it and which field of the state it is")

    def test_the_fields_that_are_saved_are_exactly_the_fields_of_the_state(self):
        declared = [s for entry in MANIFEST["fields"].values() if entry["policy"] == "saved" for s in entry["state"]]
        self.assertEqual(sorted(declared), sorted(set(declared)), "a field of the state belongs to one core field")
        self.assertEqual(self.saved, set(declared))


if __name__ == "__main__":
    unittest.main()
