"""MED02 (T27): a track the guest writes in a format that is not AmigaDOS is not lost.

A plain ADF has eleven sectors per track and nowhere to put anything else, and the engine used to drop a written track that did not decode into them. Here the micro-ROM
of med02.py (raw_rom_source) writes a revolution that has no sector in it (a sync word of its own, 0x8914, and a pattern), moves the head away and back and reads it with that
sync. What it reads is what it wrote. The raw track is machine state like the sectors: a state taken before it takes it back, one taken after it brings it, the disk cannot be exported
as an ADF while it holds one (and says why), and writing the track as AmigaDOS afterwards replaces it.
"""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_med02 import Med02Case, POOL, RAW_SLOTS, RAW_WORDS, overlay_of, raw_of, with_overlay, sha  # noqa: E402
import med02  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402

PATTERN = struct.pack(">%dH" % med02.RAW_PATTERN_WORDS, *med02.raw_pattern())


class Med02RawCase(Med02Case):
    ROMS = {"med02": (med02.rom_source(), None, None), "raw": (med02.raw_rom_source(), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.dir / "raw.bin").write_bytes(med02.raw_buffer_bytes())

    @classmethod
    def raw_args(cls, writable=True, poke=True):
        args = cls.args_for(writable, False)
        if poke:
            args += ["--ram-at", "1", hex(med02.RAW_ADDR), str(cls.dir / "raw.bin"), "--ram-at", "1", hex(med02.BUF_ADDR["buf1"]), str(cls.dir / "buf1.bin")]
        return args

    @classmethod
    def run_raw(cls, *arguments):
        import subprocess
        from tests.state.harness import Run
        result = subprocess.run([str(cls.exe), str(cls.roms["raw"]), *arguments], capture_output=True, text=True, timeout=1800)
        assert result.returncode == 0, result.stderr[-400:]
        return result.stdout, Run(result.stdout)


class Med02RawTests(Med02RawCase):
    @classmethod
    def scenario(cls):
        if "scenario" not in cls.cache:
            d = cls.dir
            ops = ["--save-at", str(med02.RAW_STATE1), str(d / "r1.state"), "--status-at", str(med02.RAW_STATE1), "0",
                   "--ramout-at", str(med02.RAW_STATE2), hex(disk.MARK_BASE), "16", str(d / "marks2.bin"),
                   "--ramout-at", str(med02.RAW_STATE2), hex(med02.CAPTURE["r1"]), str(2 * med02.RAW_PATTERN_WORDS), str(d / "cap_raw.bin"),
                   "--export-at", str(med02.RAW_STATE2), "0", str(d / "e2.bin"), "--status-at", str(med02.RAW_STATE2), "0",
                   "--save-at", str(med02.RAW_STATE2), str(d / "r2.state"),
                   "--ramout-at", str(med02.RAW_END), hex(disk.MARK_BASE), "16", str(d / "marks_end.bin"),
                   "--ramout-at", str(med02.RAW_END), hex(med02.CAPTURE["r2"]), str(2 * med02.CAPTURE_WORDS), str(d / "cap_ad.bin"),
                   "--export-at", str(med02.RAW_END), "0", str(d / "e_end.bin"), "--status-at", str(med02.RAW_END), "0",
                   "--save-at", str(med02.RAW_END), str(d / "r_end.state")]
            cls.cache["scenario"] = cls.run_raw("--units", str(med02.RAW_END), "--every", "100", *cls.raw_args(), *ops)
        return cls.cache["scenario"]

    def test_the_track_is_kept_exactly_read_back_and_stops_the_export(self):
        text, run = self.scenario()
        d = self.dir
        status, export = self.statuses(text), self.exports(text)
        self.assertEqual(status[med02.RAW_STATE1, 0]["used"], 0)
        self.assertEqual(med02.raw_markers((d / "marks2.bin").read_bytes()), ["raw_w", "raw_r"])
        # What the guest read after the sync is what it wrote after it: from the raw copy, since the head had left the track.
        slipped = med02.same_raw_read((d / "cap_raw.bin").read_bytes(), PATTERN)
        self.assertIsNot(slipped, False, "the guest did not read back what it wrote (bit for bit, up to the drive's own slip at the index)")
        # The disk holds a track an ADF cannot represent: the export says so and writes nothing; the status counts it.
        self.assertEqual(export[med02.RAW_STATE2, 0]["code"], 4)
        self.assertEqual(export[med02.RAW_STATE2, 0]["written"], 0)
        self.assertEqual(text.count("format that is not AmigaDOS"), 1)
        self.assertRegex(text, r"status %d 0 0 capacity=%d used=0 medium=0 refused=0 raw=1 " % (med02.RAW_STATE2, POOL))
        tracks, _ = raw_of((d / "r2.state").read_bytes())
        self.assertEqual([(m, t) for m, t, _, _ in tracks], [(0, 0)])
        medium, track, bits, words = tracks[0]
        self.assertIn(bits, (6334 * 16, 6392 * 16))                  # the revolution it wrote (PAL: 12668 bytes)
        # The words are in the order of the drive's track buffer, which is a rotation of the revolution the guest wrote (it starts where the head was): the sync, then the pattern.
        values = struct.unpack("<%dH" % (len(words) // 2), words)
        self.assertEqual(values.count(med02.RAW_SYNC), 1)
        at = values.index(med02.RAW_SYNC)
        self.assertEqual([values[(at + 1 + i) % len(values)] for i in range(med02.RAW_PATTERN_WORDS)], med02.raw_pattern())

    def test_writing_the_track_as_amigados_replaces_the_raw_copy(self):
        text, run = self.scenario()
        d = self.dir
        self.assertEqual(med02.raw_markers((d / "marks_end.bin").read_bytes()), list(med02.RAW_MARKERS))
        status, export = self.statuses(text), self.exports(text)
        self.assertEqual(status[med02.RAW_END, 0]["raw"], 0)
        self.assertRegex(text, r"status %d 0 0 capacity=%d used=11 medium=11 refused=0 raw=0 " % (med02.RAW_END, POOL))
        self.assertEqual(export[med02.RAW_END, 0]["code"], 0)
        self.assertEqual((d / "e_end.bin").read_bytes(), med02.expected_image(self.a, ["buf1"]))
        good, found = med02.sectors_of((d / "cap_ad.bin").read_bytes(), "buf1")
        self.assertEqual((good, found), (11, 11))
        self.assertEqual(raw_of((d / "r_end.state").read_bytes())[0], [])

    def test_a_state_takes_the_raw_track_back_or_brings_it(self):
        d = self.dir
        self.scenario()
        # Rewind: load the state of unit 50, before the guest wrote anything, at the end: the disk is the base, no raw track, and the future repeats up to the end.
        ops = ["--load-at", str(med02.RAW_END), str(d / "r1.state"), "--status-at", str(med02.RAW_END), "0", "--export-at", str(med02.RAW_END), "0", str(d / "back1.bin"),
               "--save-at", str(med02.RAW_END + 240), str(d / "again.state")]
        text, run = self.run_raw("--units", str(med02.RAW_END + 240), "--every", "240", *self.raw_args(), *ops)
        self.assertEqual([(med02.RAW_END, 0, "")], run.loads)
        self.assertRegex(text, r"status %d 0 0 capacity=%d used=0 medium=0 refused=0 raw=0 " % (med02.RAW_END, POOL))
        self.assertEqual((d / "back1.bin").read_bytes(), self.a)
        # S2 loaded in a new process brings the raw track; the disk cannot be exported; the future is the original's, byte for byte.
        ops = ["--load-at", "0", str(d / "r2.state"), "--status-at", "0", "0", "--export-at", "0", "0", str(d / "n.bin"),
               "--save-at", str(med02.RAW_END - med02.RAW_STATE2), str(d / "n_end.state")]
        text, run = self.run_raw("--units", str(med02.RAW_END - med02.RAW_STATE2), "--every", "200", *self.raw_args(poke=False), *ops)
        self.assertRegex(text, r"status 0 0 0 capacity=%d used=0 medium=0 refused=0 raw=1 " % POOL)
        self.assertEqual(self.exports(text)[0, 0]["code"], 4)
        self.assertEqual((d / "n_end.state").read_bytes(), (d / "r_end.state").read_bytes())
        # The state saved right after a load is the state that was loaded (the raw track included).
        ops = ["--load-at", "5", str(d / "r2.state"), "--save-at", "5", str(d / "resave.state")]
        self.run_raw("--units", "6", "--every", "6", *self.raw_args(), *ops)
        self.assertEqual((d / "resave.state").read_bytes(), (d / "r2.state").read_bytes())

    def test_raw_tracks_that_no_machine_could_have_are_refused(self):
        state = self.state_of("good.state", "raw", med02.RAW_STATE2, *self.raw_args())
        tracks, _ = raw_of(state)
        self.assertEqual(len(tracks), 1)
        medium, track, bits, words = tracks[0]
        _, slots, _, _ = overlay_of(state)
        attempts = [
            ("a raw track named twice", with_overlay(state, slots, tracks=tracks + tracks), 9, "twice"),
            ("a raw track of a medium beyond the manifest", with_overlay(state, slots, tracks=[(2, track, bits, words)]), 9, "manifest"),
            ("a raw track beyond the disk", with_overlay(state, slots, tracks=[(0, 160, bits, words)]), 9, "range"),
            ("a raw track of zero bits", with_overlay(state, slots, tracks=[(0, 0, 0, b"")]), 9, "range"),
            ("a raw track longer than a slot", with_overlay(state, slots, tracks=[(0, 0, RAW_WORDS * 16 + 1, bytes(2 * RAW_WORDS + 2))]), 9, "range"),
            ("a raw track cut short", with_overlay(state, slots, tracks=[(0, 0, bits, words[:-2])]), 9, "body"),
            ("more raw tracks than slots", with_overlay(state, slots, tracks=[(0, t, 16, b"\0\0") for t in range(RAW_SLOTS + 1)]), 9, "bound"),
        ]
        self.refusals(attempts, rom="raw", extra=self.raw_args(), unit=med02.RAW_STATE2, units=med02.RAW_STATE2 + 3)


if __name__ == "__main__":
    unittest.main()
