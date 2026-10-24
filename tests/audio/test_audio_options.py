"""T73 (AV02, LR01): Paula's output options through the libretro core, run by the minimal frontend.

puae6_sound_stereo_separation (0..100 %), puae6_sound_interpolation (none, anti, rh, crux), puae6_sound_filter (emulated, off, on) and puae6_sound_filter_type (auto, A500,
A1200) are restart options: they are the p6_config fields sound_separation, sound_interpolation, sound_filter and sound_filter_type. What is decided here:
  * the defaults are the profile's: the PCM of a session that names them is the PCM of one that does not, byte for byte, and equals the headless runner's (which has no option);
  * each option changes the PCM, and in the way the hardware documentation says (the separation as the mix of the two sides, hand-computed; mono at 0 %), the same way twice;
  * they are part of the configuration identity: a state saved with one is refused by a core with another and loaded by the same one; at the profile's value the identity
    is the one it always was (the state of a session that names the profile's values loads in one that names nothing);
  * the state and the rewind converge with the option on (the audio of a unit replayed after a load is the one of the first pass);
  * a value the schema does not have (sinc, 95 %) fails the load naming the key, the .uae importer translates them.
Run: python3 -m unittest discover -s tests/audio -p "test_audio_options.py"
"""
from pathlib import Path
import re
import struct
import sys
import unittest

HOST = Path(__file__).resolve().parents[1] / "libretro-host"
sys.path.insert(0, str(HOST))
import test_libretro as lr  # noqa: E402

from tests.state import envelope as e  # noqa: E402
from tests.scenarios.audio.audio import HIGH_WEIGHT, LOW_WEIGHT, Tone, rom_source  # noqa: E402

LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
WARMUP = 4          # runs before the samples that are compared: the first one holds the reset head and the DMA start
RUNS = 14
FULL = 12800        # 100 * 64 * 2: one channel at full volume on its own side before the mix
SEPARATION = "puae6_sound_stereo_separation"
INTERPOLATION = "puae6_sound_interpolation"
FILTER = "puae6_sound_filter"
FILTER_TYPE = "puae6_sound_filter_type"


def option(key, value):
    return ("--set", f"{key}={value}")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class AudioOptionTests(lr.LibretroHarness):
    MACHINE = ()
    NAMES = lr.KICKSTART_NAMES
    MODEL_HAS_NO_FIXED_STAGE = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Channel 0 alone (the left output) with the power LED off, and the four channels with the power LED on (the LED filter of the hardware is on).
        for name, tone, led_on in (("tone0", Tone("tone0", (0,)), False), ("tone_led", Tone("tone_led", (0, 1, 2, 3), period=124), True)):
            source = rom_source(tone)
            if led_on:
                source = source.replace("MOVE.B #2,$BFE001.L          ; power LED off: the LED filter stays off", "MOVE.B #0,$BFE001.L          ; power LED on")
                assert "power LED on" in source
            rom = Path(cls.tmp.name) / f"{name}.rom"
            rom.write_bytes(lr.builder.build(source))
            cls.roms[name] = rom

    def play(self, rom, *options, runs=RUNS, extra=()):
        """The samples of the visible runs after the warm-up (interleaved L, R), and the output of the frontend."""
        directory = self.work()
        out = self.frontend(rom, "--runs", runs, "--dump-audio", directory, *self.MACHINE, *options, *extra, name=self.NAMES[0]).stdout
        samples = []
        for n in range(WARMUP + 1, runs + 1):
            data = (directory / f"audio-{n:03d}.bin").read_bytes()
            samples.extend(struct.unpack("<%dh" % (len(data) // 2), data))
        self.assertGreater(len(samples), 1000)
        return samples, out

    @staticmethod
    def sides(samples):
        return samples[0::2], samples[1::2]

    # --- the defaults are the profile's -------------------------------------------------------------------------------------------------------

    def test_the_profile_values_named_one_by_one_are_the_pcm_of_naming_nothing(self):
        for rom in ("tone0", "tone_led"):
            with self.subTest(rom=rom):
                plain, plain_out = self.play(rom)
                named, named_out = self.play(rom, *option(SEPARATION, 70), *option(INTERPOLATION, "anti"), *option(FILTER, "emulated"), *option(FILTER_TYPE, "auto"))
                self.assertEqual(named, plain)
                self.assertEqual(self.runs(named_out), self.runs(plain_out))          # the same pictures, ticks and audio hashes of every unit

    def test_the_default_pcm_is_the_headless_runner_which_has_no_option(self):
        out = self.frontend("tone0", "--runs", 8, *self.MACHINE, name=self.NAMES[0]).stdout
        self.compare_with_headless(out, "tone0", 8, "--machine", self.HEADLESS)

    def test_the_automatic_filter_type_is_the_model_of_the_machine_and_the_other_model_is_another_pcm(self):
        own, other = ("A1200", "A500") if self.MACHINE == lr.A1200 else ("A500", "A1200")
        auto, _ = self.play("tone_led")
        self.assertEqual(self.play("tone_led", *option(FILTER_TYPE, own))[0], auto)
        self.assertNotEqual(self.play("tone_led", *option(FILTER_TYPE, other))[0], auto)

    # --- separation -------------------------------------------------------------------------------------------------------------------------

    def test_the_separation_is_the_mix_of_the_two_sides_of_the_hardware_documentation(self):
        # Channel 0 is on the left. A step s of tenths gives each side the weights (16 + e) / 32 of its own channels and (16 - e) / 32 of the other, e = 3 s / 2 and 16 from 15 (upstream's
        # mixer). Hand-computed from the 12800 of the channel at full volume; the filter is the fixed one of the machine, so the plateau of a square wave of 8 samples per
        # half period is reached before each edge.
        for percent in (0, 20, 50, 70, 100):
            with self.subTest(percent=percent):
                samples, _ = self.play("tone0", *option(SEPARATION, percent), *option(FILTER, "off"))
                left, right = self.sides(samples)
                step = percent // 10
                e = 16 if step * 3 // 2 >= 15 else step * 3 // 2         # upstream: a sum of 15 or more is the full separation
                self.assertEqual(max(map(abs, left)), FULL * (16 + e) // 32)
                self.assertEqual(max(map(abs, right)), FULL * (16 - e) // 32)

    def test_zero_percent_is_mono_and_one_hundred_keeps_the_channel_on_its_side(self):
        left, right = self.sides(self.play("tone0", *option(SEPARATION, 0))[0])
        self.assertEqual(left, right)
        self.assertGreater(max(map(abs, left)), 0)
        left, right = self.sides(self.play("tone0", *option(SEPARATION, 100))[0])
        self.assertEqual(set(right), {0})
        self.assertGreater(max(map(abs, left)), 0)

    def test_the_default_separation_is_the_one_of_the_documented_plateaus(self):
        left, right = self.sides(self.play("tone0", *option(FILTER, "off"))[0])
        self.assertEqual((max(map(abs, left)), max(map(abs, right))), (HIGH_WEIGHT, LOW_WEIGHT))

    # --- interpolation ----------------------------------------------------------------------------------------------------------------------

    def test_each_interpolation_is_its_own_pcm_the_same_every_time(self):
        seen = {}
        for mode in ("none", "anti", "rh", "crux"):
            with self.subTest(mode=mode):
                first = self.play("tone_led", *option(INTERPOLATION, mode))[0]
                self.assertEqual(self.play("tone_led", *option(INTERPOLATION, mode))[0], first)
                seen[mode] = first
        self.assertEqual(len({tuple(v) for v in seen.values()}), 4, "two interpolations give the same samples")
        self.assertEqual(seen["anti"], self.play("tone_led")[0])
        # Without interpolation and without the filter a square wave has no value between its plateaus.
        flat = self.play("tone0", *option(INTERPOLATION, "none"), *option(FILTER, "off"))[0]
        self.assertLessEqual(len(set(flat)), 4)

    # --- filter -----------------------------------------------------------------------------------------------------------------------------

    def test_the_filter_off_emulated_and_on_are_other_pcm_and_the_led_one_follows_the_power_led(self):
        off = self.play("tone0", *option(FILTER, "off"))[0]
        emulated = self.play("tone0")[0]
        forced = self.play("tone0", *option(FILTER, "on"))[0]
        self.assertNotEqual(forced, off)
        self.assertNotEqual(forced, emulated)
        # With the power LED off the emulated filter is the fixed stage of the model only: the A500 has one (the 6 dB low-pass of its output), the A1200 does not.
        self.assertEqual(emulated == off, self.MODEL_HAS_NO_FIXED_STAGE)
        # With the LED on the emulated filter has the LED stage too: another PCM than off, and the same as always on once the LED has come on.
        led_off = self.play("tone_led", *option(FILTER, "off"))[0]
        led_emulated = self.play("tone_led")[0]
        self.assertNotEqual(led_off, led_emulated)
        self.assertEqual(self.play("tone_led", *option(FILTER, "on"))[0][-4000:], led_emulated[-4000:])

    def test_the_filter_type_is_ignored_while_the_filter_is_off_and_changes_the_pcm_otherwise(self):
        off_a500 = self.play("tone_led", *option(FILTER, "off"), *option(FILTER_TYPE, "A500"))[0]
        off_a1200 = self.play("tone_led", *option(FILTER, "off"), *option(FILTER_TYPE, "A1200"))[0]
        self.assertEqual(off_a500, off_a1200)
        a500 = self.play("tone_led", *option(FILTER_TYPE, "A500"))[0]
        a1200 = self.play("tone_led", *option(FILTER_TYPE, "A1200"))[0]
        self.assertNotEqual(a500, a1200)

    # --- identity, state and rewind --------------------------------------------------------------------------------------------------------

    def state_file(self, rom, *options, at=6):
        path = self.work() / "x.state"
        self.frontend(rom, "--runs", at + 2, *self.MACHINE, *options, "--do", f"{at}:savefile:{path}", name=self.NAMES[0])
        return path

    def load_ok(self, rom, path, *options):
        out = self.frontend(rom, "--runs", 3, *self.MACHINE, *options, "--do", f"0:loadfile:{path}:pos=6", name=self.NAMES[0]).stdout
        return LOAD.search(out)[3] == "1"

    def test_a_state_loads_in_the_same_options_and_is_refused_by_any_other(self):
        base = {SEPARATION: 30, INTERPOLATION: "crux", FILTER: "on", FILTER_TYPE: "A1200"}

        def options(settings):
            return tuple(a for key, value in settings.items() for a in option(key, value))

        path = self.state_file("tone_led", *options(base))
        self.assertTrue(self.load_ok("tone_led", path, *options(base)))
        for key, value in ((SEPARATION, 40), (SEPARATION, 100), (INTERPOLATION, "rh"), (INTERPOLATION, "none"), (INTERPOLATION, "anti"), (FILTER, "off"), (FILTER, "emulated"),
                           (FILTER_TYPE, "A500")):
            with self.subTest(key=key, value=value):
                self.assertFalse(self.load_ok("tone_led", path, *options({**base, key: value})))
        self.assertFalse(self.load_ok("tone_led", path))

    def config_hash_of(self, path):
        """The config_hash in the header of the native state inside a state file of the core (after the adapter's own header and section)."""
        data = path.read_bytes()
        start = data.index(b"P6NATIVE")
        return data[start + 176:start + 208]

    def test_the_config_hash_is_the_profile_one_or_the_derivation_of_the_envelope_definition(self):
        # Made here from tests/state/envelope.py, not read from the core: machine, PAL, 48000 Hz, seed 1 and the epoch of 2000 (the options' defaults).
        machine = 3 if self.MACHINE == lr.A1200 else 1
        own = 1 if machine == 3 else 0
        base = e.config_hash(machine, 1, 48000, 0, 1, 946684800)
        self.assertEqual(self.config_hash_of(self.state_file("tone0")), base)               # the series' hash, as it has always been
        cases = (((SEPARATION, 100),), ((INTERPOLATION, "none"),), ((INTERPOLATION, "rh"),), ((INTERPOLATION, "crux"),), ((FILTER, "off"),), ((FILTER, "on"),),
                 ((FILTER_TYPE, "A1200" if machine == 1 else "A500"),), ((SEPARATION, 0), (INTERPOLATION, "none"), (FILTER, "off"), (FILTER_TYPE, "A1200")))
        seen = {base}
        for case in cases:
            with self.subTest(case=case):
                settings = dict(case)
                names = {"none": 0, "anti": 1, "rh": 3, "crux": 4}
                filter_ = {"off": 0, "emulated": 1, "on": 2}[settings.get(FILTER, "emulated")]
                kind = {"A500": 0, "A1200": 1}.get(settings.get(FILTER_TYPE), own)
                expected = e.config_hash_audio(base, settings.get(SEPARATION, 70) // 10, names[settings.get(INTERPOLATION, "anti")], filter_, kind if filter_ else 0)
                options = tuple(a for key, value in case for a in option(key, value))
                found = self.config_hash_of(self.state_file("tone0", *options))
                self.assertEqual(found, expected)
                self.assertNotIn(found, seen)
                seen.add(found)

    def test_at_the_profile_values_the_identity_is_the_one_it_always_was(self):
        path = self.state_file("tone_led")
        self.assertTrue(self.load_ok("tone_led", path, *option(SEPARATION, 70), *option(INTERPOLATION, "anti"), *option(FILTER, "emulated"), *option(FILTER_TYPE, "auto")))
        own = "A1200" if self.MACHINE == lr.A1200 else "A500"
        self.assertTrue(self.load_ok("tone_led", path, *option(FILTER_TYPE, own)))
        self.assertFalse(self.load_ok("tone_led", path, *option(SEPARATION, 60)))

    def test_the_filter_type_does_not_enter_the_identity_while_the_filter_is_off(self):
        path = self.state_file("tone_led", *option(FILTER, "off"), *option(FILTER_TYPE, "A500"))
        self.assertTrue(self.load_ok("tone_led", path, *option(FILTER, "off"), *option(FILTER_TYPE, "A1200")))

    def test_a_load_replays_the_audio_of_each_unit_with_the_options_on(self):
        on = option(SEPARATION, 30) + option(INTERPOLATION, "rh") + option(FILTER, "on") + option(FILTER_TYPE, "A1200")
        for rom in ("tone0", "tone_led"):
            with self.subTest(rom=rom):
                out = self.frontend(rom, "--runs", 24, *self.MACHINE, *on, "--do", "8:save:a", "--do", "16:load:a", name=self.NAMES[0]).stdout
                seen = {}
                for run in self.runs(out):
                    seen.setdefault(run["unit"], []).append((run["ticks"], run["audio"], run["ahash"]))
                replayed = [views for views in seen.values() if len(views) > 1]
                self.assertGreaterEqual(len(replayed), 6)
                for views in replayed:
                    self.assertEqual(len(set(views)), 1, views)

    def test_runahead_leaves_the_audio_of_the_options_as_a_plain_run_has_it(self):
        on = option(SEPARATION, 30) + option(INTERPOLATION, "crux") + option(FILTER, "on")
        plain = self.runs(self.frontend("tone_led", "--runs", 16, *self.MACHINE, *on, name=self.NAMES[0]).stdout)
        ahead = self.runs(self.frontend("tone_led", "--runs", 16, *self.MACHINE, *on, "--ahead-every", 2, name=self.NAMES[0]).stdout)
        self.assertEqual([(r["audio"], r["ahash"]) for r in ahead], [(r["audio"], r["ahash"]) for r in plain])

    # --- refusals ----------------------------------------------------------------------------------------------------------------------------

    def test_a_value_of_no_schema_fails_the_load_naming_the_key(self):
        for key, value, allowed in ((INTERPOLATION, "sinc", "none, anti, rh, crux"), (SEPARATION, 95, None), (FILTER, "fixedonly", "emulated, off, on"),
                                    (FILTER_TYPE, "A600", "auto, A500, A1200")):
            with self.subTest(key=key, value=value):
                out = self.frontend("tone0", *self.MACHINE, *option(key, value), name=self.NAMES[0], expect=3).stdout
                self.assertIn(f'the option {key} has the value "{value}", which this core does not support', out)
                if allowed:
                    self.assertIn(f"(allowed: {allowed})", out)
                self.assertNotIn("run 1 ", out)

    def test_a_change_while_the_machine_runs_is_reported_and_applies_at_the_next_load(self):
        system = self.system_for("tone0", self.NAMES[0])
        base = self.frontend("tone0", "--runs", 12, *self.MACHINE, system=system).stdout
        changed = self.frontend("tone0", "--runs", 12, *self.MACHINE, "--set-at", f"5:{SEPARATION}=20", system=system).stdout
        self.assertEqual(self.messages(changed), [f"PUAE6: {SEPARATION} is now 20 but the machine runs with 70: reload the content to apply it"])
        self.assertEqual([(r["audio"], r["ahash"]) for r in self.runs(changed)], [(r["audio"], r["ahash"]) for r in self.runs(base)])

    compare_with_headless = lr.LibretroTests.compare_with_headless
    messages = staticmethod(lr.LibretroTests.messages)
    HEADLESS = "a500"


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class AudioOptionA1200Tests(AudioOptionTests):
    """The same on the A1200: its automatic filter type is the A1200 model, so naming A500 is the crossed one."""
    MACHINE = lr.A1200
    NAMES = lr.KICKSTART_NAMES_A1200
    HEADLESS = "a1200"
    MODEL_HAS_NO_FIXED_STAGE = True


if __name__ == "__main__":
    unittest.main()
