"""T76: puae6_machine=auto, puae6_video_standard=PAL auto / NTSC auto and the tags in the names of the files, through the libretro core run by the minimal frontend (LR01).

The model and the video standard are resolved before the machine exists (adapters/libretro/model.h): a pure function of the options, of whether the content has a hard disk and of the
names (the path of the content, then each disk of the playlist, then the members of an archive). What is checked:
  * every tag of the table of docs/options.md picks its machine, in PUAE5's order, and the same content picks the same machine in every new process;
  * a model, a preset or a video standard chosen by hand is never changed by a tag; File Name Tags off reads none;
  * a tag of what this core does not have (68030, 68040, CDTV, MultiDrive, Arcadia) is a warning and never picks another machine;
  * the names of a playlist and of an archive count, in order; the content type decides without tags (floppy: A500, hard disk: A1200);
  * the resolved machine is the machine of the p6_config: a state of a content whose tag picked another machine is refused.
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import test_libretro as lr  # noqa: E402
import test_machines as tm  # noqa: E402
from tests.hdf import hdf_image  # noqa: E402
from tests.media import containers as c  # noqa: E402

MACHINE = re.compile(r"machine (\w+) \(puae6_machine\)")
VIDEO = re.compile(r"^av .* fps=([\d.]+) .*$", re.M)
LOAD = re.compile(r"state load \S+ \S+ ok=(\d)")
SIZE = re.compile(r"^state size=(\d+)", re.M)


def option(key, value):
    return ("--set", f"{key}={value}")


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class ModelTagTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A directory with a Kickstart for every family: whatever machine the tags pick finds its own.
        cls.everything = Path(cls.tmp.name) / "everything"
        cls.everything.mkdir()
        for name in (lr.KICKSTART_NAMES[0], lr.KICKSTART_NAMES_A600[0], lr.KICKSTART_NAMES_A1200[0], tm.NEW["A500PLUS"][2][0], tm.NEW["A2000"][2][0]):
            (cls.everything / name).write_bytes(cls.roms["boot"].read_bytes())

    def disk(self, name, folder=None, seed=None):
        """A disk file called `name` (a path with folders is allowed) in a new directory."""
        path = (folder or self.work()) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(lr.adf(seed or "model-tags"))
        return path

    def play(self, content, *options, runs=2, expect=0):
        flat = [part for option in options for part in (option if isinstance(option, tuple) else (option,))]          # an option() pair or a plain argument
        return self.frontend("boot", "--runs", runs, *flat, content=content, system=self.everything, expect=expect).stdout

    def machine(self, content, *options, **keywords):
        out = self.play(content, *options, **keywords)
        found = MACHINE.findall(out)
        self.assertEqual(len(found), 1, out[-600:])
        return found[0]

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message PUAE6: (.*)$", text)

    # --- the table of tags ----------------------------------------------------------------------------------------------------------------

    TAGS = {
        # a name -> the machine; PUAE5's tags first (libretro-core.c retro_create_config), then the A2000 and the A1000, which PUAE5 has no tag for
        "Game.adf": "A500", "Game (A500).adf": "A500", "Game (A500OG).adf": "A500OG", "Game (512K).adf": "A500OG", "Game (512KB).adf": "A500OG",
        "Game (A500+).adf": "A500PLUS", "Game (A500PLUS).adf": "A500PLUS", "Game (A600).adf": "A600", "Game (A1200).adf": "A1200", "Game (A1200OG).adf": "A1200",
        "Game (A1200NF).adf": "A1200", "Game (CD32).adf": "A1200", "Game (CD32NF).adf": "A1200", "Game CD32.adf": "A1200", "Game (AGA).adf": "A1200",
        "Game AGA.adf": "A1200", "Game_AGA_v2.adf": "A1200", "Game (AmigaCD).adf": "A1200", "Game (ECS).adf": "A600", "Game (OCS).adf": "A500",
        "Game (A2000).adf": "A2000", "Game (A2000OG).adf": "A2000OG", "Game (A1000).adf": "A1000",
        # the first tag of the table wins inside a name, whatever the order in the name
        "Game (A500) (A1200).adf": "A1200", "Game (A1200) (A500).adf": "A1200", "Game (A500+) (A600).adf": "A600", "Game (A500) (512K).adf": "A500OG",
        "Game (A1200OG) (A600).adf": "A1200", "Game (ECS) (A500).adf": "A500", "Game (AGA) (A600).adf": "A600",
        # a bare word must not touch a letter or a digit (PUAE5's strstr finds AGA in SAGA); case matters
        "SAGA.adf": "A500", "Game AGAIN.adf": "A500", "PALADIN.adf": "A500", "Game (a1200).adf": "A500", "Game (A1200 ).adf": "A500", "Game (A12000).adf": "A500",
        "Game (A5000).adf": "A500", "Game [A1200].adf": "A500", "Game A1200.adf": "A500",
    }

    def test_every_tag_picks_its_machine(self):
        for name, expected in self.TAGS.items():
            with self.subTest(name=name):
                self.assertEqual(self.machine(self.disk(name)), expected)

    def test_a_tag_in_a_folder_counts_as_in_the_file_name(self):
        for name, expected in (("Games (A600)/Game.adf", "A600"), ("AGA/Game.adf", "A1200"), ("(A1200)/(A600)/Game.adf", "A1200"), ("(A600)/(A500+)/Game.adf", "A600"), ("(A500)/Game (A1200).adf", "A1200")):
            with self.subTest(name=name):
                self.assertEqual(self.machine(self.disk(name)), expected)

    def test_the_same_content_picks_the_same_machine_in_every_process_and_wherever_it_is(self):
        for name in ("Game (A600).adf", "Game (AGA).adf", "Game.adf", "Game (A500+) (NTSC).adf"):
            outs = [self.play(self.disk(name), "--cycles", 2) for _ in range(3)]
            first = [(MACHINE.findall(o), VIDEO.findall(o), re.findall(r"audio_hash=(\w+)", o)) for o in outs]
            self.assertEqual(first[0], first[1], name)
            self.assertEqual(first[0], first[2], name)
            self.assertEqual(len(set(first[0][0])), 1, name)          # both cycles of a run are the same machine
        # Where the file is changes nothing when the path holds no tag (a temporary directory of this test never does).
        self.assertEqual(self.machine(self.disk("Game.adf")), self.machine(self.disk("deeper/still/Game.adf")))

    def test_the_content_type_decides_when_no_tag_does(self):
        floppy = self.disk("Game.adf")
        self.assertEqual(self.machine(floppy), "A500")
        self.assertEqual(self.machine(floppy, option("puae6_model_fd", "A600")), "A600")
        self.assertEqual(self.machine(floppy, option("puae6_model_fd", "A2000OG")), "A2000OG")
        self.assertEqual(self.machine(floppy, option("puae6_model_hd", "A600")), "A500", "puae6_model_hd is for content with a hard disk")
        hard = self.work() / "Game.hdf"
        hard.write_bytes(hdf_image.plain_partition())
        self.assertEqual(self.machine(hard), "A1200", "PUAE5: a hard disk defaults to the A1200")
        self.assertEqual(self.machine(hard, option("puae6_model_hd", "A600")), "A600")
        self.assertEqual(self.machine(hard, option("puae6_model_hd", "A500PLUS")), "A500PLUS")
        self.assertEqual(self.machine(hard, option("puae6_model_fd", "A600")), "A1200", "puae6_model_fd is for content with no hard disk")
        tagged = self.work() / "Game (A600).hdf"
        tagged.write_bytes(hdf_image.plain_partition())
        self.assertEqual(self.machine(tagged), "A600")
        with_floppy = self.work() / "mixed.m3u"
        (with_floppy.parent / "d.adf").write_bytes(lr.adf("d"))
        (with_floppy.parent / "h.hdf").write_bytes(hdf_image.plain_partition())
        with_floppy.write_text("d.adf\nh.hdf\n")
        self.assertEqual(self.machine(with_floppy), "A1200", "a hard disk anywhere in the content makes it hard disk content")

    def test_no_content_boots_the_model_of_a_floppy(self):
        out = self.frontend("boot", "--runs", 2, system=self.everything).stdout
        self.assertEqual(MACHINE.findall(out), ["A500"])

    # --- weak words -----------------------------------------------------------------------------------------------------------------------

    def test_a_chipset_word_moves_the_model_only_when_the_model_of_the_content_type_does_not_have_that_chipset(self):
        for fd, name, expected in (("A500", "Game (ECS).adf", "A600"), ("A500PLUS", "Game (ECS).adf", "A500PLUS"), ("A600", "Game (ECS).adf", "A600"), ("A1200", "Game (ECS).adf", "A600"),
                                   ("A2000", "Game (ECS).adf", "A2000"), ("A500", "Game (AGA).adf", "A1200"), ("A1200", "Game (AGA).adf", "A1200"), ("A600", "Game (AGA).adf", "A1200"),
                                   ("A1200", "Game (OCS).adf", "A500"), ("A500OG", "Game (OCS).adf", "A500OG"), ("A500PLUS", "Game (OCS).adf", "A500PLUS"), ("A1000", "Game (OCS).adf", "A1000"),
                                   ("A600", "Game (OCS).adf", "A500")):
            with self.subTest(model_fd=fd, name=name):
                self.assertEqual(self.machine(self.disk(name), option("puae6_model_fd", fd)), expected)

    def test_a_strong_tag_beats_a_chipset_word_even_in_a_later_name(self):
        directory = self.work()
        (directory / "list.m3u").write_text("Game (ECS) (Disk 1).adf\nGame (Disk 2) (A1200).adf\n")
        for name in ("Game (ECS) (Disk 1).adf", "Game (Disk 2) (A1200).adf"):
            (directory / name).write_bytes(lr.adf(name))
        self.assertEqual(self.machine(directory / "list.m3u"), "A1200")

    # --- what a person chose is not changed ---------------------------------------------------------------------------------------------------

    def test_a_model_or_a_preset_chosen_by_hand_is_never_changed_by_a_tag(self):
        game = self.disk("Game (A1200).adf")
        out = self.play(game, option("puae6_machine", "A600"))
        self.assertEqual(MACHINE.findall(out), ["A600"])
        self.assertRegex(out, r"the tag \(A1200\) in .* is not used: the model is fixed by puae6_machine or puae6_preset")
        self.assertEqual(self.machine(self.disk("Game (A600).adf"), option("puae6_machine", "A500")), "A500")
        self.assertEqual(self.machine(self.disk("Game (A600).adf"), option("puae6_preset", "a1200_fast8")), "A1200")
        self.assertEqual(self.machine(self.disk("Game (AGA).adf"), option("puae6_machine", "A600")), "A600")

    def test_file_name_tags_off_reads_no_tag(self):
        off = option("puae6_filename_tags", "disabled")
        for name in ("Game (A1200).adf", "Game (AGA).adf", "Game (A600).adf"):
            with self.subTest(name=name):
                out = self.play(self.disk(name), off)
                self.assertEqual(MACHINE.findall(out), ["A500"])
                self.assertIn("File Name Tags is off", out)
        self.assertEqual(self.machine(self.disk("Game (A1200).adf"), off, option("puae6_model_fd", "A600")), "A600")
        out = self.play(self.disk("Game (NTSC) (A4030).adf"), off)
        self.assertEqual(self.messages(out), [], "with the tags off nothing is read, so nothing is warned")

    # --- the memory a tag carries -------------------------------------------------------------------------------------------------------------

    def test_the_two_tags_that_carry_memory_are_the_presets_and_a_memory_option_the_person_set_wins(self):
        size = lambda out: int(SIZE.search(out)[1])
        plain = size(self.play(self.disk("Game (A1200OG).adf")))
        fast8 = size(self.play(self.disk("Game (A1200).adf")))
        self.assertGreater(fast8, plain + 7 * 1024 * 1024, "(A1200) is PUAE5's A1200: 8 MiB of Fast RAM")
        self.assertEqual(fast8, size(self.play(self.disk("Game.adf"), option("puae6_preset", "a1200_fast8"))), "the tag is the schema's preset")
        self.assertEqual(fast8, size(self.play(self.disk("Game (CD32).adf"))))
        by_hand = size(self.play(self.disk("Game (A1200).adf"), option("puae6_fast_size", "MB1")))
        self.assertGreater(by_hand, plain)
        self.assertLess(by_hand, fast8, "1 MiB of Fast RAM chosen by hand stays")
        a600 = self.play(self.disk("Game (A600).adf"))
        self.assertRegex(a600, r"memory-desc \d+ name=CHIP start=0x0 len=0x200000")
        self.assertRegex(a600, r"memory-desc \d+ name=FAST start=0x200000 len=0x800000")
        self.assertRegex(self.play(self.disk("Game (A600).adf"), option("puae6_chip_size", "MB1")), r"memory-desc \d+ name=CHIP start=0x0 len=0x100000")

    # --- the video standard -----------------------------------------------------------------------------------------------------------------

    def fps(self, content, *options):
        return float(VIDEO.search(self.play(content, *options))[1])

    def test_region_tags_choose_the_video_standard_with_the_automatic_options_only(self):
        pal, ntsc = 50.0, 60.0
        for name, expected in (("Game.adf", pal), ("Game (NTSC).adf", ntsc), ("Game (USA).adf", ntsc), ("Game NTSC.adf", ntsc), ("Game (PAL).adf", pal), ("Game (Europe).adf", pal),
                               ("Game (France).adf", pal), ("Game (Germany).adf", pal), ("Game (Italy).adf", pal), ("Game (Spain).adf", pal), ("Game (Finland).adf", pal),
                               ("Game (Denmark).adf", pal), ("Game (Sweden).adf", pal), ("Game (USA) (Europe).adf", ntsc), ("Game (USA, Europe).adf", pal), ("Game (NTSC) (PAL).adf", ntsc), ("Game (PAL) (USA).adf", ntsc),
                               ("PALADIN.adf", pal), ("NTSCX.adf", pal), ("Game (Japan).adf", pal)):
            with self.subTest(name=name):
                self.assertAlmostEqual(self.fps(self.disk(name)), expected, delta=0.2)
        ntsc_auto = option("puae6_video_standard", "NTSC auto")
        self.assertAlmostEqual(self.fps(self.disk("Game.adf"), ntsc_auto), ntsc, delta=0.2)
        self.assertAlmostEqual(self.fps(self.disk("Game (PAL).adf"), ntsc_auto), pal, delta=0.2)
        self.assertAlmostEqual(self.fps(self.disk("Game (Europe).adf"), ntsc_auto), pal, delta=0.2)
        self.assertAlmostEqual(self.fps(self.disk("Game (NTSC).adf"), ntsc_auto), ntsc, delta=0.2)
        # A standard chosen by hand is never changed by a tag.
        self.assertAlmostEqual(self.fps(self.disk("Game (NTSC).adf"), option("puae6_video_standard", "PAL")), pal, delta=0.2)
        self.assertAlmostEqual(self.fps(self.disk("Game (PAL).adf"), option("puae6_video_standard", "NTSC")), ntsc, delta=0.2)
        self.assertAlmostEqual(self.fps(self.disk("Game (NTSC).adf"), option("puae6_filename_tags", "disabled")), pal, delta=0.2)
        out = self.play(self.disk("Game (NTSC).adf"), option("puae6_video_standard", "PAL"))
        self.assertRegex(out, r"the tag NTSC in .* is not used: puae6_video_standard is not automatic")

    def test_the_model_and_the_standard_are_independent(self):
        out = self.play(self.disk("Game (A600) (NTSC).adf"))
        self.assertEqual(MACHINE.findall(out), ["A600"])
        self.assertAlmostEqual(float(VIDEO.search(out)[1]), 60.0, delta=0.2)

    # --- tags of what this core does not have ---------------------------------------------------------------------------------------------------

    def test_a_tag_of_what_does_not_exist_is_a_warning_and_never_picks_another_machine(self):
        for name, text in (("Game (A4030).adf", "(A4030) in"), ("Game (030).adf", "(030) in"), ("Game (A4040).adf", "(A4040) in"), ("Game (040).adf", "(040) in")):
            with self.subTest(name=name):
                out = self.play(self.disk(name))
                self.assertEqual(MACHINE.findall(out), ["A500"])
                self.assertTrue(any(text in m and "does not have" in m for m in self.messages(out)), self.messages(out))
        # It blocks the weaker tags, as PUAE5's first match does: the machine comes from the options, not from the tag that happens to be left.
        for name in ("Game (A4030) (A1200).adf", "Game (A1200) (040).adf", "Game (A4040) AGA.adf"):
            with self.subTest(name=name):
                out = self.play(self.disk(name), option("puae6_model_fd", "A600"))
                self.assertEqual(MACHINE.findall(out), ["A600"], name)
        for name, text in (("Game CDTV.adf", "CDTV"), ("Game (MD).adf", "(MD)"), ("Game_Arcadia.adf", "_Arcadia")):
            with self.subTest(name=name):
                out = self.play(self.disk(name))
                self.assertEqual(MACHINE.findall(out), ["A500"])
                self.assertTrue(any(f"the tag {text} in" in m and "is ignored" in m for m in self.messages(out)), self.messages(out))
        out = self.play(self.disk("Game (CE).adf"))
        self.assertEqual(self.messages(out), [], "(CE) is not a warning: every machine here is cycle exact")
        self.assertRegex(out, r"the tag \(CE\) in .* changes nothing")
        # Said once, however many names hold it.
        directory = self.work()
        (directory / "list.m3u").write_text("a (MD).adf\nb (MD).adf\n")
        for name in ("a (MD).adf", "b (MD).adf"):
            (directory / name).write_bytes(lr.adf(name))
        self.assertEqual(len([m for m in self.messages(self.play(directory / "list.m3u")) if "(MD)" in m]), 1)

    # --- playlists and archives -------------------------------------------------------------------------------------------------------------

    def playlist(self, name, entries):
        directory = self.work()
        for entry in entries:
            (directory / entry).write_bytes(lr.adf(entry))
        (directory / name).write_text("".join(f"{entry}\n" for entry in entries))
        return directory / name

    def test_the_names_of_a_playlist_count_in_order_the_playlist_first(self):
        self.assertEqual(self.machine(self.playlist("Game.m3u", ["Game (Disk 1).adf", "Game (Disk 2).adf"])), "A500")
        self.assertEqual(self.machine(self.playlist("Game.m3u", ["Game (Disk 1).adf", "Game (Disk 2) (A1200).adf"])), "A1200")
        self.assertEqual(self.machine(self.playlist("Game.m3u", ["Game (Disk 1) (A500+).adf", "Game (Disk 2) (A1200).adf"])), "A500PLUS", "the first disk with a tag decides")
        self.assertEqual(self.machine(self.playlist("Game (A600).m3u", ["Game (Disk 1) (A500+).adf", "Game (Disk 2) (A1200).adf"])), "A600", "the playlist's own name is first")
        self.assertEqual(self.machine(self.playlist("Game.m3u", ["Game (Disk 1) (NTSC).adf", "Game (Disk 2) (Europe).adf"])), "A500")
        out = self.play(self.playlist("Game.m3u", ["Game (Disk 1) (NTSC).adf", "Game (Disk 2) (Europe).adf"]))
        self.assertAlmostEqual(float(VIDEO.search(out)[1]), 60.0, delta=0.2, msg="the first disk with a region decides")

    def test_the_members_of_an_archive_count_after_the_archive(self):
        directory = self.work()
        members = [("Game (Disk 1) (A600).adf", c.adf("one")), ("Game (Disk 2).adf", c.adf("two"))]
        (directory / "Game.zip").write_bytes(c.zip_file(members))
        self.assertEqual(self.machine(directory / "Game.zip"), "A600")
        (directory / "Game (A1200).zip").write_bytes(c.zip_file(members))
        self.assertEqual(self.machine(directory / "Game (A1200).zip"), "A1200", "the archive's own name is first")
        (directory / "One.zip").write_bytes(c.zip_file([("Game (NTSC) (A500+).adf", c.adf("one"))]))
        out = self.play(directory / "One.zip")
        self.assertEqual(MACHINE.findall(out), ["A500PLUS"], "an archive of one disk: its member's name counts too")
        self.assertAlmostEqual(float(VIDEO.search(out)[1]), 60.0, delta=0.2)
        self.assertEqual(self.machine(self.disk("Game.adf", directory)), "A500")

    # --- a .uae as content ------------------------------------------------------------------------------------------------------------------

    def configuration(self, name, text, disk="a.adf"):
        directory = self.work()
        (directory / disk).write_bytes(lr.adf(disk))
        path = directory / name
        path.write_text(text)
        return path

    def test_a_configuration_takes_the_tags_of_its_own_name_and_its_keys_win(self):
        # The automatic model of a .uae comes from the name of the .uae (the disks it names are not looked at); its keys are checked against it and refine it.
        self.assertEqual(self.machine(self.configuration("Game.uae", "floppy0=a.adf\n")), "A500")
        self.assertEqual(self.machine(self.configuration("Game (A600).uae", "floppy0=a.adf\n")), "A600")
        self.assertEqual(self.machine(self.configuration("Game (A600).uae", "chipset_compatible=A1200\nfloppy0=a.adf\n")), "A1200", "the file names the machine: the tag is not used")
        self.assertEqual(self.machine(self.configuration("Game (A500+).uae", "chipset=ecs\ncpu_model=68000\nfloppy0=a.adf\n")), "A600", "the chipset names the machine")
        self.assertEqual(self.machine(self.configuration("Game.uae", "floppy0=Disk (A1200).adf\n", "Disk (A1200).adf")), "A500", "the tags of the disks of a .uae are not read")
        self.assertEqual(self.machine(self.configuration("Game.uae", "floppy0=a.adf\n"), option("puae6_model_fd", "A600")), "A600")
        # What the tag chose is the machine the keys are checked against.
        refused = self.play(self.configuration("Game (A1200).uae", "cpu_model=68000\nfloppy0=a.adf\n"), expect=3)
        self.assertIn("is not the one of the machine (A1200", refused)
        self.assertEqual(self.machine(self.configuration("Game (A1200).uae", "cpu_model=68020\nfloppy0=a.adf\n")), "A1200")
        out = self.play(self.configuration("Game (NTSC).uae", "floppy0=a.adf\n"))
        self.assertAlmostEqual(float(VIDEO.search(out)[1]), 60.0, delta=0.2)
        out = self.play(self.configuration("Game (NTSC).uae", "ntsc=false\nfloppy0=a.adf\n"))
        self.assertAlmostEqual(float(VIDEO.search(out)[1]), 50.0, delta=0.2, msg="the keys of the file win over the tag")
        out = self.play(self.configuration("Game.uae", "puae6_machine=auto\nfloppy0=a.adf\n"), expect=3)
        self.assertIn("is the frontend's automatic choice, not a value for a file", out)
        out = self.play(self.configuration("Game.uae", "puae6_video_standard=NTSC auto\nfloppy0=a.adf\n"), expect=3)
        self.assertIn("is the frontend's automatic choice, not a value for a file", out)
        self.assertEqual(self.machine(self.configuration("Game (A1200).uae", "floppy0=a.adf\n"), option("puae6_filename_tags", "disabled")), "A500", "tags off: the name of the .uae is not read")

    # --- the identity of the state ----------------------------------------------------------------------------------------------------------

    def test_a_state_of_a_content_whose_tag_picked_another_machine_is_refused(self):
        folder = self.work()
        path = folder / "x.state"
        same_bytes = "same bytes, three names"
        a1200 = self.disk("Game (A1200).adf", folder, same_bytes)
        self.frontend("boot", "--runs", 8, "--do", f"4:savefile:{path}", content=a1200, system=self.everything)
        other_name = self.disk("Another name (A1200).adf", folder, same_bytes)
        loaded = self.frontend("boot", "--runs", 6, "--do", f"0:loadfile:{path}:pos=4", content=other_name, system=self.everything).stdout
        self.assertEqual(LOAD.search(loaded)[1], "1", "another name that picks the same machine for the same disk loads it")
        for name, why in (("Game (A500).adf", "the A500"), ("Game (A600).adf", "the A600"), ("Game (A1200OG).adf", "the A1200 without the Fast RAM of (A1200)"), ("Game.adf", "no tag")):
            with self.subTest(name=name, why=why):
                refused = self.frontend("boot", "--runs", 6, "--do", f"0:loadfile:{path}:pos=4", content=self.disk(name, folder, same_bytes), system=self.everything).stdout
                self.assertEqual(LOAD.search(refused)[1], "0")
                self.assertEqual(len(self.runs(refused)), 6, "the refusal left the machine usable")
        with self.subTest("a model chosen by hand"):
            by_hand = self.frontend("boot", "--runs", 6, *option("puae6_preset", "a1200_fast8"), "--do", f"0:loadfile:{path}:pos=4",
                                    content=self.disk("Plain.adf", folder, same_bytes), system=self.everything).stdout
            self.assertEqual(LOAD.search(by_hand)[1], "1", "the preset and the tag are the same machine: the state is the same")
        with self.subTest("the standard"):
            ntsc = self.frontend("boot", "--runs", 6, "--do", f"0:loadfile:{path}:pos=4", content=self.disk("Game (A1200) (NTSC).adf", folder, same_bytes),
                                 system=self.everything).stdout
            self.assertEqual(LOAD.search(ntsc)[1], "0", "a tag that picks NTSC is another machine")

    # --- the menu ---------------------------------------------------------------------------------------------------------------------------

    def test_the_menu_offers_the_automatic_values_and_starts_on_them(self):
        out = self.frontend("boot", "--runs", 1, system=self.everything).stdout
        self.assertRegex(out, r"option puae6_machine default=auto values=auto\|A500\|A600\|A1200\|A500OG\|A500PLUS\|A1000\|A2000OG\|A2000 ")
        self.assertRegex(out, r"option puae6_video_standard default=PAL auto values=PAL auto\|NTSC auto\|PAL\|NTSC ")
        self.assertRegex(out, r"option puae6_model_fd default=A500 values=A500\|")
        self.assertRegex(out, r"option puae6_model_hd default=A1200 values=A500\|")
        self.assertRegex(out, r"option puae6_filename_tags default=enabled values=enabled\|disabled ")
        legacy = self.frontend("boot", "--runs", 1, "--options-api", 0, system=self.everything).stdout
        self.assertRegex(legacy, r"option puae6_machine default=auto values=auto\|A500")
        self.assertEqual(MACHINE.findall(self.frontend("boot", "--runs", 1, "--no-options", content=self.disk("Game (A600).adf"), system=self.everything).stdout), ["A600"],
                         "a frontend with no options at all holds nothing: every option keeps the core's default, Automatic with the tags read")

    def test_a_menu_change_while_it_runs_is_reported_and_applies_at_the_next_load(self):
        game = self.disk("Game (A1200).adf")
        out = self.frontend("boot", "--runs", 10, "--set-at", "5:puae6_filename_tags=disabled", content=game, system=self.everything).stdout
        self.assertEqual(self.messages(out), ["puae6_filename_tags is now disabled but the machine runs with enabled: reload the content to apply it"])
        self.assertEqual(MACHINE.findall(out), ["A1200"])
        out = self.frontend("boot", "--runs", 10, "--set-at", "5:puae6_machine=A600", content=game, system=self.everything).stdout
        self.assertEqual(self.messages(out), ["puae6_machine is now A600 but the machine runs with A1200: reload the content to apply it"])
        out = self.frontend("boot", "--runs", 10, "--set-at", "5:puae6_machine=A1200", content=game, system=self.everything).stdout
        self.assertEqual(self.messages(out), [], "choosing by hand what the tag already picked changes nothing")


if __name__ == "__main__":
    unittest.main()
