"""tools/check.py for SAFE01 and FUZZ01 (T26, and FUZZ01 for T15 and T17): suites are real, anything not covered is NOT_IMPLEMENTED."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check  # noqa: E402

COUNTS = dict(tests=3, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)


def completed(counts, code=0):
    return subprocess.CompletedProcess([], code, "noise\n" + json.dumps(counts), "")


class TaskSuiteDispatchTests(unittest.TestCase):
    def test_public_profile_fuzz_is_scoped_to_its_native_oracle(self):
        suites = check.TASK_SUITES[("FUZZ01", "T55c9")]
        self.assertEqual(suites["public_profile_config_oracle"], "tests.fuzz.test_fuzz.ConfigProfileFuzzTests")
        gap = check.dispatch("FUZZ01", task="T55c9", required=("state",))
        self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("FUZZ01", task="T55c9")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(sorted(suites), result["assertions_executed"])
        self.assertEqual(len(suites), runner.call_count)

    def test_the_state_fuzz_target_is_not_implemented_for_t26_and_covered_for_t15(self):
        # T26 predates the native state: asking it for the state target is a gap, reported as such. T15 owns the target.
        result = check.dispatch("FUZZ01", task="T26", required=("state",))
        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertIn("state", result["diagnostic"])
        self.assertEqual(result["assertions_executed"], [])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("state", check.TASK_SUITES[("FUZZ01", "T15")])
        self.assertEqual(check.TASK_SUITES[("FUZZ01", "T15")]["state"], "tests.fuzz.test_fuzz.StateFuzzTests")
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("FUZZ01", task="T15", required=("state",))
        self.assertEqual(result["status"], "PASS")
        self.assertIn("state", result["assertions_executed"])
        self.assertEqual(result["assertions_executed"], sorted(check.TASK_SUITES[("FUZZ01", "T15")]))
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(runner.call_args.args[1], check.TASK_SUITE_ENV)
        # An assertion nobody implemented is still a gap for T15.
        gap = check.dispatch("FUZZ01", task="T15", required=("netplay",))
        self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))

    def test_the_libretro_content_and_option_fuzzers_are_covered_for_t17(self):
        suites = check.TASK_SUITES[("FUZZ01", "T17")]
        self.assertEqual(suites["libretro_content"], "tests.fuzz.test_fuzz.ContentFuzzTests")
        self.assertEqual(suites["normalize_config"], "tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests")
        self.assertTrue(set(check.TASK_SUITES[("FUZZ01", "T15")]) <= set(suites))      # the earlier targets stay in the check
        # T15 and T26 predate the adapter's content: asking them for it is a gap, reported as such.
        for task in ("T15", "T26"):
            gap = check.dispatch("FUZZ01", task=task, required=("libretro_content",))
            self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("FUZZ01", task="T17", required=("libretro_content", "normalize_config"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions_executed"], sorted(suites))
        self.assertEqual(runner.call_count, len(suites))
        self.assertEqual(runner.call_args.args[1], check.TASK_SUITE_ENV)
        gap = check.dispatch("FUZZ01", task="T17", required=("netplay",))
        self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))

    def test_the_hard_file_fuzzer_is_covered_for_t29d_and_the_earlier_tasks_have_a_gap(self):
        suites = check.TASK_SUITES[("FUZZ01", "T29d")]
        self.assertEqual(suites["hard_file"], "tests.fuzz.test_fuzz.HardFileFuzzTests")
        self.assertTrue(set(check.TASK_SUITES[("FUZZ01", "T27")]) <= set(suites))
        gap = check.dispatch("FUZZ01", task="T27", required=("hard_file",))
        self.assertEqual((gap["status"], gap["assertions_executed"]), ("NOT_IMPLEMENTED", []))
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("FUZZ01", task="T29d", required=("hard_file",))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions_executed"], sorted(suites))
        self.assertEqual(runner.call_count, len(suites))

    def test_the_content_fuzzer_with_hard_files_is_covered_for_t63a_and_keeps_every_earlier_target(self):
        suites = check.TASK_SUITES[("FUZZ01", "T63a")]
        self.assertEqual(suites["libretro_content"], "tests.fuzz.test_fuzz.ContentFuzzTests")        # which fuzzes decode_hard_file (the hdz target) since T63a
        self.assertTrue(set(check.TASK_SUITES[("FUZZ01", "T29d")]) <= set(suites))
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("FUZZ01", task="T63a", required=("libretro_content", "hard_file"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions_executed"], sorted(suites))
        self.assertEqual(runner.call_count, len(suites))

    def test_other_tasks_and_profiles_are_not_covered(self):
        for kwargs in ({"task": "T27"}, {"task": "T26", "profile": "other"}):
            self.assertEqual(check.dispatch("SAFE01", **kwargs)["status"], "NOT_IMPLEMENTED")

    def test_all_suites_passing_is_reported_with_the_full_assertion_set(self):
        with mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
            result = check.dispatch("SAFE01", task="T26")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions_executed"], sorted(check.TASK_SUITES[("SAFE01", "T26")]))
        self.assertEqual(result["assertion_set"], result["assertions_executed"])
        self.assertFalse(result["scenario_executed"])
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(runner.call_args.args[1], check.TASK_SUITE_ENV)   # the opt-in sanitizer runs are switched on

    def test_a_failing_skipping_empty_or_truncated_suite_cannot_pass(self):
        cases = [(completed(COUNTS, 1), "FAIL"), (completed(dict(COUNTS, failures=1)), "FAIL"),
                 (completed(dict(COUNTS, tests=0)), "FAIL"), (completed(dict(COUNTS, skipped=1)), "BLOCKED"),
                 (subprocess.CompletedProcess([], 0, "", ""), "FAIL")]
        for process, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                    mock.patch.object(check, "_run_suite", return_value=process):
                result = check.dispatch("FUZZ01", task="T26")
            self.assertEqual(result["status"], expected)
            self.assertNotEqual(result["exit_code"], 0)

    def test_missing_toolchain_is_blocked(self):
        with mock.patch.object(check.shutil, "which", return_value=None):
            self.assertEqual(check.dispatch("SAFE01", task="T26")["status"], "BLOCKED")


class PublicCpuDispatchTests(unittest.TestCase):
    # Exact selectors are the coverage contract, independent of registry labels.
    LATE = {
        "GEN01": {
            "tests.config.test_schema", "tests.config.test_parity",
            "tests.codegen.test_gencpu_models",
        },
        "DET02": {
            "tests.state.test_a4040.A4040Tests", "tests.state.test_z3",
            "tests.state.test_cpu_modes", "tests.host.test_cache040.Cache040Tests",
            "tests.host.test_cpu_late_state.CpuLateStateTests",
            "tests.host.test_internal_fpu_state.InternalFpuStateTests",
            "tests.host.test_frestore040.Frestore040Tests",
            "tests.state.test_state_fpu.FpuLayoutTests",
            "tests.state.test_kickstart_a4040.NativeKickstart040Tests",
            "tests.state.test_kickstart_a4040.NativeKickstart060Tests",
        },
        "WB03": {
            "tests.waterbox-state.test_wrapper_a4040.WrapperA4040Tests",
            "tests.waterbox-state.test_wrapper_z3.WrapperZ3Tests.test_domains_and_same_new_sandbox_and_process_restore",
            "tests.waterbox-state.test_wrapper_z3.WrapperZ3Tests.test_actual_heap_commitment_has_the_existing_two_times_margin",
            "tests.waterbox-state.test_wrapper_machine.WrapperMachineTests.test_wb03_real_a4000_040_060_boot_and_snapshot",
        },
        "LR01": {
            "tests.libretro-host.test_a4040.A4040FrontendTests",
            "tests.libretro-host.test_z3",
            "tests.state.test_kickstart_a4040.LibretroKickstart040Tests",
            "tests.state.test_kickstart_a4040.LibretroKickstart060Tests",
        },
    }
    EARLY = {
        "GEN01": set(),
        "DET02": {"tests.state.test_a4030", "tests.state.test_kickstart_a4030.NativeKickstartTests"},
        "WB03": {"tests.waterbox-state.test_wrapper_machine.WrapperMachineTests.test_wb03_real_a4000_boot_and_snapshot_in_same_new_sandbox_and_process"},
        "LR01": {"tests.state.test_kickstart_a4030.LibretroKickstartTests"},
    }

    def selections(self):
        for task in ("T55c", "T55"):
            for gate in self.LATE:
                yield task, gate, check.TASK_SUITES[(gate, task)]

    def test_public_cpu_gates_have_exact_unique_coverage(self):
        for task, gate, suites in self.selections():
            with self.subTest(task=task, gate=gate):
                expected = self.LATE[gate] | (self.EARLY[gate] if task == "T55" else set())
                self.assertEqual(expected, set(suites.values()))
                self.assertEqual(len(expected), len(suites), "one execution per selector")

    def test_mocked_public_cpu_gates_transport_every_selector_as_harness_only(self):
        for task, gate, suites in self.selections():
            with self.subTest(task=task, gate=gate), \
                    mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                    mock.patch.object(check, "_run_suite", return_value=completed(COUNTS)) as runner:
                result = check.dispatch(gate, task=task)
            self.assertEqual("PASS", result["status"])
            self.assertEqual("HARNESS", result["evidence"])
            self.assertFalse(result["scenario_executed"])
            self.assertIsNone(result["core_build_id"])
            self.assertEqual(sorted(suites), result["assertions_executed"])
            self.assertEqual(sorted(suites), result["assertion_set"])
            self.assertEqual([suites[name] for name in sorted(suites)],
                             [call.args[0] for call in runner.call_args_list])
            self.assertTrue(all(call.args[1] == check.TASK_SUITE_ENV for call in runner.call_args_list))

    def test_public_cpu_failures_skips_empty_or_invalid_results_never_pass(self):
        cases = [(completed(COUNTS, 1), "FAIL"),
                 (completed(dict(COUNTS, failures=1)), "FAIL"),
                 (completed(dict(COUNTS, errors=1)), "FAIL"),
                 (completed(dict(COUNTS, unexpected_successes=1)), "FAIL"),
                 (completed(dict(COUNTS, tests=0)), "FAIL"),
                 (completed(dict(COUNTS, skipped=1)), "BLOCKED"),
                 (completed(dict(COUNTS, expected_failures=1)), "BLOCKED"),
                 (subprocess.CompletedProcess([], 0, "{", ""), "FAIL")]
        for task, gate, _ in self.selections():
            for process, expected in cases:
                with self.subTest(task=task, gate=gate, counts=process.stdout), \
                        mock.patch.object(check.shutil, "which", return_value="/usr/bin/tool"), \
                        mock.patch.object(check, "_run_suite", return_value=process) as runner:
                    result = check.dispatch(gate, task=task)
                self.assertEqual(expected, result["status"])
                self.assertNotEqual(0, result["exit_code"])
                self.assertEqual([], result["assertions_executed"])
                self.assertEqual(1, runner.call_count)

    def test_cu06_denormal_and_unknown_required_coverage_do_not_run_admitted_frames(self):
        with mock.patch.object(check, "_run_suite") as runner:
            result = check.dispatch("STATE01", task="T55c12")
            self.assertEqual("NOT_IMPLEMENTED", result["status"])
            self.assertEqual([], result["assertions_executed"])
            for task, gate, _ in self.selections():
                result = check.dispatch(gate, task=task, required=("cu06_denormal",))
                self.assertEqual("NOT_IMPLEMENTED", result["status"])
                self.assertEqual([], result["assertions_executed"])
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
