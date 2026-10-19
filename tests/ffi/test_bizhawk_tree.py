"""T20: BizHawk loads PUAE6 explicitly, keeps UAE and does not touch MacHawk.

integration/bizhawk/patches/build_tree.py exports the pinned MacHawk sources out of its git object store (nothing is written in ../MacHawk), applies the
patches of integration/bizhawk/patches, installs the PUAE6 sources into BizHawk.Emulation.Cores and builds that real project (net48, BizHawk's own analyzers,
packages from the local cache). tests/ffi/inventory then loads the assembly as it was built and asks BizHawk's own CoreInventory for the Amiga:

* both cores are registered, UAE with its normal priority and PUAE6 with a low one, so UAE is still what an Amiga game opens with and PUAE6 is chosen
  explicitly (the core picker or Config.PreferredCores); the order the loader uses (RomLoader.MakeCoreFromCoreInventory) is reproduced here;
* the constructor that BizHawk calls takes CoreLoadParameters, the core name is the new constant, and the assembly records the guest it belongs with;
* the patches apply to the pinned commit with `git apply --check`, which writes nothing, and MacHawk's HEAD and its tracked files are as they were;
* opt-in (P6_EMUHAWK_BUILD=1): EmuHawk.exe, with the patched MainForm.cs that gives the .SaveRAM file whole to PUAE6 (T28b), compiles in a second tree.

Needs dotnet, the pinned ../MacHawk checkout and the local NuGet cache (no network), and the guest receipt (tools/build_waterbox.py --puae6); skips without them.
    python3 -m unittest discover -s tests/ffi -p "test_bizhawk_tree.py"
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "integration/bizhawk/patches"))
import build_tree  # noqa: E402

RECEIPT = ROOT / "build/waterbox/puae6/puae6-receipt.json"
AVAILABLE = (ROOT / "../MacHawk/.git").exists() and RECEIPT.is_file() and subprocess.run(["which", "dotnet"], capture_output=True).returncode == 0


def tracked_state():
    """HEAD of MacHawk and the content hash of what git says is modified there (a read; git status may refresh the index, which is not content)."""
    head = build_tree.git("rev-parse", "HEAD").stdout.decode().strip()
    diff = build_tree.git("diff", "--no-ext-diff", "--", "src", "References", "Common.props", "Directory.Packages.props").stdout
    return head, diff


@unittest.skipUnless(AVAILABLE, "needs dotnet, ../MacHawk at the pinned commit and the guest receipt")
class BizHawkTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = tracked_state()
        cls.guest = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        build_tree.check_patches()
        if build_tree.export_tree():
            build_tree.apply_patches()
            build_tree.install_sources()
        cls.assembly = build_tree.build(cls.guest)
        environment = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"), "DOTNET_NOLOGO": "1"}
        project = ROOT / "tests/ffi/inventory/Inventory.csproj"
        subprocess.run(["dotnet", "build", str(project), "--nologo", "-v", "q", f"-p:RestoreSources={Path.home() / '.nuget/packages'}"], check=True,
                       capture_output=True, env=environment, cwd=ROOT)
        result = subprocess.run(["dotnet", str(ROOT / "build/ffi-inventory/bin/Debug/net8.0/Inventory.dll"), str(cls.assembly.parent)], capture_output=True, text=True, env=environment)
        assert result.returncode == 0, result.stdout + result.stderr
        cls.out = result.stdout

    def cores(self):
        return [(m[1], int(m[2]), m[3], m[4]) for m in re.finditer(r"^core (\S+) priority=(\d+) type=(\S+) ctor=(\S+)$", self.out, re.M)]

    def test_both_amiga_cores_are_registered_and_uae_is_still_the_one_a_game_opens_with(self):
        cores = self.cores()
        self.assertEqual({"UAE", "PUAE6"}, {c[0] for c in cores})
        by_name = {c[0]: c for c in cores}
        self.assertEqual("BizHawk.Emulation.Cores.Computers.Amiga.UAE", by_name["UAE"][2])
        self.assertEqual("Puae6.Puae6Core", by_name["PUAE6"][2])
        self.assertEqual(0, by_name["UAE"][1])
        self.assertEqual(100, by_name["PUAE6"][1])             # CorePriority.Low
        self.assertEqual("CoreLoadParameters`2", by_name["PUAE6"][3])
        # What RomLoader does: the preferred core of the config first, then the loader's own priority.
        def order(preferred):
            return [c[0] for c in sorted(cores, key=lambda c: -300 if c[0] == preferred else c[1])]
        self.assertEqual(["UAE", "PUAE6"], order(None))
        self.assertEqual(["PUAE6", "UAE"], order("PUAE6"))     # chosen explicitly

    def test_the_core_has_its_own_name_and_the_assembly_records_the_guest_it_belongs_with(self):
        self.assertIn("names PUAE6=PUAE6 UAE=UAE", self.out)
        self.assertIn(f"pin {self.guest}", self.out)

    def test_the_patched_files_are_the_ones_the_patch_says(self):
        tree = build_tree.TREE
        self.assertIn('public const string PUAE6 = "PUAE6";', (tree / "src/BizHawk.Emulation.Cores/CoreNames.cs").read_text(encoding="utf-8-sig"))
        self.assertIn("[ CoreNames.UAE, CoreNames.PUAE6 ]", (tree / "src/BizHawk.Client.Common/config/Config.cs").read_text())
        installed = {p.name for p in (tree / build_tree.INSTALL_AT).glob("*.cs")}
        self.assertTrue({"Puae6Core.cs", "Puae6Core.EmuHawk.cs", "Puae6Guest.cs", "GuestIdentity.cs", "Abi.cs"} <= installed)

    def test_each_machine_asks_the_firmware_database_of_bizhawk_for_its_own_kickstart_and_the_database_has_both(self):
        # T23e, T24f: Machine.A500 / A600 / A1200 are the FirmwareIDs (Amiga, "A500" / "A600" / "A1200") of BizHawk's database, which MacHawk already has: nothing is added to it.
        settings = (ROOT / "integration/bizhawk/core/Puae6/Settings.cs").read_text()
        machines = re.search(r"enum Puae6Machine \{ ([^}]*) \}", settings)[1].replace(" ", "").split(",")
        self.assertEqual(["A500", "A600", "A1200", "A500OG", "A500PLUS", "A1000", "A2000OG", "A2000", "A4030", "CD32"], machines)
        launcher = (ROOT / "integration/bizhawk/core/EmuHawk/Puae6Core.EmuHawk.cs").read_text()
        self.assertIn("new(VSystemID.Raw.Amiga, FirmwareName(machine))", launcher)
        self.assertIn("FirmwareOf(machine)", launcher)
        self.assertIn("Puae6WhdLoad.Resolve(new Puae6Parameters", launcher)
        # T50: the five machines that came after ask for the entry of their family, so nothing is added to the database (FirmwareName's rows are the whole mapping)
        rows = re.findall(r"((?:Puae6Machine\.\w+(?: or )?)+) => \"(\w+)\"", re.search(r"static string FirmwareName.*?\};", launcher, re.S)[0])
        asked = {member: name for members, name in rows for member in re.findall(r"Puae6Machine\.(\w+)", members)}
        self.assertEqual({"A500OG": "A500", "A1000": "A500", "A2000OG": "A500", "A500PLUS": "A600", "A2000": "A600", "A4030": "A4000"}, asked)
        database = (build_tree.TREE / "src/BizHawk.Emulation.Common/Database/FirmwareDatabase.cs").read_text(encoding="utf-8-sig")
        for machine in ["A500", "A600", "A1200", "A4000", "CD32", "CD32ext"]:
            self.assertRegex(database, rf'Firmware\("Amiga", "{machine}", ', machine)
            self.assertRegex(database, rf'Option\("Amiga", "{machine}", File\([^\n]*FirmwareOptionStatus\.Ideal\)', f"{machine} has an ideal option")
        self.assertIn("Kickstart v1.3", re.search(r'Option\("Amiga", "A500", [^\n]*Ideal\)', database)[0])
        self.assertIn("Kickstart v2.05", re.search(r'Option\("Amiga", "A600", [^\n]*Ideal\)', database)[0])
        self.assertIn("Kickstart v3.1 r40.068", re.search(r'Option\("Amiga", "A1200", [^\n]*Ideal\)', database)[0])

    @unittest.skipUnless(os.environ.get("P6_EMUHAWK_BUILD") == "1", "opt-in (about a minute and 200 MB): P6_EMUHAWK_BUILD=1 compiles EmuHawk.exe in a second tree")
    def test_emuhawk_compiles_with_the_patched_mainform_that_hands_the_save_ram_file_to_the_core(self):
        # T28b: MainForm.LoadSaveRam names the cores that get the whole .SaveRAM file; the patch adds Puae6.Puae6Core. EmuHawk (WinForms, net48) is compiled, never run.
        exe = build_tree.build_emuhawk(self.guest)
        self.assertTrue(exe.is_file())
        patched = (build_tree.TREE_EMUHAWK / "src/BizHawk.Client.EmuHawk/MainForm.cs").read_text(encoding="utf-8-sig")
        self.assertIn('NES { BoardName: "FDS" } or Puae6.Puae6Core)', patched)

    def test_macHawk_was_not_touched(self):
        self.assertEqual(self.before, tracked_state())
        self.assertEqual(build_tree.pinned_commit(), self.before[0])


if __name__ == "__main__":
    unittest.main()
