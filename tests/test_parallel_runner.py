"""T48d y T48e: selección, preparación, reparto por clase y nivel con nombre del runner paralelo, sin builds nativos."""
from contextlib import nullcontext
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tests.host import run_parallel as runner


class Future:
    def __init__(self, result):
        self.value = result

    def result(self):
        return self.value


class InlinePool:
    events = None

    def __init__(self, max_workers):
        self.max_workers = max_workers
        InlinePool.last_jobs = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, function, *args):
        self.events.append(("worker", args))
        return Future(function(*args))


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p6-parallel-runner-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        self.build.mkdir()
        for suite, names in (("host", ("alpha", "beta", "bridge_lifecycle", "bridge_memory")),
                             ("state", ("alpha", "beta", "components", "envelope", "envelope_native", "probe"))):
            folder = self.root / "tests" / suite
            folder.mkdir(parents=True)
            for name in names:
                (folder / f"test_{name}.py").write_text("# selected by runner test\n")
        self.root_patch = patch.object(runner, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_selection_defaults_deduplicates_and_rejects_other_names(self):
        self.assertEqual(["test_alpha", "test_beta", "test_components", "test_envelope",
                          "test_envelope_native", "test_probe"], runner.select_modules("state", []))
        self.assertEqual(["test_alpha", "test_beta"], runner.select_modules(
            "state", ["test_alpha", "tests.state.test_beta", "test_alpha"]))
        for invalid in ("tests.host.test_alpha", "test_missing"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                runner.select_modules("state", [invalid])

    def test_shared_targets_union_host_and_state_requirements(self):
        host = runner.shared_targets(["test_bridge_lifecycle", "test_bridge_memory", "test_bridge_lifecycle"])
        self.assertEqual({"p6_lifecycle_probe", "p6_memory_probe"}, set(host))
        self.assertEqual([(self.root / "build/link-audit", host, False)],
                         runner.preparations("host", ["test_bridge_lifecycle", "test_bridge_memory"]))
        self.assertEqual([], runner.preparations("host", ["test_alpha"]))

    def prepared(self, modules, variant=None):
        with patch.dict(os.environ, {"P6_STATE_PROBE_BUILD": variant} if variant else {}):
            if not variant:
                os.environ.pop("P6_STATE_PROBE_BUILD", None)
            return {directory.name: (targets, optimized) for directory, targets, optimized in runner.preparations("state", modules)}

    def test_state_probe_goes_to_the_optimized_tree_unless_the_environment_says_debug(self):
        with patch("tests.build_support.ROOT", self.root):
            self.assertEqual({"state-opt": (["p6_state_probe"], True)}, self.prepared(["test_alpha", "test_components"]))
            self.assertEqual({"link-audit": (["p6_state_probe"], False)}, self.prepared(["test_alpha"], "debug"))
            with self.assertRaisesRegex(ValueError, "P6_STATE_PROBE_BUILD"):
                self.prepared(["test_alpha"], "release")

    def test_state_modules_that_name_their_own_tree_and_the_parity_test_get_theirs(self):
        with patch("tests.build_support.ROOT", self.root):
            self.assertEqual({}, self.prepared(["test_components", "test_envelope"]))
            self.assertEqual({"link-audit": (["p6_state_probe"], False)}, self.prepared(["test_probe"]))
            self.assertEqual({"state-opt": (["p6_state_probe"], True), "link-audit": (["p6_state_probe"], False)},
                             self.prepared(["test_alpha", "test_probe"]))
            self.assertEqual({"link-audit": (["p6_envelope_probe", "p6_archive_probe"], False)}, self.prepared(["test_envelope_native"]))
            both = self.prepared(["test_build_parity", "test_envelope_native"])
            self.assertEqual((["p6_state_probe"], True), both["state-opt"])
            self.assertEqual((["p6_state_probe", "p6_envelope_probe", "p6_archive_probe"], False), both["link-audit"])
            # Alone, the parity test asks for both builds, and in debug mode they are the same tree (nothing is built twice).
            self.assertEqual({"state-opt": (["p6_state_probe"], True), "link-audit": (["p6_state_probe"], False)}, self.prepared(["test_build_parity"]))

    def test_default_jobs_come_from_cpus_and_memory(self):
        gib = 1 << 30
        self.assertEqual(6, runner.default_jobs("host", cpus=8))
        self.assertEqual(7, runner.default_jobs("state", cpus=8, memory=16 * gib))
        self.assertEqual(3, runner.default_jobs("state", cpus=8, memory=3 * gib))
        self.assertEqual(1, runner.default_jobs("state", cpus=1, memory=16 * gib))
        self.assertEqual(1, runner.default_jobs("state", cpus=8, memory=gib // 4))
        with patch.object(runner.os, "sysconf", side_effect=ValueError):
            self.assertEqual(3, runner.default_jobs("state", cpus=4))

    def test_split_jobs_splits_only_modules_with_several_classes(self):
        classes = {"test_alpha": {"One": 3, "Two": 4}, "test_beta": {"Only": 5}}
        self.assertEqual([("test_alpha", "test_alpha.One", 3), ("test_alpha", "test_alpha.Two", 4), ("test_beta", "test_beta", 5),
                          ("test_components", "test_components", None)],
                         runner.split_jobs(["test_alpha", "test_beta", "test_components"], classes))

    def test_discovery_keeps_module_names_and_gives_up_quietly(self):
        found = Mock(stdout='{"tests.state.test_alpha": {"One": 1, "Two": 2}}')
        with patch.object(runner.subprocess, "run", return_value=found) as command:
            self.assertEqual({"test_alpha": {"One": 1, "Two": 2}}, runner.discover_classes(["tests.state.test_alpha"]))
        self.assertEqual("tests.state.test_alpha", command.call_args.args[0][-1])
        for broken in (Mock(stdout="not json"), OSError("no python")):
            with patch.object(runner.subprocess, "run", **({"side_effect": broken} if isinstance(broken, OSError) else {"return_value": broken})):
                self.assertEqual({}, runner.discover_classes(["tests.state.test_alpha"]))

    def test_the_iteration_tier_is_a_named_selection_of_the_state_suite(self):
        self.assertEqual(("test_state_container", "test_state_upstream", "test_det02", "test_det02_kickstart", "test_state_bridge"),
                         runner.TIERS["iter"])
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        stack[0] = patch.object(sys, "argv", ["runner", "--suite", "state", "--tier", "iter", "-j", "1"])
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch.object(runner, "TIERS", {"iter": ("test_alpha", "test_beta")}), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, runner.main())
        self.assertEqual(["tests.state.test_alpha", "tests.state.test_beta"], sorted(event[1][0] for event in events if event[0] == "worker"))
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        stack[0] = patch.object(sys, "argv", ["runner", "--tier", "iter"])
        with stack[0], patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            runner.main()

    def _main_mocks(self, worker_result, classes=None):
        events = []
        InlinePool.events = events
        prepared = type("Prepared", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        def generator(*args, **kwargs):
            events.append(("generate", args[0]))
            return prepared

        def build(*args, **kwargs):
            events.append(("build", tuple(args[1])))
            return {}

        def receipt(path):
            events.append(("receipt", path))

        def worker(name, timeout, path):
            return (name, *worker_result)

        stack = [patch.object(sys, "argv", ["runner", "--suite", "state", "-j", "1", "test_alpha"]),
                 patch.object(runner, "build_lock", nullcontext),
                 patch.object(runner.subprocess, "run", side_effect=generator),
                 patch.object(runner, "build_targets", side_effect=build),
                 patch.object(runner, "write_receipt", side_effect=receipt),
                 patch.object(runner, "run_module", side_effect=worker),
                 patch.object(runner, "ThreadPoolExecutor", InlinePool),
                 patch.object(runner, "as_completed", side_effect=lambda futures: futures),
                 patch.dict(os.environ, {"P6_TEST_BUILD_RECEIPT": "stale"})]
        discovery = patch.object(runner, "discover_classes", return_value=classes or {})
        discovery.start(); self.addCleanup(discovery.stop)
        return events, stack

    def test_state_main_prepares_once_before_workers_and_passes_qualified_name(self):
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stdout", new=io.StringIO()):
            result = runner.main()
        self.assertEqual(0, result)
        self.assertEqual("tests.state.test_alpha", events[-1][1][0])
        self.assertEqual(("p6_state_probe",), next(event[1] for event in events if event[0] == "build"))
        kinds = [event[0] for event in events]
        self.assertLess(kinds.index("build"), kinds.index("receipt"))
        self.assertLess(kinds.index("receipt"), kinds.index("worker"))
        self.assertEqual(1, kinds.count("generate"))

    def test_bad_selection_and_preparation_failure_start_no_workers(self):
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        stack[0] = patch.object(sys, "argv", ["runner", "--suite", "state", "tests.host.test_alpha"])
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            self.assertNotEqual(0, runner.main())
        self.assertEqual([], events)

        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        failed = type("Prepared", (), {"returncode": 1, "stdout": "bad", "stderr": "prepare failed"})()
        stack[2] = patch.object(runner.subprocess, "run", return_value=failed)
        with stack[0], stack[1], stack[2] as generator, stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            self.assertNotEqual(0, runner.main())
        self.assertEqual([], events)
        generator.assert_called_once()

    def test_worker_failure_fails_suite_and_state_module_uses_worker_receipt(self):
        events, stack = self._main_mocks((1, 0.01, "Ran 1 test in 0.01s\nFAILED\n"))
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(1, runner.main())
        self.assertEqual("tests.state.test_alpha", events[-1][1][0])

        process = type("Process", (), {"returncode": 0, "pid": 4321,
                                       "communicate": lambda self, timeout: ("ok", "")})()
        with patch.object(runner.subprocess, "Popen", return_value=process) as command, \
                patch.dict(os.environ, {}, clear=False):
            result = runner.run_module("tests.state.test_alpha", 12, "receipt.json")
        argv, kwargs = command.call_args
        self.assertEqual("tests.state.test_alpha", argv[0][-1])
        self.assertEqual("1", kwargs["env"]["P6_TESTS_GENERATED"])
        self.assertEqual("receipt.json", kwargs["env"]["P6_TEST_BUILD_RECEIPT"])
        if os.name != "nt":
            self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(0, result[1])

    def test_state_defaults_and_build_error_precede_workers(self):
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        stack[0] = patch.object(sys, "argv", ["runner", "--suite", "state", "test_alpha"])
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, runner.main())
        self.assertEqual(runner.default_jobs("state"), InlinePool.last_jobs)
        self.assertEqual(7200, events[-1][1][1])

        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        stack[3] = patch.object(runner, "build_targets", side_effect=RuntimeError("bad build"))
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], \
                patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(1, runner.main())
        self.assertNotIn("worker", [event[0] for event in events])

    def test_discovery_is_asked_for_the_qualified_names_of_the_selected_modules(self):
        events, stack = self._main_mocks((0, 0.01, "Ran 1 test in 0.01s\nOK\n"))
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], patch("sys.stdout", new=io.StringIO()):
            runner.main()
            runner.discover_classes.assert_called_once_with(["tests.state.test_alpha"])

    def run_split(self, outputs, classes):
        """main() over test_alpha and test_beta with `classes` discovered; `outputs` maps the unit a worker runs to (code, seconds, text)."""
        events, stack = self._main_mocks((0, 0, ""), classes)
        stack[0] = patch.object(sys, "argv", ["runner", "--suite", "state", "-j", "2", "test_alpha", "test_beta"])
        stack[5] = patch.object(runner, "run_module", side_effect=lambda name, timeout, path: (name, *outputs[name]))
        printed = io.StringIO()
        with stack[0], stack[1], stack[2], stack[3], stack[4], stack[5], stack[6], stack[7], stack[8], patch("sys.stdout", new=printed):
            code = runner.main()
        return code, events, printed.getvalue()

    def test_state_modules_with_several_classes_run_per_class_and_are_reported_per_module(self):
        done = lambda tests, extra="": (0, 1.5, f"Ran {tests} tests in 1s\nOK{extra}\n")
        code, events, text = self.run_split({
            "tests.state.test_alpha.One": done(3), "tests.state.test_alpha.Two": done(4, " (skipped=1, expected failures=2)"),
            "tests.state.test_beta": done(5, " (expected failures=1)")}, {"test_alpha": {"One": 3, "Two": 4}, "test_beta": {"Only": 5}})
        self.assertEqual(0, code)
        self.assertEqual(["tests.state.test_alpha.One", "tests.state.test_alpha.Two", "tests.state.test_beta"],
                         sorted(event[1][0] for event in events if event[0] == "worker"))
        self.assertIn("2 modules, 12 tests, 1 skipped, 3 expected failures, 0 failed", text)
        self.assertIn("3.0s  test_alpha  (7 tests, 1 skipped, 2 expected failures, 2 classes)", text)
        self.assertIn("1.5s  test_beta  (5 tests, 0 skipped, 1 expected failures)", text)

    def test_a_split_that_runs_other_tests_than_the_loader_found_fails(self):
        code, _, text = self.run_split({"tests.state.test_alpha.One": (0, 1, "Ran 3 tests in 1s\nOK\n"),
                                        "tests.state.test_alpha.Two": (0, 1, "Ran 3 tests in 1s\nOK\n"),
                                        "tests.state.test_beta": (0, 1, "Ran 5 tests in 1s\nOK\n")},
                                       {"test_alpha": {"One": 3, "Two": 4}, "test_beta": {"Only": 5}})
        self.assertEqual(1, code)
        self.assertIn("Ran 3 tests where the loader found 4", text)

    def test_a_failed_class_fails_its_module_and_a_timeout_is_counted(self):
        code, _, text = self.run_split({"tests.state.test_alpha.One": (0, 1, "Ran 3 tests in 1s\nOK\n"),
                                        "tests.state.test_alpha.Two": (124, 9, "timeout after 9s\nRan 4 tests in 9s\n"),
                                        "tests.state.test_beta": (0, 1, "Ran 5 tests in 1s\nOK\n")},
                                       {"test_alpha": {"One": 3, "Two": 4}, "test_beta": {"Only": 5}})
        self.assertEqual(1, code)
        self.assertIn("1 failed (1 timeouts)", text)
        self.assertRegex(text, r"FAIL\s+10\.0s  test_alpha")

    def test_the_slowest_unit_of_the_last_run_starts_first(self):
        (self.build / ".test-durations.json").write_text('{"test_beta": 90, "test_alpha.Two": 5}')
        code, events, _ = self.run_split({"tests.state.test_alpha.One": (0, 1, "Ran 3 tests in 1s\nOK\n"),
                                          "tests.state.test_alpha.Two": (0, 1, "Ran 4 tests in 1s\nOK\n"),
                                          "tests.state.test_beta": (0, 1, "Ran 5 tests in 1s\nOK\n")},
                                         {"test_alpha": {"One": 3, "Two": 4}, "test_beta": {"Only": 5}})
        self.assertEqual(["tests.state.test_beta", "tests.state.test_alpha.Two", "tests.state.test_alpha.One"],
                         [event[1][0] for event in events if event[0] == "worker"])
        self.assertEqual(1.0, runner.load_history(self.root)["test_beta"])       # and the run leaves its own times for the next one

    @unittest.skipIf(os.name == "nt", "la limpieza del grupo se prueba aquí en POSIX")
    def test_worker_timeout_kills_process_group_and_drains_output(self):
        process = type("Process", (), {"returncode": None, "pid": 4321})()
        timeout = subprocess.TimeoutExpired("worker", 2, output="parcial", stderr="error parcial")
        process.communicate = Mock(side_effect=[timeout, ("salida completa", "error completo")])
        with patch.object(runner.subprocess, "Popen", return_value=process) as command, \
                patch.object(runner.os, "killpg") as killpg:
            name, code, _, output = runner.run_module("tests.state.test_alpha", 2, "receipt.json")
        self.assertEqual(("tests.state.test_alpha", 124), (name, code))
        self.assertIn("timeout", output.lower())
        self.assertIn("salida completa", output)
        self.assertIn("error completo", output)
        killpg.assert_called_once_with(4321, signal.SIGKILL)
        self.assertEqual(2, process.communicate.call_count)
        self.assertTrue(command.call_args.kwargs["start_new_session"])

    @unittest.skipIf(os.name == "nt", "requiere grupos POSIX")
    def test_timeout_stops_real_worker_child(self):
        marker = self.root / "orphan.txt"
        child = f"import time; from pathlib import Path; time.sleep(2.5); Path({str(marker)!r}).write_text('orphan')"
        actor = ("import subprocess, sys, time; "
                 f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                 "print('child ready', flush=True); time.sleep(10)")
        popen = subprocess.Popen
        processes = []

        def start_actor(argv, **kwargs):
            process = popen([sys.executable, "-c", actor], **kwargs)
            processes.append(process)
            return process

        try:
            with patch.object(runner.subprocess, "Popen", side_effect=start_actor):
                _, code, _, output = runner.run_module("tests.state.test_alpha", 1, "receipt.json")
            self.assertEqual(124, code)
            self.assertIn("child ready", output)
            time.sleep(3)
            self.assertFalse(marker.exists(), "el hijo sobrevivió al timeout")
        finally:
            for process in processes:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def test_windows_tree_cleanup_error_is_a_reported_failure(self):
        process = Mock(returncode=None, pid=4321)
        process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 1),
                                          subprocess.TimeoutExpired("worker pipes", 30, output=b"partial")]
        with patch.object(runner.os, "name", "nt"), \
                patch.object(runner.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch.object(runner.subprocess, "Popen", return_value=process), \
                patch.object(runner.subprocess, "run", side_effect=OSError("taskkill unavailable")) as stop:
            _, code, _, output = runner.run_module("tests.state.test_alpha", 1, "receipt.json")
        self.assertEqual(124, code)
        self.assertIn("partial", output)
        self.assertIn("children may still be active", output)
        stop.assert_called_once()
        process.kill.assert_called_once()
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
