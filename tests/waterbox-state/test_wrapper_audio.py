"""T73a (AV02, WB03): Paula's output options on the three surfaces. headless takes them as flags, the BizHawk core as sync settings, libretro as options (T73).

The four options are p6_config.sound_separation, sound_interpolation, sound_filter and sound_filter_type (config/schema/options.json names every spelling; this file reads the
schema and has no table of its own). What is decided here:
PCM   the same combination gives the same samples on the three surfaces, unit by unit: the Waterbox core (every value of every option, a combination, the A500 and the A1200) and
      the libretro core against p6_headless with the flags of the same combination; the options are real (they change the PCM) and the default is the PCM of naming nothing.
WB03  save at N, go on, load, repeat: the same units and digest with the options on, and the state runs in a new sandbox of the same combination.
ID    a state or a movie of one combination is refused by a core with any other (OtherConfig, before the sandbox is touched) and accepted by the same one; a core at the profile's
      settings has the identity it always had (the text of the identity names a setting only when it is off the profile's).
The native config_hash (bridge/state.cpp) has no accessor in headless or in the Waterbox guest (neither serializes the machine; the guest is snapshotted as a whole), so the identity of the
wrapper is checked here as what a state and a movie carry, and the native hash of libretro states in tests/audio/test_audio_options.py.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet and the local NuGet cache for the wrapper part, and the native build for headless and libretro.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_audio.py"
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
sys.path.insert(0, str(ROOT / "tests/libretro-host"))
import build_waterbox  # noqa: E402
import generate_options as gen  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.scenarios.audio.audio import Tone, rom_source  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402
import test_libretro as lr  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
FNV_BASIS, FNV_PRIME = 1469598103934665603, 1099511628211
UNITS = 12

SCHEMA = gen.load_schema()
OPTIONS = {o["id"]: o for o in SCHEMA["options"]}
SOUND = ("sound_separation", "sound_interpolation", "sound_filter", "sound_filter_type")
SESSION_KEY = {"sound_separation": "sep", "sound_interpolation": "interp", "sound_filter": "filter", "sound_filter_type": "ftype"}


def values(option_id):
    """The values of an option as the three adapters spell them: (schema value, wrapper member, headless spelling)."""
    option = OPTIONS[option_id]
    return [(v["value"], v.get("wrapper_name", v["value"]), option["headless"]["names"][v["value"]]) for v in option["values"]]


def default_of(option_id):
    return OPTIONS[option_id]["default"]


def spellings(combination):
    """A combination is {option id: schema value}; the same in each adapter's words: session words, headless flags, libretro --set arguments."""
    session, headless, libretro = [], [], []
    for option_id, value in combination.items():
        member, spelling = next((m, s) for v, m, s in values(option_id) if v == value)
        session.append(f"{SESSION_KEY[option_id]}={member}")
        headless += [OPTIONS[option_id]["headless"]["flag"], spelling]
        libretro += ["--set", f"{OPTIONS[option_id]['libretro']['key']}={value}"]
    return session, headless, libretro


def singles():
    """Every value of every option that is not the default, alone."""
    return [{option_id: v} for option_id in SOUND for v, _, _ in values(option_id) if v != default_of(option_id)]


COMBINED = {"sound_separation": "0", "sound_interpolation": "crux", "sound_filter": "on", "sound_filter_type": "A1200"}
CROSSED = {"sound_separation": "100", "sound_interpolation": "rh", "sound_filter": "emulated", "sound_filter_type": "A500"}       # the A500 model on whatever machine


def fnv(data):
    h = FNV_BASIS
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def headless_pcm(headless, rom, machine, flags, units=UNITS):
    """The FNV-1a of the samples of each unit p6_headless hands over with these flags (the hash the Waterbox session prints as pcm=), and the files' bytes."""
    with tempfile.TemporaryDirectory(prefix="p6-audio-") as directory:
        result = subprocess.run([str(headless), "--run", str(rom), "--units", str(units), "--machine", machine, *flags, "--dump-audio", directory], capture_output=True, text=True, timeout=900)
        assert result.returncode == 0, result.stderr
        files = [Path(directory) / f"audio-{n:03d}.bin" for n in range(1, units + 1)]
        data = [f.read_bytes() for f in files]
    return [f"{fnv(d):016x}" for d in data], data


def tone_roms(directory):
    roms = {}
    for name, tone, led_on in (("tone0", Tone("tone0", (0,)), False), ("tone_led", Tone("tone_led", (0, 1, 2, 3), period=124), True)):
        source = rom_source(tone)
        if led_on:
            source = source.replace("MOVE.B #2,$BFE001.L          ; power LED off: the LED filter stays off", "MOVE.B #0,$BFE001.L          ; power LED on")
            assert "power LED on" in source
        roms[name] = Path(directory) / f"{name}.rom"
        roms[name].write_bytes(builder.build(source))
    return roms


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6)")
class WrapperAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.headless = build_probe(ROOT / "build/link-audit", False, "p6_headless")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wrapper-audio-")
        cls.dir = Path(cls.tmp.name)
        cls.roms = tone_roms(cls.dir)
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, lines, rom="tone_led", expect=0):
        WrapperAudioTests.count += 1
        path = self.dir / f"program-{WrapperAudioTests.count}.txt"
        path.write_text("\n".join(lines) + "\n")
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.roms[rom]), "--program", str(path))
        if expect is None:
            self.assertNotEqual(0, result.returncode, result.stdout[-1500:])
            return result.stdout + result.stderr
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    @staticmethod
    def core(name, machine, combination=None):
        return " ".join(["core", name, f"machine={machine}", *spellings(combination or {})[0]])

    # --- the same PCM on the wrapper and on headless -------------------------------------------------------------------------------------

    def compare(self, rom, machine, combinations):
        program = []
        for n, combination in enumerate(combinations):
            program += [self.core(f"C{n}", machine, combination), f"run C{n} {UNITS}", f"dispose C{n}"]
        out = self.session(program, rom)
        pcms = []
        for n, combination in enumerate(combinations):
            with self.subTest(machine=machine, rom=rom, combination=combination):
                units = self.units(out, f"C{n}")
                self.assertEqual(UNITS, len(units))
                expected, data = headless_pcm(self.headless, self.roms[rom], machine, spellings(combination)[1])
                self.assertEqual(expected, [u[5] for u in units], "the samples of the Waterbox core are not the ones of p6_headless with the same options")
                self.assertGreater(sum(len(d) for d in data), 4000, "the ROM does not play")
                pcms.append(tuple(expected))
        return pcms

    def test_every_value_of_every_option_gives_the_pcm_of_headless_with_the_flags_of_the_same_combination(self):
        combinations = [{}] + singles()
        pcms = self.compare("tone_led", "a500", combinations)
        self.assertEqual(len(pcms), len(combinations))
        # the options are real: another PCM than the default for each single value, except what the hardware makes equal (the filter type with the filter emulated and the LED on the A500
        # model it already is: A500 on an A500 is the default)
        default = pcms[0]
        changed = [c for c, p in zip(combinations, pcms) if p != default]
        self.assertGreaterEqual(len(changed), len(combinations) - 3, f"most values must change the samples: {changed}")

    def test_a_combination_gives_the_pcm_of_headless_on_each_machine_and_the_crossed_filter_type_is_another_pcm(self):
        for machine, rom in (("a500", "tone_led"), ("a1200", "tone_led"), ("a500", "tone0")):
            with self.subTest(machine=machine, rom=rom):
                pcms = self.compare(rom, machine, [{}, COMBINED, CROSSED])
                self.assertEqual(len(set(pcms)), 3)

    def test_naming_the_profile_is_naming_nothing(self):
        profile = {option_id: default_of(option_id) for option_id in SOUND}
        out = self.session([self.core("A", "a500"), f"run A {UNITS}", self.core("B", "a500", profile), f"run B {UNITS}"])
        self.assertEqual([u[1:] for u in self.units(out, "A")], [u[1:] for u in self.units(out, "B")])
        expected, _ = headless_pcm(self.headless, self.roms["tone_led"], "a500", [])
        self.assertEqual(expected, [u[5] for u in self.units(out, "A")])

    # --- WB03 ---------------------------------------------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_machine_with_the_options_on(self):
        for combination in (COMBINED, CROSSED):
            for machine in ("a500", "a1200"):
                for n in (4, 7):
                    with self.subTest(combination=combination, machine=machine, saved_at=n):
                        m = 6
                        out = self.session([self.core("A", machine, combination), f"run A {n}", "save A s", f"run A {m}", "digest A first", "load A s", f"run A {m}", "digest A second"])
                        units = self.units(out, "A")
                        self.assertEqual(n + 2 * m, len(units))
                        self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]])
                        digests = self.digests(out, "A")
                        self.assertEqual(digests["first"], digests["second"])

    def test_a_state_runs_in_a_new_sandbox_of_the_same_combination_as_in_the_first(self):
        out = self.session([self.core("A", "a500", COMBINED), "run A 5", "save A s", self.core("B", "a500", COMBINED), "load B s", "run A 6", "run B 6", "digest A a", "digest B b"])
        self.assertEqual([u[1:] for u in self.units(out, "A")][5:], [u[1:] for u in self.units(out, "B")])
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    # --- the identity ------------------------------------------------------------------------------------------------------------------

    def matrix(self):
        return [("default", {}), ("separation", {"sound_separation": "0"}), ("interpolation", {"sound_interpolation": "rh"}), ("filter", {"sound_filter": "on"}),
                ("type", {"sound_filter_type": "A1200"}), ("combined", COMBINED)]

    def test_a_state_of_a_combination_loads_in_the_same_one_and_is_refused_by_any_other_before_the_sandbox_is_touched(self):
        matrix = self.matrix()
        program = []
        for name, combination in matrix:
            program += [self.core(f"S{name}", "a500", combination), f"run S{name} 4", f"save S{name} s{name}", f"digest S{name} saved", f"dispose S{name}"]
        for name, combination in matrix:
            program += [self.core(f"T{name}", "a500", combination), f"run T{name} 2", f"digest T{name} before"]
            program += [f"load T{name} s{other}" for other, _ in matrix]
            program += [f"digest T{name} after"]
        out = self.session(program)
        for name, _ in matrix:
            refusals = re.findall(rf"^rejected T{name} s(\w+) reason=(\w+) message=(.*)$", out, re.M)
            self.assertEqual(sorted(other for other, _ in matrix if other != name), sorted(r[0] for r in refusals), f"{name} refuses every state of another combination and only those")
            for other, reason, message in refusals:
                self.assertEqual("OtherConfig", reason, f"{name} <- {other}")
                self.assertIn("other sync settings", message)
        # the instance that refused five states went on as if never asked: after its own state (the only one it accepted) it is the machine that state was saved from, whatever it refused around it
        for name, _ in matrix:
            self.assertEqual(self.digests(out, f"S{name}")["saved"], self.digests(out, f"T{name}")["after"], name)
            self.assertNotEqual(self.digests(out, f"T{name}")["before"], self.digests(out, f"T{name}")["after"], name)

    def test_a_movie_of_a_combination_is_refused_by_any_other_and_accepted_by_the_same_one(self):
        matrix = self.matrix()
        movies = {}
        for name, combination in matrix:
            movies[name] = self.dir / f"movie-{name}.bk2"
            out = self.session([self.core("R", "a500", combination), "run R 6 record", f"endmovie R {movies[name]}"])
            self.assertIn(f"movie {movies[name]} frames=6", out)
        for name, combination in matrix:
            with self.subTest(played=name):
                others = [other for other, _ in matrix if other != name]
                out = self.session([self.core("P", "a500", combination)] + [f"moviecheck P {movies[other]}" for other in others] + [f"moviecheck P {movies[name]}"])
                self.assertEqual(len(others), len(re.findall(r"^movie rejected P reason=OtherConfig ", out, re.M)), out[-1500:])
                self.assertEqual(1, out.count("movie accepted P"))

    def test_at_the_profile_settings_the_identity_is_the_one_it_always_was(self):
        source = (ROOT / "integration/bizhawk/core/Puae6/Puae6Core.cs").read_text(encoding="utf-8")
        # the text of the configuration names a sound setting only when it is off the profile's, so the hash of a core that names none is the hash of the text it always had
        self.assertIn('if (sound.Length != 0) memory += ";sound=" + sound.Substring(1);', source)
        out = self.session([self.core("A", "a500"), "run A 4", "save A s", self.core("B", "a500", {option_id: default_of(option_id) for option_id in SOUND}), "load B s", "run A 3", "run B 3", "digest A a", "digest B b"])
        self.assertNotIn("rejected", out)
        self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class LibretroAudioTests(lr.LibretroHarness):
    """libretro against p6_headless: the same combination is the same samples, so the three surfaces agree through headless."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.roms.update(tone_roms(cls.tmp.name))

    def libretro_samples(self, rom, machine, libretro_options, runs):
        directory = self.work()
        self.frontend(rom, "--runs", runs, "--dump-audio", directory, *(lr.A1200 if machine == "a1200" else ()), *libretro_options,
                      name=lr.KICKSTART_NAMES_A1200[0] if machine == "a1200" else lr.KICKSTART_NAMES[0])
        return [(directory / f"audio-{n:03d}.bin").read_bytes() for n in range(1, runs + 1)]

    def test_the_libretro_core_and_headless_hand_over_the_same_samples_for_the_same_combination(self):
        runs = 10
        for machine in ("a500", "a1200"):
            for combination in ({}, {"sound_separation": "0"}, {"sound_interpolation": "crux"}, {"sound_filter": "on"}, {"sound_filter_type": "A1200"}, COMBINED, CROSSED):
                with self.subTest(machine=machine, combination=combination):
                    _, headless_flags, libretro_options = spellings(combination)
                    expected, data = headless_pcm(self.headless, self.roms["tone_led"], machine, headless_flags, runs)
                    got = self.libretro_samples("tone_led", machine, libretro_options, runs)
                    self.assertEqual([fnv(d) for d in data], [fnv(d) for d in got])
                    self.assertGreater(sum(len(d) for d in got), 4000)


if __name__ == "__main__":
    unittest.main()
