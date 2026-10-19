"""Recipe coverage and fail-closed infrastructure; not emulator evidence."""
import ast
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ci import nightly as ci


class NightlyRecipeTests(unittest.TestCase):
    def test_every_current_module_is_selected_and_split_simd_classes_are_all_covered(self):
        selected = {s for lane in ci.LANES for s in ci.suites(lane)}
        for path in (ci.ci.ROOT / "tests").rglob("test_*.py"):
            name = ".".join(path.relative_to(ci.ci.ROOT).with_suffix("").parts)
            if name in selected:
                continue
            classes = {n.name for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef)}
            self.assertTrue(classes, name)
            self.assertTrue({name + "." + cls for cls in classes} <= selected, name)

    def test_performance_has_no_sanitizer_classes_and_all_lanes_are_nonempty(self):
        for lane in ci.LANES:
            self.assertTrue(ci.suites(lane) or lane == "clean")
        self.assertFalse(any(s.endswith((".SafetyTests", ".EquivalenceTests")) for s in ci.suites("performance")))
        with self.assertRaises(ValueError):
            ci.suites("missing")

    def test_functional_keeps_the_frame_boundary_mutation_checks_enabled(self):
        self.assertEqual(ci.ENV["P6_TEST_MUTANTS"], "1")
        self.assertIn("tests.step.test_step", ci.suites("functional"))

    def test_programmable_beam_audio_is_selected_on_both_supported_presets(self):
        from tests.audio.test_audio import AudioTests, AudioA600Tests, AudioA1200Tests
        name = "test_cumulative_frames_hold_when_the_guest_programs_the_beam"
        loader = unittest.defaultTestLoader
        self.assertNotIn(name, loader.getTestCaseNames(AudioTests))
        self.assertIn(name, loader.getTestCaseNames(AudioA600Tests))
        self.assertIn(name, loader.getTestCaseNames(AudioA1200Tests))
        self.assertIn("tests.audio.test_audio", ci.suites("functional"))

    def test_an_unclassified_new_simd_file_cannot_disappear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests/simd").mkdir(parents=True)
            (root / "tests/simd/test_new.py").write_text("# synthetic fixture")
            with mock.patch.object(ci.ci, "ROOT", root), self.assertRaisesRegex(ValueError, "unclassified"):
                ci.suites("functional")

    def test_functional_assets_are_required_before_any_preparation_or_test(self):
        with mock.patch.object(ci.ci, "preflight", side_effect=ValueError("missing required asset")) as preflight, \
             mock.patch.object(ci, "prepare") as prepare:
            result = ci.run("functional")
        self.assertEqual(result["status"], "BLOCKED")
        preflight.assert_called_once_with(ci.ASSETS)
        prepare.assert_not_called()

    def test_changed_image_tags_do_not_authorize_other_container_bytes(self):
        p = subprocess.CompletedProcess([], 0, json.dumps([{"Id": "wrong", "Architecture": "arm64"}]), "")
        with mock.patch.object(ci.ci, "command", return_value=p), self.assertRaisesRegex(ValueError, "frozen"):
            ci.images()

    def test_a_zero_exit_pass_without_identity_or_complete_suites_is_not_evidence(self):
        for result in ({}, {"status": "PASS", "lane": "clean"},
                       {"status": "PASS", "lane": "clean", "identity": {"source_tree_sha256": "a" * 64, "inputs": {"pin": "a"}}}):
            with self.assertRaises(ValueError):
                ci.validate_result(result, "clean")
        ci.validate_result({"status": "BLOCKED", "lane": "functional"}, "functional")

    def test_preparation_logs_redact_paths_and_quote_arguments(self):
        output = io.StringIO()
        with mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, "", "")), contextlib.redirect_stderr(output):
            ci.execute(["cmake", "-S", ci.ci.ROOT, "-Dflags=two words"])
        self.assertNotIn(str(ci.ci.ROOT), output.getvalue())
        self.assertIn("@repo", output.getvalue())
        self.assertIn("'-Dflags=two words'", output.getvalue())

    def test_existing_matrix_never_pulls_or_uses_network(self):
        from tests.portability.run_matrix import Row
        row = Row("linux-arm64-clang")
        with tempfile.TemporaryDirectory() as directory:
            row.host_dir = Path(directory)
            with mock.patch("tests.portability.run_matrix.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as execute:
                row.run("true", Path(directory) / "fixture.log", 10)
            argv = execute.call_args.args[0]
        self.assertIn("--pull=never", argv)
        self.assertIn("--network=none", argv)

    def test_workflow_is_manual_pinned_and_has_no_error_suppression_or_upload(self):
        workflow = (ci.ci.ROOT / ".github/workflows/puae6.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertIn("needs: checks", workflow)
        self.assertIn("puae6-perf", workflow)
        self.assertEqual(workflow.count("uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"), 2)
