"""T55b: real A4000/030 boot and public native/libretro restoration.

Opt in with P6_KICKSTART_A4000_ROM, an authorized raw Kickstart 3.1 image.
The image is read in place; disposable states and pictures live under build/.
"""
from importlib import import_module
import os
from pathlib import Path
import tempfile
import unittest

from tests.state import test_det02 as d
from tests.state.harness import ProbeCase, ROOT
from tests.build_support import build_targets, state_probe_tree
from tests.step.test_kickstart_a1200 import last_picture

lr = import_module("tests.libretro-host.test_libretro")
ROM = os.environ.get("P6_KICKSTART_A4000_ROM")
AVAILABLE = bool(ROM and Path(ROM).is_file())
BOOT = 1800
FUTURE = 60


@unittest.skipUnless(AVAILABLE, "BLOCKED: set P6_KICKSTART_A4000_ROM to an authorized A4000 ROM")
class NativeKickstartTests(ProbeCase):
    MACHINE = "a4030"
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"
    # DET02 compares chipset groups, excluding only upstream's write-only
    # debugger register mirror (custom_storage); it is not machine state.
    differing = d.Det02Case.differing

    @classmethod
    def firmware(cls):
        return Path(ROM).resolve()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp.cleanup()
        cls.tmp = tempfile.TemporaryDirectory(prefix="a4030-native-", dir=ROOT / "build")
        cls.dir = Path(cls.tmp.name)

    def test_real_firmware_public_restore_same_and_new_process(self):
        state = self.dir / "cut.state"
        flags = ("--cpu-compatibility", "normal")
        # Hash the full machine only where futures are compared, rather than
        # hashing 10 MiB of RAM on every unit of the 1800-unit firmware boot.
        digests = [part for u in range(BOOT + 1, BOOT + 2 * FUTURE + 1)
                   for part in ("--digest-at", str(u))]
        original = self.probe("kickstart", "--units", str(BOOT + 2 * FUTURE), *flags, "--every", "1000000",
                              self.SAVE_OP, str(BOOT), str(state),
                              *digests,
                              self.LOAD_OP, str(BOOT + FUTURE), str(state))
        self.assertFalse(original.save_errors)
        self.assertEqual([(BOOT + FUTURE, 0, "")], original.loads)
        # --digest-at uses the probe's "after-load" block syntax even without
        # loading. Index those observations by unit for the shared DET02 oracle;
        # the one actual load sits between the two future windows.
        self.assertEqual(2 * FUTURE + 1, len(original.after))
        for i, unit in enumerate(range(BOOT + 1, BOOT + 2 * FUTURE + 1)):
            at = i + int(i >= FUTURE)
            original.digests.update({(unit, s): value for s, value in original.after[at].items()})
            original.chip.update({(unit, g): value for g, value in original.chip_after[at].items()})
        fresh = self.probe("kickstart", "--units", str(FUTURE), *flags, "--every", "1", self.LOAD_OP, "0", str(state))
        self.assertEqual([(0, 0, "")], fresh.loads)
        for run, offset in ((original, BOOT + FUTURE), (fresh, 0)):
            for j in range(1, FUTURE + 1):
                self.assertEqual([], self.differing(original, run, BOOT, offset, j), (offset, j))


@unittest.skipUnless(AVAILABLE, "BLOCKED: set P6_KICKSTART_A4000_ROM to an authorized A4000 ROM")
class LibretroKickstartTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        directory, optimized = state_probe_tree()
        binaries = build_targets(directory, ["p6_libretro_host", "puae6_libretro", "p6_headless"], optimized=optimized)
        cls.host, cls.core, cls.headless = (binaries[t] for t in ("p6_libretro_host", "puae6_libretro", "p6_headless"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="a4030-libretro-", dir=ROOT / "build")
        cls.roms = {"real": Path(ROM).resolve()}
        cls.counter = 0

    def test_real_boot_matches_headless_and_restores_in_another_process(self):
        system = self.work()
        (system / "kick40068.A4000").symlink_to(self.roms["real"])
        state = self.work() / "cut.state"
        pictures = self.work()
        settings = ("--set", "puae6_preset=a4030_normal")
        out = self.frontend("real", "--runs", BOOT + 2 * FUTURE, *settings,
                            "--dump-pictures", pictures, "--dump-from", BOOT + 2 * FUTURE,
                            "--do", f"{BOOT}:save:s", "--do", f"{BOOT}:savefile:{state}",
                            "--do", f"{BOOT + FUTURE}:load:s", system=system).stdout
        self.assertIn("state load s exact ok=1", out)
        rows = self.runs(out)
        self.assertEqual(BOOT + 2 * FUTURE, len(rows))
        self.assertGreater(len({r["hash"] for r in rows[BOOT - 200:BOOT]}), 1, "Kickstart animation stopped")
        (pictures / f"picture-{BOOT + 2 * FUTURE:03d}.bin").rename(pictures / "frame-boot.bin")
        width, height, colours, _ = last_picture(pictures)
        self.assertGreaterEqual(len(colours), 5, "no Kickstart logo/text/drive")
        self.assertGreater(colours.most_common(1)[0][1], width * height // 2, "no insert-disk background")
        native = self.headless_units("real", BOOT, "--machine", "a4030", "--cpu-compatibility", "normal")
        for row in rows[:BOOT]:
            unit = native[row["unit"]]
            self.assertEqual((row["ticks"], row["hash"], row["audio"], row["size"]),
                             (unit["ticks"], unit["hash"], unit["audio"], unit["size"]))
        view = lambda r: (r["unit"], r["ticks"], r["audio"], r["ahash"], r["hash"], r["size"])
        expected = list(map(view, rows[BOOT:BOOT + FUTURE]))
        self.assertEqual(expected, list(map(view, rows[BOOT + FUTURE:])))
        fresh = self.frontend("real", "--runs", FUTURE, *settings,
                              "--do", f"0:loadfile:{state}:pos={BOOT}", system=system).stdout
        self.assertIn("ok=1", fresh)
        self.assertEqual(expected, list(map(view, self.runs(fresh))))
