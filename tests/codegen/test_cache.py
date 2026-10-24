"""Verified build-cache hits, invalidation and forced regeneration (no Amiga state is cached)."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tests.codegen import test_materialize as m
import generate_cpu as gen


class MaterializeCacheTests(m.MaterializeTests):
    def test_hit_skips_copy_and_apply_but_not_snapshot_verification(self):
        first = self.run_materializer()
        with patch.object(m.materializer.shutil, "copytree", side_effect=AssertionError("cache copied")), \
                patch.object(m.materializer, "_apply", side_effect=AssertionError("cache applied")), \
                patch.object(m.materializer.import_sources, "verify_source", return_value=self.manifest) as verify:
            self.assertEqual(self.run_materializer(), first)
            verify.assert_called_once_with(self.root)

    def test_force_applies_again_and_preserves_bytes(self):
        first = self.run_materializer()
        with patch.object(m.materializer, "_apply", wraps=m.materializer._apply) as apply:
            self.assertEqual(m.materializer.materialize(self.root, force=True), first)
            self.assertEqual(apply.call_count, 2)

    def test_patch_bytes_are_checked_on_a_hit(self):
        self.run_materializer()
        self.patch_path.write_bytes(self.patch_bytes + b"tampered\n")
        with self.assertRaisesRegex(ValueError, "Hash de parche"):
            self.run_materializer()

    def test_cache_rejects_missing_extra_and_changed_outputs(self):
        _, output = self.run_materializer()
        sample = output / "sample.cpp"
        for corruption in ("changed", "missing", "extra"):
            with self.subTest(corruption=corruption):
                sample.write_bytes(b"changed" if corruption == "changed" else self.after)
                if corruption == "missing":
                    sample.unlink()
                extra = output / "extra"
                if corruption == "extra":
                    extra.write_bytes(b"extra")
                with self.assertRaisesRegex(ValueError, "no se sobrescribe"):
                    self.run_materializer()
                extra.unlink(missing_ok=True)

    def test_changed_series_cannot_borrow_proof_of_exact_application(self):
        self.run_materializer()
        # The new patch has the same declared result, but cannot apply.
        self.write_series(patch_bytes=self.patch_bytes.replace(b"-old", b"-BAD"))
        with self.assertRaisesRegex(ValueError, "git apply fallo"):
            self.run_materializer()

    def test_invalid_proof_falls_back_to_exact_application(self):
        self.run_materializer()
        (self.root / "build/.upstream-materialize.json").write_text("invalid json")
        with patch.object(m.materializer, "_apply", wraps=m.materializer._apply) as apply:
            self.run_materializer()
            self.assertEqual(apply.call_count, 2)


@unittest.skipUnless(shutil.which("clang") and shutil.which("clang++"), "HOST compilers unavailable")
class CodegenCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p6-codegen-cache-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.upstream = self.root / "upstream/winuae"
        for name in ("build", "manifests", "tools/codegen_host", "tests/codegen", "upstream/winuae/include"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.upstream / "gen.c").write_text('#include <stdio.h>\nint main(void) { puts("#define ANSWER 42"); return 0; }\n')
        (self.upstream / "sample.cpp").write_text("// no generated includes\n")
        (self.root / "tests/codegen/blitter_truth.c").write_text("int main(void) { return 0; }\n")
        recipe = dict(schema_version=1, upstream_commit=m.COMMIT, environment={"LC_ALL": "C"}, absent_inputs=[],
                      flags={"c": ["-std=c11"], "c++": ["-std=c++17"]},
                      generators=[dict(name="fixture", language="c", sources=["upstream/gen.c"], outputs=["result.h"],
                                       runs=[dict(cwd="generated", args=[], stdout="generated/result.h")])])
        (self.root / "manifests/codegen.json").write_text(json.dumps(recipe))
        (self.root / "manifests/source-manifest.json").write_text(json.dumps(
            dict(selection=dict(upstream_units=["sample.cpp"], generated_units=["result.h"])) ))
        # Materialization integrity has its own real-patch tests above. This small
        # generator uses the actual compiler and publisher without the full engine.
        for mocked in (patch.object(gen, "materialize", return_value=(dict(upstream_commit=m.COMMIT, series_sha256="a" * 64), self.upstream)),
                       patch.object(gen, "verify_source")):
            mocked.start()
            self.addCleanup(mocked.stop)

    def generate(self, **kwargs):
        return gen.generate(self.root, **kwargs)

    def test_hit_checks_compilers_and_outputs_without_compiling(self):
        first = self.generate()
        with patch.object(gen, "run", wraps=gen.run) as run:
            self.assertEqual(self.generate(), first)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(sum(command[-1] == "--version" for command in commands), 2)
            self.assertTrue(all(command[-1] == "--version" or "-M" in command for command in commands))
            self.assertTrue(first['identity']['host_inputs'], 'System headers must be recorded')

    def test_system_header_changes_and_new_shadowing_headers_invalidate(self):
        sdk = tempfile.TemporaryDirectory(prefix='p6-host-headers-')
        self.addCleanup(sdk.cleanup)
        early, late = Path(sdk.name).resolve() / 'early', Path(sdk.name).resolve() / 'late'
        early.mkdir(); late.mkdir()
        header = late / 'fixture.h'
        header.write_text('#define ANSWER 42\n')
        (self.upstream / 'gen.c').write_text(
            '#include <stdio.h>\n#include <fixture.h>\nint main(void) { printf("#define ANSWER %d\\n", ANSWER); return 0; }\n')
        path = self.root / 'manifests/codegen.json'
        recipe = json.loads(path.read_text())
        recipe['flags']['c'] += ['-isystem', str(early), '-isystem', str(late)]
        path.write_text(json.dumps(recipe))
        original = self.generate()
        self.assertIn(str(header), original['identity']['host_inputs'])
        receipt = self.root / 'build/generated/codegen-receipt.json'
        recorded = receipt.read_bytes()
        before = header.stat()
        header.write_text('#define ANSWER 42\n// changed bytes\n')
        os.utime(header, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'Identidad de codegen distinta'):
            self.generate()
        forced = self.generate(force=True)
        self.assertEqual(original['files'], forced['files'])
        self.assertEqual(recorded, receipt.read_bytes(), 'Force must not refresh the receipt')
        with self.assertRaisesRegex(ValueError, 'Identidad de codegen distinta'):
            self.generate()
        refreshed = self.generate(refresh_receipt=True)
        self.assertEqual(original['files'], refreshed['files'])
        self.assertNotEqual(original['identity'], refreshed['identity'])
        shadow = early / 'fixture.h'
        shadow.write_text('#define ANSWER 42\n')
        with self.assertRaisesRegex(ValueError, 'Identidad de codegen distinta'):
            self.generate()
        refreshed = self.generate(refresh_receipt=True)
        self.assertIn(str(shadow), refreshed['identity']['host_inputs'])
        self.assertNotIn(str(header), refreshed['identity']['host_inputs'])
        recorded = receipt.read_bytes()
        shadow.write_text('#define ANSWER 43\n')
        for options in ({'force': True}, {'refresh_receipt': True}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'no se sobrescribe'):
                self.generate(**options)
            self.assertEqual(recorded, receipt.read_bytes())

    def test_untracked_system_header_inside_project_also_invalidates(self):
        sdk = self.root / 'sdk'
        sdk.mkdir()
        header = sdk / 'fixture.h'
        header.write_text('// first bytes\n')
        source = self.upstream / 'gen.c'
        source.write_text('#include <fixture.h>\n' + source.read_text())
        path = self.root / 'manifests/codegen.json'
        recipe = json.loads(path.read_text())
        recipe['flags']['c'] += ['-isystem', str(sdk)]
        path.write_text(json.dumps(recipe))
        self.generate()
        header.write_text('// different bytes\n')
        with self.assertRaisesRegex(ValueError, 'Identidad de codegen distinta'):
            self.generate()

    def test_force_runs_generators_and_matches_cached_bytes(self):
        first = self.generate()
        with patch.object(gen, "run", wraps=gen.run) as run:
            self.assertEqual(self.generate(force=True)["files"], first["files"])
            self.assertGreater(run.call_count, 2)
        gen.materialize.assert_called_with(self.root.resolve(), force=True)

    def test_input_identity_changes_are_not_silently_accepted(self):
        self.generate()
        for relative in ("manifests/codegen.json", "manifests/source-manifest.json", "tests/codegen/blitter_truth.c"):
            path = self.root / relative
            original = path.read_bytes()
            with self.subTest(input=relative):
                path.write_bytes(original + b"\n")
                with self.assertRaisesRegex(ValueError, "Identidad de codegen distinta"):
                    self.generate()
                path.write_bytes(original)
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "123456789"}):
            with self.assertRaisesRegex(ValueError, "Identidad de codegen distinta"):
                self.generate()
        (self.root / "tools/codegen_host/new.h").write_text("// new input\n")
        with self.assertRaisesRegex(ValueError, "Identidad de codegen distinta"):
            self.generate()

    def test_corrupt_partial_and_unlisted_output_is_never_repaired_silently(self):
        self.generate()
        directory = self.root / "build/generated"
        output = directory / "result.h"
        original = output.read_bytes()
        for corruption in ("changed", "missing", "extra", "receipt"):
            with self.subTest(corruption=corruption):
                output.write_bytes(b"wrong\n" if corruption == "changed" else original)
                if corruption == "missing":
                    output.unlink()
                extra = directory / "extra"
                if corruption == "extra":
                    extra.write_bytes(b"extra")
                if corruption == "receipt":
                    (directory / "codegen-receipt.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "alterada o incompleta"):
                    self.generate()
                extra.unlink(missing_ok=True)

    def test_receipt_refresh_requires_a_fresh_identical_generation(self):
        first = self.generate()
        recipe = self.root / "manifests/codegen.json"
        recipe.write_text(recipe.read_text() + "\n")
        with patch.object(gen, "run", wraps=gen.run) as run:
            refreshed = self.generate(refresh_receipt=True)
            self.assertGreater(run.call_count, 2)
            self.assertEqual(refreshed["files"], first["files"])
            self.assertNotEqual(refreshed["identity"], first["identity"])
        source = self.upstream / "gen.c"
        original = source.read_text()
        source.write_text('#include <stdio.h>\nint main(void) { puts("different"); return 0; }\n')
        with self.assertRaisesRegex(ValueError, "no se sobrescribe"):
            self.generate(refresh_receipt=True)
        source.write_text(original)
        self.assertEqual(self.generate(), refreshed)


if __name__ == "__main__":
    unittest.main()
