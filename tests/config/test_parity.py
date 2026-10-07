"""T17b: headless, the libretro core and the BizHawk wrapper use exactly the defaults and values of the option schema.

The adapters are read, never edited: the sources of tests/ are compared with config/schema/options.json (through the table that
tools/generate_options.py makes). A divergence fails with the file, the field and both values, and the way to fix it is a task of its own for
that adapter. The checks read source text on purpose: they must run without a toolchain, in the Waterbox-less and .NET-less environments too.

    python3 -m unittest discover -s tests/config -t .
"""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import generate_options as gen  # noqa: E402

HEADLESS = ROOT / "adapters/headless/main.cpp"
LIBRETRO = ROOT / "adapters/libretro/libretro_core.cpp"
GENERATED = ROOT / "adapters/libretro/options_generated.h"
SETTINGS_CS = ROOT / "integration/bizhawk/core/Puae6/Settings.cs"
CORE_CS = ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs"
ABI_CS = ROOT / "integration/bizhawk/core/Puae6/Abi.cs"
# The members of p6_config that an adapter sets because every config needs them, not because a person chooses them.
FIXED_ASSIGNMENTS = {"abi_version", "struct_size", "machine"}


def read(path):
    return path.read_text(encoding="utf-8")


def camel(text):
    return "".join(part.capitalize() for part in text.split("_"))


class Schema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = gen.load_schema()
        cls.table = json.loads(read(gen.TABLE))
        cls.options = {o["id"]: o for o in cls.schema["options"]}
        cls.reboot = {o["id"]: o for o in cls.schema["options"] if o["class"] == "reboot"}
        header = read(ROOT / "bridge/include/p6.h")
        cls.constants = {m[1]: int(m[2]) for m in re.finditer(r"#define (P6_\w+)\s+UINT32_C\((\d+)\)", header)}

    def default_of(self, name):
        return self.table["p6_config"][self.options[name]["maps_to"].partition(".")[2]]["default"]

    def number(self, option, value_name):
        return next(v["abi"] for v in option["values"] if v["value"] == value_name)


class HeadlessParity(Schema):
    """adapters/headless/main.cpp: --ntsc, --seed S, audio 48000, epoch 2000-01-01."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = read(HEADLESS)

    def test_every_config_field_headless_sets_is_one_of_the_schema_and_each_reboot_option_is_set(self):
        assigned = set(re.findall(r"\bconfig\.(\w+) = ", self.source))
        expected = FIXED_ASSIGNMENTS | {o["maps_to"].partition(".")[2] for o in self.reboot.values()}
        self.assertEqual(assigned, expected, "adapters/headless/main.cpp sets a p6_config field that the schema does not have, or misses one")

    def test_each_option_is_taken_the_way_the_schema_says_with_the_schema_default(self):
        for name, option in self.reboot.items():
            how = option["headless"]
            with self.subTest(option=name, how=how["how"]):
                field = option["maps_to"].partition(".")[2]
                default = self.default_of(name)
                if how["how"] == "fixed":
                    found = re.search(rf"config\.{field} = (\d+);", self.source)
                    self.assertIsNotNone(found, f"headless does not set config.{field} to a literal")
                    self.assertEqual(int(found[1]), default, f"headless fixes {field} at {found[1]}, the schema default is {default}")
                elif how["how"] == "value":
                    local = re.search(rf"uint64_t seed = (\d+);", self.source)
                    self.assertIsNotNone(local, "headless has no local with the default of the value")
                    self.assertEqual(int(local[1]), default, f"headless defaults {name} to {local[1]}, the schema says {default}")
                    self.assertRegex(self.source, rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)')
                    self.assertIn(f"config.{field} = seed;", self.source)
                else:
                    # A flag that selects one value of an enum; the local starts at the schema default.
                    start = re.search(r"uint32_t units = 1, standard = (P6_\w+);", self.source)
                    self.assertEqual(self.constants[start[1]], default, f"headless starts with {start[1]}, the schema default is {default}")
                    flagged = re.search(rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)\) standard = (P6_\w+);', self.source)
                    self.assertIsNotNone(flagged, f"headless has no {how['flag']} that sets the standard")
                    self.assertEqual(self.constants[flagged[1]], self.number(option, how["sets"]),
                                     f"{how['flag']} sets {flagged[1]}, the schema says {how['sets']}")
                    self.assertIn(f"config.{field} = standard;", self.source)

    def test_headless_has_no_flag_for_a_key_the_schema_fixes(self):
        flags = set(re.findall(r'strcmp\(argv\[i\], "(--[\w-]+)"\)', self.source))
        flagged = {o["headless"]["flag"] for o in self.reboot.values() if "flag" in o["headless"]}
        self.assertEqual(flags - {"--units", "--dump"}, flagged, "headless takes a flag for a key the schema does not give it, or lacks one")

    def test_the_audio_rate_headless_cannot_select_is_still_the_schema_default(self):
        # --rate belongs to p6_state_probe (bridge/state_probe.cpp); the schema says headless fixes the audio rate, so a change of the schema
        # default must come with a change of headless, which this test and the one above make visible.
        self.assertEqual(self.reboot["audio_rate_hz"]["headless"], {"how": "fixed"})
        self.assertEqual(self.default_of("audio_rate_hz"), 48000)


class WrapperParity(Schema):
    """integration/bizhawk/core/Puae6: Puae6SyncSettings and the config the core hands to the machine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.settings = read(SETTINGS_CS)
        cls.core = read(CORE_CS)
        cls.abi = read(ABI_CS)
        body = re.search(r"class Puae6SyncSettings\s*\{(.*?)public Puae6SyncSettings Clone", cls.settings, re.S)
        cls.properties = {m[2]: (m[1], m[3]) for m in re.finditer(r"public (\w+) (\w+) \{ get; set; \} = ([^;]+);", body[1])}

    def test_every_sync_setting_is_a_schema_option_or_reserved_in_it(self):
        known = {o["wrapper"]["setting"] for o in self.reboot.values() if o["wrapper"]["how"] == "setting"}
        known |= {entry["wrapper_setting"] for entry in self.schema["reserved"]}
        self.assertEqual(set(self.properties), known, "the wrapper has a sync setting the schema does not know, or lacks one it lists")

    def test_the_settings_have_the_schema_defaults_and_the_enum_has_the_schema_values(self):
        for name, option in self.reboot.items():
            how = option["wrapper"]
            if how["how"] != "setting":
                continue
            with self.subTest(option=name):
                kind, default = self.properties[how["setting"]]
                self.assertEqual(kind, how["type"], f"{how['setting']} is a {kind}, the schema says {how['type']}")
                if option["type"] == "enum":
                    members = re.search(rf"enum {kind} \{{ ([^}}]*) \}}", self.settings)
                    self.assertEqual([m.strip() for m in members[1].split(",")], [v["value"] for v in option["values"]],
                                     f"enum {kind} does not list the schema's values in order")
                    self.assertEqual(default, f"{kind}.{option['default']}", f"{how['setting']} defaults to {default}, the schema to {option['default']}")
                else:
                    self.assertEqual(int(default), option["default"], f"{how['setting']} defaults to {default}, the schema to {option['default']}")

    def test_the_standard_reaches_the_machine_as_the_abi_numbers_of_the_schema(self):
        pal, ntsc = (self.number(self.options["video_standard"], v) for v in ("PAL", "NTSC"))
        self.assertIn(f"const uint VideoPal = {pal}, VideoNtsc = {ntsc};", self.abi)
        self.assertIn("init.Config.VideoStandard = _syncSettings.Region == Puae6Region.NTSC ? P6.VideoNtsc : P6.VideoPal;", self.core)
        self.assertIn(f"const uint MachineA500 = {self.constants['P6_MACHINE_A500']}", self.abi.replace("public ", "").replace("static ", ""))

    def test_the_wrapper_fixes_the_audio_rate_at_the_schema_default_in_the_machine_and_in_the_identity(self):
        default = self.default_of("audio_rate_hz")
        self.assertEqual(self.reboot["audio_rate_hz"]["wrapper"], {"how": "fixed", "where": "audio"})
        found = re.search(r"init\.Config\.AudioRateHz = (\d+);", self.core)
        self.assertIsNotNone(found)
        self.assertEqual(int(found[1]), default, f"the wrapper fixes the audio rate at {found[1]}, the schema default is {default}")
        identity = re.search(r";audio=(\d+)\"", self.core)
        self.assertIsNotNone(identity)
        self.assertEqual(int(identity[1]), default, f"the state identity says audio={identity[1]}, the schema default is {default}")

    def test_the_settings_reach_the_config_and_the_identity(self):
        for name, option in self.reboot.items():
            how = option["wrapper"]
            if how["how"] == "setting":
                field = camel(option["maps_to"].partition(".")[2])
                self.assertRegex(self.core, rf"init\.Config\.{field} = [^;]*_syncSettings\.{how['setting']}", f"{how['setting']} does not reach the config")
        sets = set(re.findall(r"init\.Config\.(\w+) = ", self.core))
        expected = {"AbiVersion", "StructSize", "Machine"} | {camel(o["maps_to"].partition(".")[2]) for o in self.reboot.values()}
        self.assertEqual(sets, expected, "the wrapper sets a config field that the schema does not have, or misses one")


class LibretroParity(Schema):
    """The core reads its options from the generated header and fills p6_config from them, with no literal of its own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.header = read(GENERATED)
        cls.core_source = read(LIBRETRO)

    def test_settings_start_at_the_schema_defaults(self):
        struct = re.search(r"struct Settings \{(.*?)\};", self.header, re.S)[1]
        found = {m[2]: m[3] for m in re.finditer(r"(\w+) (\w+) = (?:U?INT64_C\()?(-?\d+)u?\)?;", struct)}
        for name, option in self.options.items():
            default = option["default"]
            expected = self.number(option, default) if option["type"] == "enum" else default
            self.assertEqual(int(found[name]), expected, f"Settings.{name} starts at {found[name]}, the schema default is {expected}")
        self.assertEqual(set(found), set(self.options))

    def test_the_options_offered_are_the_exposed_ones_with_their_values_and_defaults(self):
        exposed = [o for o in self.schema["options"] if o["libretro"]]
        keys = re.findall(r'^    \{"(puae6_\w+)", (true|false), &Settings::(\w+), (\w+), (\d+), (\d+)\},$', self.header, re.M)
        self.assertEqual([k[0] for k in keys], [o["libretro"]["key"] for o in exposed])
        for (key, reboot, field, _, count, index), option in zip(keys, exposed):
            self.assertEqual((field, reboot == "true", int(count)), (option["id"], option["class"] == "reboot", len(option["values"])), key)
            self.assertEqual(option["values"][int(index)]["value"], option["default"], f"{key}: the default index points at another value")
            # The key is in the v2 definitions, the v1 ones and the legacy strings.
            self.assertEqual(self.header.count(f'"{key}"'), 4, key)
        for option in self.schema["options"]:
            if option["libretro"] is None:
                self.assertEqual(option["id"] in {k[2] for k in keys}, False, f"{option['id']} is not offered by libretro, so it keeps its default")

    def test_every_reboot_option_says_so_in_the_menu_and_a_presentation_one_does_not(self):
        for option in self.schema["options"]:
            if option["libretro"]:
                label = re.search(rf'\{{"{option["libretro"]["key"]}", "([^"]*)"', self.header)[1]
                self.assertEqual(label.endswith("(Restart)"), option["class"] == "reboot", label)

    def test_the_core_fills_p6_config_from_the_options_and_types_no_value_of_its_own(self):
        assigned = set(re.findall(r"\bconfig\.(\w+) = ", self.core_source))
        self.assertEqual(assigned, FIXED_ASSIGNMENTS, "libretro_core.cpp sets a p6_config field by hand: it belongs to the schema")
        self.assertIn("p6_options::to_config(core.settings, &config);", self.core_source)
        for stale in ("kSeed", "kRtcEpoch", "kAudioRateHz", "946684800", "48000"):
            self.assertNotIn(stale, self.core_source, f"libretro_core.cpp still has a literal of its own: {stale}")
        for option in self.reboot.values():
            self.assertIn(f"config->{option['maps_to'].partition('.')[2]} = settings.{option['id']};", self.header)

    def test_the_machine_the_core_runs_follows_the_options_in_av_info_and_region(self):
        self.assertIn("core.settings.video_standard == P6_VIDEO_NTSC", self.core_source)
        self.assertIn("info->timing.sample_rate = core.settings.audio_rate_hz;", self.core_source)


class AdaptersAgree(Schema):
    def test_all_three_adapters_default_to_the_same_normalized_p6_config(self):
        """The one statement the card asks for, in one place: whatever each adapter defaults to, it is the table's p6_config."""
        defaults = {name: entry["default"] for name, entry in self.table["p6_config"].items()}
        headless = read(HEADLESS)
        libretro = {m[1]: m[2] for m in re.finditer(r"(\w+) = (?:U?INT64_C\()?(-?\d+)u?\)?;", re.search(r"struct Settings \{(.*?)\};", read(GENERATED), re.S)[1])}
        wrapper = read(SETTINGS_CS)
        self.assertEqual(defaults, {"video_standard": 1, "audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800})
        for field, value in defaults.items():
            self.assertEqual(int(libretro[field]), value, f"libretro {field}")
        self.assertIn("standard = P6_VIDEO_PAL;", headless)
        self.assertIn("public Puae6Region Region { get; set; } = Puae6Region.PAL;", wrapper)
        self.assertIn(f"RandomSeed {{ get; set; }} = {defaults['random_seed']};", wrapper)
        self.assertIn(f"RtcEpochSeconds {{ get; set; }} = {defaults['rtc_epoch_seconds']};", wrapper)


if __name__ == "__main__":
    unittest.main()
