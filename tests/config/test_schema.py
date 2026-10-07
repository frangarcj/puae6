"""T17b: the option schema and its generator. The generator refuses a malformed schema, its outputs are the committed ones, a configuration
request is checked against the schema with a diagnostic that names the key and the value, and the schema asks for no more than the ABI honours.

    python3 -m unittest discover -s tests/config -t .
"""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import generate_options as gen  # noqa: E402

GENERATOR = ROOT / "tools/generate_options.py"


def schema():
    return copy.deepcopy(gen.load_schema())


def option(document, name):
    return next(o for o in document["options"] if o["id"] == name)


class SchemaValidationTests(unittest.TestCase):
    def refuses(self, document, text):
        with self.assertRaises(gen.SchemaError) as caught:
            gen.validate_schema(document, gen.p6_config_fields())
        self.assertIn(text, str(caught.exception))

    def test_the_committed_schema_is_valid_and_covers_every_p6_config_field(self):
        gen.validate_schema(schema(), gen.p6_config_fields())
        fields = set(gen.p6_config_fields())
        mapped = {o["maps_to"].partition(".")[2] for o in schema()["options"] if o["class"] == "reboot"}
        self.assertEqual(fields - gen.FIXED_CONFIG_FIELDS, mapped)

    def test_an_unknown_field_is_refused(self):
        document = schema()
        option(document, "video_standard")["colour"] = "blue"
        self.refuses(document, "option 'video_standard': unknown field 'colour'")
        document = schema()
        document["extra"] = 1
        self.refuses(document, "schema: unknown field 'extra'")

    def test_a_missing_field_is_refused(self):
        document = schema()
        del option(document, "audio_rate_hz")["maps_to"]
        self.refuses(document, "missing field 'maps_to'")

    def test_duplicate_ids_libretro_keys_and_values_are_refused(self):
        document = schema()
        document["options"].append(copy.deepcopy(option(document, "video_aspect")))
        self.refuses(document, "duplicate option id")
        document = schema()
        option(document, "video_aspect")["libretro"]["key"] = "puae6_video_standard"
        self.refuses(document, "duplicate libretro key 'puae6_video_standard'")
        document = schema()
        option(document, "video_standard")["values"][1]["value"] = "PAL"
        self.refuses(document, "duplicate value 'PAL'")
        document = schema()
        option(document, "video_standard")["values"][1]["abi"] = 1
        self.refuses(document, "duplicate abi value 1")

    def test_a_default_that_is_not_one_of_the_values_is_refused(self):
        document = schema()
        option(document, "video_standard")["default"] = "SECAM"
        self.refuses(document, "default 'SECAM' is not one of the values")
        document = schema()
        option(document, "random_seed")["default"] = -1
        self.refuses(document, "default -1 is not within min 0")

    def test_a_reboot_key_without_a_p6_config_mapping_is_refused(self):
        document = schema()
        option(document, "video_standard")["maps_to"] = "presentation.video_standard"
        self.refuses(document, "a reboot option must map to a p6_config field")
        document = schema()
        option(document, "video_standard")["maps_to"] = "p6_config.cpu_model"
        self.refuses(document, "p6_config has no field 'cpu_model'")
        document = schema()
        option(document, "video_standard")["maps_to"] = "p6_config.machine"
        self.refuses(document, "p6_config.machine has one valid value in ABI v1")
        document = schema()
        option(document, "video_standard")["maps_to"] = "video_standard"
        self.refuses(document, "maps_to must be 'p6_config.FIELD'")

    def test_a_p6_config_field_nothing_maps_to_is_refused(self):
        document = schema()
        document["options"] = [o for o in document["options"] if o["id"] != "random_seed"]
        self.refuses(document, "p6_config.random_seed is not covered")

    def test_two_options_cannot_share_a_field(self):
        document = schema()
        option(document, "audio_rate_hz")["maps_to"] = "p6_config.video_standard"
        self.refuses(document, "already used by option 'video_standard'")

    def test_a_presentation_key_cannot_touch_the_machine(self):
        document = schema()
        option(document, "video_aspect")["maps_to"] = "p6_config.flags"
        self.refuses(document, "a presentation option has no effect on emulation")
        document = schema()
        option(document, "video_aspect")["headless"] = {"how": "fixed"}
        self.refuses(document, "headless 'none' is for presentation options only")

    def test_a_class_that_is_neither_reboot_nor_presentation_is_refused(self):
        document = schema()
        option(document, "video_standard")["class"] = "live"
        self.refuses(document, "class must be 'reboot' or 'presentation'")

    def test_a_key_libretro_does_not_offer_must_say_why(self):
        document = schema()
        del option(document, "random_seed")["libretro_policy"]
        self.refuses(document, "needs a libretro_policy")
        document = schema()
        option(document, "video_standard")["libretro_policy"] = "no reason at all, really."
        self.refuses(document, "libretro_policy is only for a key libretro does not offer")

    def test_libretro_keys_categories_and_numbers_are_checked(self):
        document = schema()
        option(document, "video_aspect")["libretro"]["key"] = "video_aspect"
        self.refuses(document, "libretro key must be 'puae6_'")
        document = schema()
        option(document, "video_aspect")["libretro"]["category"] = "sound"
        self.refuses(document, "libretro category 'sound' is not declared")
        document = schema()
        option(document, "random_seed")["libretro"] = {"key": "puae6_seed", "category": "machine", "label": "Seed", "info": "A seed."}
        del option(document, "random_seed")["libretro_policy"]
        self.refuses(document, "libretro offers enums only")

    def test_text_must_be_plain_ascii(self):
        document = schema()
        option(document, "video_aspect")["libretro"]["label"] = "Relación"
        self.refuses(document, "libretro label must be non-empty ASCII text")

    def test_adapter_blocks_are_checked(self):
        document = schema()
        option(document, "video_standard")["headless"] = {"how": "flag", "flag": "--ntsc", "sets": "SECAM"}
        self.refuses(document, "headless flag sets 'SECAM'")
        document = schema()
        option(document, "random_seed")["wrapper"] = {"how": "setting", "setting": "RandomSeed"}
        self.refuses(document, "wrapper 'setting' takes exactly the fields")

    def test_unsupported_and_reserved_names_cannot_shadow_an_option(self):
        document = schema()
        document["unsupported"].append({"key": "video_standard", "why": "contradiction."})
        self.refuses(document, "'video_standard' is already an option")
        document = schema()
        document["reserved"].append({"id": "random_seed", "for": "T99", "wrapper_setting": "X", "why": "contradiction."})
        self.refuses(document, "id must be new")

    def test_every_problem_is_reported_not_only_the_first(self):
        document = schema()
        option(document, "video_standard")["default"] = "SECAM"
        option(document, "audio_rate_hz")["class"] = "live"
        with self.assertRaises(gen.SchemaError) as caught:
            gen.validate_schema(document, gen.p6_config_fields())
        self.assertGreaterEqual(len(str(caught.exception).splitlines()), 2)


class NormalizationTests(unittest.TestCase):
    def test_nothing_requested_gives_the_defaults(self):
        document = schema()
        normalized = gen.normalize_config(document, {})
        self.assertEqual(normalized, {"video_standard": "PAL", "audio_rate_hz": "48000", "random_seed": 1, "rtc_epoch_seconds": 946684800,
                                      "video_aspect": "pixel"})
        self.assertEqual(gen.to_p6_config(document, normalized),
                         {"video_standard": 1, "audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800})

    def test_values_are_normalized_and_numbers_accept_text(self):
        document = schema()
        normalized = gen.normalize_config(document, {"video_standard": "NTSC", "audio_rate_hz": "44100", "random_seed": "42"})
        self.assertEqual(gen.to_p6_config(document, normalized)["video_standard"], 2)
        self.assertEqual(gen.to_p6_config(document, normalized)["audio_rate_hz"], 44100)
        self.assertEqual(normalized["random_seed"], 42)

    def refuses(self, requested, *texts):
        with self.assertRaises(gen.ConfigError) as caught:
            gen.normalize_config(schema(), requested)
        for text in texts:
            self.assertIn(text, str(caught.exception))

    def test_a_value_outside_the_schema_names_the_key_and_the_value(self):
        self.refuses({"video_standard": "SECAM"}, "option video_standard", "'SECAM'", "allowed: PAL, NTSC")
        self.refuses({"audio_rate_hz": "22050"}, "option audio_rate_hz", "'22050'", "allowed: 44100, 48000")
        self.refuses({"random_seed": -3}, "option random_seed", "-3", "from 0 to")
        self.refuses({"rtc_epoch_seconds": 10 ** 12}, "option rtc_epoch_seconds")

    def test_cpu_ram_rom_and_other_machines_are_refused_with_their_reason(self):
        self.refuses({"machine": "A1200"}, "option machine is not supported: ABI v1 emulates the A500 only")
        self.refuses({"cpu_model": "68020"}, "option cpu_model is not supported: The 68000")
        self.refuses({"chipmem_size": 2048}, "option chipmem_size is not supported")
        self.refuses({"kickstart_rom_file": "/etc/passwd"}, "never chosen by a path")
        self.refuses({"uae_config_file": "a.uae"}, "only the keys of this schema are accepted")

    def test_unknown_and_reserved_keys_are_refused_and_every_problem_is_listed(self):
        self.refuses({"turbo": "yes"}, "option turbo is unknown (supported: video_standard")
        self.refuses({"controller_port1": "Mouse"}, "option controller_port1 is not available yet")
        self.refuses({"cpu_model": "68020", "video_standard": "SECAM", "turbo": 1}, "cpu_model", "SECAM", "turbo")


class AbiTests(unittest.TestCase):
    """The schema asks for nothing the ABI does not honour: its values are the ones the header names and the bridge validates."""

    def test_enum_values_match_the_constants_of_the_abi_header(self):
        header = (ROOT / "bridge/include/p6.h").read_text()
        constants = {m[1]: int(m[2]) for m in re.finditer(r"#define (P6_\w+)\s+UINT32_C\((\d+)\)", header)}
        for entry in schema()["options"]:
            for value in entry.get("values", []):
                if "abi_name" in value:
                    self.assertEqual(constants[value["abi_name"]], value["abi"], f"{entry['id']} {value['value']}")
        self.assertEqual(constants["P6_MACHINE_A500"], 1)

    def test_the_bridge_accepts_exactly_the_values_of_the_schema(self):
        source = (ROOT / "bridge/config.cpp").read_text()
        rates = sorted(int(n) for n in re.findall(r"audio_rate_hz != (\d+)", source))
        self.assertEqual(rates, sorted(v["abi"] for v in option(schema(), "audio_rate_hz")["values"]))
        self.assertIn("c.video_standard != P6_VIDEO_PAL && c.video_standard != P6_VIDEO_NTSC", source)
        self.assertEqual([v["abi_name"] for v in option(schema(), "video_standard")["values"]], ["P6_VIDEO_PAL", "P6_VIDEO_NTSC"])

    def test_the_clock_range_is_the_one_the_bridge_enforces(self):
        civil = (ROOT / "ports/portable/civil_time.h").read_text()
        low = int(re.search(r"P6_CIVIL_MIN_SECONDS INT64_C\((-?\d+)\)", civil)[1])
        high = int(re.search(r"P6_CIVIL_MAX_SECONDS INT64_C\((-?\d+)\)", civil)[1])
        epoch = option(schema(), "rtc_epoch_seconds")
        self.assertEqual((epoch["min"], epoch["max"]), (low, high))


class GeneratorTests(unittest.TestCase):
    def run_generator(self, *arguments):
        return subprocess.run([sys.executable, str(GENERATOR), *map(str, arguments)], capture_output=True, text=True, timeout=120)

    def test_the_committed_generated_files_are_current(self):
        result = self.run_generator("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_stale_generated_file_fails_the_check(self):
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / "options_generated.h"
            header.write_text(gen.HEADER.read_text().replace('"PAL"', '"SECAM"', 1))
            result = self.run_generator("--check", "--header", header, "--table", gen.TABLE)
            self.assertEqual(result.returncode, 1)
            self.assertIn("stale", result.stderr)
            missing = self.run_generator("--check", "--header", Path(directory) / "nothing.h", "--table", gen.TABLE)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("missing", missing.stderr)

    def test_a_changed_schema_makes_the_committed_files_stale_until_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            document = schema()
            option(document, "audio_rate_hz")["default"] = "44100"
            path = Path(directory) / "options.json"
            path.write_text(json.dumps(document))
            stale = self.run_generator("--check", "--schema", path)
            self.assertEqual(stale.returncode, 1, stale.stderr)
            header, table = Path(directory) / "h.h", Path(directory) / "t.json"
            self.assertEqual(self.run_generator("--schema", path, "--header", header, "--table", table).returncode, 0)
            self.assertIn("uint32_t audio_rate_hz = 44100u;", header.read_text())
            self.assertEqual(self.run_generator("--check", "--schema", path, "--header", header, "--table", table).returncode, 0)

    def test_the_generator_refuses_a_malformed_schema_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            document = schema()
            option(document, "video_standard")["default"] = "SECAM"
            path = Path(directory) / "options.json"
            path.write_text(json.dumps(document))
            header = Path(directory) / "h.h"
            result = self.run_generator("--schema", path, "--header", header, "--table", Path(directory) / "t.json")
            self.assertEqual(result.returncode, 1)
            self.assertIn("default 'SECAM' is not one of the values", result.stderr)
            self.assertFalse(header.exists())

    def test_the_outputs_say_they_are_generated_and_are_reproducible(self):
        document = schema()
        self.assertEqual(gen.generate_header(document), gen.generate_header(document))
        self.assertTrue(gen.HEADER.read_text().startswith("// Generated by tools/generate_options.py"))
        self.assertIn("Do not edit by hand", gen.HEADER.read_text().splitlines()[0])
        self.assertIn("generated_by", json.loads(gen.TABLE.read_text()))

    def test_the_table_has_the_normalized_p6_config_that_adapters_must_use(self):
        table = json.loads(gen.TABLE.read_text())
        self.assertEqual({name: entry["default"] for name, entry in table["p6_config"].items()},
                         {"video_standard": 1, "audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800})
        self.assertEqual(table["p6_config"]["video_standard"]["allowed"], [1, 2])
        self.assertEqual(table["p6_config"]["audio_rate_hz"]["allowed"], [44100, 48000])
        self.assertEqual(list(table["presentation"]), ["video_aspect"])


if __name__ == "__main__":
    unittest.main()
