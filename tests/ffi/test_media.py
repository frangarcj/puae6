"""T21a: disks in the PUAE6 core (MED01 through the BizHawk wrapper).

The guest exports p6_media_event_enqueue (p6w_media_event_enqueue) and the core translates the disk buttons (Next Drive, Next Slot, Eject Disk, Insert Disk) into
media events at the head of the unit, by edge, with a sequence number that only grows. The scenario is the one of tests/media (the floppy micro-ROM reads DF0, watches
the drive lines while the host swaps the disk, reads again, tries to write, reads back): with disk A in DF0 from power-on and disk B inserted into DF0 at unit 80
by the buttons, Chip RAM after the last unit is byte for byte the one the native media probe leaves when it mounts B with p6_media_event_enqueue, in both
standards and with two seeds. A held button is one event; Next Drive wraps with one drive; the guest sees eject and reinsert; a disk that is not an 880 KiB ADF
is refused; and the ADFs the core was given are not written.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/ffi -p "test_media.py"
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.media.test_med01 import LABEL_A, LABEL_B, run_scenario  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
SWAP = f"{disk.MOUNT_UNIT - 1}:Next Slot;{disk.MOUNT_UNIT}:Insert Disk"


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.native = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-ffi-media-")
        cls.dir = Path(cls.tmp.name)
        (cls.dir / "disk.rom").write_bytes(builder.build(disk.rom_source()))
        cls.adf = {"A": disk.make_adf(LABEL_A), "B": disk.make_adf(LABEL_B)}
        for name, image in cls.adf.items():
            (cls.dir / f"{name}.adf").write_bytes(image)
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def core(self, press, *args, units=disk.RUN_UNITS, adf="A,B", expect=0):
        MediaTests.count += 1
        dump = self.dir / f"chip-{MediaTests.count}.bin"
        adfs = ",".join(str(self.dir / f"{name}.adf") for name in adf.split(",") if name)
        result = build_waterbox.run_ffi("media", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.dir / "disk.rom"),
                                        "--adf", adfs, "--units", str(units), "--press", press, "--dump", str(dump), *args)
        self.assertEqual(expect, result.returncode, f"{result.stdout[-1500:]}\n{result.stderr[-1500:]}")
        return (dump.read_bytes() if dump.exists() else b""), result.stdout

    def scenario(self, standard, seed, mount_unit=disk.MOUNT_UNIT):
        result = subprocess.run([str(self.native), str(self.dir / "disk.rom"), standard, str(seed), str(disk.RUN_UNITS), str(mount_unit), str(self.dir / "A.adf"),
                                 str(self.dir / "B.adf"), str(self.dir / f"native-{standard}-{seed}-{mount_unit}.bin")], capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stdout[-400:] + result.stderr)
        return (self.dir / f"native-{standard}-{seed}-{mount_unit}.bin").read_bytes()

    def test_a_swap_made_with_the_buttons_leaves_chip_ram_as_the_native_event_does(self):
        for standard, flag in (("pal", []), ("ntsc", ["--ntsc"])):
            for seed in (1, 2):
                with self.subTest(standard=standard, seed=seed):
                    ram, _ = self.core(SWAP, "--seed", str(seed), *flag)
                    self.assertEqual(524288, len(ram))
                    native = self.scenario(standard, seed)
                    self.assertEqual(native, ram, "the same machine, byte for byte")
                    timeline = disk.swap_timeline(disk.pra_log(ram))
                    self.assertTrue(all(step is not None for step in timeline.values()), timeline)

    def test_next_drive_wraps_when_the_machine_has_one_drive(self):
        ram, out = self.core(f"{disk.MOUNT_UNIT - 2}:Next Drive;{SWAP}")
        self.assertEqual(self.scenario("pal", 1), ram)
        self.assertEqual("events 2", out.strip().splitlines()[-1])

    def test_a_held_button_is_one_event_and_the_guest_sees_eject_and_reinsert(self):
        ram, out = self.core("60-65:Eject Disk;69-72:Insert Disk", adf="A")
        self.assertEqual("events 3", out.strip().splitlines()[-1], "the boot disk, one eject and one insert, however long the buttons were held")
        timeline = disk.swap_timeline(disk.pra_log(ram))
        self.assertTrue(all(step is not None for step in timeline.values()), f"the guest saw the drive empty and the disk come back: {timeline}")
        self.assertLess(timeline["ejected"], timeline["reinserted"])

    def test_the_second_drive_takes_a_disk_without_changing_what_the_first_one_does(self):
        ram, out = self.core(f"{disk.MOUNT_UNIT - 2}:Next Drive;{SWAP}", "--drives", "2", adf="A,B")
        self.assertEqual("events 3", out.strip().splitlines()[-1], "A and B at power-on in DF0 and DF1 (two events) and the Insert in DF1: three")
        two = self.scenario("pal", 1, mount_unit=0)
        self.assertEqual(disk.pra_log(two), disk.pra_log(ram), "DF0 saw the same drive lines as with nothing else done")

    def test_a_disk_that_is_not_an_adf_is_refused_and_the_adfs_given_are_not_written(self):
        short = self.dir / "short.adf"
        short.write_bytes(b"\0" * 1000)
        result = build_waterbox.run_ffi("media", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", str(self.dir / "disk.rom"),
                                        "--adf", str(short), "--units", "1")
        self.assertEqual(1, result.returncode)
        self.assertIn("an ADF has 901120 bytes", result.stderr)
        before = [(self.dir / f"{name}.adf").read_bytes() for name in "AB"]
        self.core(SWAP)
        self.assertEqual(before, [(self.dir / f"{name}.adf").read_bytes() for name in "AB"])
        self.assertEqual(before, [self.adf["A"], self.adf["B"]])


if __name__ == "__main__":
    unittest.main()
