"""T30a, SRC01: the licence catalogue says what every captured WHDLoad file and every input of the preparation is, and nothing commercial or owned by the user is in the repository.

The captured files are references/machawk-uae/whdload (the boot image and the files the old core ships); several are AmigaOS commands of Commodore and are not free to redistribute, so each file
is classified by manifests/licenses.json exactly once and pinned by the hashes of the snapshot manifest. The inputs of the preparation (WHDLoad's package, a game's install, the user's Kickstart)
are `user_supplied_inputs`: their evidence is quoted and hashed, and none of them is ever stored here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import unittest

from tests.whdload import lha_fixture      # noqa: F401  (puts tools/ on sys.path)
from whdload import lha

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "manifests/licenses.json").read_text(encoding="utf-8"))
SNAPSHOT = {f["path"]: f["sha256"] for f in json.loads((ROOT / "references/machawk-uae.manifest.json").read_text(encoding="utf-8"))["files"] if f["path"].startswith("whdload/")}
CAPTURED = [c for c in CATALOG["components"] if c["id"].startswith("machawk-whdload-")]


class CapturedFilesTests(unittest.TestCase):
    def test_every_captured_file_is_classified_exactly_once_and_pinned_by_hash(self):
        seen = {}
        for component in CAPTURED:
            pin = component["captured_files"]
            self.assertEqual(sorted(pin["paths"]), pin["paths"], component["id"])
            for path in pin["paths"]:
                self.assertNotIn(path, seen, "%s is in two components" % path)
                self.assertIn(path, SNAPSHOT, "%s is not in the snapshot" % path)
                seen[path] = component["id"]
            digest = hashlib.sha256("".join("%s\0%s\n" % (p, SNAPSHOT[p]) for p in pin["paths"]).encode()).hexdigest()
            self.assertEqual(digest, pin["set_sha256"], component["id"])
        self.assertEqual(set(SNAPSHOT), set(seen), "files of the snapshot that no component classifies")
        self.assertEqual(6, len(CAPTURED))

    def test_no_commodore_or_unaudited_file_is_eligible_for_an_image(self):
        for component in CAPTURED:
            self.assertRegex(component["build_eligibility"], r"^(excluded|reference_only)", component["id"])
        commodore = next(c for c in CAPTURED if c["id"] == "machawk-whdload-commodore-os")
        self.assertIn("whdload/WHDLoad_files/C/Copy", commodore["captured_files"]["paths"])
        self.assertIn("Proprietary", commodore["declared_license"])
        self.assertIn("whdload/WHDLoad_files/C/Execute", commodore["captured_files"]["paths"])
        self.assertIn("not_redistributable", commodore["build_eligibility"])

    def test_the_components_name_a_source_the_lock_knows_and_leave_the_notice_check_alone(self):
        lock = json.loads((ROOT / "plan/upstream.lock.json").read_text(encoding="utf-8"))
        known = {s["id"] for s in lock["sources"]}
        for component in CAPTURED:
            self.assertIn(component["source_id"], known)
            self.assertEqual([], component["notice_files"])         # tools/import_sources.py verifies notice files against the snapshot; these entries pin captured_files instead


class UserSuppliedInputTests(unittest.TestCase):
    inputs = {entry["id"]: entry for entry in CATALOG["user_supplied_inputs"]}

    def test_every_entry_has_its_evidence_and_a_redistribution_verdict(self):
        self.assertEqual({"whdload", "fatal-mission", "user-kickstart", "generated"}, set(self.inputs))
        for entry in self.inputs.values():
            for key in ("name", "author", "origin", "license_evidence", "redistribution", "handling"):
                self.assertTrue(entry[key], (entry["id"], key))
            self.assertIn(entry["redistribution"], ("yes", "no", "unverified"))
            for digest in entry["evidence_sha256"].values():
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual("no", self.inputs["user-kickstart"]["redistribution"])
        self.assertEqual("yes", self.inputs["generated"]["redistribution"])
        for name in ("whdload", "fatal-mission"):
            self.assertEqual("unverified", self.inputs[name]["redistribution"], "a free download is not a redistribution grant until someone reads one")

    def test_the_kickstart_is_never_hashed_or_pinned_by_anything(self):
        entry = self.inputs["user-kickstart"]
        self.assertEqual({}, entry["evidence_sha256"])
        self.assertIsNone(entry["pinned_member"])
        self.assertIn("never recorded or hashed", entry["handling"])

    @unittest.skipUnless(os.environ.get("P6_WHDLOAD_LHA"), "BLOCKED: set P6_WHDLOAD_LHA to check the quoted WHDLoad licence text against the package")
    def test_the_quoted_whdload_evidence_is_the_text_of_the_package(self):
        files = {e.name: e.data for e in lha.read_archive(Path(os.environ["P6_WHDLOAD_LHA"]).read_bytes()) if not e.is_dir}
        for member, digest in self.inputs["whdload"]["evidence_sha256"].items():
            self.assertEqual(digest, hashlib.sha256(files[member]).hexdigest(), member)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", files["WHDLoad/Docs/en/copy.html"].decode("latin-1")))
        self.assertIn("Commercial use is only allowed with an explicit, written permission by the author", text)


class RepositoryContentTests(unittest.TestCase):
    def test_no_rom_archive_or_disk_image_is_in_the_places_this_task_writes(self):
        for folder in ("config/whdload", "tests/whdload", "tools", "manifests", "docs"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    self.assertNotIn(path.suffix.lower(), (".rom", ".lha", ".lzx", ".hdf", ".adf", ".dms", ".slave", ".key", ".rtb"), str(path))

    def test_the_licence_ids_a_config_uses_are_catalogued_and_user_files_are_declared_as_assets(self):
        for path in sorted((ROOT / "config/whdload").glob("*.json")):
            config = json.loads(path.read_text(encoding="utf-8"))
            for asset in config["user_assets"]:
                if "kick" in asset["to"].lower():
                    self.assertEqual("user-kickstart", asset["license"])
                self.assertNotIn("sha256", asset, "a user's file is checked by size only: its hash is not recorded")


if __name__ == "__main__":
    unittest.main()
