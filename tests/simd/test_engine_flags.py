"""T35b: flag fallback and explicit failures; full-engine evidence is opt-in."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.simd import engine_flags as experiment


class ConfigurationTests(unittest.TestCase):
    def test_zero_count_roots_keep_strict_profiles_and_refuse_a_changed_function(self):
        with tempfile.TemporaryDirectory(prefix="p6-pgo-unlinked-") as work:
            root = Path(work)
            (root / "main.cpp").write_text('int main(int n, char**) { return n == 1 ? 0 : 3; }\n')
            (root / "unused.cpp").write_text('int never_linked(int n) { return n * 3; }\n')
            (root / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.21)\nproject(pgo_test LANGUAGES C CXX)\n'
                f'include("{experiment.ROOT}/cmake/Optimization.cmake")\n'
                'add_executable(linked main.cpp unused.cpp)\ntarget_link_options(linked PRIVATE "LINKER:-dead_strip")\n')
            def configure(name, *extra):
                experiment.run(["cmake", "-S", root, "-B", root / name, "-G", "Ninja", "-DCMAKE_C_COMPILER=/usr/bin/clang",
                                "-DCMAKE_CXX_COMPILER=/usr/bin/clang++", "-DCMAKE_BUILD_TYPE=Release", *extra])
            configure("generate", "-DP6_PGO_MODE=GENERATE", "-DP6_PGO_TRAIN_ROOTS=__Z12never_linkedi")
            experiment.run(["cmake", "--build", root / "generate"])
            experiment.run([root / "generate/linked"])
            profiles = sorted((root / "generate/profiles").glob("*.profraw"))
            self.assertTrue(profiles)
            experiment.run(["/usr/bin/xcrun", "llvm-profdata", "merge", *profiles, "-o", root / "training.profdata"])
            configure("use", "-DP6_PGO_MODE=USE", f"-DP6_PGO_DATA={root}/training.profdata")
            result = subprocess.run(["cmake", "--build", str(root / "use")], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('no profile data available', result.stdout + result.stderr)
            experiment.run([root / "use/linked"])
            (root / "main.cpp").write_text('int main(int n, char**) { if (n == 1) return 0; if (n == 2) return 3; return 4; }\n')
            stale = subprocess.run(["cmake", "--build", str(root / "use")], capture_output=True, text=True, timeout=90)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("-Werror", stale.stdout + stale.stderr)
            self.assertIn("-Wprofile-instr-", stale.stdout + stale.stderr)

    def test_defaults_and_invalid_profile_requests(self):
        with tempfile.TemporaryDirectory(prefix="p6-flags-config-") as work:
            root = Path(work)
            (root / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.21)\nproject(flag_test LANGUAGES C CXX)\n'
                f'include("{experiment.ROOT}/cmake/Optimization.cmake")\n'
                'file(WRITE "${CMAKE_BINARY_DIR}/selected.txt" "${P6_NATIVE_IPO}/${P6_PGO_MODE}/${CMAKE_INTERPROCEDURAL_OPTIMIZATION}")\n')
            cases = (([], None), (["-DP6_PGO_MODE=typo"], "must be OFF, GENERATE or USE"),
                     (["-DP6_PGO_MODE=USE"], "existing absolute .profdata"),
                     (["-DP6_NATIVE_IPO=ON", "-DP6_PGO_MODE=GENERATE"], "Evaluate IPO and PGO separately"))
            for index, (flags, error) in enumerate(cases):
                result = subprocess.run(["cmake", "-S", str(root), "-B", str(root / str(index)),
                                         "-DCMAKE_C_COMPILER=/usr/bin/clang", "-DCMAKE_CXX_COMPILER=/usr/bin/clang++", *flags],
                                        text=True, capture_output=True, timeout=90)
                if error:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(error, result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((root / str(index) / "selected.txt").read_text(), "OFF/OFF/")


@unittest.skipUnless(os.environ.get("P6_TEST_ENGINE_FLAGS"), "prepare T35b builds and set P6_TEST_ENGINE_FLAGS=1; absent experiments are BLOCKED")
class EquivalenceTests(unittest.TestCase):
    def receipt(self, variant):
        directory = experiment.WORK / variant
        r = json.loads((directory / "verification.json").read_text())
        self.assertEqual(r["source_identity"], experiment.source_identity())
        self.assertEqual(r["probe_sha256"], experiment.sha(directory / "p6_state_probe"))
        for path, digest in r["harness"].items():
            self.assertEqual(digest, experiment.sha(experiment.ROOT / path), path)
        expected = [f"{e.id}@{n}/{mode}" for e in experiment.corpus.ENTRIES for n in e.points for mode in e.modes]
        self.assertEqual(r["restored"], expected)
        self.assertEqual(set(r["canonical"]["cases"]), {e.id for e in experiment.corpus.ENTRIES})
        self.assertEqual(r["canonical"]["errors"], {})
        commands = json.loads((directory / "compile_commands.json").read_text())
        for command in commands:
            text = command["command"]
            self.assertIn("-fno-fast-math", text)
            self.assertIn("-ffp-contract=off", text)
            self.assertNotIn("-march=native", text)
            if variant.startswith("lto"):
                self.assertIn("-flto", text)
            if variant.startswith("pgo"):
                self.assertIn("-fprofile-instr-use=", text)
            if variant.endswith("-san"):
                self.assertIn("-fsanitize=address,undefined", text)
        return r

    def test_full_engine_and_restore_match_the_unoptimized_flag_fallback(self):
        base = self.receipt("base")["canonical"]["cases"]
        for variant in ("lto", "pgo"):
            candidate = self.receipt(variant)["canonical"]["cases"]
            for name, result in base.items():
                for key in ("hardware", "av", "per_unit", "units", "lines"):
                    self.assertEqual(result[key], candidate[name][key], f"{variant} {name}: {key}")


class SafetyTests(EquivalenceTests):
    def test_full_engine_and_restore_match_the_unoptimized_flag_fallback(self):
        base = self.receipt("base")["canonical"]["cases"]
        for variant in ("lto-san", "pgo-san"):
            candidate = self.receipt(variant)["canonical"]["cases"]
            for name, result in base.items():
                for key in ("hardware", "av", "per_unit", "units", "lines"):
                    self.assertEqual(result[key], candidate[name][key], f"{variant} {name}: {key}")


@unittest.skipUnless(os.environ.get("P6_TEST_ENGINE_FLAGS"), "run the T35b timing experiment first")
class PerformanceTests(unittest.TestCase):
    def test_measurements_follow_equivalence_and_keep_the_same_machine(self):
        from tools.bench import SCENARIOS
        r = json.loads((experiment.WORK / "measurements.json").read_text())
        self.assertEqual(len(r["runs"]), 75)
        for variant, digest in r["verified"].items():
            self.assertEqual(digest, experiment.sha(experiment.WORK / variant / "verification.json"))
            self.assertEqual(r["benchmarks"][variant], experiment.sha(experiment.WORK / variant / "p6_bench_probe"))
        for scenario in (s for s in SCENARIOS if s.kind == "microrom"):
            reference = None
            for variant in ("base", "lto", "pgo"):
                rows = [x for x in r["runs"] if x["variant"] == variant and x["scenario"] == scenario.id]
                self.assertEqual({x["repeat"] for x in rows}, set(range(5)))
                for row in rows:
                    raw = row["raw"]
                    self.assertEqual(raw["warmup_units"], 300)
                    self.assertEqual(raw["units"], 10000 if scenario.primary else 3000)
                    self.assertEqual(len(raw["unit_cpu_ns"]), raw["units"])
                    self.assertTrue(all(t > 0 for t in raw["unit_cpu_ns"]))
                    machine = {k: raw[k] for k in ("master_ticks_at_window_end", "unit_index_at_window_end", "audio_frames", "active", "cadence", "state")}
                    reference = reference or machine
                    self.assertEqual(machine, reference)
            self.assertIn(scenario.id, r["summary"])


@unittest.skipUnless(os.environ.get("P6_TEST_ENGINE_FLAGS"), "T35b decision requires completed experiments")
class DecisionTests(unittest.TestCase):
    def test_the_measured_builds_use_public_training_and_explicit_options(self):
        training = json.loads((experiment.WORK / "training.json").read_text())
        self.assertFalse(training["private_assets"])
        measurements = json.loads((experiment.WORK / "measurements.json").read_text())
        self.assertEqual(set(measurements["verified"]), {"base", "lto", "pgo"})
        adr = (experiment.ROOT / "docs/adr/0004-native-optimizations.md").read_text()
        self.assertIn("Decision de flags del motor (T35b)", adr)
        self.assertIn("desactivadas por defecto", adr)


if __name__ == "__main__":
    unittest.main()
