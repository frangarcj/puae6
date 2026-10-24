"""T28: the write overlay and its persistence in the BizHawk/Waterbox core, proved with the real sandbox (WB10, WB04, WB11).

The guest is the floppy-writing micro-ROM of MED02 (tests/media-cow/med02.py, with its track buffers inside the ROM): it writes, overwrites and reads back whole AmigaDOS tracks of
DF0. The core is created with the sync setting FloppyWrites; the snapshot of the sandbox carries the overlay, so the checks are the same ones a state has to pass, applied to a disk:

WB10  the disk after the guest's writes is the model's, the state taken in the middle takes the later writes back (bytes, the track the guest read, the drive lines and everything
      else) in the same sandbox and in a new one, a movie branch or seek does the same, and neither the disks the core was given nor any file changes.
WB04  the state of one sandbox restored in a second and a third (also in another process), taking turns with the first, gives the same units, the same disk and the same digest.
WB11  a state or a movie of another FloppyWrites, of another disk, or made after another SaveRam was stored, is refused with its reason and the instance goes on as if never asked.
ISaveRam  rollback and durable copy are different things: the SaveRam is a bounded list of changed sectors tagged with the content id of their base, taken explicitly, never during
      the replay of a movie, and given back only before the first unit; a SaveRam that is not whole, not ours or not for these disks is refused and changes nothing.

    python3 -m unittest discover -s tests/waterbox-media -p "test_media_overlay.py"
"""
import unittest

from support import AVAILABLE, SKIP, Sessions, med02, disk, sha, watch, build_waterbox  # noqa: F401

STATE_UNIT, END_UNIT = med02.STATE_UNIT, med02.END_UNIT
LATER = END_UNIT - STATE_UNIT


def snap(name):
    """Everything that tells what the disk and the drive are now: both disks, the overlay, the guest's markers, the drive lines it logged and the tracks it read."""
    return [f"export {name} 0", f"export {name} 1", f"status {name} 0", f"status {name} 1", *watch(name, 300)]


def read(self, out, name, n):
    """The n-th snap() of `name` in `out`, as comparable values."""
    exports, statuses, peeks = self.exports(out, name)[2 * n:2 * n + 2], self.statuses(out, name)[2 * n:2 * n + 2], self.peeks(out, name)[5 * n:5 * n + 5]
    return dict(disks=[e[3] for e in exports], codes=[e[1] for e in exports], status=[{k: v for k, v in s.items() if k != "index"} for s in statuses],
                marks=med02.marker_words(peeks[0]), log=peeks[1], captures=peeks[2:5])


@unittest.skipUnless(AVAILABLE, SKIP)
class OverlayTests(Sessions):
    def expected(self, writes):
        return sha(med02.expected_image(self.adf["A"], writes))

    # --- WB10 ----------------------------------------------------------------------------------------------------------------------------

    def test_the_guest_writes_overwrites_and_reads_back_and_the_disks_it_was_given_are_untouched(self):
        out = self.session([self.core("A"), *snap("A"), f"run A {STATE_UNIT}", *snap("A"), f"run A {LATER}", *snap("A")])
        before, middle, end = (read(self, out, "A", n) for n in range(3))
        self.assertEqual([sha(self.adf["A"]), sha(self.adf["B"])], before["disks"], "before the guest writes, the disk is its base")
        self.assertEqual([0, 0], before["codes"])
        self.assertEqual([0, 0], [s["used"] for s in before["status"]])
        # in the middle: write 1 is on the disk and nothing else; at the end write 2a replaced it and 2b added the other side
        self.assertEqual(["w1", "r1"], middle["marks"])
        self.assertEqual([self.expected(["buf1"]), sha(self.adf["B"])], middle["disks"])
        self.assertEqual([(11, 11, 0, 0), (11, 0, 0, 0)], [(s["used"], s["medium"], s["refused"], s["raw"]) for s in middle["status"]])
        self.assertEqual(list(med02.MARKERS), end["marks"])
        self.assertEqual([self.expected(["buf2", "buf3"]), sha(self.adf["B"])], end["disks"])
        self.assertEqual([(22, 22, 0, 0), (22, 0, 0, 0)], [(s["used"], s["medium"], s["refused"], s["raw"]) for s in end["status"]])
        self.assertEqual(3520, end["status"][0]["capacity"])
        # what the guest read back came from the disk (the head had left the track) and decodes to the sectors it wrote
        for capture, wanted in zip(end["captures"], ("buf1", "buf3", "buf2")):
            good, found = med02.sectors_of(capture, wanted)
            self.assertGreaterEqual(good, 10, f"{good} of {found} sectors decode to {wanted}")
            self.assertEqual(good, found)

    def test_a_core_without_the_setting_keeps_every_disk_protected_and_has_no_save_ram(self):
        out = self.session([self.core("A", writes=False), "services A", "export A 0", "status A 0", f"run A {END_UNIT}", "export A 0", "saveram A sr", "modified A",
                            f"peek A Chip_RAM {disk.LOG_BASE:x} 300"])
        self.assertIn("services A saveram=0 memorydomains=1", out)
        self.assertEqual([(0, 4), (0, 4)], [(e[0], e[1]) for e in self.exports(out, "A")], "UNSUPPORTED: no overlay was reserved")
        self.assertEqual([4], [s["result"] for s in self.statuses(out, "A")])
        self.assertEqual([("sr", None, None, 0)], self.saverams(out, "A"))
        self.assertEqual([0], self.modified(out, "A"))
        log = self.peeks(out, "A")[0]
        self.assertTrue(all(disk.pra_state(b).write_protected for b in log[med02.SPINUP + 2:med02.SPINUP + 20]), "/WPRO is asserted on every disk")

    def test_a_state_in_the_middle_takes_the_later_writes_back_in_the_same_sandbox_and_the_future_repeats(self):
        out = self.session([self.core("A"), f"run A {STATE_UNIT}", "save A s", "digest A d105", *snap("A"), f"run A {LATER}", "digest A d300", *snap("A"),
                            "load A s", "digest A r105", *snap("A"), f"run A {LATER}", "digest A r300", *snap("A")])
        first, again = self.units(out, "A")[STATE_UNIT:END_UNIT], self.units(out, "A")[END_UNIT:]
        self.assertEqual(LATER, len(first))
        self.assertEqual([u[1:] for u in first], [u[1:] for u in again], "every unit after the load is the one that followed the save")
        digests = self.digests(out, "A")
        self.assertEqual(digests["d105"], digests["r105"], "the state taken back is the state that was saved: overlay, drive, track buffer")
        self.assertEqual(digests["d300"], digests["r300"])
        self.assertNotEqual(digests["d105"], digests["d300"])
        m105, m300, r105, r300 = (read(self, out, "A", n) for n in range(4))
        self.assertEqual(m105, r105, "bytes, status, markers, drive lines and tracks are those of the state")
        self.assertEqual(m300, r300)
        self.assertEqual([self.expected(["buf1"]), sha(self.adf["B"])], r105["disks"])
        self.assertEqual(11, r105["status"][0]["used"])

    def test_the_state_runs_in_new_sandboxes_and_they_can_take_turns_with_the_first(self):
        # WB04: a core B and a core C, new sandboxes in the same process, load the state of A; then A, B and C go on in turns.
        step = 15
        turns = [f"run {n} {step}" for _ in range(LATER // step) for n in "ABC"]
        out = self.session([self.core("A"), f"run A {STATE_UNIT}", "save A s", f"savefile A {self.out / 'wb04.state'}", self.core("B"), self.core("C"), "load B s", "load C s",
                            *snap("B"), *snap("C"), *turns, "digest A end", "digest B end", "digest C end", *snap("A"), *snap("B"), *snap("C")])
        digests = {n: self.digests(out, n)["end"] for n in "ABC"}
        self.assertEqual(1, len(set(digests.values())), "three sandboxes, three address spaces, one machine")
        self.assertEqual(len(self.units(out, "A")[STATE_UNIT:]), len(self.units(out, "B")))
        self.assertEqual(len(self.units(out, "B")), len(self.units(out, "C")))
        self.assertEqual([u[1:] for u in self.units(out, "A")[STATE_UNIT:]], [u[1:] for u in self.units(out, "B")], "B repeats what A did after the state")
        self.assertEqual([u[1:] for u in self.units(out, "B")], [u[1:] for u in self.units(out, "C")])
        b105, c105 = read(self, out, "B", 0), read(self, out, "C", 0)
        self.assertEqual(b105, c105)
        self.assertEqual([self.expected(["buf1"]), sha(self.adf["B"])], b105["disks"], "the disk the new sandbox gets from the state has the write")
        a_end, b_end, c_end = (read(self, out, n, 1 if n != "A" else 0) for n in "ABC")
        self.assertEqual(a_end, b_end)
        self.assertEqual(b_end, c_end)
        # and in another process, from the file
        later = self.session([self.core("D"), f"loadfile D {self.out / 'wb04.state'}", *snap("D"), f"run D {LATER}", "digest D end", *snap("D")])
        self.assertEqual(digests["A"], self.digests(later, "D")["end"])
        self.assertEqual(b105, read(self, later, "D", 0))
        self.assertEqual(a_end, read(self, later, "D", 1))
        self.assertEqual([u[1:] for u in self.units(out, "B")], [u[1:] for u in self.units(later, "D")])

    def test_a_movie_branch_and_a_seek_leave_the_overlay_where_the_state_says_and_nothing_outside_the_sandbox_changes(self):
        movie = self.out / "wb10.bk2"
        recorded = self.session([self.core("R"), f"run R {END_UNIT} record", f"endmovie R {movie}", "digest R end", *snap("R")])
        branch = f"{STATE_UNIT + 15}:Eject_Disk"
        out = self.session([self.core("M"), f"moviecheck M {movie}", f"run M {STATE_UNIT} movie={movie}", "save M s", "saveram M during", "modified M",
                            f"run M {LATER} movie={movie}", "digest M play", "moviefinish M " + str(movie), *snap("M"),
                            "load M s", f"run M {LATER} movie={movie}", "digest M seek", *snap("M"),
                            "load M s", f"run M {LATER} press={branch}", "digest M branch", *snap("M"),
                            "load M s", f"run M {LATER} movie={movie}", "digest M back", *snap("M"), "replay M off", "saveram M after keep"])
        self.assertIn(f"movie accepted M", out)
        digests = self.digests(out, "M")
        self.assertEqual(self.digests(recorded, "R")["end"], digests["play"], "the movie plays back to the machine that recorded it, disk included")
        self.assertEqual(digests["play"], digests["seek"], "a seek back to the state and forward again is the same machine")
        self.assertEqual(digests["play"], digests["back"], "and so is the movie again after a branch")
        self.assertNotEqual(digests["play"], digests["branch"], "the branch (the disk is ejected before the guest's second write) is another machine")
        play, seek, branch_snap, back = (read(self, out, "M", n) for n in range(4))
        self.assertEqual(read(self, recorded, "R", 0), play)
        self.assertEqual(play, seek)
        self.assertEqual(play, back)
        self.assertEqual(self.expected(["buf1"]), branch_snap["disks"][0], "the branch did not see the later writes: its overlay is the state's, plus nothing")
        self.assertEqual([("during", None, None, 0)], self.saverams(out, "M")[:1], "while the movie plays, the SaveRam is not read")
        self.assertEqual([0], self.modified(out, "M")[:1])
        after = self.saverams(out, "M")[1]
        self.assertEqual(("after", 12 + 40 + 22 * 514 + 32), after[:2], "the movie is over: the SaveRam is the overlay of the machine, nothing more")

    # --- ISaveRam: rollback and durable copy ---------------------------------------------------------------------------------------------

    def test_the_save_ram_is_a_bounded_list_of_changed_sectors_and_is_not_the_rollback(self):
        path = self.out / "sr300.bin"
        out = self.session([self.core("A"), "services A", "modified A", f"run A {STATE_UNIT}", "modified A", "saveram A sr105", "modified A", "save A s",
                            f"run A {LATER}", "modified A", "saveram A sr300 keep", "modified A", f"writesaveram sr300 {path}",
                            "load A s", "modified A", "saveram A back", "modified A", "load A s", f"run A {LATER}", "saveram A again"])
        self.assertIn("services A saveram=1", out)
        rows = {tag: (size, digest, modified) for tag, size, digest, modified in self.saverams(out, "A")}
        # nothing is written until something changed; 11 sectors after write 1, 22 at the end: 12 + 40 + n * 514 + 32 bytes, never a disk image
        self.assertEqual([0, 1, 0, 1, 1, 0, 0], self.modified(out, "A"), "modified follows the writes, and clean after a clone that clears")
        self.assertEqual(12 + 40 + 11 * 514 + 32, rows["sr105"][0])
        self.assertEqual(12 + 40 + 22 * 514 + 32, rows["sr300"][0])
        self.assertLess(rows["sr300"][0], disk.ADF_BYTES // 50, "bounded: nothing like the 901120 bytes of the disk")
        self.assertEqual(rows["sr300"][0], path.stat().st_size)
        # rollback to the state (the overlay of write 1) is not a change of the durable copy: it is the copy that was kept
        self.assertEqual(rows["sr105"][1], rows["back"][1], "the SaveRam after the rollback is the one taken at that point")
        self.assertEqual(rows["sr300"][1], rows["again"][1], "and after going forward again, the later one")
        self.assertEqual(path.read_bytes()[:4], b"P6SR")

    def test_a_save_ram_given_back_before_the_first_unit_is_the_disk_the_machine_has_and_changes_what_states_are_valid_for(self):
        path = self.out / "sr.bin"
        first = self.session([self.core("A"), f"run A {END_UNIT}", "saveram A sr", f"writesaveram sr {path}", "save A plain"])
        sr = path.read_bytes()
        self.assertEqual(12 + 40 + 22 * 514 + 32, len(sr))
        end = self.expected(["buf2", "buf3"])
        out = self.session([self.core("A"), f"readsaveram sr {path}", "storesaveram A sr", "export A 0", "status A 0", "modified A", "saveram A back keep", "services A",
                            "save A stored", "digest A d0", f"run A {END_UNIT}", "digest A d300", "saveram A sr2", "export A 0",
                            self.core("B"), "storesaveram B sr", "save B same", "load A same",                        # the same SaveRam, the same identity: the state loads
                            self.core("P"), "save P plain", "load A plain"])                                         # no SaveRam, another identity: refused
        self.assertEqual([("back", len(sr), sha(sr), 0)], [r for r in self.saverams(out, "A")][:1], "what was stored is what is kept: nothing is modified")
        exports = self.exports(out, "A")
        self.assertEqual(end, exports[0][3], "the machine has the disk the SaveRam made, before running a unit")
        self.assertEqual(0, self.statuses(out, "A")[0]["medium"], "and the overlay is empty: the stored sectors are in the base of the guest")
        self.assertEqual(0, self.statuses(out, "A")[0]["used"])
        self.assertEqual(end, exports[1][3], "the guest writes the same tracks again and the disk is still that")
        self.assertEqual(sha(sr), self.saverams(out, "A")[1][2], "so is the SaveRam: cumulative, relative to the disk the core was given, not to what the guest has")
        self.assertIn("loaded A same", out)
        self.assertEqual([("OtherConfig", True)], [(r, "SaveRam" in m or "other sync settings" in m) for r, m in self.rejections(out, "A")], "a state made without that SaveRam does not load")
        # the files the core was given are still the originals (tearDown); the SaveRam file is the only thing this test wrote, and it is in out/

    def test_a_save_ram_that_is_not_whole_not_ours_or_not_for_these_disks_is_refused_and_changes_nothing(self):
        path = self.out / "good.bin"
        self.session([self.core("A"), f"run A {END_UNIT}", "saveram A sr", f"writesaveram sr {path}"])
        size = path.stat().st_size
        attempts = [("magic", "flip:0", "NotSaveRam"), ("version", "flip:4", "WrongVersion"), ("disk count", "flip:8", "BadFields"), ("a sector", "flip:100", "Corrupt"),
                    ("the checksum", f"flip:{size - 1}", "Corrupt"), ("content id", "flip:19", "OtherMedia"), ("cut", f"truncate:{size - 1}", "BadLength"),
                    ("cut in the header", "truncate:30", "BadLength"), ("empty", "truncate:0", "NotSaveRam"), ("junk after", "junk:3", "BadLength")]
        lines = [self.core("Z"), f"readsaveram sr {path}", "digest Z before"]
        for label, mutation, _ in attempts:
            lines += [f"mutatesaveram sr bad {mutation}", "storesaveram Z bad"]
        lines += ["digest Z after", "export Z 0", "storesaveram Z sr", "export Z 0", self.core("W", writes=False), "storesaveram W sr", self.core("S", adf="BA"), "storesaveram S sr",
                  "mutatesaveram sr padded pad:5000", self.core("Y"), "storesaveram Y padded", "saveram Y y keep", self.core("X"), "run X 1", "storesaveram X sr",
                  "digest X after-run", self.core("V"), "run V 1", "replay V on", "storesaveram V sr", "digest V last"]
        out = self.session(lines)
        self.assertEqual([reason for _, _, reason in attempts], [r for r, _ in self.saveram_rejections(out, "Z")], "every damaged SaveRam is refused for its own reason")
        digests = self.digests(out, "Z")
        self.assertEqual(digests["before"], digests["after"], "a refused SaveRam leaves the instance exactly as it was")
        before, after = self.exports(out, "Z")
        self.assertEqual(sha(self.adf["A"]), before[3])
        self.assertEqual(self.expected(["buf2", "buf3"]), after[3], "and the good one still applies")
        self.assertEqual(["WritesDisabled"], [r for r, _ in self.saveram_rejections(out, "W")])
        self.assertEqual(["OtherMedia"], [r for r, _ in self.saveram_rejections(out, "S")], "a SaveRam of disk A does not go into a core whose first disk is B")
        self.assertIn("storedsaveram Y padded", out, "zero padding after the data (a frontend sizing the file) is ignored")
        self.assertEqual(size, self.saverams(out, "Y")[0][1], "and is not part of the SaveRam")
        self.assertEqual(["TooLate"], [r for r, _ in self.saveram_rejections(out, "X")], "the machine already ran")
        self.assertEqual(["TooLate"], [r for r, _ in self.saveram_rejections(out, "V")], "and not into a replay either")

    # --- WB11 ----------------------------------------------------------------------------------------------------------------------------

    def refused(self, loader_kwargs, saved_kwargs):
        """A state made by a core with `saved_kwargs` is refused by a core with `loader_kwargs`; the loader goes on as one that was never asked. Returns the reason."""
        control = [self.core("K", **loader_kwargs), "run K 20", "digest K a", "run K 20", "digest K b"]
        out = self.session([self.core("S", **saved_kwargs), "run S 20", "save S s",
                            self.core("L", **loader_kwargs), "run L 20", "digest L before", "load L s", "digest L after", "run L 20", "digest L later",
                            *control])
        reasons = self.rejections(out, "L")
        self.assertEqual(1, len(reasons), out[-800:])
        digests = self.digests(out, "L")
        self.assertEqual(digests["before"], digests["after"], "a refused state changes nothing")
        self.assertEqual(self.digests(out, "K")["b"], digests["later"], "and the instance goes on as one that was never asked")
        return reasons[0][0]

    def test_a_state_of_a_core_with_floppy_writes_does_not_load_in_one_without_and_back(self):
        # the setting changes the heaps of the sandbox as well (the overlay's room), which the envelope checks first; both are in the identity
        self.assertIn(self.refused(dict(writes=False), saved_kwargs=dict(writes=True)), ("OtherHeaps", "OtherConfig"))
        self.assertIn(self.refused(dict(writes=True), saved_kwargs=dict(writes=False)), ("OtherHeaps", "OtherConfig"))

    def test_a_state_made_with_other_disks_does_not_load(self):
        self.assertEqual("OtherMedia", self.refused(dict(adf="BA"), saved_kwargs=dict(adf="AB")))
        self.assertEqual("OtherMedia", self.refused(dict(adf="AC"), saved_kwargs=dict(adf="AB")))

    def test_a_movie_belongs_to_the_setting_it_was_recorded_with(self):
        movie, plain = self.out / "writes.bk2", self.out / "plain.bk2"
        out = self.session([self.core("R"), f"run R 30 record", f"endmovie R {movie}", self.core("Q", writes=False), "run Q 30 record", f"endmovie Q {plain}",
                            self.core("W"), f"moviecheck W {movie}", f"moviecheck W {plain}", self.core("P", writes=False), f"moviecheck P {movie}", f"moviecheck P {plain}"])
        self.assertEqual(["movie accepted W", "movie rejected W reason=OtherConfig", "movie rejected P reason=OtherConfig", "movie accepted P"],
                         [" ".join(line.split()[:4 if "rejected" in line else 3]) for line in out.splitlines() if line.startswith(("movie accepted", "movie rejected"))])


if __name__ == "__main__":
    unittest.main()
