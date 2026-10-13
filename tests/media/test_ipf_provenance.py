"""SRC01-style checks for the IPF decoder imported from MAME (T58a): what is in third_party/mame-ipf is what PROVENANCE.json, the license entry and the ADR say it is.

No network: the originals are kept in the tree, so the test applies the local changes backwards and compares the result with the recorded hashes of the files as MAME has them.
Run: python3 -m unittest tests.media.test_ipf_provenance
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "third_party/mame-ipf"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prov = json.loads((DIR / "PROVENANCE.json").read_text())

    def test_the_pin_is_a_full_commit_and_not_a_branch(self):
        self.assertRegex(self.prov["source"]["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(self.prov["source"]["repository"], "https://github.com/mamedev/mame")
        self.assertIn(self.prov["source"]["commit"], self.prov["source"]["files_downloaded_from"])

    def test_the_originals_are_the_recorded_files(self):
        for item in self.prov["originals"]:
            self.assertEqual(sha(DIR / item["path"]), item["sha256"], item["path"])

    def test_the_compiled_files_are_the_originals_with_the_local_changes(self):
        self.assertEqual(sha(DIR / self.prov["local_changes"]["path"]), self.prov["local_changes"]["sha256"])
        with tempfile.TemporaryDirectory(prefix="p6-ipf-prov-") as tmp:
            for item in self.prov["compiled"]:
                self.assertEqual(sha(DIR / item["path"]), item["sha256"], item["path"])
                shutil.copy(DIR / item["path"], tmp)
            subprocess.run(["patch", "-R", "-p1", "-s", "-i", str(DIR / "local-changes.diff")], cwd=tmp, check=True, capture_output=True)
            for item in self.prov["originals"]:
                self.assertEqual(sha(Path(tmp) / Path(item["path"]).name), item["sha256"], f"{item['path']} comes back from the compiled file")

    def test_the_license_is_declared_in_the_files_and_the_text_is_the_recorded_one(self):
        for item in self.prov["compiled"] + self.prov["originals"]:
            head = (DIR / item["path"]).read_text().splitlines()[:2]
            self.assertEqual(head, ["// license:BSD-3-Clause", "// copyright-holders:Olivier Galibert"], item["path"])
        self.assertEqual(sha(DIR / self.prov["license"]["text_file"]), self.prov["license"]["text_sha256"])
        text = (DIR / "LICENSE-BSD-3-Clause.txt").read_text()
        for clause in ("Redistributions of source code must retain", "Redistributions in binary form must reproduce", "Neither the name of the copyright holder", "THE COPYRIGHT HOLDERS AND CONTRIBUTORS"):
            self.assertIn(clause, text)
        notice = (DIR / "NOTICE.md").read_text()
        self.assertIn("Olivier Galibert", notice)
        self.assertIn(self.prov["source"]["commit"], notice)

    def test_nothing_else_of_mame_is_in_the_tree_and_the_includes_are_the_ones_replaced(self):
        names = sorted(p.name for p in DIR.iterdir())
        self.assertEqual(names, sorted(["original", "LICENSE-BSD-3-Clause.txt", "NOTICE.md", "PROVENANCE.json", "flopimg.h", "ipf_dsk.cpp", "ipf_dsk.h", "local-changes.diff", "multibyte.h"]))
        self.assertEqual(sorted(p.name for p in (DIR / "original").iterdir()), ["ipf_dsk.cpp", "ipf_dsk.h"])
        includes = set()
        for name in ("ipf_dsk.cpp", "ipf_dsk.h"):
            includes |= {line.split('"')[1] for line in (DIR / name).read_text().splitlines() if line.startswith('#include "')}
        self.assertEqual(includes, {"ipf_dsk.h", "multibyte.h", "flopimg.h"}, "the includes of the decoder are its own header and the two stand-ins")
        for name in ("flopimg.h", "multibyte.h"):
            self.assertIn("contains no MAME code", (DIR / name).read_text())

    def test_capsimg_is_not_used(self):
        for path in list(DIR.glob("*.cpp")) + list(DIR.glob("*.h")) + [ROOT / "ports/portable/media/ipf_track.cpp", ROOT / "ports/portable/media/ipf_track.h"]:
            text = path.read_text().lower()
            for word in ("capsinit", "capslockimage", "capsimg.dll", "libcapsimage", "dlopen"):
                self.assertNotIn(word, text, f"{path.name} names {word}")

    def test_the_license_inventory_points_at_the_files(self):
        licenses = json.loads((ROOT / "manifests/licenses.json").read_text())
        entry = next(c for c in licenses["components"] if c["id"] == "mame-ipf")
        self.assertIn("BSD-3-Clause", entry["declared_license"])
        self.assertIn(self.prov["source"]["commit"], entry["provenance"])
        for notice in entry["notice_files"]:
            self.assertEqual(sha(ROOT / notice["path"]), notice["sha256"], notice["path"])
        capsimg = next(c for c in licenses["components"] if c["id"] == "capsimg-reference")
        self.assertEqual(capsimg["build_eligibility"], "excluded_initial_profile", "capsimg stays excluded")
        self.assertTrue((ROOT / "docs/adr/0013-ipf-decoder-licence.md").is_file())


if __name__ == "__main__":
    unittest.main()
