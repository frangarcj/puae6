"""T18: save states, rewind and runahead through the libretro adapter (STATE01, STATE02, DET02, DET05, LR02), run by the minimal frontend.

The core frames the native state (p6_state_*, T15) for libretro: a header, the adapter's own section (disk tray, audio not yet taken, last picture), the
native state and zero padding up to retro_serialize_size. tests/libretro-host/host.cpp plays the frontend: it saves into buffers of exactly that size,
loads blobs that were cut, extended, flipped or taken from another configuration, and runs ahead the way RetroArch does (save, k runs nobody sees, load).
Everything is compared with a plain run of the same script: the pictures, ticks and audio of each unit, and the hash of the whole state at the end,
which is the machine, the media queue and the adapter's side together. Run: python3 -m unittest discover -s tests/libretro-host -p "test_state.py"

Three gaps of the native state showed up here first and are pinned by the test_GAP_* tests at the end of StateTests: the picture of a unit without drawn
lines after a load came from the abandoned timeline (or a fresh process), a unit that had no new picture was drawn after a load, and one byte of the
chipset body (DIWHIGH) differed after save-load-save. T15j closed them in the native state (patch 0103: PIXL, GEOM and BEAM), and the tests now pass.
The blob variants that cut a state are relative to the bytes it uses (usedsize): serialize_size is mostly zero padding since PIXL's worst case is in the bound.

With P6_KICKSTART_ROM and P6_GAME_ADF set (the paths of a Kickstart 1.3 and a game disk), one test also boots that disk for a few hundred units and saves
and loads in the middle of it; it never copies, hashes or prints those files, and it is skipped without the variables.
"""
import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

SAVE = re.compile(r"^state save (\S+) ok=(\d) size=(\d+) used=(\d+) hash=(\w+) section=(\w+)$", re.M)
LOAD = re.compile(r"^state load (\S+) (\S+) ok=(\d) size=(\d+)$", re.M)
FINAL = re.compile(r"^state final ok=(\d) size=(\d+) hash=(\w+)$", re.M)
IMAGES = re.compile(r"^state images count=(\d+) index=(\d+) eject=(\d)$", re.M)
SPEC = re.compile(r"^spec (\d+) video=\S+ dupe=(\d) hash=(\w+) px=(\w+) audio=(\d+) ahash=(\w+) unit=(\d+) ticks=(\d+)$", re.M)

# Input that changes every few positions: UP, then B+A+X+UP+RIGHT on port 0, LEFT on port 1, all released. The position, not the run, decides.
INPUT = ("--joy-at", "4:0:10", "--joy-at", "8:0:391", "--joy-at", "12:1:40", "--joy-at", "15:0:0", "--joy-at", "19:1:0", "--joy-at", "21:0:80")


def view(run, picture=True):
    """What one unit looked like to the frontend: the unit, its ticks and its audio, and (unless `picture` is False) the picture."""
    return (run["unit"], run["ticks"], run["audio"], run["ahash"]) + ((run["hash"], run["px"]) if picture else ())


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class StateTests(lr.LibretroHarness):
    def views(self, out, picture=True):
        return [view(r, picture) for r in self.runs(out)]

    def by_unit(self, out, picture=True):
        """unit -> every time the frontend saw it (a replay after a load sees a unit again)."""
        seen = {}
        for run in self.runs(out):
            seen.setdefault(run["unit"], []).append(view(run, picture))
        return seen

    def assert_replays(self, out, first, last, again=2, picture=True):
        """Units first..last were seen `again` times and every time exactly the same."""
        seen = self.by_unit(out, picture)
        for unit in range(first, last + 1):
            self.assertEqual(len(seen[unit]), again, f"unit {unit}: {seen[unit]}")
            self.assertEqual(len(set(seen[unit])), 1, f"unit {unit} differs after the load: {seen[unit]}")

    @staticmethod
    def final(out):
        found = FINAL.search(out)
        return found and found[3]

    def saved(self, out):
        return {m[1]: {"ok": m[2] == "1", "size": int(m[3]), "used": int(m[4]), "hash": m[5], "section": m[6]} for m in SAVE.finditer(out)}

    # --- STATE01: the size --------------------------------------------------------------------------------------------------------------

    def test_the_size_is_the_frame_plus_the_native_bound_and_does_not_move_with_options_content_or_time(self):
        sizes = {}
        base = self.frontend("boot", "--runs", 30, "--do", "15:save:a")
        sizes["default"] = int(re.search(r"state size=(\d+)", base.stdout)[1])
        self.assertEqual(self.saved(base.stdout)["a"]["size"], sizes["default"])
        self.assertLess(self.saved(base.stdout)["a"]["used"], sizes["default"])             # the bound is a ceiling: there is padding
        # A presentation option and a reboot option changed while it runs (the host checks the size after every run), another audio rate and NTSC.
        self.frontend("boot", "--runs", 20, "--set-at", "5:puae6_video_aspect=4:3", "--set-at", "8:puae6_audio_rate=44100")
        for options in (("--set", "puae6_video_standard=NTSC"), ("--set", "puae6_audio_rate=44100"), ("--set", "puae6_video_aspect=4:3")):
            size = int(re.search(r"state size=(\d+)", self.frontend("boot", "--runs", 3, *options).stdout)[1])
            if "puae6_video_aspect=4:3" in options:
                self.assertEqual(size, sizes["default"])
            self.assertGreater(size, 1_000_000)
        # The media are part of what the bound is built from (the manifest is in the state), and the number of loads does not matter.
        with_disks = self.frontend("boot", "--runs", 3, "--cycles", 2, content=self.playlist("a.adf\nb.adf\n")).stdout
        found = re.findall(r"state size=(\d+)", with_disks)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0], found[1])

    def test_a_buffer_smaller_than_the_size_is_refused_and_a_larger_one_is_written_only_up_to_the_size(self):
        out = self.frontend("boot", "--runs", 6, "--do", "3:save:x:short", "--do", "3:save:x:long", "--do", "3:save:ok").stdout
        self.assertIn("state save x short ok=0 tail=intact", out)
        self.assertIn("state save x long ok=1 tail=intact", out)
        self.assertTrue(self.saved(out)["ok"]["ok"])
        early = self.frontend("boot", "--runs", 3, "--do", "0:save:early", "--do", "1:save:late").stdout
        self.assertFalse(self.saved(early)["early"]["ok"])            # no machine to describe before the first unit: refused, not a violation
        self.assertTrue(self.saved(early)["late"]["ok"])

    # --- DET02 through libretro: save N, run to M, load, M again ---------------------------------------------------------------------------

    def test_a_load_replays_the_same_units_whatever_the_rom_and_the_moment(self):
        for rom in ("boot", "input_all", "long_frame"):
            for save_at, load_at in ((10, 22), (3, 14)):
                with self.subTest(rom=rom, save_at=save_at, load_at=load_at):
                    control = self.frontend(rom, "--runs", load_at, *INPUT).stdout
                    out = self.frontend(rom, "--runs", load_at + 8, *INPUT, "--do", f"{save_at}:save:a", "--do", f"{load_at}:save:before",
                                        "--do", f"{load_at}:load:a", "--do", f"{load_at}:save:again").stdout
                    self.assertEqual(self.views(out)[:load_at], self.views(control))                      # saving changed nothing
                    picture = rom != "long_frame"                          # see the gaps in the docstring
                    self.assert_replays(out, save_at + 1, save_at + 8, picture=picture)
                    saves = self.saved(out)
                    self.assertEqual(saves["again"]["hash"], saves["a"]["hash"])                           # the loaded machine is the saved one, byte for byte
                    self.assertNotEqual(saves["before"]["hash"], saves["a"]["hash"])
                    self.assertEqual(self.views(out, picture)[load_at:], self.views(out, picture)[save_at:save_at + 8])

    def test_a_state_file_loaded_by_another_process_continues_as_the_first_one_did(self):
        path = self.work() / "x.state"
        first = self.frontend("input_all", "--runs", 14, *INPUT, "--do", f"6:savefile:{path}", system=self.system("input_all")).stdout
        self.assertEqual(path.stat().st_size, int(re.search(r"state size=(\d+)", first)[1]))                  # a file is exactly serialize_size bytes
        second = self.frontend("input_all", "--runs", 8, *INPUT, "--do", f"0:loadfile:{path}:pos=6", system=self.system("input_all")).stdout
        self.assertIn("ok=1", LOAD.search(second)[0])
        self.assertEqual(self.views(second), self.views(first)[6:])
        # The same file with a presentation option changed is the same machine; the aspect is not part of it.
        third = self.frontend("input_all", "--runs", 8, *INPUT, "--set", "puae6_video_aspect=4:3", "--do", f"0:loadfile:{path}:pos=6",
                              system=self.system("input_all")).stdout
        self.assertEqual(self.views(third), self.views(first)[6:])

    # --- STATE02: what is refused, and that nothing changes -----------------------------------------------------------------------------------

    ACCEPTED = ("", "size=-1", "usedsize=0", "size=4096", "size=65536", "size=100,fill=0x00")
    REFUSED = ("usedsize=-1000000", "usedsize=-1", "usedsize=-100000", "xor=-1", "xor=100000", "xor=0", "xor=8", "xor=12", "xor=20", "xor=26", "xor=40",
               "xor=70", "xorend=0", "size=4096,fill=0x33", "size=1,fill=0x01", "usedsize=-1000")

    def test_a_blob_with_the_right_length_or_zero_padding_loads_and_replays(self):
        for variant in self.ACCEPTED:
            with self.subTest(variant=variant or "exact"):
                out = self.frontend("input_all", "--runs", 14, *INPUT, "--do", "5:save:a", "--do", f"8:load:a:{variant}" if variant else "8:load:a").stdout
                self.assertEqual(LOAD.search(out)[3], "1")
                self.assert_replays(out, 6, 8)

    def test_a_blob_that_is_cut_flipped_padded_with_garbage_or_not_a_state_is_refused_and_the_core_continues_untouched(self):
        script = []
        for variant in self.REFUSED:
            script += ["--do", f"8:load:a:{variant}"]
        control = self.frontend("input_all", "--runs", 14, *INPUT, "--do", "5:save:a").stdout
        out = self.frontend("input_all", "--runs", 14, *INPUT, "--do", "5:save:a", *script).stdout
        loads = LOAD.findall(out)
        self.assertEqual(len(loads), len(self.REFUSED))
        self.assertEqual([(m[1], m[2]) for m in loads], [(v, "0") for v in self.REFUSED])
        self.assertEqual(self.views(out), self.views(control))           # sixteen refusals later, the same units as if nothing had been tried
        self.assertEqual(self.final(out), self.final(control))           # and the very same machine, byte for byte
        self.assertRegex(out, r"log 2 \[PUAE6\] the state is refused: the bytes after the state are not zero")

    def test_a_zero_blob_a_noise_blob_and_a_blob_with_a_valid_header_over_noise_are_refused(self):
        path = self.work() / "noise.state"
        size = int(re.search(r"state size=(\d+)", self.frontend("boot", "--runs", 1).stdout)[1])
        control = self.frontend("boot", "--runs", 5, "--do", "5:save:end").stdout
        for label, data in (("zeros", bytes(size)), ("noise", (bytes(range(256)) * (size // 256 + 1))[:size]), ("tiny", b"P6LRSTAT"), ("empty", b"")):
            with self.subTest(label=label):
                path.write_bytes(data)
                out = self.frontend("boot", "--runs", 5, "--do", f"2:loadfile:{path}", "--do", "5:save:end").stdout
                self.assertEqual(LOAD.search(out)[3], "0")
                self.assertEqual(self.views(out), self.views(control))
                self.assertEqual(self.saved(out)["end"]["hash"], self.saved(control)["end"]["hash"])

    def test_a_state_of_another_configuration_firmware_or_media_is_refused_and_the_target_runs_as_if_it_had_not_been_tried(self):
        path = self.work() / "a.state"
        files = ["a.adf", "b.adf", "c.adf"]
        disks = self.playlist("a.adf\nb.adf\n", files)
        self.frontend("boot", "--runs", 8, "--do", f"5:savefile:{path}", content=disks)                       # PAL, 48 kHz, boot ROM, disks a and b
        same = self.frontend("boot", "--runs", 9, "--do", f"5:loadfile:{path}:pos=5", content=disks, system=self.system("boot")).stdout
        self.assertEqual(LOAD.search(same)[3], "1")                                                      # control: the very same machine accepts it
        cases = (("a600", dict(options=lr.A600, name=lr.KICKSTART_NAMES_A600[0])), ("a1200", dict(options=lr.A1200, name=lr.KICKSTART_NAMES_A1200[0])),
                 ("ntsc", dict(options=("--set", "puae6_video_standard=NTSC"))), ("rate", dict(options=("--set", "puae6_audio_rate=44100"))),
                 ("firmware", dict(rom="input_all")), ("media", dict(content=self.playlist("a.adf\nc.adf\n", files))),
                 ("no media", dict(content=None)), ("media in another order", dict(content=self.playlist("b.adf\na.adf\n", files))),
                 ("another disk first", dict(content=self.playlist("c.adf\na.adf\nb.adf\n", files))))
        for label, case in cases:
            with self.subTest(case=label):
                arguments = ("--runs", 9, *case.get("options", ()))
                kwargs = {"content": case["content"] if "content" in case else disks}
                if "name" in case:
                    kwargs["name"] = case["name"]
                target = case.get("rom", "boot")
                control = self.frontend(target, *arguments, **kwargs).stdout
                out = self.frontend(target, *arguments, "--do", f"5:loadfile:{path}", **kwargs).stdout
                self.assertEqual(LOAD.search(out)[3], "0", label)
                self.assertEqual(self.views(out), self.views(control))
                self.assertEqual(self.final(out), self.final(control))

    def test_a_state_of_the_a600_loads_in_the_a600_and_is_refused_by_the_a500_and_the_other_way_round(self):
        # T23d: the machine is in the configuration the state identity carries, so the adapter hands the native load a state of another machine and gets a
        # refusal that leaves the target running as if nothing had been tried.
        both = self.system_for("boot", lr.KICKSTART_NAMES[0], lr.KICKSTART_NAMES_A600[0])
        files = {}
        for machine, options in (("A500", ()), ("A600", lr.A600)):
            files[machine] = self.work() / f"{machine}.state"
            saved = self.frontend("boot", "--runs", 8, *options, "--do", f"5:savefile:{files[machine]}", system=both).stdout
            self.assertEqual(self.saved(saved)[str(files[machine])]["ok"], True)
        for machine, options in (("A500", ()), ("A600", lr.A600)):
            other = "A600" if machine == "A500" else "A500"
            control = self.frontend("boot", "--runs", 9, *options, system=both).stdout
            same = self.frontend("boot", "--runs", 9, *options, "--do", f"5:loadfile:{files[machine]}:pos=5", system=both).stdout
            self.assertEqual(LOAD.search(same)[3], "1", f"the {machine} state in the {machine}")
            refused = self.frontend("boot", "--runs", 9, *options, "--do", f"5:loadfile:{files[other]}", system=both).stdout
            self.assertEqual(LOAD.search(refused)[3], "0", f"the {other} state in the {machine}")
            self.assertEqual(self.views(refused), self.views(control))
            self.assertEqual(self.final(refused), self.final(control))

    def test_a_state_of_another_machine_is_refused_by_the_a1200_and_the_a500_and_a600_refuse_the_one_of_the_a1200(self):
        # T24e: the machine is in the state identity for the three machines. The refusal is the adapter's: the A500 and A600 states are real states that the A1200
        # must hand to the native load and get refused, and what the A1200 itself saves is only identity here (its complete native state is T24c).
        both = self.system_for("boot", *(names[0] for _, names, _, _ in lr.MACHINES.values()))
        files = {}
        for machine, (options, _, _, _) in lr.MACHINES.items():
            files[machine] = self.work() / f"{machine}.state"
            saved = self.frontend("boot", "--runs", 8, *options, "--do", f"5:savefile:{files[machine]}", system=both).stdout
            if not self.saved(saved)[str(files[machine])]["ok"]:
                self.assertEqual(machine, "A1200")
                self.skipTest("T24c: the A1200 has no native state yet (020 CPU state, AGA palette), so its state cannot be saved")
        for machine, (options, _, _, _) in lr.MACHINES.items():
            control = self.frontend("boot", "--runs", 9, *options, system=both).stdout
            same = self.frontend("boot", "--runs", 9, *options, "--do", f"5:loadfile:{files[machine]}:pos=5", system=both).stdout
            self.assertEqual(LOAD.search(same)[3], "1", f"the {machine} state in the {machine}")
            for other in lr.MACHINES:
                if other == machine:
                    continue
                refused = self.frontend("boot", "--runs", 9, *options, "--do", f"5:loadfile:{files[other]}", system=both).stdout
                self.assertEqual(LOAD.search(refused)[3], "0", f"the {other} state in the {machine}")
                self.assertEqual(self.views(refused), self.views(control))
                self.assertEqual(self.final(refused), self.final(control))

    def test_a_state_loads_in_a_machine_whose_media_start_with_the_ones_it_was_taken_with(self):
        # T17e: the manifest only grows, so the state of the disks a and b is a state of a machine that has a, b and c (the playlist lists one more at the end).
        path = self.work() / "ab.state"
        files = ["a.adf", "b.adf", "c.adf"]
        disks = self.playlist("a.adf\nb.adf\n", files)
        more = self.playlist("a.adf\nb.adf\nc.adf\n", files)
        same = self.frontend("boot", "--runs", 9, "--do", f"5:savefile:{path}", "--do", f"5:loadfile:{path}", content=disks).stdout
        out = self.frontend("boot", "--runs", 9, "--do", f"5:loadfile:{path}", content=more, system=self.system("boot")).stdout
        self.assertEqual(LOAD.search(out)[3], "1")
        self.assertEqual(self.views(out)[5:], self.views(same)[5:])

    # --- the adapter's side of a state: the disk tray ----------------------------------------------------------------------------------------

    def three_disks(self):
        return self.playlist("a.adf|A\nb.adf|B\nc.adf|C\n", ["a.adf", "b.adf", "c.adf"])

    def test_a_state_saved_with_the_tray_open_restores_the_open_tray_and_the_chosen_disk(self):
        content = self.three_disks()
        steps = ("--disk", "10:eject", "--disk", "14:index=1", "--disk", "15:insert")
        control = self.frontend("observer", "--runs", 135, *steps, content=content).stdout
        out = self.frontend("observer", "--runs", 280, *steps, "--do", "12:save:open", "--do", "12:images", "--do", "140:images", "--do", "140:load:open",
                            "--do", "140:images", content=content).stdout
        before, after, restored = [(int(m[1]), int(m[2]), m[3] == "1") for m in IMAGES.finditer(out)][:3]
        self.assertEqual(before, (3, 0, True))                    # the tray was open on disk 0 when the state was taken
        self.assertEqual(after, (3, 1, False))                    # by run 140 the person had chosen disk 1 and closed it
        self.assertEqual(restored, (3, 0, True))                  # the load put the frontend's view of the drive back
        self.assert_replays(out, 13, 132)                          # ... and the replay (disk 1 chosen, closed, inserted a hundred units later) is the same
        replay = self.by_unit(out)
        original = self.by_unit(control)
        for unit in range(13, 136):
            self.assertEqual(replay[unit][0], original[unit][0], f"unit {unit}")
        self.assertGreater(len({r["px"] for r in self.runs(control)}), 1)                          # the observer did see the drive change

    def test_a_state_saved_with_requests_not_yet_applied_applies_them_after_the_load_and_forgets_those_made_after_it(self):
        content = self.playlist("a.adf|A\nb.adf|B\n")
        control = self.frontend("observer", "--runs", 40, "--do", "12:disk:eject", "--do", "14:save:end", content=content).stdout
        # The eject is only recorded when the state is taken; the tray is closed again (also only recorded) before the load, and the load must bring
        # back the recorded eject, which the next run turns into the event.
        out = self.frontend("observer", "--runs", 44, "--do", "12:disk:eject", "--do", "12:save:p", "--do", "12:disk:insert", "--do", "12:images",
                            "--do", "20:load:p", "--do", "20:images", "--do", "22:save:end", content=content).stdout
        first, second = [(int(m[2]), m[3] == "1") for m in IMAGES.finditer(out)]
        self.assertEqual(first, (0, False))
        self.assertEqual(second, (0, True))
        self.assertEqual(self.saved(out)["end"]["section"], self.saved(control)["end"]["section"])                  # the adapter's side at unit 14 of both
        self.assertEqual(self.views(out)[20:22], self.views(control)[12:14])
        # The tray closed again before the load is still an eject and an insert for the original timeline; the replay brings back only the eject.
        self.assertEqual(re.findall(r"log \d+ \[PUAE6\] (DF0: [^\n]*)", out), ["DF0: medium 0 inserted", "DF0: ejected", "DF0: medium 0 inserted", "DF0: ejected"])

    def test_a_request_made_on_a_speculative_run_does_not_reach_the_future_and_the_swap_happens_when_it_should(self):
        content = self.three_disks()
        steps = ("--disk", "10:eject", "--disk", "14:index=1", "--disk", "15:insert", "--disk", "30:eject", "--disk", "31:index=2", "--disk", "32:insert")
        plain = self.frontend("observer", "--runs", 150, *steps, "--do", "150:images", "--do", "150:save:end", content=content).stdout
        for k in (1, 2, 3, 5):
            with self.subTest(k=k):
                out = self.frontend("observer", "--runs", 150, *steps, "--ahead-every", k, "--do", "150:images", "--do", "150:save:end", content=content).stdout
                self.assertEqual(self.views(out), self.views(plain))
                self.assertEqual(self.saved(out)["end"]["section"], self.saved(plain)["end"]["section"])
                self.assertEqual(IMAGES.findall(out), IMAGES.findall(plain))
        self.assertGreater(len({r["px"] for r in self.runs(plain)}), 1)                            # the observer did see the drive change

    # --- LR02: runahead -------------------------------------------------------------------------------------------------------------------------

    def test_runahead_of_k_runs_leaves_no_input_audio_or_machine_state_of_the_speculative_runs_behind(self):
        for rom in ("input_all", "boot", "long_frame"):
            picture = rom != "long_frame"                                                           # see the gaps in the docstring
            plain = self.frontend(rom, "--runs", 40, *INPUT, "--do", "40:save:end").stdout
            plain_by_unit = self.by_unit(plain, picture)
            for k in (1, 2, 3):
                with self.subTest(rom=rom, k=k):
                    out = self.frontend(rom, "--runs", 40, *INPUT, "--ahead-every", k, "--do", "40:save:end").stdout
                    self.assertEqual(self.views(out, picture), self.views(plain, picture))
                    self.assertEqual(self.saved(out)["end"]["section"], self.saved(plain)["end"]["section"])   # the adapter's side of the state
                    self.assertEqual(CYCLE_AUDIO.search(out).groups()[1:], CYCLE_AUDIO.search(plain).groups()[1:])      # the audio the frontend heard
                    spec = [m for m in SPEC.finditer(out)]
                    self.assertEqual(len(spec), 40 * k)
                    for m in spec:                                                                          # a speculative run is the unit that will come
                        position = int(m[1])
                        if position <= 40:
                            self.assertEqual((int(m[7]), int(m[8])), plain_by_unit[position][0][:2], f"speculative position {position}")

    def test_a_runahead_branch_that_sees_other_input_leaves_nothing_of_it_in_the_machine(self):
        # The frontend polls the input of the future in the speculative runs. A press of a single frame (position 9) is seen by the guest once, at 9.
        presses = ("--joy-at", "9:0:391", "--joy-at", "10:0:0")
        plain = self.frontend("input_all", "--runs", 25, *presses, "--do", "25:save:end").stdout
        none = self.frontend("input_all", "--runs", 25, "--do", "25:save:end").stdout
        self.assertNotEqual(self.saved(plain)["end"]["hash"], self.saved(none)["end"]["hash"])                    # the press is real: it is in the machine
        for k in (1, 3):
            out = self.frontend("input_all", "--runs", 25, *presses, "--ahead-every", k, "--do", "25:save:end").stdout
            self.assertEqual(self.views(out), self.views(plain))
            self.assertEqual(self.saved(out)["end"]["section"], self.saved(plain)["end"]["section"])
        # And a branch that has the press when the real timeline does not: the speculative runs 9 and 10 must leave nothing behind at run 8 either.
        before = self.frontend("input_all", "--runs", 8, *presses, "--ahead", "8:3", "--do", "8:save:end").stdout
        self.assertEqual(self.saved(before)["end"]["hash"], self.saved(self.frontend("input_all", "--runs", 8, "--do", "8:save:end").stdout)["end"]["hash"])

    def test_a_single_runahead_after_a_reset_and_around_a_load_converges(self):
        plain = self.frontend("input_all", "--runs", 24, *INPUT, "--do", "9:reset").stdout
        for after in (8, 9, 10):
            with self.subTest(ahead_after=after):
                out = self.frontend("input_all", "--runs", 24, *INPUT, "--do", "9:reset", "--ahead", f"{after}:3").stdout
                self.assertEqual(self.views(out), self.views(plain))

    # --- reset around a state -------------------------------------------------------------------------------------------------------------------

    def test_a_reset_made_after_the_state_is_not_in_the_replay_and_one_queued_when_it_was_taken_is(self):
        plain = self.frontend("input_all", "--runs", 12, *INPUT).stdout
        # Reset after the save, load before it applies and after it did: the replay is the plain run, the reset is the abandoned future's.
        for load_at in (7, 11):
            with self.subTest(load_at=load_at):
                out = self.frontend("input_all", "--runs", load_at + 5, *INPUT, "--do", "5:save:a", "--do", "6:reset", "--do", f"{load_at}:load:a").stdout
                self.assertEqual(self.views(out)[load_at:], self.views(plain)[5:5 + 5])
        # Reset queued when the state is taken: it applies in the unit after the load, as it did after the save.
        control = self.frontend("input_all", "--runs", 10, *INPUT, "--do", "7:reset").stdout
        out = self.frontend("input_all", "--runs", 14, *INPUT, "--do", "7:reset", "--do", "7:save:r", "--do", "10:load:r").stdout
        self.assert_replays(out, 8, 10)
        self.assertEqual(self.views(out)[:10], self.views(control))
        self.assertNotEqual(self.views(control)[7:10], self.views(plain)[7:10])                                    # the reset did change the guest

    # --- DET05: the frontend not taking what it is offered -----------------------------------------------------------------------------------

    def test_a_frontend_that_takes_no_audio_around_a_save_and_a_load_changes_neither_the_machine_nor_the_audio_it_gets_later(self):
        plain = self.frontend("boot", "--runs", 20).stdout
        stalled = self.frontend("boot", "--runs", 20, "--audio-stall", "3:9").stdout
        self.assertEqual([(r["unit"], r["ticks"], r["hash"]) for r in self.runs(stalled)], [(r["unit"], r["ticks"], r["hash"]) for r in self.runs(plain)])
        self.assertEqual(CYCLE_AUDIO.search(stalled).groups()[1:], CYCLE_AUDIO.search(plain).groups()[1:])         # the same audio, later
        for save_at, load_at in ((6, 14), (2, 11), (10, 15)):
            with self.subTest(save_at=save_at, load_at=load_at):
                out = self.frontend("boot", "--runs", load_at + 8, "--audio-stall", "3:9", "--do", f"{save_at}:save:s", "--do", f"{load_at}:load:s").stdout
                # Position by position, what the frontend got is what it got the first time: the audio not yet taken travels with the state.
                self.assert_replays(out, save_at + 1, min(load_at, save_at + 8))

    def test_a_speculative_run_whose_picture_and_audio_nobody_sees_does_not_change_the_future(self):
        # The same as runahead, with a frontend that also takes no audio in the visible runs.
        plain = self.frontend("boot", "--runs", 24, "--audio-stall", "5:12").stdout
        out = self.frontend("boot", "--runs", 24, "--audio-stall", "5:12", "--ahead-every", 2).stdout
        self.assertEqual(self.views(out), self.views(plain))
        self.assertEqual(CYCLE_AUDIO.search(out).groups()[1:], CYCLE_AUDIO.search(plain).groups()[1:])

    # --- a frontend that cannot repeat a picture -------------------------------------------------------------------------------------------

    def test_a_state_carries_the_last_picture_only_for_a_frontend_that_cannot_repeat_one_and_loads_in_either_kind(self):
        dupe = int(re.search(r"state size=(\d+)", self.frontend("long_frame", "--runs", 1).stdout)[1])
        nodupe = int(re.search(r"state size=(\d+)", self.frontend("long_frame", "--runs", 1, "--no-dupe").stdout)[1])
        self.assertGreaterEqual(nodupe - dupe, 756 * 574 * 4)                                         # room for one picture, no more
        self.assertLess(nodupe - dupe, 760 * 576 * 4 + 4096)
        path = self.work() / "p.state"
        saved = self.saved(self.frontend("long_frame", "--runs", 12, "--no-dupe", "--do", f"11:savefile:{path}").stdout)
        other = self.saved(self.frontend("long_frame", "--runs", 12, "--do", f"11:savefile:{path}.dupe").stdout)
        self.assertNotEqual(saved[str(path)]["section"], other[f"{path}.dupe"]["section"])
        for options in ((), ("--no-dupe",)):
            out = self.frontend("long_frame", "--runs", 3, *options, "--do", f"0:loadfile:{path}:pos=11").stdout
            self.assertEqual(LOAD.search(out)[3], "1")

    # --- the state of an image that is not there ------------------------------------------------------------------------------------------

    def test_a_new_load_announces_the_same_size(self):
        out = self.frontend("boot", "--runs", 3, "--cycles", 3).stdout
        self.assertEqual(len(set(re.findall(r"state size=(\d+)", out))), 1)

    # --- the gaps of the native state a replay showed first (closed by T15j); each keeps its reproducer -------------------------------------------

    def test_GAP_the_picture_of_a_unit_drawn_after_a_load_comes_from_the_timeline_that_was_left(self):
        # long_frame sets its palette in the middle of the run and never again. Run ahead 2 units from unit 1 and come back: unit 2 is drawn with the
        # colour of unit 3. The probe shows the same without any adapter: p6_state_probe long_frame --units 6 --public-save-at 1 F --public-load-at 3 F.
        plain = self.frontend("long_frame", "--runs", 6).stdout
        out = self.frontend("long_frame", "--runs", 6, "--ahead", "1:2").stdout
        self.assertEqual(self.views(out), self.views(plain))

    def test_GAP_a_state_file_loaded_in_a_new_process_draws_the_palette_of_a_machine_that_just_started(self):
        path = self.work() / "p.state"
        first = self.frontend("long_frame", "--runs", 14, "--do", f"12:savefile:{path}").stdout
        second = self.frontend("long_frame", "--runs", 2, "--do", f"0:loadfile:{path}:pos=12").stdout
        self.assertEqual(self.views(second), self.views(first)[12:])

    def test_GAP_a_unit_without_a_new_picture_is_drawn_after_a_load(self):
        # long_frame has no picture from unit 10 on (the core repeats the last, or hands over a copy); after a load the renderer draws one.
        plain = self.frontend("long_frame", "--runs", 15, "--no-dupe").stdout
        out = self.frontend("long_frame", "--runs", 15, "--no-dupe", "--ahead-every", 1).stdout
        self.assertEqual(self.views(out), self.views(plain))

    def test_GAP_the_whole_state_after_a_load_and_some_units_is_the_state_of_a_machine_that_never_loaded(self):
        # One byte of the chipset body differs (offset 329 of CHIP, the low byte of DIWHIGH). p6_state_probe boot.rom --units 4 --public-save-at 1 F
        # --public-load-at 1 F --public-save-at 4 G, against a probe without the first two: G differs from the plain state by that byte (and the checksums).
        plain = self.frontend("boot", "--runs", 4, "--do", "4:save:end").stdout
        out = self.frontend("boot", "--runs", 4, "--do", "1:save:a", "--do", "1:load:a", "--do", "4:save:end").stdout
        self.assertEqual(self.saved(out)["end"]["hash"], self.saved(plain)["end"]["hash"])


CYCLE_AUDIO = re.compile(r"^cycle (\d+) end runs=(\d+) audio_frames=(\d+) audio_hash=(\w+)$", re.M)


# --- a real game, with the Kickstart and the disk the user has -------------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM") and os.environ.get("P6_GAME_ADF"), "P6_KICKSTART_ROM and P6_GAME_ADF are not set")
class RealGameTests(lr.LibretroHarness):
    def test_a_real_game_saved_while_loading_and_while_playing_replays_the_same_units(self):
        rom, game = Path(os.environ["P6_KICKSTART_ROM"]), Path(os.environ["P6_GAME_ADF"])
        self.assertTrue(rom.is_file() and game.is_file())
        system = self.work()
        (system / "kick34005.A500").symlink_to(rom)                      # never copied
        runs = int(os.environ.get("P6_GAME_UNITS", "420"))
        first, second = int(os.environ.get("P6_GAME_SAVE_1", "180")), int(os.environ.get("P6_GAME_SAVE_2", "330"))
        # Fire now and then, as a person getting past the title would; the position decides, so a replay presses the same.
        presses = []
        for position in range(60, runs, 40):
            presses += ["--joy-at", f"{position}:0:1", "--joy-at", f"{position + 3}:0:0"]
        out = self.frontend(None, "--runs", runs + 40, *presses, "--do", f"{first}:save:loading", "--do", f"{second}:save:playing",
                            "--do", f"{runs}:load:loading", "--do", f"{runs + 10}:load:playing", content=game, system=system).stdout
        seen = self.by_unit(out)
        self.assertEqual(len(LOAD.findall(out)), 2)
        self.assertTrue(all(m[3] == "1" for m in LOAD.finditer(out)))
        # After the first load the units first+1..first+10 come again; after the second the units second+1..second+30.
        for unit in range(first + 1, first + 11):
            self.assertEqual(len(seen[unit]), 2, unit)
            self.assertEqual(seen[unit][0], seen[unit][1], f"unit {unit} after the load of the state taken at unit {first}")
        for unit in range(second + 1, second + 31):
            self.assertGreaterEqual(len(seen[unit]), 2, unit)
            self.assertEqual(seen[unit][0], seen[unit][-1], f"unit {unit} after the load of the state taken at unit {second}")
        pictures = {r["hash"] for r in self.runs(out)}
        print(f"\nreal game: {len(pictures)} distinct pictures in {len(self.runs(out))} runs", file=sys.stderr)

    def frontend(self, rom, *options, content=None, system=None, **kwargs):
        return super().frontend("boot", *options, content=content, system=system, **kwargs)

    def by_unit(self, out):
        return StateTests.by_unit(self, out)


if __name__ == "__main__":
    unittest.main()
