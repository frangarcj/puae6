"""T17e: disks added after the load through libretro's Disk Control (add_image_index, replace_image_index), run by the minimal frontend (LR01, MED01, STATE02, DET02).

RetroArch's "Add disk" ejects the tray, calls add_image_index (an empty slot at the end of the list) and replace_image_index(slot, file), then chooses the slot and closes the tray. The
core reads the file as it reads content at load (VFS or stdio, caps, ADZ/ZIP/DMS decoded in memory), registers the ADF with p6_media_append at the END of the media manifest and binds the
slot to the index it got; mounting is still the tray's business at the next unit boundary. What is compared here is always a machine that was told the disk at load: the hot-added
disk has to give the same units, pictures, audio, events and (for the state) the same bytes.

Rewind and states: a state taken before a disk was added loads after it (the manifest only grows: the state is bound to the prefix it was taken with) and leaves the drives as they were,
the added disk stays registered and in the list and can be inserted again; a state taken after the addition loads only where the disk was added again (same content, same order), which is
what a RetroArch state file needs after a restart, and is refused by name otherwise, without touching the machine. Run: python3 -m unittest discover -s tests/libretro-host -p "test_media_append.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402
import test_state as ts  # noqa: E402  (the module, so that its test classes are not collected twice)
from tests.media import containers as c  # noqa: E402

MEDIUM = re.compile(r"medium (\d+) sha256=(\w+)")
UNSEEN = "0000000000000000"


def events(out):
    """(run, text) for each 'DF0:' line the core logged while that run was being made."""
    found, pending = [], []
    for line in out.splitlines():
        if m := re.match(r"log \d+ \[PUAE6\] (DF0: .*)$", line):
            pending.append(m[1])
        elif m := re.match(r"run (\d+) ", line):
            found += [(int(m[1]), text) for text in pending]
            pending = []
    return found


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class AppendTests(lr.LibretroHarness):
    def disk(self, name, seed=None, directory=None):
        """A file of the plain ADF `seed` (default: the name) in a directory of its own, as a playlist makes them."""
        path = (directory or self.work()) / name
        path.write_bytes(lr.adf(seed or name))
        return path

    def views(self, out):
        return [ts.view(r) for r in self.runs(out)]

    def media(self, out):
        return {int(m[1]): m[2] for m in MEDIUM.finditer(out)}

    def saved(self, out):
        return {m[1]: {"hash": m[5], "section": m[6]} for m in ts.SAVE.finditer(out)}

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    @staticmethod
    def images(out):
        return [(int(m[1]), int(m[2]), m[3] == "1") for m in ts.IMAGES.finditer(out)]

    @staticmethod
    def hot(path, slot=1, eject=8, choose=None, insert=None):
        """The calls of RetroArch's "Add disk" (eject, add, replace) as actions of the real timeline, and the choice of the new slot and the close of the tray if asked for."""
        steps = ["--do", f"{eject}:disk:eject", "--do", f"{eject}:disk:add", "--do", f"{eject}:disk:replace={slot}:{path}"]
        if choose is not None:
            steps += ["--do", f"{choose}:disk:index={slot}"]
        if insert is not None:
            steps += ["--do", f"{insert}:disk:insert"]
        return steps

    def one_disk(self):
        return self.playlist("a.adf|A\n", ["a.adf"])

    def two_disks(self):
        return self.playlist("a.adf|A\nx.adf|x\n", ["a.adf", "x.adf"])

    # --- the disk is a disk of the manifest like any other ----------------------------------------------------------------------------------

    def test_a_disk_added_after_the_load_is_inserted_exactly_like_one_the_playlist_named(self):
        x = self.disk("x.adf")
        steps = ("--do", "20:images", "--do", "140:save:end")
        control = self.frontend("observer", "--runs", 140, "--disk", "10:eject", "--disk", "14:index=1", "--disk", "15:insert", *steps, content=self.two_disks()).stdout
        hot = self.frontend("observer", "--runs", 140, "--disk", "10:eject", "--disk", "10:add", "--disk", f"10:replace=1:{x}", "--disk", "14:index=1", "--disk", "15:insert",
                            *steps, content=self.one_disk()).stdout
        self.assertIn("disk 10 add result=1 eject=1 index=0 count=2", hot)
        self.assertIn(f"disk 10 replace=1:{x} result=1 eject=1 index=0 count=2", hot)
        self.assertIn(f"state image 1 path={x} label=x", hot)
        self.assertEqual(events(hot), [(1, "DF0: medium 0 inserted"), (10, "DF0: ejected"), (15, "DF0: medium 1 inserted")])
        self.assertEqual(events(hot), events(control))
        self.assertEqual(self.views(hot), self.views(control))                                   # units, ticks, audio, pictures: the drive took the new disk the same way
        self.assertEqual(self.media(hot), self.media(control))                                   # the same content id, index 1
        self.assertEqual(self.images(hot), self.images(control))
        self.assertEqual(self.saved(hot)["end"], self.saved(control)["end"])                      # and the whole state, the adapter's side included, is the same bytes
        self.assertGreater(len({r["px"] for r in self.runs(hot)}), 1)                            # the observer did see the drive change

    def test_the_disk_may_come_in_any_container_and_is_the_same_medium(self):
        disk = lr.adf("x.adf")
        control = self.frontend("observer", "--runs", 120, "--disk", "10:eject", "--disk", "14:index=1", "--disk", "15:insert", content=self.two_disks()).stdout
        directory = self.work()
        files = {"x.adz": c.adz(disk), "x.zip": c.zip_file([("readme.txt", b"hello"), ("x.adf", disk)]), "x.dms": c.dms_file(disk, lambda n: n % 3 % 2),
                 "inner.zip": c.zip_file([("x.dms", c.dms_file(disk))]), "X.ADF": disk}
        for name, data in files.items():
            (directory / name).write_bytes(data)
        for name in files:
            for options in ((), ("--no-vfs",)):
                with self.subTest(name, options=options):
                    out = self.frontend("observer", "--runs", 120, *options, "--disk", "10:eject", "--disk", "10:add", "--disk", f"10:replace=1:{directory / name}",
                                        "--disk", "14:index=1", "--disk", "15:insert", content=self.one_disk()).stdout
                    self.assertIn(f"disk 10 replace=1:{directory / name} result=1", out)
                    self.assertEqual(self.views(out), self.views(control))
                    self.assertEqual(self.media(out), self.media(control))

    def test_a_disk_with_the_bytes_of_one_the_manifest_has_is_that_medium_and_registers_nothing_new(self):
        content = self.playlist("a.adf|A\nb.adf|B\n")
        copy = self.disk("copy-of-a.adf", "a.adf")
        out = self.frontend("observer", "--runs", 140, *self.hot(copy, slot=2, choose=12, insert=13), "--do", "20:images", content=content).stdout
        self.assertEqual(sorted(self.media(out)), [0, 1])                                      # no third medium was registered
        self.assertIn("image 2: medium 0", out)
        self.assertEqual(self.images(out)[0], (3, 2, False))
        self.assertEqual([text for _, text in events(out)], ["DF0: medium 0 inserted", "DF0: ejected", "DF0: medium 0 inserted"])

    def test_a_replace_is_refused_with_a_message_and_changes_nothing_when_it_cannot_be_done(self):
        directory = self.work()
        files = {"short.adf": b"\x00" * 1000, "damaged.zip": b"PK\x03\x04" + bytes(40), "notes.txt": b"hello", "game.ipf": b"CAPS" + bytes(100), "empty.adf": b""}
        for name, data in files.items():
            (directory / name).write_bytes(data)
        good = self.disk("x.adf")
        control = self.frontend("observer", "--runs", 40, content=self.one_disk()).stdout
        tries = [str(directory / name) for name in files] + [str(directory / "missing.adf")]
        steps = ["--do", "5:disk:eject", "--do", "5:disk:add"]
        for number, path in enumerate(tries):
            steps += ["--do", f"{6 + number}:disk:replace=1:{path}"]
        steps += ["--do", "20:disk:index=1", "--do", "20:disk:insert", "--do", "21:images"]
        out = self.frontend("observer", "--runs", 40, *steps, content=self.one_disk()).stdout
        for number, path in enumerate(tries):
            self.assertIn(f"disk {6 + number} replace=1:{path} result=0 eject=1", out)
        messages = self.messages(out)
        self.assertEqual(len(messages), len(tries), messages)
        self.assertTrue(all(m.startswith("PUAE6: image 1 (") and "cannot be replaced" in m for m in messages), messages)
        self.assertIn("it cannot be opened", messages[-1])
        self.assertIn("disk 20 index=1 result=0 eject=1", out)                                  # the slot has no disk: it cannot be chosen
        self.assertEqual(self.images(out)[-1], (2, 0, False))
        self.assertIn("state image 1 path= label= (missing)", out)                                    # and it has no path or label to show
        self.assertEqual(sorted(self.media(out)), [0])
        # The tray is the only thing the failed attempts and the close of the tray changed: the drive saw a swap of the same disk, nothing else was registered.
        self.assertEqual([text for _, text in events(out)], ["DF0: medium 0 inserted", "DF0: ejected", "DF0: medium 0 inserted"])
        # The tray has to be open to replace, and an index past the list is refused.
        again = self.frontend("observer", "--runs", 12, "--do", "5:disk:replace=0:" + str(good), "--do", "6:disk:eject", "--do", "6:disk:replace=3:" + str(good), "--do", "7:images",
                              content=self.one_disk()).stdout
        self.assertIn(f"disk 5 replace=0:{good} result=0 eject=0", again)
        self.assertIn(f"disk 6 replace=3:{good} result=0 eject=1", again)
        self.assertEqual(sorted(self.media(again)), [0])
        self.assertEqual(len(self.runs(control)), 40)

    def test_the_manifest_has_a_limit_and_a_disk_it_already_holds_still_works_when_it_is_full(self):
        names = [f"d{i}.adf" for i in range(16)]
        content = self.playlist("".join(f"{n}\n" for n in names), names)
        extra = self.disk("extra.adf")
        copy = self.disk("copy.adf", "d3.adf")
        out = self.frontend("observer", "--runs", 30, "--do", "3:disk:eject", "--do", "3:disk:add", "--do", "4:disk:remove=15", "--do", "4:disk:add",
                            "--do", f"5:disk:replace=15:{extra}", "--do", f"6:disk:replace=15:{copy}", "--do", "7:images", content=content).stdout
        self.assertIn("disk 3 add result=0 eject=1 index=0 count=16", out)                      # the list of the tray holds 16 at most
        self.assertIn("disk 4 remove=15 result=1 eject=1 index=0 count=15", out)
        self.assertIn("disk 4 add result=1 eject=1 index=0 count=16", out)
        self.assertIn(f"disk 5 replace=15:{extra} result=0", out)                                # the manifest is full: 16 distinct disks
        self.assertTrue(any("the disk cannot be added: the media manifest is full" in m for m in self.messages(out)), self.messages(out))
        self.assertIn(f"disk 6 replace=15:{copy} result=1", out)                                 # but the content of disk 3 is one of them
        self.assertIn("image 15: medium 3", out)

    # --- states ---------------------------------------------------------------------------------------------------------------------------

    def test_rewinding_to_before_an_addition_puts_the_drive_back_and_leaves_the_added_disk_in_the_list(self):
        x = self.disk("x.adf")
        plain = self.frontend("observer", "--runs", 35, content=self.one_disk()).stdout
        out = self.frontend("observer", "--runs", 160, "--do", "5:save:before", *self.hot(x, eject=8, choose=8, insert=9), "--do", "130:images",
                            "--do", "130:load:before", "--do", "130:images", content=self.one_disk()).stdout
        self.assertEqual([m[3] for m in ts.LOAD.finditer(out)], ["1"])
        self.assertEqual(self.images(out), [(2, 1, False), (2, 0, False)])                      # the disk was chosen and in; after the rewind a is selected again, x is still there
        self.assertIn(f"state image 1 path={x} label=x", out)                                    # the added disk is still the second image
        self.assertEqual(events(out), [(1, "DF0: medium 0 inserted"), (9, "DF0: ejected"), (10, "DF0: medium 1 inserted")])      # nothing is logged by the load
        # Everything the machine does after the load is what the plain run (one disk, no addition) did after the same state.
        self.assertEqual(self.views(out)[130:160], self.views(plain)[5:35])

    def test_the_disk_a_rewind_left_behind_can_be_inserted_again_and_goes_on_like_a_machine_that_always_had_it(self):
        x = self.disk("x.adf")
        control = self.frontend("observer", "--runs", 125, "--do", "5:disk:eject", "--do", "5:disk:index=1", "--do", "6:disk:insert", "--do", "125:save:end",
                                content=self.two_disks()).stdout
        out = self.frontend("observer", "--runs", 250, "--do", "5:save:before", *self.hot(x, eject=8, choose=8, insert=9), "--do", "130:load:before",
                            "--do", "130:disk:eject", "--do", "130:disk:index=1", "--do", "131:disk:insert", "--do", "250:save:end", content=self.one_disk()).stdout
        self.assertEqual(self.views(out)[130:250], self.views(control)[5:125])
        self.assertGreater(len({r["px"] for r in self.runs(control)[5:]}), 1)                   # the swap is in there
        self.assertEqual(self.saved(out)["end"], self.saved(control)["end"])                      # the machine, the manifest and the adapter's side are the same bytes

    def test_a_state_file_taken_after_an_addition_loads_where_the_disk_was_added_again_and_is_refused_by_name_elsewhere(self):
        x = self.disk("x.adf")
        path = self.work() / "added.state"
        first = self.frontend("observer", "--runs", 130, *self.hot(x, eject=8, choose=8, insert=9), "--do", f"15:savefile:{path}", content=self.one_disk()).stdout
        self.assertEqual(path.stat().st_size, int(re.search(r"state size=(\d+)", first)[1]))      # the size did not move with the addition (STATE01)
        x2 = self.disk("renamed.adf", "x.adf")
        y = self.disk("y.adf")
        z = self.disk("z.adf")

        def second(*steps, runs=121, content=None):
            return self.frontend("observer", "--runs", runs, *steps, content=content or self.one_disk()).stdout

        again = ["--do", "3:disk:eject", "--do", "3:disk:add", "--do", f"3:disk:replace=1:{x2}", "--do", "4:disk:insert"]     # the same content under another name
        loads = [*again, "--do", f"6:loadfile:{path}"]
        out = second(*loads)
        self.assertEqual([m[3] for m in ts.LOAD.finditer(out)], ["1"])
        self.assertEqual(self.views(out)[6:121], self.views(first)[15:130])                       # the saved timeline, swap landing included
        out = second(*again, "--do", "5:disk:add", "--do", f"5:disk:replace=2:{z}", "--do", f"6:loadfile:{path}")                      # a disk more at the end of the list: the manifest starts with the state's
        self.assertEqual([m[3] for m in ts.LOAD.finditer(out)], ["1"])
        self.assertEqual(self.views(out)[6:121], self.views(first)[15:130])
        # Where the disk was not added, or another one was, the state is refused before anything of the machine is touched.
        for label, steps in (("no addition", []), ("another disk", ["--do", "3:disk:eject", "--do", "3:disk:add", "--do", f"3:disk:replace=1:{y}", "--do", "4:disk:insert"]),
                             ("two disks in another order", ["--do", "3:disk:eject", "--do", "3:disk:add", "--do", "3:disk:add", "--do", f"3:disk:replace=1:{y}",
                                                             "--do", f"3:disk:replace=2:{x2}", "--do", "4:disk:insert"])):
            with self.subTest(label):
                control = second(*steps, runs=30)
                out = second(*steps, "--do", f"6:loadfile:{path}", runs=30)
                self.assertEqual([m[3] for m in ts.LOAD.finditer(out)], ["0"], out[-600:])
                self.assertEqual(self.views(out), self.views(control))
        refused = second("--do", f"6:loadfile:{path}", runs=8)
        self.assertRegex(refused, r"the state is refused: its disk list does not start with the list of this session")
        wrong = second("--do", "3:disk:eject", "--do", "3:disk:add", "--do", f"3:disk:replace=1:{y}", "--do", "4:disk:insert", "--do", f"6:loadfile:{path}", runs=8)
        self.assertRegex(wrong, r"p6_state_load failed \(10\): state refused: media_manifest_hash")

    def test_a_state_taken_before_an_addition_loads_in_a_new_process_that_added_the_disk(self):
        x = self.disk("x.adf")
        path = self.work() / "before.state"
        first = self.frontend("observer", "--runs", 43, "--do", f"9:savefile:{path}", content=self.one_disk()).stdout
        out = self.frontend("observer", "--runs", 40, *self.hot(x, eject=2, choose=2, insert=3), "--do", f"6:loadfile:{path}", "--do", "6:images", content=self.one_disk()).stdout
        self.assertEqual([m[3] for m in ts.LOAD.finditer(out)], ["1"])
        self.assertEqual(self.images(out), [(2, 0, False)])                                      # as at the save, with the added slot behind
        self.assertEqual(self.views(out)[6:], self.views(first)[9:43])

    # --- runahead ----------------------------------------------------------------------------------------------------------------------------

    def test_runahead_across_an_addition_converges_on_the_plain_run(self):
        x = self.disk("x.adf")
        steps = (*self.hot(x, eject=10, choose=14, insert=15), "--do", "150:images", "--do", "150:save:end")
        plain = self.frontend("observer", "--runs", 150, *steps, content=self.one_disk()).stdout
        for k in (1, 2, 3):
            with self.subTest(k=k):
                out = self.frontend("observer", "--runs", 150, *steps, "--ahead-every", k, content=self.one_disk()).stdout
                self.assertEqual(self.views(out), self.views(plain))
                self.assertEqual(self.saved(out)["end"]["section"], self.saved(plain)["end"]["section"])        # the adapter's side, as in test_state.py's runahead tests
                self.assertEqual(self.images(out), self.images(plain))
        self.assertGreater(len({r["px"] for r in self.runs(plain)}), 1)


if __name__ == "__main__":
    unittest.main()
