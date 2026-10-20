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
import zipfile

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

    def test_whdload_builder_and_loader_cases_are_selected_once(self):
        def ids(suite):
            result = []
            for case in suite:
                result.extend(ids(case) if isinstance(case, unittest.TestSuite) else [case.id()])
            return result
        names = ("tests.whdload.test_cpp_bundle", "tests.whdload.test_autoload")
        selected = ci.suites("functional")
        groups = []
        for name in names:
            self.assertIn(name, selected)
            cases = ids(unittest.defaultTestLoader.loadTestsFromName(name))
            self.assertTrue(cases)
            self.assertEqual(len(cases), len(set(cases)))
            groups.append(set(cases))
        self.assertFalse(groups[0] & groups[1], "imported builder cases ran again with the loader")

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

    def test_missing_a4000_or_cd32_assets_stop_before_build_or_image_preparation(self):
        required = {"P6_KICKSTART_A4000_ROM", "P6_CD32_ROM_MAIN", "P6_CD32_ROM_EXT", "P6_CD32_GAME_CUE"}
        self.assertLessEqual(required, set(ci.ASSETS))
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "fixture"
            asset.write_bytes(b"preflight checks presence, never loads private content")
            for missing in sorted(required):
                environment = {name: str(asset) for name in ci.ASSETS if name != missing}
                with self.subTest(missing=missing), mock.patch.dict(ci.os.environ, environment, clear=True), \
                     mock.patch.object(ci.ci.platform, "system", return_value="Darwin"), \
                     mock.patch.object(ci.ci.platform, "machine", return_value="arm64"), \
                     mock.patch.object(ci.ci.os, "sysconf", return_value=16384), \
                     mock.patch.object(ci.ci.shutil, "which", return_value="/local/tool"), \
                     mock.patch.object(ci, "images") as images, mock.patch.object(ci, "prepare") as prepare, \
                     mock.patch.object(ci, "execute") as execute:
                    result = ci.run("functional")
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(missing, result["diagnostic"])
                images.assert_not_called()
                prepare.assert_not_called()
                execute.assert_not_called()

    def test_stale_package_stops_before_build_or_image_preparation(self):
        with tempfile.TemporaryDirectory(prefix="ci-package-", dir=ci.ci.ROOT / "build") as directory:
            archive = Path(directory) / "preview.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("manifest.json", json.dumps(dict(schema_version=1, kind="local-preview",
                            platform="macos-arm64", revision="b" * 40)))
            counts = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
            with mock.patch.dict(ci.os.environ, P6_PACKAGE_ZIP=str(archive)), \
                 mock.patch("tools.package.clean_revision", return_value="a" * 40), \
                 mock.patch.object(ci.ci, "preflight"), mock.patch.object(ci, "suites", return_value=["fixture"]), \
                 mock.patch.object(ci, "identity", return_value={}), mock.patch.object(ci, "images", return_value={}) as images, \
                 mock.patch.object(ci, "prepare", return_value={}) as prepare, mock.patch.object(ci, "execute") as execute, \
                 mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")):
                result = ci.run("functional")
            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("package revision", result["diagnostic"])
            images.assert_not_called(); prepare.assert_not_called(); execute.assert_not_called()

    def test_package_manifest_is_bounded_and_matches_the_clean_candidate(self):
        manifest = dict(schema_version=1, kind="local-preview", platform="macos-arm64", revision="a" * 40)
        with tempfile.TemporaryDirectory(prefix="ci-package-", dir=ci.ci.ROOT / "build") as directory:
            path = Path(directory) / "preview.zip"
            with mock.patch.dict(ci.os.environ, P6_PACKAGE_ZIP=str(path)), \
                 mock.patch("tools.package.clean_revision", return_value="a" * 40) as clean:
                for payload in (b"[]", b"{}", b"not JSON", b" " * (64 * 1024 + 1),
                                json.dumps(dict(manifest, platform="other")), json.dumps(dict(manifest, kind="other"))):
                    with self.subTest(payload=str(payload)[:40]):
                        with zipfile.ZipFile(path, "w") as archive:
                            archive.writestr("manifest.json", payload)
                        with self.assertRaisesRegex(ValueError, "package manifest"):
                            ci.candidate_package()
                for name in ("manifest.json", "missing.json"):
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr(name, json.dumps(manifest))
                    if name == "manifest.json":
                        ci.candidate_package()
                    else:
                        with self.assertRaisesRegex(ValueError, "package manifest"):
                            ci.candidate_package()
                path.write_bytes(b"not ZIP")
                with self.assertRaisesRegex(ValueError, "package manifest"):
                    ci.candidate_package()
                clean.side_effect = ValueError("Commit the public sources before packaging")
                with self.assertRaisesRegex(ValueError, "Commit the public sources"):
                    ci.candidate_package()

    def test_cpu_campaign_is_prepared_once_before_its_measurements_are_consumed(self):
        with mock.patch.object(ci, "execute") as execute:
            ci.prepare("performance")
        commands = [list(map(str, call.args[0])) for call in execute.call_args_list]
        build = [ci.sys.executable, "tools/bench.py", "build", "--jobs", "2"]
        run = [ci.sys.executable, "tools/bench.py", "run", "--root", str(ci.CPU_PERF_DIR.parent),
               "--id", ci.CPU_PERF_DIR.name, "--compare-cpu-levels", "--force"]
        self.assertEqual(commands.count(build), 1)
        self.assertEqual(commands.count(run), 1)
        self.assertLess(commands.index(build), next(i for i, argv in enumerate(commands) if "measure" in argv))
        self.assertEqual(ci.ENV["P6_CPU_PERF_DIR"], str(ci.CPU_PERF_DIR))
        self.assertEqual(ci.ENV["P6_RUN_CPU_PERF"], "0")
        self.assertIn("tests.bench.test_cpu_campaign", ci.suites("performance"))

    def test_cpu_campaign_preparation_failure_stops_before_measurement(self):
        def stop(argv):
            if "tools/bench.py" in argv:
                raise RuntimeError("missing CPU benchmark artifact")
        with mock.patch.object(ci, "execute", side_effect=stop) as execute, self.assertRaisesRegex(RuntimeError, "missing CPU"):
            ci.prepare("performance")
        self.assertFalse(any("measure" in call.args[0] for call in execute.call_args_list))

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
