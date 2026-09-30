"""AV02 and DET05 for Paula's audio through p6_run_frame.

Counts come from the clock alone (integers); content comes from micro-ROMs whose output follows from the hardware
documentation; delivery on/off must leave the machine and the delivered samples unchanged.
"""
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest

from tests.scenarios.audio.audio import HIGH_WEIGHT, LOW_WEIGHT, NTSC_CLOCK, PAL_CLOCK, CYCLE_UNIT, Tone, frames_bound, rom_source
from tests.step.test_step import BUILD, FIXTURES, ROOT, TOOLS, builder, configure_trace_tree, frames_of, make_rom, run_probe

CLOCK = {"pal": PAL_CLOCK, "ntsc": NTSC_CLOCK}


def audio_of(row):
    """(delivered?, frames) of a `frame` report row."""
    return row[20] != "-", row[21]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-audio-")
        cls.roms = {}
        for name, source in (("vsync", "vsync.s"), ("long_ops", "long_ops.s")):
            cls.roms[name] = make_rom(cls.tmp.name, name, source, {"PAD": 3} if name == "long_ops" else {})
        for tone in (Tone("tone0", (0,)), Tone("tone1", (1,)), Tone("tone2", (2,)), Tone("tone3", (3,)),
                     Tone("tone_all", (0, 1, 2, 3), period=124)):
            path = Path(cls.tmp.name) / (tone.name + ".rom")
            path.write_bytes(builder.build(rom_source(tone)))
            cls.roms[tone.name] = path

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_frames(self, rom, units, standard="pal", rate=48000, mode="frames", pcm=None, pattern=None):
        env = {"P6_AUDIO_RATE": str(rate)}
        if pcm:
            Path(pcm).mkdir(exist_ok=True)
            env["P6_AUDIO_DUMP"] = str(pcm)
        if pattern:
            env["P6_AUDIO_PATTERN"] = pattern
        code, out = run_probe(self.exe, self.roms[rom], units, mode, 1, timeout=900, extra=[standard], env=env)
        self.assertEqual(code, 0, out[-500:])
        return frames_of(out), out

    def pcm(self, directory, unit):
        data = (Path(directory) / f"audio-{unit:03d}.bin").read_bytes()
        return struct.unpack("<%dh" % (len(data) // 2), data)

    def test_cumulative_frames_follow_the_clock_with_no_drift(self):
        for standard in ("pal", "ntsc"):
            for rate in (44100, 48000):
                for rom in ("vsync", "long_ops"):
                    rows, _ = self.run_frames(rom, 400, standard, rate)
                    total = 0
                    for row in rows:
                        delivered, frames = audio_of(row)
                        self.assertTrue(delivered)
                        total += frames
                        exact, low, high = frames_bound(row[1], rate, CLOCK[standard])
                        with self.subTest(standard=standard, rate=rate, rom=rom, unit=row[0]):
                            self.assertEqual(total, exact)
                            self.assertTrue(low <= total <= high, (low, total, high))

    def test_a_square_wave_has_the_pitch_the_registers_ask_for_on_the_side_of_its_channel(self):
        with tempfile.TemporaryDirectory(prefix="p6-pcm-") as pcm:
            rows, _ = self.run_frames("tone0", 40, pcm=pcm)
            left, right = [], []
            for unit in range(8, 41):
                samples = self.pcm(pcm, unit)
                left += samples[0::2]
                right += samples[1::2]
            # The A500 output filter rounds the edges, so the levels are checked at the peaks of the wave.
            self.assertLessEqual(abs(max(left) - HIGH_WEIGHT), 3)
            self.assertLessEqual(abs(min(left) + HIGH_WEIGHT), 3)
            self.assertLessEqual(abs(max(right) - LOW_WEIGHT), 3)
            self.assertLessEqual(abs(min(right) + LOW_WEIGHT), 3)
            rising = sum(1 for a, b in zip(left, left[1:]) if a < 0 <= b)
            # 16 bytes at 200 colour clocks per byte: a cycle every 3200 clocks = 1638400 ticks.
            span = rows[-1][1] - rows[6][1]
            self.assertLessEqual(abs(rising - span / (3200 * CYCLE_UNIT)), 1.5)

    def test_channels_zero_and_three_are_left_one_and_two_are_right(self):
        for name, loud_left in (("tone0", True), ("tone1", False), ("tone2", False), ("tone3", True)):
            with self.subTest(channel=name), tempfile.TemporaryDirectory(prefix="p6-pcm-") as pcm:
                self.run_frames(name, 14, pcm=pcm)
                samples = [v for unit in range(8, 15) for v in self.pcm(pcm, unit)]
                left, right = max(abs(v) for v in samples[0::2]), max(abs(v) for v in samples[1::2])
                big, small = (left, right) if loud_left else (right, left)
                self.assertTrue(abs(big - HIGH_WEIGHT) <= 3 and abs(small - LOW_WEIGHT) <= 3, (left, right))

    def test_without_paula_activity_the_output_is_silence(self):
        with tempfile.TemporaryDirectory(prefix="p6-pcm-") as pcm:
            self.run_frames("vsync", 12, pcm=pcm)
            self.assertEqual({v for unit in range(1, 13) for v in self.pcm(pcm, unit)}, {0})

    def test_delivering_audio_or_not_changes_nothing_of_the_machine_or_of_the_samples(self):
        with tempfile.TemporaryDirectory(prefix="p6-pcm-") as always, tempfile.TemporaryDirectory(prefix="p6-pcm-") as partial:
            base_rows, base_out = self.run_frames("tone_all", 32, pcm=always)
            pattern = "1101" + "0110"
            rows, out = self.run_frames("tone_all", 32, pcm=partial, pattern=pattern)
            self.assertEqual(base_out.splitlines()[-1], out.splitlines()[-1])          # final digest and ticks
            for unit, (a, b) in enumerate(zip(base_rows, rows), 1):
                self.assertEqual((a[1], a[19]), (b[1], b[19]))                          # same ticks, same picture
                wanted = pattern[(unit - 1) % len(pattern)] == "1"
                self.assertEqual(audio_of(b)[0], wanted)
                if wanted:
                    self.assertEqual(self.pcm(always, unit), self.pcm(partial, unit))   # the same samples
                    self.assertEqual(audio_of(a)[1], audio_of(b)[1])
                else:
                    self.assertFalse((Path(partial) / f"audio-{unit:03d}.bin").exists())

    def test_the_build_forbids_fused_multiply_add(self):
        import json
        commands = json.loads((BUILD / "compile_commands.json").read_text())
        audio = next(c for c in commands if c["file"].endswith("/build/upstream/audio.cpp"))
        self.assertIn("-ffp-contract=off", audio["command"])

    def test_n_calls_and_continuous_advance_flush_paula_alike(self):
        for rom in ("tone_all", "long_ops"):
            steps = run_probe(self.exe, self.roms[rom], 7, "steps", 1, timeout=300, extra=["pal"])
            continuous = run_probe(self.exe, self.roms[rom], 7, "continuous", 1, timeout=300, extra=["pal"])
            self.assertEqual(steps, continuous)


if __name__ == "__main__":
    unittest.main()
