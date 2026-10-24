"""LIFE01: manifests/state-inventory.json stays true to the code it describes.

The inventory says what a load puts back, what survives an unload and why that is harmless. It is only useful while each
claim can be checked: every entry has the same fields, the files and patches it cites exist, and every symbol it names is
still somewhere in the engine, the bridge or the portable layer.
"""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "manifests/state-inventory.json"
STATUSES = {"reset", "rebuilt", "noncausal", "open"}
FIELDS = {"id", "subsystem", "symbols", "owner", "at_load", "survives_unload", "status", "evidence"}


def source_text():
    parts = []
    for base, suffixes in ((ROOT / "build/upstream", {".cpp", ".h", ".c"}), (ROOT / "bridge", {".cpp", ".h"}),
                           (ROOT / "ports/portable", {".cpp", ".h", ".c"}), (ROOT / "adapters/waterbox", {".cpp", ".h"})):
        if base.is_dir():
            parts += [path.read_text(encoding="utf-8", errors="replace") for path in base.rglob("*")
                      if path.suffix in suffixes and path.is_file()]
    return "\n".join(parts)


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.entries = cls.data["entries"]

    def test_every_entry_has_the_same_fields_and_a_known_status(self):
        self.assertEqual(self.data["schema_version"], 1)
        self.assertEqual(set(self.data["status_values"]), STATUSES)
        ids = [entry["id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        for entry in self.entries:
            with self.subTest(entry=entry.get("id")):
                self.assertEqual(set(entry), FIELDS)
                self.assertIn(entry["status"], STATUSES)
                self.assertIsInstance(entry["survives_unload"], bool)
                self.assertIsInstance(entry["symbols"], list)
                self.assertIsInstance(entry["evidence"], list)
                for field in ("subsystem", "owner", "at_load"):
                    self.assertTrue(entry[field].strip(), field)

    def test_what_is_reset_does_not_survive_and_what_survives_says_why(self):
        for entry in self.entries:
            with self.subTest(entry=entry["id"]):
                if entry["status"] == "reset":
                    self.assertFalse(entry["survives_unload"])
                    self.assertTrue(entry["evidence"], "a reset needs the test or patch that proves it")
                else:
                    self.assertTrue(entry["survives_unload"])
                if entry["status"] == "open":
                    self.assertTrue(entry["evidence"], "an open difference needs the test that shows it")

    def test_every_cited_file_and_patch_exists(self):
        series = json.loads((ROOT / "patches/winuae/series.json").read_text(encoding="utf-8"))
        registered = {patch["file"] for patch in series["patches"]}
        for entry in self.entries:
            for path in entry["evidence"]:
                with self.subTest(entry=entry["id"], path=path):
                    self.assertTrue((ROOT / path).exists(), path)
                    if path.startswith("patches/winuae/") and path.endswith(".patch"):
                        self.assertIn(path, registered, "a patch that is not in series.json")

    def test_every_symbol_is_still_in_the_code(self):
        text = source_text()
        if "p6_malloc_array" not in text and not (ROOT / "build/upstream").is_dir():
            self.skipTest("engine sources are not materialized")
        for entry in self.entries:
            for symbol in entry["symbols"]:
                with self.subTest(entry=entry["id"], symbol=symbol):
                    self.assertIsNotNone(re.search(r"\b%s\b" % re.escape(symbol), text), f"missing inventory symbol: {symbol}")


if __name__ == "__main__":
    unittest.main()
