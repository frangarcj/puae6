"""FUZZ01 for the surfaces that exist today: config struct, firmware image, logical names and scenario JSON.

The native targets run in the sanitizer build (build/native-debug, ASan + UBSan, no recover), so a memory error
or undefined behaviour ends the run. Runs are deterministic: same seed and count, same result. The states target
does not exist before T15 and is reported as NOT_IMPLEMENTED by tools/check.py, not skipped here.
"""
import copy
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.step.test_step import builder

sys.path.insert(0, str(ROOT / "tools"))
from scenario_schema import load_json, validate_scenario  # noqa: E402

CORPUS = json.loads((ROOT / "tests/fuzz/corpus/regressions.json").read_text())
ENV = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}


def clean(result):
    return result.returncode == 0 and "runtime error" not in result.stderr and "Sanitizer" not in result.stderr


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class NativeFuzzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        source = (ROOT / "tests/fixtures/microrom/vsync.s").read_text(encoding="utf-8")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-")
        cls.rom = Path(cls.tmp.name) / "vsync.rom"
        cls.rom.write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, target, seed, iterations, extra_env=None):
        args = [target] + ([str(self.rom)] if target in ("firmware", "media") else []) + [str(seed), str(iterations)]
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True, timeout=900,
                              env={**ENV, **(extra_env or {})})

    def test_the_recorded_seeds_run_clean(self):
        for run in CORPUS["runs"]:
            with self.subTest(**{k: run[k] for k in ("target", "seed", "iterations")}):
                result = self.run_target(run["target"], run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-600:])
                self.assertTrue(result.stdout.strip())

    def test_a_run_is_reproducible(self):
        for target in ("config", "blob"):
            first = self.run_target(target, 7, 3000)
            self.assertTrue(clean(first), first.stderr[-400:])
            self.assertEqual(first.stdout, self.run_target(target, 7, 3000).stdout)

    def test_the_config_fuzzer_reaches_accepted_and_rejected_inputs(self):
        result = self.run_target("config", 3, 5000)
        codes = {int(line.split(":")[0].rsplit(" ", 1)[1]) for line in result.stdout.splitlines()}
        self.assertLessEqual({0, 1, 4}, codes)      # OK, BAD_ARGUMENT, UNSUPPORTED

    def test_the_firmware_fuzzer_loads_some_images_and_rejects_most(self):
        result = self.run_target("firmware", 5, 60)
        self.assertTrue(clean(result), result.stderr[-400:])
        text = result.stdout
        self.assertIn("firmware result 0:", text)
        self.assertIn("firmware result 6:", text)

    def test_the_media_fuzzer_loads_valid_lists_and_refuses_the_rest_like_the_oracle(self):
        result = self.run_target("media", 3, 40)
        self.assertTrue(clean(result), result.stderr[-600:])
        text = result.stdout
        self.assertIn("media result 0:", text)           # some lists load
        self.assertIn("media result 6:", text)           # WRONG_MEDIA: size, header or identity
        self.assertIn("media result 2000:", text)        # some events are queued
        self.assertRegex(text, r"media result 200[1-9]")  # and some refused

    def test_the_harness_can_fail(self):
        # With the oracle inverted on purpose the same run must be reported as a failure.
        result = self.run_target("config", 1, 200, {"P6_FUZZ_SELFTEST": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FUZZ FAILURE", result.stderr)


def mutate(value, rng, depth=0):
    """A structural or scalar mutation of a JSON value; always returns a new value."""
    if isinstance(value, dict) and value and rng.random() < 0.7:
        key = rng.choice(sorted(value))
        result = dict(value)
        action = rng.random()
        if action < 0.15:
            del result[key]
        elif action < 0.25:
            result[key + "x"] = result[key]
        else:
            result[key] = mutate(value[key], rng, depth + 1)
        return result
    if isinstance(value, list) and value and rng.random() < 0.7:
        index = rng.randrange(len(value))
        result = list(value)
        action = rng.random()
        if action < 0.2:
            del result[index]
        elif action < 0.4:
            result.insert(index, copy.deepcopy(value[index]))
        else:
            result[index] = mutate(value[index], rng, depth + 1)
        return result
    return rng.choice([None, True, False, 0, 1, -1, 2**63, 2**64, -2**63 - 1, 1.5, float("nan"), "", "0" * 64, "A" * 32,
                       "zz", [], {}, [1], {"a": 1}, 524288, 4294967296, value])


class ScenarioFuzzTests(unittest.TestCase):
    def test_mutated_scenarios_are_accepted_or_rejected_with_a_value_error(self):
        seeds = sorted((ROOT / "tests/scenarios").rglob("*.json"))
        seeds = [p for p in seeds if p.name == "counter68k.json" or p.name.startswith("display-")]
        self.assertGreaterEqual(len(seeds), 2)
        originals = [load_json(p) for p in seeds]
        for run in CORPUS["scenario_runs"]:
            rng = random.Random(run["seed"])
            accepted = rejected = 0
            for iteration in range(run["iterations"]):
                candidate = copy.deepcopy(rng.choice(originals))
                for _ in range(1 + rng.randrange(3)):
                    candidate = mutate(candidate, rng)
                try:
                    validate_scenario(candidate)
                    accepted += 1
                except ValueError:
                    rejected += 1
                except Exception as error:      # anything but a ValueError is a defect of the validator
                    self.fail(f"seed {run['seed']} iteration {iteration}: {type(error).__name__}: {error}")
            self.assertGreater(rejected, run["iterations"] // 2)
            self.assertGreater(accepted, 0)

    def test_broken_json_text_is_rejected_cleanly(self):
        rng = random.Random(11)
        text = (ROOT / "tests/scenarios/counter68k.json").read_bytes()
        with tempfile.TemporaryDirectory(prefix="p6-json-fuzz-") as tmp:
            path = Path(tmp) / "broken.json"
            for _ in range(400):
                data = bytearray(text)
                for _ in range(1 + rng.randrange(4)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
                if rng.random() < 0.3:
                    del data[rng.randrange(len(data)):]
                path.write_bytes(bytes(data))
                try:
                    validate_scenario(load_json(path))
                except (ValueError, UnicodeDecodeError):
                    pass


if __name__ == "__main__":
    unittest.main()
