"""T48f: the Waterbox guest is only measured with current engine trees and generated builtin assets.

Everything happens in a temporary project; the repository's own trees and series are not touched. The series is the one of test_materialize (one patch over one
file), the generated tree carries a codegen receipt like the one tools/generate_cpu.py writes.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_waterbox
import generate_cpu
import import_sources
import materialize_upstream as materializer

COMMIT = "857f48fcb09399d4d95527e29a2fdeaedb41f827"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CurrentTreesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p6-trees-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.source = self.root / "upstream/winuae"
        self.source.mkdir(parents=True)
        self.base = b"head\nold\nfoot\n"
        (self.source / "sample.cpp").write_bytes(self.base)
        (self.root / "patches/winuae").mkdir(parents=True)
        (self.root / "manifests").mkdir()
        (self.root / "manifests/codegen.json").write_text(json.dumps({"generators": [{"outputs": ["gen.c"]}]}))
        (self.root / "manifests/source-manifest.json").write_text(json.dumps({"selection": {}}))
        self.manifest = {"destination": "upstream/winuae", "source": {"commit": COMMIT}, "files": import_sources.inventory(self.source)}
        verify = patch.object(materializer.import_sources, "verify_source", return_value=self.manifest)
        verify.start()
        self.addCleanup(verify.stop)
        self.write_series("new")

    def write_series(self, word):
        """A series with one patch that turns `old` into `word`; returns the sha of series.json."""
        patch_bytes = (b"diff --git a/sample.cpp b/sample.cpp\n--- a/sample.cpp\n+++ b/sample.cpp\n"
                       b"@@ -1,3 +1,3 @@\n head\n-old\n+" + word.encode() + b"\n foot\n")
        name = f"0001-{word}.patch"
        for old in (self.root / "patches/winuae").glob("*.patch"):
            old.unlink()
        (self.root / "patches/winuae" / name).write_bytes(patch_bytes)
        after = b"head\n" + word.encode() + b"\nfoot\n"
        series = {"schema_version": 1, "upstream_commit": COMMIT,
                  "patches": [{"id": "sample", "file": f"patches/winuae/{name}", "sha256": digest(patch_bytes),
                               "files": [{"path": "sample.cpp", "before_sha256": digest(self.base), "after_sha256": digest(after)}]}]}
        path = self.root / "patches/winuae/series.json"
        path.write_text(json.dumps(series, indent=2) + "\n")
        return digest(path.read_bytes())

    def write_generated(self, **identity_overrides):
        """build/generated with the receipt generate_cpu writes, for the current upstream commit, series, recipe and source manifest."""
        generated = self.root / "build/generated"
        generated.mkdir(parents=True, exist_ok=True)
        (generated / "gen.c").write_text("int gen;\n")
        identity = {"upstream_commit": COMMIT, "series": digest((self.root / "patches/winuae/series.json").read_bytes()),
                    "recipe": digest((self.root / "manifests/codegen.json").read_bytes()),
                    "source_manifest": digest((self.root / "manifests/source-manifest.json").read_bytes()), **identity_overrides}
        files = [item for item in import_sources.inventory(generated) if item["path"] != "codegen-receipt.json"]
        (generated / "codegen-receipt.json").write_text(json.dumps({"schema_version": 1, "identity": identity, "tools": {}, "checks": {}, "files": files}))
        return generated

    def test_current_trees_pass_and_an_absent_upstream_is_materialized_by_the_official_path(self):
        self.assertFalse((self.root / "build/upstream").exists())
        self.write_generated()
        found = build_waterbox.ensure_current_trees(self.root)
        self.assertEqual(b"head\nnew\nfoot\n", (self.root / "build/upstream/sample.cpp").read_bytes())
        self.assertEqual(COMMIT, found["upstream_commit"])
        self.assertEqual(found, build_waterbox.ensure_current_trees(self.root), "a second check is a cache hit with the same answer")

    def test_an_upstream_materialized_with_another_series_is_refused_and_left_alone(self):
        materializer.materialize(self.root)
        self.write_generated()
        build_waterbox.ensure_current_trees(self.root)
        self.write_series("newer")                         # the series moved on, build/upstream did not
        with self.assertRaisesRegex(build_waterbox.BuildError, "(?s)no corresponde a la serie actual.*no se sobrescribe.*build/stale"):
            build_waterbox.ensure_current_trees(self.root)
        self.assertEqual(b"head\nnew\nfoot\n", (self.root / "build/upstream/sample.cpp").read_bytes(), "the old tree is not overwritten or deleted")

    def test_a_forged_materialization_receipt_does_not_pass(self):
        materializer.materialize(self.root)
        self.write_generated()
        (self.root / "build/upstream/sample.cpp").write_bytes(b"head\nforged\nfoot\n")       # same series, other bytes
        proof = self.root / "build/.upstream-materialize.json"
        proof.write_text(json.dumps({"identity": json.loads(proof.read_text())["identity"]}))   # the sidecar still says "applied"
        with self.assertRaisesRegex(build_waterbox.BuildError, "no se sobrescribe"):
            build_waterbox.ensure_current_trees(self.root)

    def test_a_generated_tree_of_another_series_recipe_or_manifest_is_refused(self):
        materializer.materialize(self.root)
        for field in ("series", "upstream_commit", "recipe", "source_manifest"):
            with self.subTest(field):
                self.write_generated(**{field: "0" * 64})
                with self.assertRaisesRegex(build_waterbox.BuildError, f"(?s)otra serie o receta.*{field}.*build/stale"):
                    build_waterbox.ensure_current_trees(self.root)
        self.write_generated()
        build_waterbox.ensure_current_trees(self.root)

    def test_a_missing_or_altered_generated_tree_is_refused(self):
        materializer.materialize(self.root)
        with self.assertRaisesRegex(build_waterbox.BuildError, "falta .*codegen-receipt.json.*generate_cpu"):
            build_waterbox.ensure_current_trees(self.root)
        generated = self.write_generated()
        (generated / "gen.c").write_text("int other;\n")
        with self.assertRaisesRegex(build_waterbox.BuildError, "alterado o incompleto"):
            build_waterbox.ensure_current_trees(self.root)

    def test_the_guest_build_checks_the_trees_before_compiling_anything(self):
        calls = []
        with patch.object(build_waterbox, "ensure_current_trees", side_effect=lambda *a: calls.append(a) or (_ for _ in ()).throw(build_waterbox.BuildError("tree of another series"))):
            with self.assertRaisesRegex(build_waterbox.BuildError, "another series"):
                build_waterbox.build_puae6({}, 1)               # an empty toolchain: it would be a KeyError if the build went on
        self.assertEqual(1, len(calls))

    def test_a_guest_is_only_measured_if_it_was_built_from_the_files_on_disk(self):
        materializer.materialize(self.root)
        generated = self.write_generated()
        asset = self.root / "build/assets/builtin.h"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"builtin\n")
        receipt = self.root / "puae6-receipt.json"
        sources = {"build/upstream/sample.cpp": digest(b"head\nnew\nfoot\n"), "build/generated/gen.c": digest(b"int gen;\n"),
                   "build/assets/builtin.h": digest(asset.read_bytes()), "bridge/other.c": "0" * 64}
        manual = {"bridge/other.c": "0" * 64}
        def write_sources(value):
            receipt.write_text(json.dumps({"sources": value, "manual_inputs": manual, "build_inputs": {"manual_inputs": manual}}))
        write_sources(sources)
        with patch.object(build_waterbox, "_TREES_CHECKED", False), patch.object(build_waterbox, "puae6_manual_inputs", return_value=manual):
            build_waterbox.check_guest_matches_trees(receipt, self.root)       # Manual closure is checked independently from engine/generated inputs.
            message = "otro build/upstream, build/generated o build/assets.*1 fuentes.*--puae6"
            for name in sources:
                if not name.startswith("build/"):
                    continue
                with self.subTest(source=name):
                    write_sources({**sources, name: "0" * 64})
                    with self.assertRaisesRegex(build_waterbox.BuildError, message) as error:
                        build_waterbox.check_guest_matches_trees(receipt, self.root)
                    self.assertIn(name, str(error.exception))
            write_sources(sources)
            asset.unlink()
            with self.assertRaisesRegex(build_waterbox.BuildError, message) as error:
                build_waterbox.check_guest_matches_trees(receipt, self.root)
            self.assertIn("build/assets/builtin.h", str(error.exception))
        self.assertTrue(generated.is_dir())


if __name__ == "__main__":
    unittest.main()
