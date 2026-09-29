"""Offline tests for exact, non-overwriting upstream materialization."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources
import materialize_upstream as materializer

COMMIT = "857f48fcb09399d4d95527e29a2fdeaedb41f827"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p6-materialize-")
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.source = self.root / "upstream/winuae"
        self.source.mkdir(parents=True)
        self.base = b"head\nold\nfoot\n"
        (self.source / "sample.cpp").write_bytes(self.base)
        (self.root / "patches/winuae").mkdir(parents=True)
        self.patch_bytes = (
            b"diff --git a/sample.cpp b/sample.cpp\n"
            b"--- a/sample.cpp\n+++ b/sample.cpp\n"
            b"@@ -1,3 +1,3 @@\n head\n-old\n+new\n foot\n"
        )
        self.patch_path = self.root / "patches/winuae/0001-sample.patch"
        self.patch_path.write_bytes(self.patch_bytes)
        self.after = b"head\nnew\nfoot\n"
        self.write_series()
        self.manifest = {
            "destination": "upstream/winuae",
            "source": {"commit": COMMIT},
            "files": import_sources.inventory(self.source),
        }
        self.verify = patch.object(materializer.import_sources, "verify_source",
                                   return_value=self.manifest)
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def tearDown(self):
        self.temp.cleanup()

    def write_series(self, *, patch_bytes=None, before=None, after=None, file="0001-sample.patch"):
        data = self.patch_bytes if patch_bytes is None and hasattr(self, "patch_bytes") else patch_bytes
        if data is None:
            data = self.patch_bytes
        self.patch_path.write_bytes(data)
        old = self.base if before is None and hasattr(self, "base") else before
        new = self.after if after is None and hasattr(self, "after") else after
        series = {
            "schema_version": 1,
            "upstream_commit": COMMIT,
            "patches": [{
                "id": "frame-sample",
                "file": f"patches/winuae/{file}",
                "sha256": digest(data),
                "files": [{"path": "sample.cpp", "before_sha256": digest(old),
                           "after_sha256": digest(new)}],
            }],
        }
        path = self.root / "patches/winuae/series.json"
        path.write_text(json.dumps(series, indent=2) + "\n")
        return path

    def run_materializer(self, destination="build/upstream"):
        return materializer.materialize(self.root, destination)

    def test_materializes_exact_patch_and_repeat_is_idempotent(self):
        pristine_before = import_sources.inventory(self.source)
        receipt, output = self.run_materializer()
        self.assertEqual((output / "sample.cpp").read_bytes(), self.after)
        self.assertEqual(receipt["upstream_commit"], COMMIT)
        self.assertEqual(receipt["files"], import_sources.inventory(output))
        self.assertEqual(receipt["series_sha256"], digest((self.root / "patches/winuae/series.json").read_bytes()))
        self.assertEqual(pristine_before, import_sources.inventory(self.source))
        receipt2, output2 = self.run_materializer()
        self.assertEqual(output2, output)
        self.assertEqual(receipt2, receipt)
        self.assertFalse((output / "receipt.json").exists())

    def test_rejects_git_apply_offset_before_publication(self):
        self.base = b"".join(f"line{i}\n".encode() for i in range(20))
        (self.source / "sample.cpp").write_bytes(self.base)
        self.patch_bytes = (
            b"diff --git a/sample.cpp b/sample.cpp\n"
            b"--- a/sample.cpp\n+++ b/sample.cpp\n"
            b"@@ -2,4 +2,4 @@\n line8\n line9\n-line10\n+LINE10\n line11\n"
        )
        self.after = self.base.replace(b"line10\n", b"LINE10\n")
        self.write_series()
        self.manifest["files"] = import_sources.inventory(self.source)
        with self.assertRaisesRegex(ValueError, "offset"):
            self.run_materializer()
        self.assertFalse((self.root / "build/upstream").exists())
        self.assertEqual((self.source / "sample.cpp").read_bytes(), self.base)

    def test_rejects_bad_patch_pre_and_post_hashes(self):
        self.write_series(before=b"wrong\n")
        with self.assertRaisesRegex(ValueError, "Hash previo"):
            self.run_materializer()
        self.write_series(after=b"wrong\n")
        with self.assertRaisesRegex(ValueError, "Hash posterior"):
            self.run_materializer()
        self.assertFalse((self.root / "build/upstream").exists())

    def test_rejects_patch_hash_and_unlisted_touched_file(self):
        series_path = self.root / "patches/winuae/series.json"
        series = json.loads(series_path.read_text())
        series["patches"][0]["sha256"] = "0" * 64
        series_path.write_text(json.dumps(series))
        with self.assertRaisesRegex(ValueError, "Hash de parche"):
            self.run_materializer()

        changed = self.patch_bytes + (
            b"diff --git a/other.cpp b/other.cpp\n"
            b"--- a/other.cpp\n+++ b/other.cpp\n"
            b"@@ -1 +1 @@\n-old\n+new\n"
        )
        self.write_series(patch_bytes=changed)
        with self.assertRaisesRegex(ValueError, "Archivos tocados"):
            self.run_materializer()

    def test_rejects_unsafe_patch_paths_and_destination(self):
        unsafe = self.patch_bytes.replace(b"a/sample.cpp b/sample.cpp", b"a/../x b/../x")
        self.write_series(patch_bytes=unsafe)
        with self.assertRaisesRegex(ValueError, "Ruta relativa insegura"):
            self.run_materializer()
        self.write_series()
        with self.assertRaisesRegex(ValueError, "dentro de build"):
            self.run_materializer("outside/result")
        with self.assertRaisesRegex(ValueError, "relativa insegura|dentro de build"):
            self.run_materializer("build/../outside")

    def test_rejects_symlinked_build_ancestry(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / "build").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_materializer()
        self.assertEqual([], list(outside.iterdir()))

    def test_existing_different_output_is_never_overwritten(self):
        destination = self.root / "build/upstream"
        destination.mkdir(parents=True)
        sentinel = destination / "local.txt"
        sentinel.write_text("keep this work\n")
        with self.assertRaisesRegex(ValueError, "no se sobrescribe"):
            self.run_materializer()
        self.assertEqual(sentinel.read_text(), "keep this work\n")
        self.assertEqual((self.source / "sample.cpp").read_bytes(), self.base)

    def test_verified_snapshot_must_match_while_copying(self):
        (self.source / "sample.cpp").write_bytes(b"altered\n")
        with self.assertRaisesRegex(ValueError, "copia staged"):
            self.run_materializer()
        self.assertFalse((self.root / "build/upstream").exists())

    def test_git_apply_is_exact_when_fixture_is_inside_repository_build(self):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="materializer-git-context-", dir=build) as tmp:
            nested = Path(tmp) / "project"
            shutil.copytree(self.root, nested)
            nested_source = nested / "upstream/winuae"
            nested_manifest = {
                "destination": "upstream/winuae",
                "source": {"commit": COMMIT},
                "files": import_sources.inventory(nested_source),
            }
            with patch.object(materializer.import_sources, "verify_source",
                              return_value=nested_manifest):
                receipt, output = materializer.materialize(nested)
            self.assertEqual((output / "sample.cpp").read_bytes(), self.after)
            self.assertEqual(receipt["files"], import_sources.inventory(output))


if __name__ == "__main__":
    unittest.main()
