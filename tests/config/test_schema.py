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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import generate_options as gen  # noqa: E402
from tests.state import envelope  # noqa: E402

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

    def test_a_value_set_is_copied_into_the_options_that_name_it_and_an_unknown_set_is_refused(self):
        document = schema()
        shared = document["value_sets"]["mapper_target"]
        for name in ("mapper_up", "mapper_rr"):
            self.assertEqual(option(document, name)["values"], shared)
            self.assertEqual(option(document, name)["values_from"], "mapper_target")
        option(document, "mapper_up")["values_from"] = "nope"
        self.refuses(document, "values_from must name a value set of the schema")
        raw = json.loads(gen.SCHEMA.read_text())
        next(o for o in raw["options"] if o["id"] == "mapper_up")["values_from"] = "nope"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            path.write_text(json.dumps(raw))
            with self.assertRaises(gen.SchemaError) as caught:
                gen.load_schema(path)
            self.assertIn("names the value set 'nope'", str(caught.exception))

    def test_a_value_set_emits_one_list_and_a_constant_for_each_name_the_adapter_uses(self):
        header = gen.HEADER.read_text()
        self.assertEqual(header.count("constexpr Value kMapperTargetSetValues[] = {"), 1)       # once, not once per mapper option
        self.assertIn("constexpr uint32_t kMapperTargetJoyFire = 6u;", header)
        self.assertIn("constexpr uint32_t kMapperTargetDefault = 0u;", header)
        for macro in re.findall(r"(?m)^#define (P6_SET_\w+)", header):
            self.assertIn(f"#undef {macro}\n", header)                                       # the helper macros are not left defined for the includer
        table = json.loads(gen.TABLE.read_text())
        self.assertEqual(table["presentation"]["mapper_up"]["values_from"], "mapper_target")
        self.assertEqual(table["value_sets"]["mapper_target"]["joy_fire"], 6)
        self.assertNotIn("allowed", table["presentation"]["mapper_up"])

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
        option(document, "video_standard")["maps_to"] = "p6_config.cpu_frequency"
        self.refuses(document, "p6_config has no field 'cpu_frequency'")
        document = schema()
        option(document, "video_standard")["maps_to"] = "p6_config.reserved7"
        self.refuses(document, "p6_config.reserved7 has one valid value in ABI v1")
        document = schema()
        option(document, "video_standard")["maps_to"] = "p6_config.flags"
        self.refuses(document, "maps_to 'p6_config.flags' is already used by option 'video_standard'")
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

    def test_a_headless_choice_names_every_value_of_the_enum_once(self):
        for names in ({"A500": "a500"}, {"A500": "a500", "A600": "a500"}, {"A500": "a500", "A600": "A600"}, {"A500": "a500", "A600": "a600"}, {"A500": "a500", "A600": "a600", "A1200": "a1200", "A4000": "a4000"}, []):
            with self.subTest(names=names):
                document = schema()
                option(document, "machine")["headless"]["names"] = names
                self.refuses(document, "headless 'choice' names every value of an enum once")
        document = schema()
        del option(document, "machine")["headless"]["names"]
        self.refuses(document, "headless 'choice' takes exactly the fields")

    def test_a_named_headless_flag_and_the_wrapper_name_of_a_value_are_checked(self):
        # T73a: Paula's output options. 'named' is a flag with a spelling per value (the numbers are the table of main.cpp), the wrapper names the members of its enum after the value
        # or after wrapper_name when the value is a number.
        for names in ({"none": "none"}, {"none": "none", "anti": "anti", "rh": "rh", "crux": "Crux"}, {"none": "none", "anti": "anti", "rh": "anti", "crux": "crux"}):
            with self.subTest(names=names):
                document = schema()
                option(document, "sound_interpolation")["headless"]["names"] = names
                self.refuses(document, "headless 'named' names every value of an enum once")
        document = schema()
        del option(document, "sound_interpolation")["headless"]["flag"]
        self.refuses(document, "headless 'named' takes exactly the fields")
        for name in ("0P", "", "P 1", 5):
            with self.subTest(wrapper_name=name):
                document = schema()
                option(document, "sound_separation")["values"][0]["wrapper_name"] = name
                self.refuses(document, "wrapper_name must be the C# identifier")

    def test_a_value_the_wrapper_does_not_offer_is_marked_false_and_only_on_an_option_the_wrapper_takes_as_a_setting(self):
        for mark in (True, "no", 0, None):
            with self.subTest(mark=mark):
                document = schema()
                option(document, "machine")["values"][1]["wrapper"] = mark
                self.refuses(document, "wrapper may only be false")
        document = schema()
        option(document, "video_standard")["wrapper"] = {"how": "fixed", "where": "audio"}
        option(document, "video_standard")["values"][1]["wrapper"] = False
        self.refuses(document, "wrapper may only be false")

    def test_a_value_marked_false_is_listed_in_the_table_as_not_offered_by_the_wrapper_and_the_real_schema_marks_none(self):
        # T24f: the wrapper's Machine setting offers the three machines, so no value of the real schema carries the mark; the mechanism stays for a future machine.
        document = schema()
        self.assertEqual([v["value"] for v in option(document, "machine")["values"] if "wrapper" in v], [])
        self.assertNotIn("wrapper_not_offered", json.loads(gen.generate_table(document))["adapters"]["machine"])
        option(document, "machine")["values"][2]["wrapper"] = False
        self.assertEqual(json.loads(gen.generate_table(document))["adapters"]["machine"]["wrapper_not_offered"], ["A1200"])

    def test_every_problem_is_reported_not_only_the_first(self):
        document = schema()
        option(document, "video_standard")["default"] = "SECAM"
        option(document, "audio_rate_hz")["class"] = "live"
        with self.assertRaises(gen.SchemaError) as caught:
            gen.validate_schema(document, gen.p6_config_fields())
        self.assertGreaterEqual(len(str(caught.exception).splitlines()), 2)

    def test_the_automatic_values_only_libretro_offers_are_checked_and_never_reach_the_other_adapters(self):
        # T76: libretro.extra_values (puae6_machine=auto, puae6_video_standard=PAL auto / NTSC auto) have a number of their own and a constant, and a libretro default.
        document = schema()
        machine = option(document, "machine")["libretro"]
        machine["extra_values"][0]["abi"] = 1
        self.refuses(document, "the number 1 is already used by a value of the option")
        document = schema()
        option(document, "machine")["libretro"]["extra_values"][0]["value"] = "A600"
        self.refuses(document, "'A600' is already a value of the option")
        document = schema()
        option(document, "machine")["libretro"]["default"] = "A9000"
        self.refuses(document, "libretro default 'A9000' is not a value of the option or an extra one")
        document = schema()
        del option(document, "machine")["libretro"]["extra_values"][0]["const"]
        self.refuses(document, "missing field 'const'")
        real = schema()
        table = json.loads(gen.generate_table(real))
        self.assertEqual(table["adapters"]["machine"]["libretro_extra_values"], {"auto": 0})
        self.assertEqual(table["adapters"]["video_standard"]["libretro_extra_values"], {"PAL auto": 0, "NTSC auto": 3})
        self.assertEqual([v for v in table["p6_config"]["machine"]["names"]], [v["value"] for v in option(real, "machine")["values"]], "p6_config never sees auto")
        self.assertNotIn("auto", table["p6_config"]["machine"]["names"])
        self.assertNotIn("PAL auto", table["p6_config"]["video_standard"]["names"])
        header = gen.generate_header(real)
        self.assertIn("constexpr uint32_t kMachineAuto = 0u;", header)
        self.assertIn("constexpr uint32_t kVideoStandardNtscAuto = 3u;", header)

    def test_the_selectors_are_checked(self):
        document = schema()
        document["selectors"][0]["values_of"] = "video_aspect"
        self.refuses(document, "values_of must name a reboot enum option")
        document = schema()
        document["selectors"][0]["libretro"]["key"] = "puae6_machine"
        self.refuses(document, "libretro key must be a new 'puae6_' key")
        document = schema()
        document["selectors"][0]["default"] = "A9000"
        self.refuses(document, "default 'A9000' is not one of the values")
        document = schema()
        document["selectors"][2]["values"][1]["abi"] = 1
        self.refuses(document, "a new name, a new integer number and a label")
        document = schema()
        document["selectors"][1]["id"] = "machine"
        self.refuses(document, "id must be a new lowercase identifier")
        document = schema()
        document["selectors"][0]["libretro"]["category"] = "nowhere"
        self.refuses(document, "libretro category 'nowhere' is not declared")
        real = schema()
        self.assertEqual([s["id"] for s in real["selectors"]], ["model_fd", "model_hd", "filename_tags", "kickstart"])
        table = json.loads(gen.generate_table(real))
        self.assertEqual(table["selectors"]["model_hd"]["default"], "A1200")
        self.assertEqual(table["selectors"]["filename_tags"]["values"], {"enabled": 1, "disabled": 0})
        with self.assertRaises(gen.ConfigError) as caught:
            gen.normalize_config(real, {"model_fd": "A600"})
        self.assertIn("option model_fd is unknown", str(caught.exception), "a selector is no option of the schema: headless and the wrapper do not take it")


class NormalizationTests(unittest.TestCase):
    def test_nothing_requested_gives_the_defaults(self):
        document = schema()
        normalized = gen.normalize_config(document, {})
        machine = {"machine": "A500", "video_standard": "PAL", "chip_size": "Auto", "slow_size": "Auto", "fast_size": "None", "fpu_model": "None", "cpu_model": "auto", "cpu_compatibility": "exact", "cpu_multiplier": "auto", "hdf_controller": "uae", "audio_rate_hz": "48000", "random_seed": 1,
                   "rtc_epoch_seconds": 946684800, "floppy_writes": "disabled",
                   "sound_separation": "70", "sound_interpolation": "anti", "sound_filter": "emulated", "sound_filter_type": "auto", "floppy_speed": "100", "floppy_drive_count": "auto", "video_resolution": "hires", "video_line_mode": "double"}
        # The presentation options (T31) are all off by default: the picture, the input and the overlays of the core are the machine's own.
        presentation = {"floppy_multidrive": "disabled", "video_aspect": "pixel", "floppy_save": "off", "hdf_save": "off", "video_crop": "disabled", "video_crop_mode": "both", "video_crop_delay": "enabled",
                        "video_vertical_pos": "auto", "video_horizontal_pos": "auto", "video_gamma": "0", "video_color_depth": "24bit",
                        "analog_mouse": "disabled", "analog_mouse_deadzone": "20", "analog_mouse_speed_left": "1.0", "analog_mouse_speed_right": "1.0",
                        "dpad_mouse_speed": "6", "mouse_speed": "100", "joyport": "joystick", "face_buttons": "disabled", "turbo_fire": "disabled",
                        "turbo_fire_button": "B", "turbo_pulse": "6", "statusbar": "bottom", "statusbar_startup": "disabled", "statusbar_messages": "disabled",
                        "vkbd_theme": "auto", "vkbd_transparency": "25%", "vkbd_dimming": "25%"}
        for name in ("up", "down", "left", "right", "b", "a", "y", "x", "select", "start", "l", "r", "l2", "r2", "l3", "r3", "lu", "ld", "ll", "lr", "ru", "rd", "rl", "rr"):
            presentation["mapper_" + name] = "---"          # every button does what it always did
        presentation["mapper_select"] = "toggle_vkbd"       # except Select, which opens the virtual keyboard (PUAE5's default)
        for name in ("mouse_toggle", "turbo_toggle", "aspect_toggle", "crop_toggle", "reset", "vkbd"):
            presentation["hotkey_" + name] = "---"          # and no key of the keyboard is taken from the Amiga
        presentation["hotkey_statusbar"] = "f12"            # but F12, which the Amiga does not have, shows the statusbar (PUAE5's default), and the bar starts hidden
        self.assertEqual(normalized, {**machine, **presentation})
        self.assertEqual(gen.to_p6_config(document, normalized),
                         {"machine": 1, "video_standard": 1, "chip_kib": 0, "slow_kib": 0, "fast_kib": 0, "fpu_model": 0, "cpu_model": 0, "cpu_compatibility": 0, "cpu_multiplier": 0, "hdf_controller": 0, "audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800, "flags": 0,
                          "sound_separation": 0, "sound_interpolation": 0, "sound_filter": 0, "sound_filter_type": 0, "floppy_speed": 0, "floppy_drives": 0, "video_resolution": 0, "video_line_mode": 0})

    def test_the_presets_are_bundles_of_the_options_and_give_the_memory_of_puae5(self):
        # T49: PUAE5's A1200 has 8 MiB of Fast RAM and its A600 is a 2 MiB Chip + 8 MiB Fast machine. A preset is values of the options, so the ABI has no field for it.
        document = schema()
        self.assertEqual([entry["id"] for entry in document["presets"]], ["a1200_fast8", "a600_puae5"])
        a1200 = gen.to_p6_config(document, gen.apply_preset(document, gen.normalize_config(document, {}), "a1200_fast8"))
        self.assertEqual((a1200["machine"], a1200["chip_kib"], a1200["slow_kib"], a1200["fast_kib"]), (3, 0, 0, 8192))
        a600 = gen.to_p6_config(document, gen.apply_preset(document, gen.normalize_config(document, {"fast_size": "MB1"}), "a600_puae5"))
        self.assertEqual((a600["machine"], a600["chip_kib"], a600["slow_kib"], a600["fast_kib"]), (2, 2048, 0, 8192))
        plain = gen.normalize_config(document, {"machine": "A600"})
        self.assertEqual(gen.apply_preset(document, plain, "none"), plain)
        with self.assertRaises(gen.ConfigError) as caught:
            gen.apply_preset(document, plain, "a4000")
        self.assertIn("none, a1200_fast8, a600_puae5", str(caught.exception))

    def test_a_preset_that_names_a_value_the_option_does_not_have_is_a_schema_error(self):
        document = schema()
        document["presets"][0]["values"]["fast_size"] = "MB16"
        with self.assertRaises(gen.SchemaError) as caught:
            gen.validate_schema(document, gen.p6_config_fields())
        self.assertIn("'MB16' is not a value of fast_size", str(caught.exception))
        document = schema()
        del document["presets"][1]["values"]["machine"]
        with self.assertRaises(gen.SchemaError) as caught:
            gen.validate_schema(document, gen.p6_config_fields())
        self.assertIn("names at least the machine", str(caught.exception))

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

    def test_the_machine_is_a_restart_option_with_the_machines_of_the_abi_and_the_a500_by_default(self):
        document = schema()
        machine = option(document, "machine")
        self.assertEqual((machine["class"], machine["maps_to"], machine["default"]), ("reboot", "p6_config.machine", "A500"))
        self.assertEqual([(v["value"], v["abi"], v["abi_name"]) for v in machine["values"]],
                         [("A500", 1, "P6_MACHINE_A500"), ("A600", 2, "P6_MACHINE_A600"), ("A1200", 3, "P6_MACHINE_A1200"), ("A500OG", 4, "P6_MACHINE_A500OG"),
                          ("A500PLUS", 5, "P6_MACHINE_A500PLUS"), ("A1000", 6, "P6_MACHINE_A1000"), ("A2000OG", 7, "P6_MACHINE_A2000OG"), ("A2000", 8, "P6_MACHINE_A2000")])
        self.assertEqual(gen.to_p6_config(document, gen.normalize_config(document, {"machine": "A600"}))["machine"], 2)
        self.assertEqual(gen.to_p6_config(document, gen.normalize_config(document, {"machine": "A1200"}))["machine"], 3)
        self.assertIn("no PCMCIA slot", machine["libretro"]["info"])      # the description says what the A600 does not have (T56: Gayle has its IDE port only with the IDE controller)
        # T24e: the A1200 is described as what it is (PUAE5's A1200OG, not its A1200 with 8 MiB of Fast RAM), and since T24f the BizHawk wrapper offers it as well.
        self.assertEqual({v["value"]: v["label"] for v in machine["values"]}["A1200"], "A1200 (68EC020, AGA, 2 MiB Chip, no Gayle)")
        self.assertIn("A1200OG", machine["libretro"]["info"])
        self.assertEqual([v["value"] for v in machine["values"] if v.get("wrapper") is False], [])
        self.assertNotIn("wrapper_not_offered", json.loads(gen.generate_table(document))["adapters"]["machine"])
        self.assertNotIn("machine", {entry["key"] for entry in document["unsupported"]})

    def test_the_same_configuration_gives_the_same_config_hash_and_only_another_machine_changes_it(self):
        # The hash the state identity carries (bridge/state.cpp, reproduced by tests/state/envelope.py) over the p6_config the schema produces.
        document = schema()

        def digest(requested):
            fields = gen.to_p6_config(document, gen.normalize_config(document, requested))
            return envelope.config_hash(fields["machine"], fields["video_standard"], fields["audio_rate_hz"], 0, fields["random_seed"],
                                        fields["rtc_epoch_seconds"])

        a500 = digest({})
        self.assertEqual(a500, digest({"machine": "A500"}))                                  # naming the default is the same configuration
        self.assertEqual(a500, digest({"machine": "A500", "video_standard": "PAL", "audio_rate_hz": "48000", "random_seed": "1"}))
        self.assertEqual(a500, envelope.config_hash(1, 1, 48000, 0, 1, 946684800))           # and it is the hash the A500 had before the option existed
        a600 = digest({"machine": "A600"})
        self.assertEqual(a600, digest({"machine": "A600"}))
        self.assertNotEqual(a500, a600)
        a1200 = digest({"machine": "A1200"})
        self.assertEqual(a1200, envelope.config_hash(3, 1, 48000, 0, 1, 946684800))
        self.assertNotIn(a1200, (a500, a600))
        self.assertEqual(a600, envelope.config_hash(2, 1, 48000, 0, 1, 946684800))
        # Nothing but the machine differs between those two: the same other choices, on either machine, are distinct from each other and shared across runs.
        for other in ({"video_standard": "NTSC"}, {"audio_rate_hz": "44100"}, {"random_seed": 7}):
            self.assertNotEqual(digest(other), a500)
            self.assertNotEqual(digest({**other, "machine": "A600"}), a600)
            self.assertNotEqual(digest({**other, "machine": "A600"}), digest(other))

    def test_a_value_outside_the_schema_names_the_key_and_the_value(self):
        self.refuses({"video_standard": "SECAM"}, "option video_standard", "'SECAM'", "allowed: PAL, NTSC")
        self.refuses({"audio_rate_hz": "22050"}, "option audio_rate_hz", "'22050'", "allowed: 44100, 48000")
        self.refuses({"random_seed": -3}, "option random_seed", "-3", "from 0 to")
        self.refuses({"rtc_epoch_seconds": 10 ** 12}, "option rtc_epoch_seconds")

    def test_cpu_ram_rom_and_other_machines_are_refused_with_their_reason(self):
        # T23d, T24e: the machine is an option of three values; any other model (or another spelling) is refused as a value, naming the key and the choices.
        self.refuses({"machine": "A4000"}, "option machine", "'A4000'", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"machine": "a600"}, "option machine", "'a600'", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"machine": "a1200"}, "option machine", "'a1200'", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"machine": "A1200OG"}, "option machine", "'A1200OG'", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"machine": 3}, "option machine", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"machine": 2}, "option machine", "allowed: A500, A600, A1200, A500OG, A500PLUS, A1000, A2000OG, A2000")
        self.refuses({"cpu_speed": "max"}, "option cpu_speed is not supported: The machine runs at its real clock")
        self.refuses({"z3fastmem_size": 64}, "option z3fastmem_size is not supported: Zorro III RAM")
        self.refuses({"fastmem2_size": 8}, "option fastmem2_size is not supported: One Zorro II Fast RAM board")
        self.refuses({"chip_size": "3M"}, "option chip_size", "'3M'", "allowed: Auto, KB512, MB1, MB2")
        self.refuses({"fast_size": "16M"}, "option fast_size", "allowed: None, MB1, MB2, MB4, MB8")
        self.refuses({"slow_size": 512}, "option slow_size", "allowed: Auto, None, KB512, MB1, MB1_5, MB1_75")
        self.refuses({"kickstart_rom_file": "/etc/passwd"}, "never chosen by a path")
        self.refuses({"uae_config_file": "a.uae"}, "only the keys of this schema are accepted")

    def test_a_digit_string_too_long_for_int_is_a_refusal_that_names_the_key_not_a_bare_value_error(self):
        # FUZZ01 (T17): int() raises ValueError past 4300 digits, which escaped without the option; the text is capped before it is converted.
        self.refuses({"random_seed": "9" * 5000}, "option random_seed")
        self.refuses({"random_seed": "0" * 65 + "1"}, "option random_seed")
        self.assertEqual(gen.normalize_config(schema(), {"random_seed": "0" * 63 + "7"})["random_seed"], 7)

    def test_a_request_that_is_not_a_mapping_is_refused(self):
        for requested in (None, [], ["video_standard"], "video_standard", 5):
            with self.subTest(requested=requested), self.assertRaises(gen.ConfigError) as caught:
                gen.normalize_config(schema(), requested)
            self.assertIn("must be a mapping", str(caught.exception))

    def test_unknown_and_reserved_keys_are_refused_and_every_problem_is_listed(self):
        self.refuses({"turbo": "yes"}, "option turbo is unknown (supported: machine, video_standard")
        self.refuses({"controller_port1": "Mouse"}, "option controller_port1 is not supported")
        self.refuses({"cpu_speed": "max", "video_standard": "SECAM", "turbo": 1}, "cpu_speed", "SECAM", "turbo")


class AbiTests(unittest.TestCase):
    """The schema asks for nothing the ABI does not honour: its values are the ones the header names and the bridge validates."""

    def test_enum_values_match_the_constants_of_the_abi_header(self):
        header = (ROOT / "bridge/include/p6.h").read_text()
        constants = {m[1]: int(m[2]) for m in re.finditer(r"#define (P6_\w+)\s+UINT32_C\((\d+)\)", header)}
        for entry in schema()["options"]:
            for value in entry.get("values", []):
                if "abi_name" in value:
                    self.assertEqual(constants[value["abi_name"]], value["abi"], f"{entry['id']} {value['value']}")
        self.assertEqual((constants["P6_MACHINE_A500"], constants["P6_MACHINE_A600"], constants["P6_MACHINE_A1200"]), (1, 2, 3))
        # T50: five more presets, additive: the three above keep their values
        self.assertEqual([constants[n] for n in ("P6_MACHINE_A500OG", "P6_MACHINE_A500PLUS", "P6_MACHINE_A1000", "P6_MACHINE_A2000OG", "P6_MACHINE_A2000")], [4, 5, 6, 7, 8])

    def test_the_bridge_accepts_exactly_the_values_of_the_schema(self):
        source = (ROOT / "bridge/config.cpp").read_text()
        rates = sorted(int(n) for n in re.findall(r"audio_rate_hz != (\d+)", source))
        self.assertEqual(rates, sorted(v["abi"] for v in option(schema(), "audio_rate_hz")["values"]))
        self.assertIn("c.video_standard != P6_VIDEO_PAL && c.video_standard != P6_VIDEO_NTSC", source)
        self.assertIn("if (!find_machine(c.machine))", source)           # T50: the machines the bridge accepts are the rows of its table, one per value of the schema
        names = [v["abi_name"] for v in option(schema(), "machine")["values"]]
        self.assertEqual(names, ["P6_MACHINE_A500", "P6_MACHINE_A600", "P6_MACHINE_A1200", "P6_MACHINE_A500OG", "P6_MACHINE_A500PLUS", "P6_MACHINE_A1000", "P6_MACHINE_A2000OG", "P6_MACHINE_A2000"])
        table = source[source.index("const MachineInfo kMachines[]"):]
        table = table[:table.index("};")]
        self.assertEqual(re.findall(r"\{(P6_MACHINE_\w+),", table), names)
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
                         {"machine": 1, "video_standard": 1, "chip_kib": 0, "slow_kib": 0, "fast_kib": 0, "fpu_model": 0, "cpu_model": 0, "cpu_compatibility": 0, "cpu_multiplier": 0, "hdf_controller": 0, "audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800, "flags": 0,
                          "sound_separation": 0, "sound_interpolation": 0, "sound_filter": 0, "sound_filter_type": 0, "floppy_speed": 0, "floppy_drives": 0, "video_resolution": 0, "video_line_mode": 0})
        self.assertEqual(table["p6_config"]["chip_kib"]["allowed"], [0, 512, 1024, 2048])
        self.assertEqual(table["p6_config"]["slow_kib"]["allowed"], [0, 4294967295, 512, 1024, 1536, 1792])
        self.assertEqual(table["p6_config"]["fast_kib"]["allowed"], [0, 1024, 2048, 4096, 8192])
        self.assertEqual(table["p6_config"]["fpu_model"]["allowed"], [0, 68881, 68882])       # T54
        self.assertEqual(table["p6_config"]["fpu_model"]["names"], {"None": 0, "M68881": 68881, "M68882": 68882})
        self.assertEqual(table["p6_config"]["machine"]["allowed"], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(table["p6_config"]["machine"]["names"], {"A500": 1, "A600": 2, "A1200": 3, "A500OG": 4, "A500PLUS": 5, "A1000": 6, "A2000OG": 7, "A2000": 8})
        self.assertNotIn("wrapper_not_offered", table["adapters"]["machine"])
        self.assertEqual(table["p6_config"]["video_standard"]["allowed"], [1, 2])
        self.assertEqual(table["p6_config"]["audio_rate_hz"]["allowed"], [44100, 48000])
        presentation = [o["id"] for o in schema()["options"] if o["class"] == "presentation"]
        self.assertEqual(list(table["presentation"]), presentation)
        self.assertEqual(presentation[:11], ["floppy_multidrive", "video_aspect", "floppy_save", "hdf_save", "video_crop", "video_crop_mode", "video_crop_delay", "video_vertical_pos",
                                            "video_horizontal_pos", "video_gamma", "video_color_depth"])
        self.assertEqual([name for name, entry in table["presentation"].items() if entry.get("restart")], ["floppy_multidrive", "video_color_depth", "joyport"])


if __name__ == "__main__":
    unittest.main()
