"""T22a: the movie of the PUAE6 core in BizHawk's own BK2 input log, written and read by BizHawk's own code.

The classes that EmuHawk uses for the input log (Bk2LogEntryGenerator, Bk2Controller, IMovieController; from the pinned capture of MacHawk, references/machawk) are compiled into
the test host. A session of the core is recorded through them: one "|...|" line per frame, one group per player and one character per button, axes with their value, the
LogKey from the core's ControllerDefinition, and a header that ties the movie to the guest, the settings, the Kickstart and the disks (the keys PUAE6* and BizHawk's Core and
Platform; the end of the movie records CycleCount and ClockRate, which the core now gives through ICycleTiming).

* every button and axis of every frame decodes to what was recorded (the absolute mouse, the ports, Power and the disk buttons included);
* a movie played from its file leaves the machine exactly as the live session did, unit by unit and in the end state;
* the movie of another core (the UAE core of BizHawk), another system, another guest, other settings, another Kickstart or other disks is refused with its reason before a
  core runs a unit, and nothing converts it.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/bizhawk-movies -p "test_movie_format.py"
"""
from pathlib import Path
import re
import unittest

from harness import AVAILABLE, Harness, fixture_inputs


@unittest.skipUnless(AVAILABLE, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class MovieFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness()
        cls.script, cls.press = fixture_inputs(cls.h)
        cls.units = 240
        cls.movie = cls.h.dir / "fixture.bk2"
        out = cls.h.session([cls.h.core("R"), f"run R {cls.units} script={cls.script} press={cls.press} record", f"endmovie R {cls.movie}", "digest R end"]).stdout
        cls.recorded_units = cls.h.units(out, "R")
        cls.recorded_digest = cls.h.digests(out, "R")["end"]
        cls.header, cls.key, cls.entries, cls.log = cls.h.read_movie(cls.movie)

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def test_the_file_is_a_bk2_with_a_header_a_log_key_and_one_line_per_frame(self):
        self.assertEqual(self.units, len(self.entries))
        self.assertEqual("[Input]", self.log[0])
        self.assertEqual("[/Input]", self.log[-1])
        groups = self.key.split("#")[1:]
        self.assertEqual(3, len(groups), "console buttons (Power, the keyboard, the disk buttons), then the buttons and axes of port 1 and of port 2")
        for entry in self.entries:
            self.assertTrue(entry.startswith("|") and entry.endswith("|"))
            self.assertEqual(len(groups) + 1, entry.count("|"), "one group per player")
        names = [n for g in groups for n in g.split("|") if n]
        self.assertIn("Power", names)
        self.assertIn("P1 Mouse X", names)
        self.assertIn("Insert Disk", names)
        self.assertEqual(len(names), len(set(names)), "every control once")
        for axis in ("P1 Mouse X", "P1 Mouse Y"):
            self.assertRegex(self.entries[0], r"\s+\d+,", f"{axis} is written with its value")

    def test_the_header_ties_the_movie_to_this_core_and_records_the_end_cycle_count(self):
        h = self.header
        self.assertEqual(("PUAE6", "Amiga"), (h["Core"], h["Platform"]))
        for key in ("PUAE6Guest", "PUAE6Config", "PUAE6Firmware", "PUAE6Media"):
            self.assertRegex(h[key], r"^[0-9a-f]{64}$", key)
        self.assertEqual(self.h.sha, h["PUAE6Guest"])
        self.assertEqual(int(self.recorded_units[-1][2]), int(h["CycleCount"]), "the cycle count at the end of the movie is the master ticks of its last unit")
        self.assertEqual(512.0 * 3546895.0, float(h["ClockRate"]))
        self.assertIn('"Region"', h["SyncSettings"])

    def test_every_button_and_axis_of_every_frame_decodes_to_what_was_recorded(self):
        out = self.h.session([self.h.core("V"), f"movieverify V {self.movie} {self.units} script={self.script} press={self.press}"]).stdout
        m = re.search(r"movie verified frames=(\d+) buttons=(\d+) axes=(\d+) compared=(\d+) entries=(\d+)", out)
        frames, buttons, axes, compared, entries = map(int, m.groups())
        self.assertEqual((self.units, self.units), (frames, entries))
        self.assertEqual(frames * (buttons + axes), compared)
        self.assertEqual(2, axes)
        self.assertGreater(buttons, 100)
        # a recording that really has something in it: keys, the mouse button, both joysticks, Power and the four disk buttons
        pressed = "".join(self.entries)
        self.assertGreater(len(set(re.findall(r"[^|.,\s\d-]", pressed))), 8)

    def test_a_movie_played_from_its_file_leaves_the_machine_as_the_live_session_did(self):
        out = self.h.session([self.h.core("P"), f"run P {self.units} movie={self.movie}", "digest P end"]).stdout
        self.assertEqual([u[1:] for u in self.recorded_units], [u[1:] for u in self.h.units(out, "P")])
        self.assertEqual(self.recorded_digest, self.h.digests(out, "P")["end"])

    def test_a_movie_that_is_not_this_cores_is_refused_with_its_reason_before_a_unit_runs(self):
        h = self.h
        cases = [("recorded with the UAE core of BizHawk", {"Core": "UAE"}, "WrongCore"), ("no core", {"Core": None}, "MissingKey"), ("another system", {"Platform": "SNES"}, "WrongSystem"),
                 ("another guest", {"PUAE6Guest": "0" * 64}, "OtherGuest"), ("other settings", {"PUAE6Config": "1" * 64}, "OtherConfig"),
                 ("another firmware", {"PUAE6Firmware": "2" * 64}, "OtherFirmware"), ("other disks", {"PUAE6Media": "3" * 64}, "OtherMedia"),
                 ("no identity key", {"PUAE6Guest": None}, "MissingKey")]
        for what, edits, reason in cases:
            with self.subTest(what=what):
                bent = h.rewrite_movie(self.movie, h.dir / f"bent-{reason}-{len(what)}.bk2", edits)
                out = h.session([h.core("P"), f"moviecheck P {bent}", f"run P 5 movie={bent}"], expect=1)
                self.assertRegex(out.stdout, rf"movie rejected P reason={reason} message=")
                self.assertEqual([], h.units(out.stdout, "P"), "not a unit ran")
        # the movie of the same core with another setup: other region, seed, drives, ports, firmware and disks
        for what, core_line, reason in (("NTSC", h.core("P", ntsc=True), "OtherConfig"), ("another seed", h.core("P", seed=2), "OtherConfig"),
                                        ("two drives", h.core("P", drives=2), "OtherConfig"), ("a joystick in port 1", h.core("P", port1="joystick"), "OtherConfig"),
                                        ("other disks", h.core("P", adf="B,A"), "OtherMedia"), ("no disks", h.core("P", adf=""), "OtherMedia"),
                                        ("another firmware", h.core("P", firmware="input_all"), "OtherFirmware")):
            with self.subTest(core=what):
                out = h.session([core_line, f"moviecheck P {self.movie}"]).stdout
                self.assertRegex(out, rf"movie rejected P reason={reason} message=")
        accepted = h.session([h.core("P"), f"moviecheck P {self.movie}"]).stdout
        self.assertIn("movie accepted P", accepted)


if __name__ == "__main__":
    unittest.main()
