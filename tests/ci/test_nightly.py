"""Recipe coverage and fail-closed infrastructure; not emulator evidence."""
import ast
import contextlib
from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from tools.ci import nightly as ci


class ParallelOptimizationTests(unittest.TestCase):
    """Controlled thread/process mocks, never compilers, Docker or emulation."""
    def setUp(self):
        self.selected = ci.suites("optimization")
        self.counts = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)
        self.facts = dict(source_tree_sha256="a"*64, inputs={"own": "fixture"})
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(mock.patch.object(ci.ci,"preflight"))
        self.stack.enter_context(mock.patch.object(ci,"execute"))
        self.images = self.stack.enter_context(mock.patch.object(ci,"images",return_value={}))
        self.identity = self.stack.enter_context(mock.patch.object(ci,"identity",return_value=self.facts))
        self.prepared = threading.Event()
        self.prepare = self.stack.enter_context(mock.patch.object(ci,"prepare",side_effect=lambda lane:self.prepared.set()))

    def process(self, counts=None, code=0):
        return subprocess.CompletedProcess([],code,json.dumps(self.counts if counts is None else counts),"own diagnostic")

    def test_two_queues_overlap_after_preparation_frontend_is_serial_and_records_are_stable(self):
        rendezvous = threading.Barrier(2)
        firsts = {queue[0] for queue in ci.OPTIMIZATION_QUEUES}
        frontend_done = threading.Event(); called = []
        def command(argv, env, timeout):
            self.assertTrue(self.prepared.is_set()); self.assertEqual(env,ci.ENV)
            self.assertEqual(timeout,6*3600)
            module = argv[-1]; called.append(module)
            if module in firsts: rendezvous.wait(timeout=3)
            if module == ci.OPTIMIZATION_QUEUES[0][0]: frontend_done.set()
            if module == ci.OPTIMIZATION_QUEUES[0][1]: self.assertTrue(frontend_done.is_set())
            return self.process()
        with mock.patch.object(ci.ci,"command",side_effect=command): result = ci.run("optimization")
        self.assertEqual(result["status"],"PASS",result)
        self.assertCountEqual(called,self.selected); self.assertEqual(len(called),len(set(called)))
        self.assertEqual(result["completed"],[dict(module=name,counts=self.counts) for name in self.selected])
        self.assertEqual(self.images.call_count,len(self.selected)+2)
        ci.validate_result(result,"optimization")

    def test_unknown_missing_or_duplicate_selectors_block_before_preflight_or_preparation(self):
        for selected in (self.selected+["own.new"],self.selected[:-1],self.selected+[self.selected[0]]):
            with mock.patch.object(ci,"suites",return_value=selected), mock.patch.object(ci.ci,"preflight") as preflight:
                result=ci.run("optimization")
            self.assertEqual(result["status"],"BLOCKED"); self.assertIn("exactly once",result["diagnostic"])
            preflight.assert_not_called(); self.prepare.assert_not_called()
        with mock.patch.object(ci,"OPTIMIZATION_QUEUES",(tuple(self.selected),tuple(self.selected))), self.assertRaises(ValueError):
            ci.optimization_queues(self.selected)

    def test_failed_skipped_empty_or_invalid_counts_drain_the_active_other_worker(self):
        bads = ((self.process(dict(self.counts,failures=1),1),"FAIL"),
                (self.process(dict(self.counts,skipped=1)),"BLOCKED"),
                (self.process(dict(self.counts,tests=0)),"FAIL"),
                (subprocess.CompletedProcess([],0,"invalid counts","own diagnostic"),"FAIL"))
        for bad, status in bads:
            with self.subTest(status=status,stdout=bad.stdout):
                rendezvous=threading.Barrier(2); active=threading.Event(); release=threading.Event(); inspecting=threading.Event()
                release_inspection=threading.Event(); submitted=threading.Event(); futures=[]
                called=[]; outcomes=[]; bad_thread=[]
                class ObservedPool(ci.ThreadPoolExecutor):
                    def submit(self,*args,**kwargs):
                        future=super().submit(*args,**kwargs); futures.append(future)
                        if len(futures)==2: submitted.set()
                        return future
                def command(argv, *args, **kwargs):
                    module=argv[-1]; called.append(module)
                    if module in {q[0] for q in ci.OPTIMIZATION_QUEUES}: rendezvous.wait(timeout=3)
                    if module==ci.OPTIMIZATION_QUEUES[0][0]: bad_thread.append(threading.get_ident()); return bad
                    active.set(); self.assertTrue(release.wait(timeout=3)); return self.process()
                def images(**kwargs):
                    if bad_thread and threading.get_ident()==bad_thread[0]:
                        inspecting.set(); self.assertTrue(release_inspection.wait(timeout=3))
                    return {}
                with mock.patch.object(ci.ci,"command",side_effect=command), mock.patch.object(ci,"images",side_effect=images), \
                     mock.patch.object(ci,"ThreadPoolExecutor",ObservedPool):
                    runner=threading.Thread(target=lambda:outcomes.append(ci.run("optimization")))
                    runner.start()
                    try:
                        self.assertTrue(active.wait(timeout=3)); self.assertTrue(inspecting.wait(timeout=3))
                        self.assertTrue(runner.is_alive(),"must await the active subprocess")
                        release.set(); self.assertTrue(submitted.wait(timeout=3)); futures[1].result(timeout=3)
                        self.assertCountEqual(called,[q[0] for q in ci.OPTIMIZATION_QUEUES],
                                              "known failure stops pending selectors while Docker inspection is held")
                        self.assertTrue(runner.is_alive(),"must also await the retained inspection")
                    finally:
                        release.set(); release_inspection.set(); runner.join(timeout=5)
                self.assertFalse(runner.is_alive()); self.assertEqual(outcomes[0]["status"],status)
                self.assertEqual(len(called),len(set(called)))
                self.assertTrue(outcomes[0]["completed"])
                self.assertTrue(all(r["counts"]==self.counts for r in outcomes[0]["completed"]))

    def test_exception_identity_drift_or_missing_worker_results_cannot_pass(self):
        with mock.patch.object(ci.ci,"command",side_effect=OSError("own process error "+str(ci.ci.ROOT))):
            result=ci.run("optimization")
        self.assertEqual(result["status"],"FAIL"); self.assertNotIn(str(ci.ci.ROOT),json.dumps(result))
        self.identity.side_effect=[self.facts,{**self.facts,"source_tree_sha256":"b"*64}]
        with mock.patch.object(ci.ci,"command",return_value=self.process()):
            result=ci.run("optimization")
        self.assertEqual(result["status"],"FAIL"); self.assertIn("inputs changed",result["diagnostic"])
        self.identity.side_effect=None
        with mock.patch.object(ci,"parallel_optimization",return_value={}): result=ci.run("optimization")
        self.assertEqual(result["status"],"FAIL"); self.assertIn("omitted",result["diagnostic"])

    def test_matrix_and_performance_keep_all_selectors_sequential(self):
        for lane in ("matrix","performance"):
            selected=ci.suites(lane); caller=threading.get_ident(); calls=[]
            def command(argv,*args,**kwargs):
                self.assertEqual(threading.get_ident(),caller); calls.append(argv[-1]); return self.process()
            with mock.patch.object(ci,"ThreadPoolExecutor",side_effect=AssertionError("unexpected concurrency")), \
                 mock.patch.object(ci.ci,"command",side_effect=command): result=ci.run(lane)
            self.assertEqual(result["status"],"PASS",result); self.assertEqual(calls,selected)


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
                    "P6_CD_CUE_FILE", "P6_CD_CCD_FILE", "P6_CD_NRG_FILE", "P6_CD_ISO_FILE", "P6_CD_WAV_FILE", "P6_LEGACY_UAE_HOST", "P6_WATERBOX_PACKAGE_ZIP"}
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
            manual = {"bridge/own.h": "a" * 64}
            valid = dict(schema_version=1, status="BUILD01_PASS_GUEST_NOT_EXECUTED",
                         sources={name: ci.wb.sha256(root / name) for name in sources},
                         manual_inputs=manual, build_inputs={"manual_inputs": manual},
                         recipe={key: ci.wb.sha256(root / name) for key, name in recipes.items()},
                         guest=dict(path=str(root / "guest.wbx"), sha256=ci.wb.sha256(root / "guest.wbx")))
            receipt = root / "receipt.json"
            stack.enter_context(mock.patch.object(ci, "GUEST_RECEIPT", receipt))
            stack.enter_context(mock.patch.object(ci.ci, "ROOT", root))
            stack.enter_context(mock.patch.object(ci.wb, "puae6_sources", return_value=list(sources)))
            headers = stack.enter_context(mock.patch.object(ci.wb, "puae6_manual_inputs", return_value=manual))
            trees = stack.enter_context(mock.patch.object(ci.wb, "ensure_current_trees"))
            elf = stack.enter_context(mock.patch.object(ci.wb, "validate_elf"))
            tls = stack.enter_context(mock.patch.object(ci.wb, "check_no_compiler_tls"))
            stack.enter_context(mock.patch.object(ci.wb, "read_runtime_receipt", return_value={"runtime_dylib": runtime}))
            runtime_path = stack.enter_context(mock.patch.object(ci.wb, "runtime_receipt_path", return_value=root / "runtime.json"))
            receipt.write_text(json.dumps(valid)); ci.waterbox_inputs()
            trees.assert_called_once_with(root=root)
            elf.assert_called_once_with(root / "guest.wbx"); tls.assert_called_once_with(root / "guest.wbx")
            runtime_path.assert_called_once_with("release")
            for kind in ("schema", "status", "selection", "source", "recipe", "guest", "runtime", "trees",
                         "manual_missing", "manual_receipt", "manual_build_inputs", "manual_header"):
                data = json.loads(json.dumps(valid))
                if kind == "schema": data["schema_version"] = 0
                if kind == "status": data["status"] = "PASS"
                if kind == "selection": data["sources"] = {}
                if kind == "source": data["sources"][sources[0]] = "0" * 64
                if kind == "recipe": data["recipe"]["makefile"] = "0" * 64
                if kind == "guest": data["guest"]["sha256"] = "0" * 64
                if kind == "manual_missing": data.pop("manual_inputs")
                if kind == "manual_receipt": data["manual_inputs"] = {}
                if kind == "manual_build_inputs": data["build_inputs"]["manual_inputs"] = {}
                headers.return_value = {"bridge/own.h": "b" * 64} if kind == "manual_header" else manual
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

    def test_frontend_build_only_cli_covers_all_rows_and_manual_prepare_still_verifies(self):
        from tests.simd import frontend_matrix as frontend
        for action in ('build', 'prepare'):
            for sanitized in (False, True):
                for rows in ([], [next(iter(frontend.ROWS))]):
                    argv = ['frontend_matrix', action, *(['--sanitizers'] if sanitized else []),
                            *[item for row in rows for item in ('--row', row)]]
                    with mock.patch.object(ci.sys, 'argv', argv), mock.patch.object(frontend, 'execute') as execute:
                        frontend.main()
                    actions = ('build', 'verify') if action == 'prepare' else ('build',)
                    self.assertEqual(execute.call_args_list,
                        [mock.call(row, step, sanitized) for row in rows or tuple(frontend.ROWS) for step in actions])
        with mock.patch.object(ci.sys, 'argv', ['frontend_matrix', 'verify']), mock.patch.object(frontend, 'verify') as verify:
            frontend.main()
        verify.assert_called_once_with(False)

    def test_optimization_builds_frontends_once_without_verifying_before_the_existing_selectors(self):
        with mock.patch.object(ci, 'execute') as execute:
            ci.prepare('optimization')
        commands = [list(map(str, call.args[0])) for call in execute.call_args_list]
        frontend = [argv for argv in commands if 'tests.simd.frontend_matrix' in argv]
        self.assertEqual(frontend, [[ci.sys.executable, '-m', 'tests.simd.frontend_matrix', 'build'],
                                    [ci.sys.executable, '-m', 'tests.simd.frontend_matrix', 'build', '--sanitizers']])
        self.assertEqual(commands[-2:], frontend)
        selected = ci.suites('optimization')
        for cls in ('EquivalenceTests', 'SafetyTests'):
            self.assertEqual(selected.count('tests.simd.test_frontend_rgb565.' + cls), 1)

    def test_cpu_campaign_is_prepared_once_before_its_measurements_are_consumed(self):
        with mock.patch.object(ci, "execute") as execute:
            ci.prepare("performance")
        commands = [list(map(str, call.args[0])) for call in execute.call_args_list]
        build = [ci.sys.executable, "tools/bench.py", "build", "--jobs", "2"]
        run = [ci.sys.executable, "tools/bench.py", "run", "--root", str(ci.CPU_PERF_DIR.parent),
               "--id", ci.CPU_PERF_DIR.name, "--compare-cpu-levels", "--force"]
        frontend = [ci.sys.executable, "-m", "tests.simd.frontend_matrix", "prepare"]
        self.assertEqual(commands.count(frontend), 1)
        self.assertLess(commands.index(frontend), commands.index(build))
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
        frontend = [ci.sys.executable, "-m", "tests.simd.frontend_matrix", "prepare"]
        self.assertEqual(sum(call.args[0] == frontend for call in execute.call_args_list), 1)
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
            selected = ci.suites(lane) if lane == "optimization" else ["fixture"]
            with self.subTest(lane=lane), ExitStack() as stack:
                for name in ("candidate_package", "waterbox_inputs", "prepare", "execute", "identity"):
                    stack.enter_context(mock.patch.object(ci, name, return_value={}))
                stack.enter_context(mock.patch.object(ci.ci, "preflight"))
                stack.enter_context(mock.patch.object(ci, "suites", return_value=selected))
                stack.enter_context(mock.patch.object(ci.ci, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps(counts), "")))
                images = stack.enter_context(mock.patch.object(ci, "images", return_value=frozen))
                self.assertEqual("PASS", ci.run(lane)["status"])
                self.assertEqual([mock.call(retroarch=lane == "functional")] * (len(selected)+2), images.call_args_list)
                images.reset_mock()
                images.side_effect = lambda **kw: frozen if images.call_count == 1 else {"fixture-tag": "sha256:changed"}
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
