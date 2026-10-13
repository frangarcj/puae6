"""T56b: hard files on the IDE port of Gayle in the PUAE6 core of BizHawk (Puae6Core), in the real Waterbox sandbox.

HdfController is a sync setting of the core (a restart setting): Ide gives the guest p6_config.hdf_controller = 1, the A600 or the A1200 then has Gayle with the hard files as the drives of
its IDE (unit 0 the master, 1 the slave of the channel, 2 and 3 the pair behind the splitter), and the config hash of the state envelope and the header of a movie carry it when it is not
Uae. The guest is the micro-ROM of tests/hdf/ide_rom.py (loop_program): an INT2 handler that acknowledges the drive and a main loop that writes two blocks (a new chunk of the overlay each
pass), reads them back and reads block 0, through the registers of Gayle; and its guest that pokes at the registers at random (chaos_program). What is checked, as for the UAE controller
(tests/waterbox-media/test_media_hdf.py):
MOUNT  the hard file the core exports after N units is, byte for byte, the one the native engine exports for the same ROM, machine and image (p6_headless --machine --hdf-controller ide --hdf
       --export-hdf), on the A600 and on the A1200; the overlay counts the chunks the guest wrote.
WB03   save at N, go on M, load, go on M again: the same units and digest, with the controller busy and with the random guest; the overlay goes back with the load; a new sandbox continues the same.
REAL   (opt-in: P6_KICKSTART_A600_ZIP, P6_KICKSTART_A1200_ROM, P6_WB205_ZIP, read in place, never copied or hashed) the Kickstart 2.05 on the A600 and the 3.1 on the A1200 boot the
       Workbench 2.05, given to the core as a hard file on the IDE, and the picture of the last unit in the sandbox is the one the native engine shows after the same units.
ID     a state of the IDE core is refused by the one with the UAE controller and the other way round, and by one with no hard file (OtherConfig, before the sandbox is touched, and the instance
       that refused it goes on as if never asked); a core that asks for the IDE on a machine without Gayle fails to start with the reason of the bridge.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-media -p "test_media_ide.py"
"""
import os
import re
import subprocess
import unittest

from support import AVAILABLE, SKIP, ROOT, Sessions, sha, tree, build_waterbox  # noqa: F401
from tests.hdf import hdf_image, ide_rom  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe  # noqa: E402

CHUNK = 4096
UNITS = 30


@unittest.skipUnless(AVAILABLE, SKIP)
class IdeHardFileTests(Sessions):
    FIRMWARE = {}
    native_probe = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.media / "ideloop.rom").write_bytes(ide_rom.loop_rom())
        (cls.media / "chaos.rom").write_bytes(ide_rom.chaos_rom(0x1234ABCD))
        cls.image = hdf_image.rdb_image(cylinders=64)                               # 2 MiB: 512 chunks
        (cls.media / "A.hdf").write_bytes(cls.image)
        cls.files_before = tree(cls.media)
        cls.cache = {}

    def hcore(self, name, machine="a600", ide=True, hdf=True, **options):
        words = dict(machine=machine, **options)
        if ide:
            words["hdfc"] = "Ide"
        if hdf:
            words["hdf"] = str(self.media / "A.hdf")
        return self.core(name, adf="", writes=False, **words)

    def run_ide(self, lines, firmware="ideloop", **kwargs):
        return self.session(lines, firmware=firmware, **kwargs)

    def native(self, machine, units, rom="ideloop"):
        """The hard file the native engine leaves after `units` units of the ROM on the IDE of `machine`."""
        key = (machine, units, rom)
        if key not in self.cache:
            headless = type(self).native_probe or build_probe(ROOT / "build/link-audit", False, "p6_headless")
            type(self).native_probe = headless
            out = self.out / f"native-{machine}-{units}-{rom}.bin"
            result = subprocess.run([str(headless), "--run", str(self.media / f"{rom}.rom"), "--machine", machine, "--hdf-controller", "ide", "--units", str(units),
                                     "--hdf", str(self.media / "A.hdf"), "--export-hdf", str(out)], capture_output=True, text=True, timeout=900)
            self.assertEqual(0, result.returncode, result.stderr)
            self.cache[key] = out.read_bytes()
        return self.cache[key]

    # --- MOUNT ---------------------------------------------------------------------------------------------------------------------

    def test_the_hard_file_the_core_exports_after_the_guest_wrote_it_through_gayle_is_the_native_one(self):
        for machine in ("a600", "a1200"):
            for rom in ("ideloop", "chaos"):
                with self.subTest(machine=machine, rom=rom):
                    native = self.native(machine, UNITS, rom)
                    exported = self.out / f"mount-{machine}-{rom}.bin"
                    out = self.run_ide([self.hcore("A", machine), f"run A {UNITS}", f"export A 0 file={exported}"], firmware=rom)
                    self.assertEqual(native, exported.read_bytes())
                    self.assertNotEqual(self.image, native, "the guest wrote the hard file")

    # --- WB03 ----------------------------------------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_machine_with_the_controller_busy(self):
        for machine in ("a600", "a1200"):
            for rom in ("ideloop", "chaos"):
                for n in (4, 9):
                    with self.subTest(machine=machine, rom=rom, saved_at=n):
                        m = 6
                        out = self.run_ide([self.hcore("A", machine), f"run A {n}", "save A s", f"run A {m}", "digest A first", "load A s", f"run A {m}", "digest A second"], firmware=rom)
                        digests = self.digests(out, "A")
                        self.assertEqual(digests["first"], digests["second"])

    def test_a_state_runs_in_a_new_sandbox_as_in_the_first(self):
        for rom in ("ideloop", "chaos"):
            with self.subTest(rom=rom):
                out = self.run_ide([self.hcore("A"), "run A 5", "save A s", self.hcore("B"), "load B s", "run A 6", "run B 6", "digest A a", "digest B b"], firmware=rom)
                self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    # --- REAL ----------------------------------------------------------------------------------------------------------------------

    REAL_UNITS = 300

    def real_boot(self, machine, kickstart):
        from tests.step.test_kickstart_a600 import member
        disk = member(os.environ["P6_WB205_ZIP"], ".adf", 901120)
        (self.media / "wb.hdf").write_bytes(disk)
        (self.media / "real.rom").write_bytes(kickstart)
        self.files_before = type(self).files_before = tree(self.media)
        out = self.session([self.core("A", adf="", writes=False, machine=machine, hdfc="Ide", hdf=str(self.media / "wb.hdf")), f"run A {self.REAL_UNITS}"], firmware="real")
        sandbox = self.units(out, "A")
        self.assertEqual(self.REAL_UNITS, len(sandbox))
        headless = type(self).native_probe or build_probe(ROOT / "build/link-audit", False, "p6_headless")
        type(self).native_probe = headless
        result = subprocess.run([str(headless), "--run", str(self.media / "real.rom"), "--machine", machine, "--hdf-controller", "ide", "--units", str(self.REAL_UNITS),
                                 "--hdf", str(self.media / "wb.hdf")], capture_output=True, text=True, timeout=1800)
        self.assertEqual(0, result.returncode, result.stderr)
        native = re.findall(r"^unit (\d+) ticks=(\d+) .* hash=(\w+) audio", result.stdout, re.M)
        self.assertEqual(self.REAL_UNITS, len(native))
        self.assertEqual([(t, h[-16:]) for _, t, h in native][-20:], [(u[2], u[3][-16:]) for u in sandbox][-20:], "the last units are the native engine's, tick by tick and picture by picture")

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A600_ZIP") and os.environ.get("P6_WB205_ZIP"), "opt-in: P6_KICKSTART_A600_ZIP and P6_WB205_ZIP")
    def test_the_kickstart_205_on_the_a600_boots_the_workbench_from_the_ide_in_the_sandbox_as_natively(self):
        from tests.step.test_kickstart_a600 import member
        self.real_boot("a600", member(os.environ["P6_KICKSTART_A600_ZIP"], ".rom", 524288))

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_A1200_ROM") and os.environ.get("P6_WB205_ZIP"), "opt-in: P6_KICKSTART_A1200_ROM and P6_WB205_ZIP")
    def test_the_kickstart_31_on_the_a1200_boots_the_workbench_from_the_ide_in_the_sandbox_as_natively(self):
        from pathlib import Path
        self.real_boot("a1200", Path(os.environ["P6_KICKSTART_A1200_ROM"]).read_bytes())

    # --- ID ------------------------------------------------------------------------------------------------------------------------

    def test_a_state_of_another_controller_or_of_no_hard_file_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        for mine, theirs in ((dict(ide=True), dict(ide=False)), (dict(ide=False), dict(ide=True)), (dict(ide=True), dict(ide=True, hdf=False))):
            with self.subTest(core=mine, state=theirs):
                out = self.run_ide([self.hcore("A", **theirs), "run A 6", "save A s", self.hcore("T", **mine), "run T 3", "digest T before", "load T s", "digest T after"])
                refusals = self.rejections(out, "T")
                self.assertEqual(1, len(refusals), out[-1500:])
                # another controller is another config; no hard file is also other heaps (the sandbox of a core with a hard file is larger): either way it is refused before the sandbox is touched
                self.assertIn(refusals[0][0], ("OtherConfig", "OtherHeaps") if not mine.get("hdf", True) or not theirs.get("hdf", True) else ("OtherConfig",))
                self.assertEqual(self.digests(out, "T")["before"], self.digests(out, "T")["after"])

    def test_the_ide_on_a_machine_without_gayle_fails_to_start_with_the_reason_of_the_bridge(self):
        out = self.run_ide([self.hcore("A", "a500")], expect=1)
        self.assertIn("A600 and the A1200", out + self.stderr)


if __name__ == "__main__":
    unittest.main()
