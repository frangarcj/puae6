"""Contrato offline del snapshot fijado de WinUAE."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "tools"))
import import_sources


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ImportSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p6-source-")
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.repo = Path(self.temp.name) / "fixture.git"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / ".gitattributes").write_text("ignored.txt export-ignore\n")
        (self.repo / "ignored.txt").write_bytes(b"must be imported\n")
        (self.repo / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
        (self.repo / "run.sh").chmod(0o755)
        (self.repo / "data.bin").write_bytes(b"\x00\xfffixture\n")
        try:
            (self.repo / "link").symlink_to("data.bin")
        except (OSError, NotImplementedError) as error:
            self.temp.cleanup()
            self.skipTest(f"No se puede crear symlink en este entorno: {error}")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "commit", "-qm", "fixture")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "tag", "-am", "6030 fixture", "6030")
        self.tag_object = git(self.repo, "rev-parse", "6030")
        plan = self.root / "plan"
        plan.mkdir()
        (plan / "upstream.lock.json").write_text(json.dumps({"sources": [{
            "id": "winuae", "repository": str(self.repo), "commit": self.commit,
            "tag": "6030", "tag_object": self.tag_object,
        }]}))
        import_sources.fetch_source(self.root)

    def make_reference(self, *, local_path=True, source_id="reference", overlay=True):
        """Create a selected local/public source with one authorized dirty file."""
        repo = Path(self.temp.name) / (source_id + "-repo")
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "public").mkdir()
        (repo / "public/engine.txt").write_text("pinned source\n")
        (repo / "public/config.ini").write_text("upstream config\n")
        (repo / "public/private").mkdir()
        (repo / "public/private/secret").symlink_to("../../outside-secret")
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "commit", "-qm", "reference")
        commit = git(repo, "rev-parse", "HEAD")
        snapshot = {
            "destination": f"references/{source_id}",
            "manifest": f"references/{source_id}.manifest.json",
            "include": ["public"], "exclude": ["public/private"],
            "overlay_sha256": {},
        }
        if overlay:
            changed = b"authorized local config\n"
            (repo / "public/config.ini").write_bytes(changed)
            import hashlib
            snapshot["overlay_sha256"]["public/config.ini"] = hashlib.sha256(changed).hexdigest()
        source = {"id": source_id, "repository": str(repo), "commit": commit,
                  "snapshot": snapshot}
        if local_path:
            source["local_path"] = str(repo)
        lock_path = self.root / "plan/upstream.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["sources"].append(source)
        lock_path.write_text(json.dumps(lock))
        return repo, source

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_import_is_faithful_and_repeatable(self):
        result = import_sources.import_source(self.root)
        manifest = (self.root / "manifests/upstream-files.json").read_bytes()
        destination = self.root / "upstream/winuae"
        self.assertEqual(result, import_sources.verify_source(self.root))
        self.assertEqual(import_sources.import_source(self.root), result)
        self.assertEqual((self.root / "manifests/upstream-files.json").read_bytes(), manifest)
        self.assertEqual((destination / "data.bin").read_bytes(), b"\x00\xfffixture\n")
        self.assertEqual((destination / "ignored.txt").read_bytes(), b"must be imported\n")
        self.assertEqual((destination / "run.sh").stat().st_mode & 0o777, 0o755)
        self.assertTrue((destination / "link").is_symlink())
        self.assertEqual(os.readlink(destination / "link"), "data.bin")

    def test_verify_detects_content_presence_mode_and_link_changes(self):
        import_sources.import_source(self.root)
        tree = self.root / "upstream/winuae"
        pristine = Path(self.temp.name) / "pristine"
        shutil.copytree(tree, pristine, symlinks=True)
        cases = [
            ("data.bin", lambda p: p.write_bytes(b"changed")),
            ("data.bin", lambda p: p.unlink()),
            ("extra", lambda p: p.write_text("extra")),
            ("run.sh", lambda p: p.chmod(0o644)),
            ("link", lambda p: (p.unlink(), p.write_text("not a link"))),
        ]
        for name, mutate in cases:
            with self.subTest(change=name):
                # Restore a pristine tree before testing the next independent mutation.
                path = tree / name
                mutate(path)
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                    import_sources.verify_source(self.root)
                shutil.rmtree(tree)
                shutil.copytree(pristine, tree, symlinks=True)

    def test_import_refuses_to_overwrite_existing_work(self):
        import_sources.import_source(self.root)
        target = self.root / "upstream/winuae/data.bin"
        target.write_bytes(b"local work")
        with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
            import_sources.import_source(self.root)
        self.assertEqual(target.read_bytes(), b"local work")

    def test_import_rejects_symlink_in_destination_ancestry(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / "upstream").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            import_sources.import_source(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_verify_rejects_symlinked_snapshot_manifest_and_manifest_parent(self):
        import_sources.import_source(self.root)
        destination, manifest = self.root / "upstream/winuae", self.root / "manifests/upstream-files.json"
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        shutil.copytree(destination, outside / "winuae", symlinks=True)
        project_upstream = Path(self.temp.name) / "project-upstream"
        shutil.move(self.root / "upstream", project_upstream)
        (self.root / "upstream").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            import_sources.verify_source(self.root)
        (self.root / "upstream").unlink()
        shutil.move(project_upstream, self.root / "upstream")
        shutil.rmtree(destination)
        destination.symlink_to(outside / "winuae", target_is_directory=True)
        with self.assertRaises(ValueError):
            import_sources.verify_source(self.root)
        destination.unlink()
        shutil.copytree(outside / "winuae", destination, symlinks=True)
        shutil.copy2(manifest, outside / "manifest.json")
        manifest.unlink()
        manifest.symlink_to(outside / "manifest.json")
        with self.assertRaises(ValueError):
            import_sources.verify_source(self.root)
        manifest.unlink()
        project_manifests = Path(self.temp.name) / "project-manifests"
        shutil.move(self.root / "manifests", project_manifests)
        (self.root / "manifests").symlink_to(outside, target_is_directory=True)
        shutil.copy2(outside / "manifest.json", outside / "upstream-files.json")
        with self.assertRaises(ValueError):
            import_sources.verify_source(self.root)

    def test_fetch_rejects_symlinked_cache(self):
        cache = self.root / "build/source-cache/winuae.git"
        shutil.rmtree(cache)
        cache.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            import_sources.fetch_source(self.root)

    def test_import_recovers_missing_manifest_only_for_matching_tree(self):
        import_sources.import_source(self.root)
        manifest = self.root / "manifests/upstream-files.json"
        expected = manifest.read_bytes()
        manifest.unlink()
        import_sources.import_source(self.root)
        self.assertEqual(manifest.read_bytes(), expected)
        manifest.unlink()
        file = self.root / "upstream/winuae/data.bin"
        file.write_bytes(b"preserve local edit")
        with self.assertRaises(ValueError):
            import_sources.import_source(self.root)
        self.assertEqual(file.read_bytes(), b"preserve local edit")
        self.assertFalse(manifest.exists())

    def test_gitlink_is_rejected_instead_of_silently_omitted(self):
        child = Path(self.temp.name) / "child"
        child.mkdir()
        git(child, "init", "-q")
        (child / "file").write_text("submodule")
        git(child, "add", "file")
        git(child, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "commit", "-qm", "child")
        child_commit = git(child, "rev-parse", "HEAD")
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000", child_commit,
            "submodule")
        git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "commit", "-qm", "gitlink")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid",
            "tag", "-fam", "6030 gitlink", "6030")
        self.tag_object = git(self.repo, "rev-parse", "6030")
        lock = self.root / "plan/upstream.lock.json"
        item = json.loads(lock.read_text())
        item["sources"][0].update(commit=self.commit, tag_object=self.tag_object)
        lock.write_text(json.dumps(item))
        cache = self.root / "build/source-cache/winuae.git"
        shutil.rmtree(cache)
        import_sources.fetch_source(self.root)
        with self.assertRaisesRegex(ValueError, "Entrada no soportada"):
            import_sources.import_source(self.root)

    def test_wrong_commit_or_annotated_tag_pin_fails(self):
        lock = self.root / "plan/upstream.lock.json"
        original = json.loads(lock.read_text())
        for key in ("commit", "tag_object"):
            with self.subTest(pin=key):
                changed = json.loads(json.dumps(original))
                changed["sources"][0][key] = "0" * 40
                lock.write_text(json.dumps(changed))
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                    import_sources.fetch_source(self.root)
        lock.write_text(json.dumps(original))

    def test_local_reference_is_read_only_selected_and_offline_verifiable(self):
        repo, _ = self.make_reference()
        import_sources.fetch_source(self.root, "reference")
        self.assertFalse((self.root / "build/source-cache/reference.git").exists())
        import_sources.import_source(self.root, source_id="reference")
        tree = self.root / "references/reference"
        self.assertEqual((tree / "public/config.ini").read_bytes(), b"authorized local config\n")
        self.assertFalse((tree / "public/private").exists())
        # Verification is based on the selected snapshot/manifest, not the original repo.
        moved = repo.with_name("reference-repo-away")
        repo.rename(moved)
        self.assertEqual(import_sources.verify_source(self.root, "reference")["destination"],
                         "references/reference")
        moved.rename(repo)
        # A subsequent source change is not covered by the pinned overlay hash.
        (repo / "public/config.ini").write_text("unauthorized change\n")
        with self.assertRaisesRegex(ValueError, "Cambio local no fijado"):
            import_sources.import_source(self.root, source_id="reference")
        self.assertEqual((tree / "public/config.ini").read_bytes(), b"authorized local config\n")
        (tree / "public/engine.txt").write_text("tampered snapshot\n")
        with self.assertRaisesRegex(ValueError, "SRC01"):
            import_sources.verify_source(self.root, "reference")

    def test_reference_selector_and_lock_mutations_invalidate_manifest(self):
        self.make_reference()
        import_sources.import_source(self.root, source_id="reference")
        lock_path = self.root / "plan/upstream.lock.json"
        original = json.loads(lock_path.read_text())
        for change in ("selector", "commit"):
            with self.subTest(change=change):
                changed = json.loads(json.dumps(original))
                item = changed["sources"][1]
                if change == "selector":
                    item["snapshot"]["exclude"] = []
                else:
                    item["commit"] = "0" * 40
                lock_path.write_text(json.dumps(changed))
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                    import_sources.verify_source(self.root, "reference")
        lock_path.write_text(json.dumps(original))

    def test_reference_include_must_exist(self):
        self.make_reference()
        lock_path = self.root / "plan/upstream.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["sources"][1]["snapshot"]["include"] = ["missing"]
        lock_path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "Seleccion vacia o inexistente"):
            import_sources.import_source(self.root, source_id="reference")

    def test_public_reference_without_tag_imports_by_commit(self):
        _, source = self.make_reference(local_path=False, source_id="public", overlay=False)
        import_sources.fetch_source(self.root, "public")
        result = import_sources.import_source(self.root, source_id="public")
        self.assertEqual(result["source"]["commit"], source["commit"])
        self.assertNotIn("tag", result["source"])
        self.assertTrue((self.root / "references/public/public/engine.txt").is_file())

    def test_license_catalog_checks_fixture_notice_hash_source_and_unique_ids(self):
        files = import_sources.import_source(self.root)["files"]
        notice = next(file for file in files if file["path"] == "data.bin")
        component = {"id": "fixture-only", "source_id": "winuae",
                     "notice_files": [{"path": "upstream/winuae/data.bin",
                                       "sha256": notice["sha256"]}]}
        catalog = self.root / "manifests/licenses.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        write = lambda components: catalog.write_text(json.dumps(
            {"schema_version": 1, "components": components}))
        write([component])
        import_sources.verify_licenses(self.root, ["winuae"])
        for invalid in (
            [{**component, "notice_files": [{**component["notice_files"][0], "sha256": "0" * 64}]}],
            [{**component, "source_id": "missing-source"}],
            [component, component],
        ):
            with self.subTest(invalid=invalid):
                write(invalid)
                with self.assertRaises(ValueError):
                    import_sources.verify_licenses(self.root, ["winuae"])


if __name__ == "__main__":
    unittest.main()
