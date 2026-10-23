"""Recipe coverage and fail-closed infrastructure; not emulator evidence."""
import ast
import contextlib
from contextlib import ExitStack
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

    def test_missing_firmware_cd_or_cartridge_assets_stop_before_build_or_image_preparation(self):
        required = {"P6_KICKSTART_A4000_ROM", "P6_CD32_ROM_MAIN", "P6_CD32_ROM_EXT", "P6_CD32_GAME_CUE",
                    "P6_CDTV_ROM_MAIN", "P6_CDTV_ROM_EXT", "P6_CDTV_BOOT_CUE", "P6_KICKSTART_A500_ROM",
                    "P6_ACTION_REPLAY1_ROM", "P6_ACTION_REPLAY2_ROM", "P6_ACTION_REPLAY3_ROM", "P6_ACTION_REPLAY1200_ROM",
                    "P6_CD_CUE_FILE", "P6_CD_CCD_FILE", "P6_CD_NRG_FILE", "P6_CD_ISO_FILE", "P6_CD_WAV_FILE"}
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

    def test_waterbox_preflight_validates_sources_recipe_guest_and_runtime_without_building(self):
        with tempfile.TemporaryDirectory(prefix="ci-waterbox-", dir=ci.ci.ROOT / "build") as directory, ExitStack() as stack:
            root = Path(directory)
            sources = ("bridge/example.cpp",)
            recipes = {"makefile": "ports/waterbox/Makefile", "common_mak": "references/machawk/waterbox/common.mak",
                       "linkscript_aarch64": "references/machawk/waterbox/linkscript_aarch64.T", "build_recipe": "tools/build_waterbox.py"}
            for name in (*sources, *recipes.values(), "guest.wbx", "runtime.dylib"):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
            runtime = dict(path=str(root / "runtime.dylib"), sha256=ci.wb.sha256(root / "runtime.dylib"))
            valid = dict(schema_version=1, status="BUILD01_PASS_GUEST_NOT_EXECUTED",
                         sources={name: ci.wb.sha256(root / name) for name in sources},
                         recipe={key: ci.wb.sha256(root / name) for key, name in recipes.items()},
                         guest=dict(path=str(root / "guest.wbx"), sha256=ci.wb.sha256(root / "guest.wbx")))
            receipt = root / "receipt.json"
            stack.enter_context(mock.patch.object(ci, "GUEST_RECEIPT", receipt))
            stack.enter_context(mock.patch.object(ci.ci, "ROOT", root))
            stack.enter_context(mock.patch.object(ci.wb, "puae6_sources", return_value=list(sources)))
            trees = stack.enter_context(mock.patch.object(ci.wb, "ensure_current_trees"))
            elf = stack.enter_context(mock.patch.object(ci.wb, "validate_elf"))
            tls = stack.enter_context(mock.patch.object(ci.wb, "check_no_compiler_tls"))
            stack.enter_context(mock.patch.object(ci.wb, "read_runtime_receipt", return_value={"runtime_dylib": runtime}))
            runtime_path = stack.enter_context(mock.patch.object(ci.wb, "runtime_receipt_path", return_value=root / "runtime.json"))
            receipt.write_text(json.dumps(valid)); ci.waterbox_inputs()
            trees.assert_called_once_with(root=root)
            elf.assert_called_once_with(root / "guest.wbx"); tls.assert_called_once_with(root / "guest.wbx")
            runtime_path.assert_called_once_with("release")
            for kind in ("schema", "status", "selection", "source", "recipe", "guest", "runtime", "trees"):
                data = json.loads(json.dumps(valid))
                if kind == "schema": data["schema_version"] = 0
                if kind == "status": data["status"] = "PASS"
                if kind == "selection": data["sources"] = {}
                if kind == "source": data["sources"][sources[0]] = "0" * 64
                if kind == "recipe": data["recipe"]["makefile"] = "0" * 64
                if kind == "guest": data["guest"]["sha256"] = "0" * 64
                runtime["sha256"] = "0" * 64 if kind == "runtime" else ci.wb.sha256(root / "runtime.dylib")
                trees.side_effect = ValueError("stale derived tree") if kind == "trees" else None
                receipt.write_text(json.dumps(data))
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "invalid current Waterbox inputs"):
                    ci.waterbox_inputs()
            for invalid in ("not JSON", "{}", "[]"):
                receipt.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid current Waterbox inputs"):
                    ci.waterbox_inputs()
            receipt.unlink()
            with self.assertRaisesRegex(ValueError, "invalid current Waterbox inputs"):
                ci.waterbox_inputs()

    def test_functional_enables_movies_with_canonical_receipt_and_rejects_stale_guest_early(self):
        counts = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        with mock.patch.dict(ci.os.environ, P6_MEDIA_MOVIE_GUEST_RECEIPT="inherited-stale.json"), ExitStack() as stack:
            for name in ("candidate_package", "prepare", "execute"):
                stack.enter_context(mock.patch.object(ci, name))
            stack.enter_context(mock.patch.object(ci.ci, "preflight"))
            stack.enter_context(mock.patch.object(ci, "suites", return_value=["tests.bizhawk-movies.test_media_start"]))
            stack.enter_context(mock.patch.object(ci, "identity", return_value={}))
            images = stack.enter_context(mock.patch.object(ci, "images", return_value={}))
            inputs = stack.enter_context(mock.patch.object(ci, "waterbox_inputs"))
            command = stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")))
            self.assertEqual("PASS", ci.run("functional")["status"])
            self.assertEqual(str(ci.GUEST_RECEIPT), command.call_args.args[1]["P6_MEDIA_MOVIE_GUEST_RECEIPT"])
            images.reset_mock(); command.reset_mock(); ci.execute.reset_mock(); ci.prepare.reset_mock()
            inputs.side_effect = ValueError("invalid current Waterbox inputs")
            self.assertEqual("BLOCKED", ci.run("functional")["status"])
            images.assert_not_called(); command.assert_not_called(); ci.execute.assert_not_called(); ci.prepare.assert_not_called()

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

    def test_native_lanes_never_inspect_images_and_sanitizers_execute_every_suite(self):
        counts = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        facts = dict(source_tree_sha256="a" * 64, inputs={"pin": "a"})
        for lane in ("clean", "sanitizers"):
            selected = ci.suites(lane)
            with self.subTest(lane=lane), ExitStack() as stack:
                stack.enter_context(mock.patch.object(ci.ci, "preflight"))
                stack.enter_context(mock.patch.object(ci, "execute"))
                stack.enter_context(mock.patch.object(ci, "prepare", return_value={"p6_headless": "a", "puae6_libretro.dylib": "b"}))
                identity = stack.enter_context(mock.patch.object(ci, "identity", return_value=facts))
                images = stack.enter_context(mock.patch.object(ci, "images", side_effect=AssertionError("native lane consulted Docker")))
                command = stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")))
                result = ci.run(lane)
            self.assertEqual("PASS", result["status"])
            self.assertEqual({}, result["images"])
            self.assertEqual(selected, [record["module"] for record in result["completed"]])
            self.assertEqual(selected, [call.args[0][-1] for call in command.call_args_list])
            self.assertEqual([mock.call({}), mock.call({})], identity.call_args_list)
            images.assert_not_called()
            ci.validate_result(result, lane)
        self.assertTrue(selected)
        self.assertTrue(all(module.startswith("tests.fuzz.") for module in selected))

    def test_native_sanitizer_failures_missing_counts_and_skips_never_pass(self):
        good = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        cases = ((1, dict(good, failures=1), "FAIL"), (0, None, "FAIL"),
                 (0, dict(good, tests=0), "FAIL"), (0, dict(good, skipped=1), "BLOCKED"))
        selected = ci.suites("sanitizers")
        for code, counts, expected in cases:
            with self.subTest(code=code, counts=counts), ExitStack() as stack:
                for name in ("prepare", "execute", "identity"):
                    stack.enter_context(mock.patch.object(ci, name, return_value={}))
                stack.enter_context(mock.patch.object(ci.ci, "preflight"))
                images = stack.enter_context(mock.patch.object(ci, "images"))
                command = stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], code, "" if counts is None else json.dumps(counts), "fixture failure")))
                result = ci.run("sanitizers")
            self.assertEqual(expected, result["status"])
            self.assertEqual(selected[0], result["module"])
            self.assertEqual([], result["completed"])
            command.assert_called_once()
            images.assert_not_called()

    def test_missing_sanitizer_suites_block_before_preparation(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ci.ci, "ROOT", Path(directory)), \
             mock.patch.object(ci.ci, "preflight") as preflight, mock.patch.object(ci, "execute") as execute, \
             mock.patch.object(ci, "prepare") as prepare, mock.patch.object(ci, "images") as images:
            result = ci.run("sanitizers")
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("empty CI lane", result["diagnostic"])
        for operation in (preflight, execute, prepare, images): operation.assert_not_called()

    def test_missing_offline_images_still_block_container_lanes_before_build(self):
        for lane in ("functional", "matrix", "optimization", "performance"):
            with self.subTest(lane=lane), ExitStack() as stack:
                for name in ("candidate_package", "waterbox_inputs"):
                    stack.enter_context(mock.patch.object(ci, name))
                stack.enter_context(mock.patch.object(ci.ci, "preflight"))
                execute = stack.enter_context(mock.patch.object(ci, "execute"))
                prepare = stack.enter_context(mock.patch.object(ci, "prepare"))
                command = stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 1, "", "absent")))
                result = ci.run(lane)
            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("required offline Docker image absent", result["diagnostic"])
            self.assertEqual(["docker", "image", "inspect"], command.call_args.args[0][:3])
            command.assert_called_once(); execute.assert_not_called(); prepare.assert_not_called()

    def test_container_lanes_recheck_the_same_frozen_images_after_suites_and_at_finish(self):
        counts = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        frozen = {"fixture-tag": "sha256:frozen"}
        for lane in ("functional", "matrix", "optimization", "performance"):
            with self.subTest(lane=lane), ExitStack() as stack:
                for name in ("candidate_package", "waterbox_inputs", "prepare", "execute", "identity"):
                    stack.enter_context(mock.patch.object(ci, name, return_value={}))
                stack.enter_context(mock.patch.object(ci.ci, "preflight"))
                stack.enter_context(mock.patch.object(ci, "suites", return_value=["fixture"]))
                stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")))
                images = stack.enter_context(mock.patch.object(ci, "images", return_value=frozen))
                self.assertEqual("PASS", ci.run(lane)["status"])
                self.assertEqual([mock.call(retroarch=lane == "functional")] * 3, images.call_args_list)
                images.side_effect = [frozen, {"fixture-tag": "sha256:changed"}]
                result = ci.run(lane)
                self.assertEqual("FAIL", result["status"])
                self.assertIn("Docker inputs changed", result["diagnostic"])

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
