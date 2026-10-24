"""T28b: the .SaveRAM file of EmuHawk and the PUAE6 core, round trip.

EmuHawk (src/BizHawk.Client.EmuHawk/MainForm.cs, LoadSaveRam and FlushSaveRAM) is a WinForms program that does not run here. What it does with a core's SaveRAM is a few lines, and
the session host of tests/ffi (MainFormSaveRam.cs, commands `mainflush` and `mainload`) runs those lines as they are, against the real core in the real Waterbox sandbox:

  FlushSaveRAM  CloneSaveRam() -> nothing when null -> FileWriter.Write(path, bytes, path + ".bak")
  LoadSaveRam   stock: the file goes whole only to a fixed list of cores; any other core gets a buffer the size of CloneSaveRam() of the core just created, which for PUAE6 is null
                (nothing differs from the base yet), so the load is skipped and the .SaveRAM is never read.
                patched: integration/bizhawk/patches/0001-register-puae6.patch adds Puae6.Puae6Core to that list, so the file goes whole to StoreSaveRam.
                Only IOException is caught ("An error occurred while loading Sram"): SaveRamRejectedException is an IOException for that reason.

What is checked:
* the patch applies to the pinned MainForm.cs and is the only change to those lines; the lines the harness reproduces are in MainForm as the harness has them (drift guard);
* a file written by FlushSaveRAM in one process is loaded by the patched path in another, before its first unit: the machine has the sectors (the disk it exports is the one that wrote
  them, no overlay is used, a second flush writes the same bytes) and goes on to the same end; the stock path loads nothing, which is the reason for the patch;
* a refused file (another disk, cut, damaged, empty, after the machine ran) is notified with its reason and does not end the program or change the machine; padding is accepted;
* a core without FloppyWrites has no ISaveRam: EmuHawk neither loads nor writes anything.

    python3 -m unittest discover -s tests/waterbox-media -p "test_media_frontend.py"
"""
import subprocess
import tempfile
import unittest

from support import AVAILABLE, SKIP, ROOT, Sessions, med02, disk, sha, build_waterbox  # noqa: F401
import sys

sys.path.insert(0, str(ROOT / "integration/bizhawk/patches"))
import build_tree  # noqa: E402

END_UNIT = med02.END_UNIT
SAVERAM_BYTES = 12 + 40 + 22 * 514 + 32
MAINFORM = "src/BizHawk.Client.EmuHawk/MainForm.cs"
PATCH = ROOT / "integration/bizhawk/patches/0001-register-puae6.patch"
HAVE_MACHAWK = (ROOT / "../MacHawk/.git").exists()


@unittest.skipUnless(HAVE_MACHAWK, "needs the pinned ../MacHawk checkout (read only)")
class MainFormPatchTests(unittest.TestCase):
    """The patch is what makes EmuHawk hand the whole file to the core."""

    @classmethod
    def setUpClass(cls):
        build_tree.check_source()
        original = build_tree.git("show", f"{build_tree.pinned_commit()}:{MAINFORM}")
        assert original.returncode == 0, original.stderr
        cls.original = original.stdout.decode("utf-8")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-mainform-")
        target = build_tree.Path(cls.tmp.name) / MAINFORM
        target.parent.mkdir(parents=True)
        target.write_bytes(original.stdout)
        result = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", str(PATCH)], cwd=cls.tmp.name, capture_output=True, text=True)
        # the other files of the patch are not in this directory: only MainForm.cs matters here
        cls.applied = "patching file '" + MAINFORM + "'" in result.stdout or "patching file " + MAINFORM in result.stdout
        cls.patched = target.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_patch_adds_puae6_to_the_list_that_gets_the_whole_file_and_nothing_else_in_mainform(self):
        self.assertTrue(self.applied)
        old, new = self.original.splitlines(), self.patched.splitlines()
        removed = [line for line in old if line not in new]
        added = [line for line in new if line not in old]
        self.assertEqual(1, len(removed), removed)
        self.assertIn('NES { BoardName: "FDS" })', removed[0])
        self.assertEqual(2, len(added), added)
        self.assertTrue(added[0].strip().startswith("//"), "a comment that says why")
        self.assertEqual(removed[0].replace('{ BoardName: "FDS" })', '{ BoardName: "FDS" } or Puae6.Puae6Core)'), added[1])

    def test_the_lines_the_session_host_reproduces_are_the_ones_of_mainform(self):
        """If BizHawk's LoadSaveRam or FlushSaveRAM change, the harness (tests/ffi/MainFormSaveRam.cs) has to follow."""
        start = self.patched.index("private void LoadSaveRam()")
        load = self.patched[start:self.patched.index("public FileWriteResult FlushSaveRAM", start)]
        flush = self.patched[self.patched.index("public FileWriteResult FlushSaveRAM"):][:900]
        for needle in ("if (Emulator.HasSaveRam())", "sram = File.ReadAllBytes(saveramToLoad.FullName);", "var oldRam = Emulator.AsSaveRam().CloneSaveRam();",
                       "if (oldRam is null)", "sram = new byte[oldRam.Length];", "_ = fs.Read(sram, 0, sram.Length);", "Emulator.AsSaveRam().StoreSaveRam(sram);",
                       "catch (IOException e)", 'AddOnScreenMessage("An error occurred while loading Sram");'):
            self.assertIn(needle, load)
        self.assertEqual(1, load.count("catch ("), "IOException is the only exception LoadSaveRam catches")
        for needle in ("if (Emulator.HasSaveRam())", "var saveram = Emulator.AsSaveRam().CloneSaveRam();", "if (saveram == null)", 'return FileWriter.Write(path, saveram, $"{path}.bak");'):
            self.assertIn(needle, flush)
        self.assertNotIn("Puae6", self.original)


@unittest.skipUnless(AVAILABLE, SKIP)
class FrontendTests(Sessions):
    def expected_end(self):
        return sha(med02.expected_image(self.adf["A"], ["buf2", "buf3"]))

    def written(self, name="first.saveram"):
        """A .SaveRAM as EmuHawk writes it: a machine that wrote its disk, FlushSaveRAM, in a process of its own."""
        path = self.out / name
        out = self.session([self.core("A"), f"run A {END_UNIT}", f"mainflush A {path}", "digest A end"])
        self.assertIn(f"mainflush A wrote={SAVERAM_BYTES}", out)
        self.assertEqual(SAVERAM_BYTES, path.stat().st_size)
        return path, self.digests(out, "A")["end"]

    @staticmethod
    def lines(out, prefix):
        return [line for line in out.splitlines() if line.startswith(prefix)]

    def test_a_save_ram_written_by_flush_comes_back_through_the_patched_path_and_the_machine_has_the_sectors(self):
        path, end_digest = self.written()
        again = self.out / "second.saveram"
        out = self.session([self.core("B"), "export B 0", f"mainload B {path} patched", "export B 0", "status B 0", "modified B", f"mainflush B {again}",
                            f"run B {END_UNIT}", "export B 0", f"mainflush B {self.out / 'third.saveram'}"])
        self.assertEqual(["mainload B loaded"], self.lines(out, "mainload"))
        before, after, end = (e[3] for e in self.exports(out, "B"))
        self.assertEqual(sha(self.adf["A"]), before, "a new core has the disk it was given")
        self.assertEqual(self.expected_end(), after, "and after the load, before any unit, the disk the machine had when the file was written")
        status = self.statuses(out, "B")[0]
        self.assertEqual((0, 0), (status["used"], status["medium"]), "the sectors are the base of the guest, not the overlay (the pool is free for what the guest writes next)")
        self.assertEqual([0], self.modified(out, "B"), "nothing is modified: what the file holds is what the core keeps")
        self.assertEqual(path.read_bytes(), again.read_bytes(), "a flush right after the load writes the file that was loaded (nothing is lost on the next exit)")
        self.assertEqual(self.expected_end(), end, "the guest goes on and writes the same tracks again: the same disk")
        self.assertEqual(path.read_bytes(), (self.out / "third.saveram").read_bytes(), "and the file is still that")
        self.assertEqual([], self.lines(out, "notice"))
        self.assertTrue(end_digest, "the first process ended with a digest")

    def test_a_reloaded_save_ram_is_the_disk_a_state_of_that_machine_runs_on(self):
        """The file is part of the identity of states and movies (docs/03-waterbox.md); EmuHawk loads it before the ROM runs, so a state taken after is valid after the next load too."""
        path, _ = self.written()
        out = self.session([self.core("B"), f"mainload B {path} patched", "run B 10", "save B s", "run B 20", "digest B d1", "load B s", "run B 20", "digest B d2",
                            self.core("C"), f"mainload C {path} patched", "load C s", "run C 20", "digest C d3"])
        digests = self.digests(out, "B")
        self.assertEqual(digests["d1"], digests["d2"])
        self.assertEqual(digests["d1"], self.digests(out, "C")["d3"], "another core that loaded the same file runs the state of the first")
        self.assertEqual([], self.rejections(out, "C"))

    def test_the_stock_path_loads_nothing_which_is_why_the_patch_exists(self):
        path, _ = self.written()
        out = self.session([self.core("B"), f"mainload B {path} stock", "export B 0", "modified B"])
        self.assertEqual(["mainload B skipped-null"], self.lines(out, "mainload"))
        self.assertEqual(sha(self.adf["A"]), self.exports(out, "B")[0][3], "the file was not read: the machine has the disk it was given")

    def test_a_file_the_core_refuses_is_notified_with_its_reason_and_neither_ends_the_program_nor_changes_the_machine(self):
        good, _ = self.written()
        sr = good.read_bytes()
        files = {
            "cut": sr[:-1],
            "cut-in-header": sr[:30],
            "empty": b"",
            "damaged": sr[:100] + bytes([sr[100] ^ 1]) + sr[101:],
            "foreign": b"NOT A SAVERAM " * 40,
        }
        for name, content in files.items():
            (self.out / f"{name}.saveram").write_bytes(content)
        lines = [self.core("Z"), "digest Z before"]
        for name in files:
            lines.append(f"mainload Z {self.out / (name + '.saveram')} patched")
        lines += ["digest Z after", "export Z 0", f"mainload Z {good} patched", "export Z 0", self.core("S", adf="BA"), "digest S before", f"mainload S {good} patched", "digest S after",
                  self.core("R"), "run R 1", f"mainload R {good} patched", "digest R after"]
        out = self.session(lines)                      # the program ends with 0: no exception got out of mainload
        rows = self.lines(out, "mainload")
        self.assertEqual(["mainload Z error=SaveRamRejectedException"] * len(files) + ["mainload Z loaded", "mainload S error=SaveRamRejectedException",
                                                                                          "mainload R error=SaveRamRejectedException"], rows)
        reasons = [line.split("(")[1].split(")")[0] for line in self.lines(out, "notice SaveRAM refused")]
        self.assertEqual(["BadLength", "BadLength", "NotSaveRam", "Corrupt", "NotSaveRam", "OtherMedia", "TooLate"], reasons)
        for name in "ZS":
            digests = self.digests(out, name)
            self.assertEqual(digests["before"], digests["after"], f"refusals leave the machine {name} as it was")
        exports = self.exports(out, "Z")
        self.assertEqual(sha(self.adf["A"]), exports[0][3])
        self.assertEqual(self.expected_end(), exports[1][3], "and the good file still applies afterwards")
        self.assertEqual(1, len(self.digests(out, "R")), "the core that ran carries on")

    def test_zero_padding_after_the_data_is_accepted(self):
        path, _ = self.written()
        padded = self.out / "padded.saveram"
        padded.write_bytes(path.read_bytes() + bytes(1 << 20))             # a frontend that sizes the file
        out = self.session([self.core("B"), f"mainload B {padded} patched", "export B 0", f"mainflush B {self.out / 'unpadded.saveram'}"])
        self.assertEqual(["mainload B loaded"], self.lines(out, "mainload"))
        self.assertEqual(self.expected_end(), self.exports(out, "B")[0][3])
        self.assertEqual(path.read_bytes(), (self.out / "unpadded.saveram").read_bytes())

    def test_a_core_without_floppy_writes_has_no_save_ram_for_emuhawk_to_load_or_write(self):
        path, _ = self.written()
        out = self.session([self.core("P", writes=False), f"mainload P {path} patched", f"mainflush P {self.out / 'none.saveram'}"])
        self.assertEqual(["mainload P skipped-nosaveram", "mainflush P nosaveram"], self.lines(out, "mainload") + self.lines(out, "mainflush"))
        self.assertFalse((self.out / "none.saveram").exists())

    def test_a_core_that_wrote_nothing_writes_no_file(self):
        out = self.session([self.core("A"), f"run A 10", f"mainflush A {self.out / 'nothing.saveram'}"])
        self.assertEqual(["mainflush A null"], self.lines(out, "mainflush"))
        self.assertFalse((self.out / "nothing.saveram").exists())


if __name__ == "__main__":
    unittest.main()
