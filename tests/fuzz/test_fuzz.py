"""FUZZ01 for the surfaces that exist today: config struct, firmware image, logical names and scenario JSON.

The native targets run in the sanitizer build (build/native-debug, ASan + UBSan, no recover), so a memory error
or undefined behaviour ends the run. Runs are deterministic: same seed and count, same result. The state target (T15: StateFuzzTests) loads mutated states of a
running image through the public p6_state_load, with the allocator capped so that an allocation sized by a forged length is a report, and checks that every refusal leaves the machine bit for
bit as it was and that the image then runs like a control that never saw the attempt. T17 adds what the libretro adapter takes from the frontend:
ContentFuzzTests (M3U playlists, a lying VFS and option values through the adapter's own headers, built here with the sanitizers and no engine) and
ConfigNormalizeFuzzTests (tools/generate_options.py normalize_config with random keys, values and types).
"""
import copy
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe
from tests.step.test_step import builder

sys.path.insert(0, str(ROOT / "tools"))
import generate_options  # noqa: E402
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


STATE_KINDS = ("truncate", "extend", "flip", "flip-resealed", "run", "extreme", "chunk-header", "header-field", "identity", "swap-chunks", "drop-dup-chunk", "foreign", "null-buffer",
               "length-forged", "manifest-prefix")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class StateFuzzTests(unittest.TestCase):
    """FUZZ01 for the native state: truncated, tampered, extended, spliced and other-build/ROM/config/media states and forged manifest prefixes, loaded into a running image with a disk passed to p6_load, with one appended after it and without one."""

    # Any single allocation above this is a report: the engine's biggest buffers are well under it, and a state of at most ~1.3 MB cannot ask for more honestly.
    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"}

    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/native-debug", True, "p6_fuzz_probe")
        source = (ROOT / "tests/fixtures/microrom/vsync.s").read_text(encoding="utf-8")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-state-")
        cls.rom = Path(cls.tmp.name) / "vsync.rom"
        cls.rom.write_bytes(builder.build(source))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_state(self, seed, iterations, extra_env=None, machine=None):
        return subprocess.run([str(self.exe), "state", str(self.rom), str(seed), str(iterations), *([machine] if machine else [])], capture_output=True, text=True, timeout=3600,
                              env={**self.ENV, **(extra_env or {})})

    @staticmethod
    def kinds(text):
        found = {}
        for line in text.splitlines():
            match = re.match(r"^state kind (\S+): accepted (\d+) refused (\d+)$", line)
            if match:
                found[match[1]] = (int(match[2]), int(match[3]))
        return found

    def test_the_recorded_state_seeds_run_clean_and_reach_every_mutation_and_every_outcome(self):
        for run in CORPUS["state_runs"]:
            with self.subTest(seed=run["seed"], iterations=run["iterations"], machine=run.get("machine")):
                result = self.run_state(run["seed"], run["iterations"], machine=run.get("machine"))      # T23b: the A600 has its own states (1 MiB Chip, an empty clock chunk)
                self.assertTrue(clean(result), result.stderr[-1200:])
                text = result.stdout
                kinds = self.kinds(text)
                self.assertEqual(sorted(STATE_KINDS), sorted(kinds))
                for kind, (accepted, refused) in kinds.items():
                    self.assertGreater(accepted + refused, 0, kind)
                results = {int(m[1]): int(m[2]) for m in re.finditer(r"^state result (\d+): (\d+)$", text, re.M)}
                self.assertLessEqual({0, 1, 9, 10}, set(results), results)         # accepted, null buffer, corrupt, incompatible
                self.assertFalse(set(results) - {0, 1, 9, 10}, f"a result outside the documented set: {results}")
                self.assertRegex(text, r"state unit checks [1-9]\d*, accepted states that ran [1-9]\d*, resyncs [1-9]\d*")

    def test_a_state_run_is_reproducible(self):
        first = self.run_state(5, 30)
        self.assertTrue(clean(first), first.stderr[-600:])
        self.assertEqual(first.stdout, self.run_state(5, 30).stdout)

    def test_the_state_harness_can_fail(self):
        # With the comparison of the machine after a refusal spoiled on purpose, the same run has to be reported as a failure.
        result = self.run_state(1, 60, {"P6_FUZZ_SELFTEST": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FUZZ FAILURE", result.stderr)
        self.assertIn("a refused state changed the machine", result.stderr)


CONTENT_KINDS = ("random-bytes", "valid-flipped", "long-line", "path-length-boundary", "nul", "bom-middle", "cr-only", "bar", "savedisk", "many-entries",
                 "huge-path", "path-forms", "utf8-invalid", "whitespace", "huge-file")
OPTION_KINDS = ("exact", "empty", "padded", "case", "suffix", "prefix", "zeros", "other-value", "non-ascii", "long", "random-bytes", "control",
                "other-options-value", "number-forms", "no-value")


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class ContentFuzzTests(unittest.TestCase):
    """FUZZ01 for the libretro content (T17): the M3U parser, the file reader over a VFS that lies, and the option values, with the adapter's headers
    compiled with ASan and UBSan next to no engine. Any single allocation above 4 MB is a report: a playlist is at most 64 KB."""

    ENV = {**ENV, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=4"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-fuzz-content-")
        cls.exe = Path(cls.tmp.name) / "fuzz_content"
        command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-I" + str(ROOT / "adapters/libretro"), "-I" + str(ROOT / "bridge/include"), "-I" + str(ROOT / "references/libretro-api/include"),
                   str(ROOT / "tests/fuzz/fuzz_content.cpp"), "-o", str(cls.exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if built.returncode:
            raise RuntimeError(built.stderr[-2000:])
        cls.schema = generate_options.load_schema()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_target(self, target, seed, iterations, extra_env=None):
        return subprocess.run([str(self.exe), target, str(seed), str(iterations)], capture_output=True, text=True, timeout=900,
                              env={**self.ENV, **(extra_env or {})})

    @staticmethod
    def histogram(text, prefix):
        found = {}
        for line in text.splitlines():
            match = re.match(rf"^{prefix} kind (\S+): accepted (\d+) refused (\d+)$", line)
            if match:
                found[match[1]] = (int(match[2]), int(match[3]))
        return found

    def test_the_recorded_seeds_run_clean_and_reach_every_mutation_and_outcome(self):
        for run in CORPUS["content_runs"]:
            with self.subTest(**run):
                result = self.run_target(run["target"], run["seed"], run["iterations"])
                self.assertTrue(clean(result), result.stderr[-1200:])
                text = result.stdout
                if run["target"] == "m3u":
                    kinds = self.histogram(text, "m3u")
                    self.assertEqual(sorted(CONTENT_KINDS), sorted(kinds))
                    self.assertGreater(sum(a for a, _ in kinds.values()), 0)
                    self.assertGreater(sum(r for _, r in kinds.values()), 0)
                    for reason in ("a NUL byte is not text", "more than the cap", "the entry has no file name", "the path is too long"):
                        self.assertIn(f'm3u refusal "{reason}"', text)
                elif run["target"] == "vfs":
                    for outcome in ("Ok", "CannotOpen", "TooLarge", "ReadFailed"):
                        self.assertIn(f"vfs result {outcome}:", text)
                    self.assertNotIn("OutOfMemory", text)
                else:
                    kinds = self.histogram(text, "options")
                    self.assertEqual(sorted(OPTION_KINDS), sorted(kinds))
                    self.assertGreater(kinds["exact"][0], 0)
                    self.assertEqual(kinds["exact"][1], 0)          # an exact name is never refused
                    for kind, (accepted, _) in kinds.items():
                        if kind not in ("exact", "no-value", "other-options-value"):
                            self.assertEqual(accepted, 0, kind)      # everything else is refused, whatever the mutation
                    # Only the value names of the schema were ever accepted, and each of them was.
                    allowed = {f"{option['libretro']['key']}={value['value']}" for option in self.schema["options"] if option["libretro"]
                               for value in option["values"]}
                    accepted = {m[1] for m in re.finditer(r"^options accepted (\S+): \d+$", text, re.M)}
                    self.assertEqual(accepted, allowed)

    def test_a_run_is_reproducible(self):
        for target in ("m3u", "options"):
            first = self.run_target(target, 7, 500)
            self.assertTrue(clean(first), first.stderr[-400:])
            self.assertEqual(first.stdout, self.run_target(target, 7, 500).stdout)

    def test_the_content_harness_can_fail(self):
        # With each oracle spoiled on purpose the same run has to be reported as a failure.
        for target in ("m3u", "vfs", "options"):
            with self.subTest(target=target):
                result = self.run_target(target, 1, 200, {"P6_FUZZ_SELFTEST": "1"})
                self.assertEqual(result.returncode, 1)
                self.assertIn("FUZZ FAILURE", result.stderr)


class ConfigNormalizeFuzzTests(unittest.TestCase):
    """FUZZ01 for tools/generate_options.py normalize_config (T17): random keys, values and types are normalized exactly or refused with a ConfigError that
    names every offending key; a partial configuration is never returned and the request is never modified."""

    HOSTILE = [None, True, False, 0, 1, -1, 2, 44100, 48000, 2**32, 2**63, 2**64 - 1, 2**64, -2**63, -2**63 - 1, 253402300799, 253402300800, -62135596800,
               1.0, 1.5, float("nan"), float("inf"), "", " ", "PAL", "pal", "PAL ", " PAL", "NTSC", "pixel", "4:3", "44100", "48000", "048000", "48000x",
               "22050", "-1", "-0", "+5", "5 ", " 5", "5\n", "5_0", "0x10", "1e3", "\u0663", "\uff11\uff12", "9" * 20, "9" * 64, "9" * 65, "9" * 5000,
               "-" + "9" * 70, "0" * 70 + "1", "\x00", "PAL\x00", "\u00e9", [], {}, [1], ["PAL"], ("PAL",), {"a": 1}, b"PAL", b"", object]
    KEYS_OTHER = ["", " ", "video_standard ", "Video_Standard", "machine", "cpu_model", "chipmem_size", "kickstart_rom_file", "uae_config_file", "controller_port1",
                  "turbo", "puae6_video_standard", "\u00e9", "a" * 300, 1, 2.5, None, True, (1, 2), b"video_standard", frozenset()]

    @staticmethod
    def expected(option, raw):
        """The normalized value of `raw`, or None for a refusal, from the schema alone (not from the function under test)."""
        if option["type"] == "enum":
            return raw if type(raw) is str and raw in [v["value"] for v in option["values"]] else None
        if type(raw) is str and 1 <= len(raw.lstrip("-")) <= 64 and len(raw) - len(raw.lstrip("-")) <= 1 and all(c in "0123456789" for c in raw.lstrip("-")):
            raw = int(raw)
        if type(raw) is int and option["min"] <= raw <= option["max"]:
            return raw
        return None

    def test_random_requests_are_normalized_exactly_or_refused_naming_every_bad_key(self):
        schema = generate_options.load_schema()
        options = {o["id"]: o for o in schema["options"]}
        known = list(options) + [u["key"] for u in schema["unsupported"]] + [r["id"] for r in schema["reserved"]]
        for run in CORPUS["config_runs"]:
            rng = random.Random(run["seed"])
            accepted = refused = 0
            for iteration in range(run["iterations"]):
                requested = {}
                for _ in range(rng.randrange(0, 6)):
                    pool = rng.random()
                    key = rng.choice(list(options)) if pool < 0.6 else rng.choice(known) if pool < 0.8 else rng.choice(self.KEYS_OTHER)
                    if isinstance(key, str) and key in options and rng.random() < 0.55:
                        valid = options[key]["values"][rng.randrange(len(options[key]["values"]))]["value"] if options[key]["type"] == "enum" \
                            else rng.choice([options[key]["min"], options[key]["max"], options[key]["default"], str(options[key]["default"]), 42])
                        requested[key] = valid
                    else:
                        requested[key] = rng.choice(self.HOSTILE)
                before = copy.deepcopy(requested)
                bad = [key for key in requested if not (isinstance(key, str) and key in options) or
                       self.expected(options[key], requested[key]) is None]
                try:
                    result = generate_options.normalize_config(schema, requested)
                except generate_options.ConfigError as error:
                    refused += 1
                    self.assertTrue(bad, f"seed {run['seed']} iteration {iteration}: a valid request was refused: {requested!r}: {error}")
                    for key in bad:
                        self.assertIn(f"option {key}", str(error), f"seed {run['seed']} iteration {iteration}: the refusal does not name {key!r}")
                except Exception as error:      # anything but a ConfigError is a defect: it names nothing and may escape a caller that handles ValueError
                    self.fail(f"seed {run['seed']} iteration {iteration}: {type(error).__name__}: {error!s:.200} for {requested!r:.200}")
                else:
                    accepted += 1
                    self.assertFalse(bad, f"seed {run['seed']} iteration {iteration}: accepted {requested!r}")
                    self.assertEqual(list(result), list(options))            # every option, in schema order, never a partial configuration
                    for name, option in options.items():
                        self.assertEqual(result[name], self.expected(option, requested[name]) if name in requested else option["default"])
                    generate_options.to_p6_config(schema, result)            # and the ABI numbers can always be derived
                self.assertEqual(requested, before)
            self.assertGreater(accepted, run["iterations"] // 50)
            self.assertGreater(refused, run["iterations"] // 2)

    def test_a_request_that_is_not_a_dict_is_a_config_error(self):
        schema = generate_options.load_schema()
        for requested in (None, [], ["video_standard"], "video_standard", 7, 1.5, (("video_standard", "PAL"),), object()):
            with self.subTest(requested=type(requested).__name__), self.assertRaises(generate_options.ConfigError):
                generate_options.normalize_config(schema, requested)


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
