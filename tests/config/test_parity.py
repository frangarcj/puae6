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
FIRMWARE = ROOT / "adapters/libretro/firmware.h"
PUAE5 = ROOT / "references/machawk-uae/libretro/libretro-core.h"
SETTINGS_CS = ROOT / "integration/bizhawk/core/Puae6/Settings.cs"
CORE_CS = ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs"
ABI_CS = ROOT / "integration/bizhawk/core/Puae6/Abi.cs"
# The members of p6_config that an adapter sets because every config needs them, not because a person chooses them.
FIXED_ASSIGNMENTS = {"abi_version", "struct_size"}


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
    """adapters/headless/main.cpp: --machine a500|a600|a1200, --ntsc, --seed S, audio 48000, epoch 2000-01-01."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = read(HEADLESS)

    def test_every_config_field_headless_sets_is_one_of_the_schema_and_each_reboot_option_is_set(self):
        assigned = set(re.findall(r"\bconfig\.(\w+) = ", self.source))
        expected = FIXED_ASSIGNMENTS | {o["maps_to"].partition(".")[2] for o in self.reboot.values() if o["headless"]["how"] != "profile"}
        self.assertEqual(assigned, expected, "adapters/headless/main.cpp sets a p6_config field that the schema does not have, or misses one")

    def test_each_option_is_taken_the_way_the_schema_says_with_the_schema_default(self):
        for name, option in self.reboot.items():
            how = option["headless"]
            with self.subTest(option=name, how=how["how"]):
                field = option["maps_to"].partition(".")[2]
                default = self.default_of(name)
                if how["how"] == "profile":
                    # An option the adapter does not offer: the field stays zero, which is the profile's own setting (the default of the option is its ABI number 0).
                    self.assertEqual(default, 0, f"{name}: the profile's setting is the ABI zero")
                    self.assertNotRegex(self.source, rf"config\.{field}\b")
                elif how["how"] == "named":
                    # T73a: Paula's output. A flag takes one spelling per value of the enum; the table `NAME_names` of main.cpp gives each spelling its p6_config number, the local starts at the
                    # schema default (the ABI zero of the profile) and p6_config gets the local.
                    self.assertEqual(default, 0, f"{name}: the schema default is not the 0 that headless starts with")
                    self.assertRegex(self.source, rf"\b{field} = 0[,;]")
                    self.assertRegex(self.source, rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)')
                    table = re.search(rf"static const Named {field}_names\[\] = \{{(.*?)\}};", self.source, re.S)
                    self.assertIsNotNone(table, f"headless has no table {field}_names")
                    spellings = {m[1]: int(m[2]) for m in re.finditer(r'\{"([^"]+)", (\d+)\}', table[1])}
                    self.assertEqual(spellings, {spelling: self.number(option, value) for value, spelling in how["names"].items()},
                                     f"{how['flag']} does not take the spellings of the schema, or maps one to another number")
                    self.assertIn(f"pick_named({field}_names, argv[++i], &{field})", self.source)
                    self.assertIn(f"config.{field} = {field};", self.source)
                elif how["how"] == "fixed":
                    found = re.search(rf"config\.{field} = (\d+);", self.source)
                    self.assertIsNotNone(found, f"headless does not set config.{field} to a literal")
                    self.assertEqual(int(found[1]), default, f"headless fixes {field} at {found[1]}, the schema default is {default}")
                elif how["how"] == "choice":
                    # A flag that names one value of an enum by a spelling of its own (--machine a600); the local starts at the schema default.
                    start = re.search(rf"\b{field} = (P6_\w+)[,;]", self.source)
                    self.assertIsNotNone(start, f"headless has no local {field} with the default of the option")
                    self.assertEqual(self.constants[start[1]], default, f"headless starts with {start[1]}, the schema default is {default}")
                    self.assertRegex(self.source, rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)')
                    spellings = {m[1]: self.constants[m[2]] for m in re.finditer(rf'strcmp\(name, "(\w+)"\)\) {field} = (P6_\w+);', self.source)}
                    self.assertEqual(spellings, {spelling: self.number(option, value) for value, spelling in how["names"].items()},
                                     f"{how['flag']} does not take the spellings of the schema, or maps one to another value")
                    self.assertIn(f"config.{field} = {field};", self.source)
                elif how["how"] == "kib":
                    # T49: the memory sizes: --chip KIB, --slow KIB|none, --fast KIB. The locals start at 0 (the machine's own memory, or no Fast RAM), which is each default.
                    self.assertEqual(default, 0, f"{name}: the schema default is not the 0 that headless starts with")
                    self.assertRegex(self.source, rf"\b{field} = 0[,;]")
                    self.assertRegex(self.source, rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)')
                    self.assertIn(f"config.{field} = {field};", self.source)
                elif how["how"] == "value":
                    local = re.search(rf"uint64_t seed = (\d+)[,;]", self.source)
                    self.assertIsNotNone(local, "headless has no local with the default of the value")
                    self.assertEqual(int(local[1]), default, f"headless defaults {name} to {local[1]}, the schema says {default}")
                    self.assertRegex(self.source, rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)')
                    self.assertIn(f"config.{field} = seed;", self.source)
                else:
                    # A flag that selects one value of an enum; the local starts at the schema default.
                    start = re.search(r"uint32_t units = 1, standard = (P6_\w+)[,;]", self.source)
                    self.assertEqual(self.constants[start[1]], default, f"headless starts with {start[1]}, the schema default is {default}")
                    flagged = re.search(rf'strcmp\(argv\[i\], "{re.escape(how["flag"])}"\)\) standard = (P6_\w+);', self.source)
                    self.assertIsNotNone(flagged, f"headless has no {how['flag']} that sets the standard")
                    self.assertEqual(self.constants[flagged[1]], self.number(option, how["sets"]),
                                     f"{how['flag']} sets {flagged[1]}, the schema says {how['sets']}")
                    self.assertIn(f"config.{field} = standard;", self.source)

    def test_headless_has_no_flag_for_a_key_the_schema_fixes(self):
        flags = set(re.findall(r'strcmp\(argv\[i\], "(--[\w-]+)"\)', self.source))
        flagged = {o["headless"]["flag"] for o in self.reboot.values() if "flag" in o["headless"]}
        # --units, --dump, --dump-from, --dump-audio and --disk are about the run (how long, what to write, what disk), not about the machine.
        self.assertEqual(flags - {"--units", "--dump", "--dump-from", "--dump-audio", "--disk", "--hdf", "--export-hdf"}, flagged, "headless takes a flag for a key the schema does not give it, or lacks one")

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

    # Settings of the wrapper that are input, not configuration (T17c): the schema lists them as unsupported options, with the reason, and each one is
    # accounted for here by the key that refuses it.
    INPUT_SETTINGS = {"ControllerPort1": "controller_port1", "ControllerPort2": "controller_port2"}

    def test_every_sync_setting_is_a_schema_option_or_reserved_in_it(self):
        known = {o["wrapper"]["setting"] for o in self.reboot.values() if o["wrapper"]["how"] == "setting"}
        known |= {entry["wrapper_setting"] for entry in self.schema["reserved"]}
        unsupported = {entry["key"] for entry in self.schema["unsupported"]}
        for setting, key in self.INPUT_SETTINGS.items():
            self.assertIn(key, unsupported, f"{setting} is input; the schema must say why {key} is not an option")
        known |= set(self.INPUT_SETTINGS)
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
                    # A value the schema marks "wrapper": false is one the wrapper does not offer yet (none since T24f): the enum lists the others, in order.
                    offered = [v.get("wrapper_name", v["value"]) for v in option["values"] if v.get("wrapper") is not False]
                    self.assertEqual([m.strip() for m in members[1].split(",")], offered,
                                     f"enum {kind} does not list the schema's values in order, less the ones marked wrapper false")
                    default_name = next(v.get("wrapper_name", v["value"]) for v in option["values"] if v["value"] == option["default"])
                    self.assertEqual(default, f"{kind}.{default_name}", f"{how['setting']} defaults to {default}, the schema to {option['default']}")
                else:
                    self.assertEqual(int(default), option["default"], f"{how['setting']} defaults to {default}, the schema to {option['default']}")

    def test_paulas_output_reaches_the_machine_as_the_abi_numbers_of_the_schema_and_the_identity_only_off_the_profile(self):
        # T73a: four sync settings; the enum members are the schema's values (wrapper_name when the value is a number), in order, and the function that makes the p6_config byte of each
        # gives the schema's number to each member: the separation is 1 + the index except the profile's 70 % (0), the others are switches with the profile's value as the default.
        for name, function in (("sound_separation", "SoundSeparationNumber"), ("sound_interpolation", "SoundInterpolationNumber"), ("sound_filter", "SoundFilterNumber"),
                               ("sound_filter_type", "SoundFilterTypeNumber"), ("floppy_speed", "FloppySpeedNumber")):
            with self.subTest(option=name):
                option = self.reboot[name]
                kind = option["wrapper"]["type"]
                body = re.search(rf"static byte {function}\(\w+ (\w+)\) => ([^\n]*);", self.core)
                self.assertIsNotNone(body, f"the core has no {function}")
                numbers = {v.get("wrapper_name", v["value"]): v["abi"] for v in option["values"]}
                if name == "sound_separation":
                    self.assertIn(f"{kind}.P70 ? (byte)0 : (byte)(1 + (int)", body[2])
                    self.assertEqual(numbers, {f"P{10 * i}": (0 if i == 7 else 1 + i) for i in range(11)})
                else:
                    explicit = {m[1]: int(m[2]) for m in re.finditer(rf"{kind}\.(\w+) => \(byte\)(\d+)", body[2])}
                    self.assertTrue(body[2].endswith("_ => (byte)0 }"), "the members that are not listed are the profile's setting, zero")
                    self.assertEqual({member: number for member, number in numbers.items() if number}, explicit)
                    self.assertEqual(numbers[next(v.get("wrapper_name", v["value"]) for v in option["values"] if v["value"] == option["default"])], 0)
                self.assertIn(f"init.Config.{camel(name)} = {function}(_syncSettings.{option['wrapper']['setting']});", self.core)
        # the identity names a setting only when it is not the default: a core at the profile's settings has the identity it always had
        for name, member in (("sound_separation", "SoundSeparation"), ("sound_interpolation", "SoundInterpolation"), ("sound_filter", "SoundFilter"), ("sound_filter_type", "SoundFilterType")):
            option = self.reboot[name]
            default_name = next(v.get("wrapper_name", v["value"]) for v in option["values"] if v["value"] == option["default"])
            self.assertIn(f"_syncSettings.{member} != {option['wrapper']['type']}.{default_name} ?", self.core)
        self.assertIn('memory += ";sound=" + sound.Substring(1);', self.core)
        # T62: the floppy speed is named in the identity only off the real drive's
        floppy = self.reboot["floppy_speed"]
        default_name = next(v.get("wrapper_name", v["value"]) for v in floppy["values"] if v["value"] == floppy["default"])
        self.assertIn(f'if (_syncSettings.FloppySpeed != {floppy["wrapper"]["type"]}.{default_name}) memory += ";floppyspeed=" + _syncSettings.FloppySpeed;', self.core)

    def test_the_standard_reaches_the_machine_as_the_abi_numbers_of_the_schema(self):
        pal, ntsc = (self.number(self.options["video_standard"], v) for v in ("PAL", "NTSC"))
        self.assertIn(f"const uint VideoPal = {pal}, VideoNtsc = {ntsc};", self.abi)
        self.assertIn("init.Config.VideoStandard = _syncSettings.Region == Puae6Region.NTSC ? P6.VideoNtsc : P6.VideoPal;", self.core)
        self.assertIn(f"const uint MachineA500 = {self.constants['P6_MACHINE_A500']}", self.abi.replace("public ", "").replace("static ", ""))

    def test_the_wrapper_machine_is_a_setting_that_reaches_the_machine_and_the_identity_as_the_abi_numbers_of_the_schema(self):
        # T23e: the machine is the wrapper's Machine sync setting (a restart setting): the enum has the schema's values, the default is the schema's, the ABI constants are the
        # schema's numbers, the config of the guest takes it from the setting and the config of a state and of a movie names it (so one machine refuses the other's).
        machine = self.reboot["machine"]
        self.assertEqual(machine["wrapper"], {"how": "setting", "setting": "Machine", "type": "Puae6Machine"})
        self.assertEqual(machine["default"], "A500")
        numbers = {v["value"]: v["abi"] for v in machine["values"]}
        self.assertIn(f"const uint MachineA500 = {numbers['A500']}, MachineA600 = {numbers['A600']}, MachineA1200 = {numbers['A1200']}, MachineA500OG = {numbers['A500OG']}, "
                      f"MachineA500Plus = {numbers['A500PLUS']}, MachineA1000 = {numbers['A1000']}, MachineA2000OG = {numbers['A2000OG']}, MachineA2000 = {numbers['A2000']};", self.abi)
        # T50: the number of every machine other than the default is a row of one switch (MachineNumber), and each row names the constant that has the schema's number
        self.assertIn("init.Config.Machine = MachineNumber(_syncSettings.Machine);", self.core)
        rows = dict(re.findall(r"Puae6Machine\.(\w+) => P6\.(Machine\w+)", re.search(r"static uint MachineNumber\(Puae6Machine machine\) => machine switch(.*?)\};", self.core, re.S)[1]))
        constants = {m[1]: int(m[2]) for m in re.finditer(r"(Machine\w+) = (\d+)", re.search(r"public const uint MachineA500 = .*?;", self.abi)[0])}
        self.assertEqual({name: constants[constant] for name, constant in rows.items()}, {name: number for name, number in numbers.items() if name != "A500"})
        # the three machines are offered, each with the FirmwareID of BizHawk's database that carries its name (tests/ffi/test_bizhawk_tree.py checks the database itself)
        self.assertEqual([v["value"] for v in machine["values"] if v.get("wrapper") is False], [])
        self.assertIn("puae6-config-1;machine={machine};", self.core)

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
        expected = {"AbiVersion", "StructSize", "Machine"} | {camel(o["maps_to"].partition(".")[2]) for o in self.reboot.values() if o["wrapper"]["how"] != "profile"}
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
        self.assertEqual(set(found), set(self.options) | {"preset"})
        self.assertEqual(int(found["preset"]), 0, "the preset starts at none")

    def test_the_options_offered_are_the_exposed_ones_with_their_values_and_defaults(self):
        exposed = [o for o in self.schema["options"] if o["libretro"]]
        keys = re.findall(r'^    \{"(puae6_\w+)", (true|false), &Settings::(\w+), (\w+), (\d+), (\d+)\},$', self.header, re.M)
        # The preset (T49) is offered right after the model and is no option of the schema: it lays the values of a preset over the options.
        self.assertEqual([k[0] for k in keys].index("puae6_preset"), 1)
        preset = [k for k in keys if k[0] == "puae6_preset"][0]
        self.assertEqual((preset[1], preset[2], int(preset[4]), int(preset[5])), ("true", "preset", 1 + len(self.schema["presets"]), 0))
        keys = [k for k in keys if k[0] != "puae6_preset"]
        self.assertEqual([k[0] for k in keys], [o["libretro"]["key"] for o in exposed])
        for (key, reboot, field, _, count, index), option in zip(keys, exposed):
            self.assertEqual((field, reboot == "true", int(count)), (option["id"], option["class"] == "reboot" or bool(option.get("restart")), len(option["values"])), key)
            self.assertEqual(option["values"][int(index)]["value"], option["default"], f"{key}: the default index points at another value")
            # The key is in the v2 definitions, the v1 ones and the legacy strings.
            self.assertEqual(self.header.count(f'"{key}"'), 4, key)
        for option in self.schema["options"]:
            if option["libretro"] is None:
                self.assertEqual(option["id"] in {k[2] for k in keys}, False, f"{option['id']} is not offered by libretro, so it keeps its default")

    def test_every_option_that_needs_a_reload_says_so_in_the_menu_and_one_that_applies_at_once_does_not(self):
        # A reboot option (part of the machine) and a presentation option the core reads when the content loads (restart, e.g. the pixel format).
        for option in self.schema["options"]:
            if option["libretro"]:
                label = re.search(rf'\{{"{option["libretro"]["key"]}", "([^"]*)"', self.header)[1]
                self.assertEqual(label.endswith("(Restart)"), option["class"] == "reboot" or bool(option.get("restart")), label)

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


class LibretroFirmware(Schema):
    """T23d: every machine of the schema has its own Kickstart names in the core, and the core takes them from that table only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.firmware = read(FIRMWARE)
        cls.core_source = read(LIBRETRO)

    def names(self, array):
        return re.findall(r'"([^"]+)"', re.search(rf"{array}\[\] = \{{(.*?)\}};", self.firmware, re.S)[1])

    def test_each_machine_of_the_schema_has_a_row_with_its_own_names_and_no_other_machine_has_one(self):
        rows = re.findall(r'\{(P6_MACHINE_\w+), "(\w+)", "([\d.]+)", (k\w+KickstartNames), ', self.firmware)
        machines = self.options["machine"]["values"]
        self.assertEqual([(r[0], r[1]) for r in rows], [(v["abi_name"], v["value"]) for v in machines])
        a500, a600, a1200 = (self.names(r[3]) for r in rows[:3])
        self.assertEqual((rows[0][2], rows[1][2], rows[2][2]), ("1.3", "2.05", "3.1"))
        for left, right in ((a500, a600), (a500, a1200), (a600, a1200)):
            self.assertFalse(set(left) & set(right), "a name that serves two machines would let one boot with the other's ROM")
        # T50: a name serves two machines only inside a family (the A500, the A500 OG, the A2000 OG and the A1000 boot the Kickstart 1.3), never across families
        families = {"A500": "1.x", "A500OG": "1.x", "A2000OG": "1.x", "A1000": "1.x", "A500PLUS": "2.04", "A600": "2.05", "A1200": "3.1", "A2000": "3.1 r40.063"}
        by_machine = {r[1]: set(self.names(r[3])) for r in rows}
        self.assertEqual(set(by_machine), set(families))
        for left in by_machine:
            for right in by_machine:
                if families[left] != families[right]:
                    self.assertFalse(by_machine[left] & by_machine[right], f"{left} and {right} are not the same family but a name serves both")
        self.assertEqual(self.names("kA500PlusKickstartNames")[:2], ["kick37175.A500", "amiga-os-204.rom"])
        self.assertEqual(self.names("kA2000KickstartNames")[:2], ["kick40063.A600", "amiga-os-310-a600.rom"])
        self.assertTrue(set(self.names("kA500KickstartNames")) < set(self.names("kA1000KickstartNames")), "the A1000 looks for the 1.3 first and then for the Kickstarts 1.2 and 1.1")
        self.assertEqual(a500[:2], ["kick34005.A500", "amiga-os-130.rom"])
        self.assertEqual(a600[:2], ["kick37350.A600", "amiga-os-205-a600.rom"])
        self.assertEqual(a1200[:2], ["kick40068.A1200", "amiga-os-310-a1200.rom"])
        self.assertIn("Kickstart v3.1 r40.68 (1993)(Commodore)(A1200)[!].rom", a1200)      # the name of the project's authorized copy
        self.assertNotIn("kick39106.A1200", a1200)                                          # the 3.0 is PUAE5's, but this A1200 boots the 3.1

    def test_the_first_two_names_of_each_machine_are_the_ones_puae5_reads(self):
        if not PUAE5.is_file():
            self.skipTest("references/machawk-uae is not there")
        header = read(PUAE5)
        for ids, array in (("A500_KS13_ROM", "kA500KickstartNames"), ("A600_KS205_ROM", "kA600KickstartNames"),
                           ("A1200_KS31_ROM", "kA1200KickstartNames")):
            entry = re.search(rf'\{{{ids},\s*"([^"]+)",\s*"([^"]+)"', header)
            self.assertIsNotNone(entry, ids)
            self.assertEqual(self.names(array)[:2], [entry[1], entry[2]], ids)

    def test_the_core_looks_only_for_the_names_of_the_machine_it_was_given(self):
        self.assertIn("firmware_for(config.machine)", self.core_source)
        self.assertNotIn("kKickstartNames", self.core_source)
        self.assertIn("for (size_t n = 0; n < firmware.count; ++n)", self.core_source)
        # The message of a missing ROM names the Kickstart version and the machine, so the person knows what to put in the system directory.
        self.assertIn("no Kickstart %s ROM in the system directory", self.core_source)


class AdaptersAgree(Schema):
    def test_all_three_adapters_default_to_the_same_normalized_p6_config(self):
        """The one statement the card asks for, in one place: whatever each adapter defaults to, it is the table's p6_config."""
        defaults = {name: entry["default"] for name, entry in self.table["p6_config"].items()}
        headless = read(HEADLESS)
        libretro = {m[1]: m[2] for m in re.finditer(r"(\w+) = (?:U?INT64_C\()?(-?\d+)u?\)?;", re.search(r"struct Settings \{(.*?)\};", read(GENERATED), re.S)[1])}
        wrapper = read(SETTINGS_CS)
        self.assertEqual(defaults, {"machine": 1, "video_standard": 1, "chip_kib": 0, "slow_kib": 0, "fast_kib": 0, "fpu_model": 0, "audio_rate_hz": 48000, "random_seed": 1,
                                    "rtc_epoch_seconds": 946684800, "flags": 0, "sound_separation": 0, "sound_interpolation": 0, "sound_filter": 0, "sound_filter_type": 0, "floppy_speed": 0})
        for field, value in defaults.items():
            self.assertEqual(int(libretro[self.table["p6_config"][field]["option"]]), value, f"libretro {field}")
        self.assertRegex(headless, r"\bstandard = P6_VIDEO_PAL[,;]")
        self.assertRegex(headless, r"\bmachine = P6_MACHINE_A500[,;]")
        self.assertIn("public Puae6Region Region { get; set; } = Puae6Region.PAL;", wrapper)
        self.assertIn(f"RandomSeed {{ get; set; }} = {defaults['random_seed']};", wrapper)
        self.assertIn(f"RtcEpochSeconds {{ get; set; }} = {defaults['rtc_epoch_seconds']};", wrapper)


if __name__ == "__main__":
    unittest.main()
