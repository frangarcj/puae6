"""Link-closure gate for the A500 engine objects; it does not run an emulator.

All engine objects and the portable library are linked into a throwaway dylib with
undefined symbols reported as errors. Every symbol left over must belong to exactly
one family of manifests/link-imports.json (owner task + disposition), and every
listed name must still be unresolved. A new upstream dependency therefore fails here
instead of surfacing at the first real link, and fixed ones must leave the list.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/link-audit"
DISPOSITIONS = {"select", "state_gate", "guard_callers", "provide", "decide"}


def base(symbol):
    """Function name without arguments; C data symbols lose the Mach-O underscore."""
    name = re.sub(r"\(.*", "", symbol)
    return name[1:] if "(" not in symbol and name.startswith("_") else name


@unittest.skipUnless(sys.platform == "darwin" and all(shutil.which(t) for t in
                     ("cmake", "ninja", "clang", "clang++")),
                     "Mach-O link probe; the ELF variant (--no-undefined) is not written yet")
class EngineImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["cmake", "-S", str(ROOT), "-B", str(BUILD), "-G", "Ninja",
                        "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                        "-DCMAKE_BUILD_TYPE=Debug", "-DP6_SANITIZERS=OFF"],
                       check=True, capture_output=True, text=True, timeout=120)
        subprocess.run(["cmake", "--build", str(BUILD), "--target", "p6_engine", "p6_portable"],
                       check=True, capture_output=True, text=True, timeout=900)
        objects = sorted((BUILD / "CMakeFiles/p6_engine.dir").rglob("*.o"))
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        cls.expected_objects = len(selection["upstream_units"]) + len(selection["generated_units"])
        cls.objects = objects
        with tempfile.TemporaryDirectory(prefix="p6-link-audit-") as directory:
            link = subprocess.run(
                ["clang++", "-dynamiclib", "-Wl,-undefined,error", "-o",
                 str(Path(directory) / "probe.dylib"), *map(str, objects),
                 str(BUILD / "libp6_portable.a")], capture_output=True, text=True, timeout=300)
        cls.link_output = link.stderr
        cls.link_returncode = link.returncode
        cls.unresolved = re.findall(r'^\s+"([^"]+)", referenced from:', link.stderr, re.M)
        cls.manifest = json.loads((ROOT / "manifests/link-imports.json").read_text())

    def test_link_probe_ran_over_every_selected_object(self):
        self.assertEqual(len(self.objects), self.expected_objects)
        self.assertEqual(len(self.unresolved), len(set(self.unresolved)))
        # Two definitions of one symbol are never acceptable, listed or not.
        self.assertNotIn("duplicate symbol", self.link_output)
        if self.unresolved:
            # The probe fails only because of the listed undefined symbols.
            self.assertIn("Undefined symbols for architecture", self.link_output)
        else:
            # Nothing unresolved: the engine and the portable layer link completely.
            self.assertEqual(self.link_returncode, 0, self.link_output)
            self.assertEqual(self.manifest["families"], [])

    def test_manifest_shape_and_owners(self):
        tasks = {t["id"] for t in json.loads((ROOT / "plan/tasks.json").read_text())["tasks"]}
        ids = [f["id"] for f in self.manifest["families"]]
        self.assertEqual(len(ids), len(set(ids)))
        for family in self.manifest["families"]:
            with self.subTest(family=family["id"]):
                self.assertIn(family["owner"], tasks)
                self.assertIn(family["disposition"], DISPOSITIONS)
                self.assertGreater(len(family["reason"]), 30)
                self.assertTrue(family.get("regex") or family.get("names"))
                if "regex" in family:
                    re.compile(family["regex"])
                    self.assertGreater(family["count"], 0)

    def test_every_unresolved_symbol_is_classified_exactly_once(self):
        owners = {}
        unclassified, ambiguous = [], []
        for symbol in self.unresolved:
            name = base(symbol)
            hits = [f["id"] for f in self.manifest["families"]
                    if name in f.get("names", ()) or
                    ("regex" in f and re.search(f["regex"], name))]
            owners[symbol] = hits
            if not hits:
                unclassified.append(symbol)
            elif len(hits) > 1:
                ambiguous.append((symbol, hits))
        self.assertEqual(unclassified, [], "new unresolved symbols: classify or remove the caller")
        self.assertEqual(ambiguous, [])

    def test_listed_names_and_counts_are_still_current(self):
        seen = {base(s) for s in self.unresolved}
        stale = [(f["id"], n) for f in self.manifest["families"]
                 for n in f.get("names", ()) if n not in seen]
        self.assertEqual(stale, [], "resolved symbols must be removed from the manifest")
        for family in self.manifest["families"]:
            if "regex" in family:
                with self.subTest(family=family["id"]):
                    found = sum(1 for s in self.unresolved if re.search(family["regex"], base(s)))
                    self.assertEqual(found, family["count"])


if __name__ == "__main__":
    unittest.main()
