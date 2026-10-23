"""T61: four real drive reads, simultaneous boot media and native restores."""
from pathlib import Path
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from tests.media import trackdisk as td
from tests.scenarios.disk import disk
from tests.state.envelope import config_hash, domain_hash
from tests.state.harness import Run
from tests.state.test_det02 import Det02Case
from tests.state import test_state_upstream as upstream

UNITS, SAVE = 220, 100
BASE, STEP = 0x10000, 0x4000


def rom_source():
    # Four drives spin independently; only one is selected on CIA-B at a time.
    a = ["entry:", "    MOVE.W #$2700,SR", "    LEA $400.W,A7", "    MOVE.B #3,$BFE201.L", "    MOVE.B #2,$BFE001.L",
         "    MOVE.B #$FF,$BFD300.L", "    MOVE.B #$FF,$BFD100.L", "    MOVE.L #hdsk,$64.L", "    MOVE.L #hvert,$6C.L",
         "    MOVE.W #$7FFF,$DFF09A.L", "    MOVE.W #$7FFF,$DFF09C.L", "    MOVE.W #$8210,$DFF096.L",
         "    MOVE.W #$C020,$DFF09A.L", "    MOVE.W #$2000,SR"]
    for drive in range(4):
        a += ["    MOVE.B #$7F,$BFD100.L", f"    MOVE.B #${0x7f & ~(8 << drive):02X},$BFD100.L"]
        a += ["    STOP #$2000"] * td.SPINUP_UNITS
        read = []
        td._read(read, "raw")
        a += [line.replace(f"#${td.CAPTURE_BASE['raw']:X},", f"#${BASE + drive * STEP:X},") for line in read]
        a += [f"    MOVE.W #$CAFE,${0x6000 + drive * 2:X}.L"]
    a += ["done:", "    STOP #$2000", "    BRA.S done", "hvert:", "    MOVE.W #$0020,$DFF09C.L", "    RTE", "hdsk:", "    MOVE.W #$0002,$DFF09C.L", "    RTE"]
    return "\n".join(a) + "\n"


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MultiDriveTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adfs = [disk.make_adf(0x41 + n) for n in range(4)]
        rom = Path(cls.tmp.name) / "four.rom"
        rom.write_bytes(lr.builder.build(rom_source()))
        cls.roms["four"] = rom

    def content(self, name="game (MD).m3u"):
        directory = self.work()
        for n, image in enumerate(self.adfs):
            (directory / f"{n}.adf").write_bytes(image)
        path = directory / name
        path.write_text("".join(f"{n}.adf\n" for n in range(4)))
        return path

    def assert_reads(self, ram):
        self.assertEqual(ram[0x6000:0x6008], b"\xca\xfe" * 4)
        for n, image in enumerate(self.adfs):
            words = struct.unpack_from(f">{td.CAPTURE_WORDS}H", ram, BASE + n * STEP)
            found = disk.decode_capture(words)
            self.assertGreaterEqual(len(found), 10, f"DF{n} did not read a track")
            for (track, sector), payload in found.items():
                self.assertEqual(track, 0)
                self.assertEqual(payload, disk.adf_sector(image, track, sector), f"DF{n}")

    def test_multidrive_reads_all_four_disks_and_restore_replays_the_future(self):
        path = self.content()
        first = self.frontend("four", "--runs", UNITS + UNITS - SAVE, "--do", f"{SAVE}:save:s", "--do", "220:save:late",
                              "--do", "220:load:s", content=path).stdout
        for drive in range(1, 4):
            self.assertIn(f"DF{drive}: medium {drive} inserted (MultiDrive)", first)
        self.assertIn("state load s exact ok=1", first)
        frames = self.runs(first)
        keys = ("hash", "ahash", "ticks", "unit", "audio")
        self.assertEqual([tuple(r[k] for k in keys) for r in frames[SAVE:UNITS]],
                         [tuple(r[k] for k in keys) for r in frames[UNITS:]])
        # The native probe can inspect RAM and compare every subsystem/PCM/picture
        # across a new process whose DF2/DF3 initially hold different media.
        root = self.work()
        state, ram = root / "four.state", root / "ram.bin"
        media = sum((["--adf", str(path.parent / f"{n}.adf")] for n in range(4)), [])
        boot = sum((["--event-at", "0", str(n), str(n), str(n + 1)] for n in range(4)), [])
        common = [str(self.probe), str(self.roms["four"]), "--floppy-drives", "4", "--every", "1", *media]
        original = subprocess.run([*common, *boot, "--units", str(UNITS), "--public-save-at", str(SAVE), str(state),
                                   "--ramout-at", str(UNITS), "0", str(0x30000), str(ram)], capture_output=True, text=True, timeout=600)
        self.assertEqual(original.returncode, 0, original.stderr[-1500:])
        self.assert_reads(ram.read_bytes())
        other_boot = sum((["--event-at", "0", str(n), str((n + 1) % 4), str(n + 1)] for n in range(4)), [])
        again = subprocess.run([*common, *other_boot, "--units", str(UNITS - SAVE), "--public-load-at", "0", str(state)],
                               capture_output=True, text=True, timeout=600)
        self.assertEqual(again.returncode, 0, again.stderr[-1500:])
        self.assertRegex(again.stdout, r"load 0 0 ")
        baseline, restored = Run(original.stdout), Run(again.stdout)
        # Use the existing DET02 contract, including every causal chipset group,
        # PCM and picture. custom_storage is only a debugger's last-write log
        # (custom.cpp writes it; debug.cpp reads it), not readable hardware state.
        for unit in range(1, UNITS - SAVE + 1):
            self.assertEqual(Det02Case.differing(self, baseline, restored, SAVE, 0, unit), [], f"restore +{unit}")
        # Rebinding foreign media must preserve BEAM's exact pending request,
        # including a genuine countdown and all flag bits, not just zero it.
        source = state.read_bytes()
        for countdown, flags in ((0, 0), (1, 0), (2, 0), (2, 0x80000004)):
            with self.subTest(countdown=countdown, flags=flags):
                def edit(subs):
                    for sub in subs:
                        if sub[0] == "BEAM":
                            data = bytearray(sub[1])
                            struct.pack_into(">II", data, 4 * upstream.BEAM_WORDS.index("config_changed"), countdown, flags)
                            sub[1] = bytes(data)
                candidate, saved = root / "pending.state", root / "pending.again"
                candidate.write_bytes(upstream.with_subs(source, b"P6CH", edit))
                loaded = subprocess.run([*common, *other_boot, "--units", "0", "--public-load-at", "0", str(candidate),
                                         "--public-save-at", "0", str(saved)], capture_output=True, text=True, timeout=600)
                self.assertEqual(loaded.returncode, 0, loaded.stderr[-1500:])
                self.assertRegex(loaded.stdout, r"load 0 0 ")
                self.assertEqual(upstream.upstream_bodies(candidate.read_bytes())[b"P6CH"]["BEAM"],
                                 upstream.upstream_bodies(saved.read_bytes())[b"P6CH"]["BEAM"])
        for n, image in enumerate(self.adfs):
            self.assertEqual((path.parent / f"{n}.adf").read_bytes(), image)

    def test_multidrive_can_be_enabled_explicitly_and_a_drive_limit_is_respected(self):
        path = self.content("plain.m3u")
        text = self.frontend("boot", "--runs", 2, "--set", "puae6_floppy_multidrive=enabled", "--set", "puae6_floppy_drives=3", content=path).stdout
        self.assertIn("DF1: medium 1 inserted (MultiDrive)", text)
        self.assertIn("DF2: medium 2 inserted (MultiDrive)", text)
        self.assertNotIn("DF3:", text)

    def test_plain_playlists_keep_one_boot_disk_and_savedisks_are_not_extra_boot_media(self):
        path = self.content("plain.m3u")
        plain = self.frontend("boot", "--runs", 2, content=path).stdout
        self.assertNotIn("MultiDrive)", plain)
        path.write_text("0.adf\n#SAVEDISK:Scores\n1.adf\n2.adf\n3.adf\n")
        text = self.frontend("boot", "--runs", 2, "--set", "puae6_floppy_multidrive=enabled", content=path).stdout
        self.assertIn("DF1: medium 2 inserted (MultiDrive)", text)
        self.assertIn("DF3: medium 4 inserted (MultiDrive)", text)

    def test_two_drive_spellings_keep_the_profile_identity_and_four_derives_from_it(self):
        root, hashes, states = self.work(), {}, {}
        for count in (0, 2, 4):
            path = root / f"count{count}.state"
            result = subprocess.run([str(self.probe), str(self.roms["boot"]), "--units", "3", "--floppy-drives", str(count),
                                     "--public-save-at", "3", str(path)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            states[count] = path.read_bytes()
            hashes[count] = states[count][176:208]
        profile = config_hash(1, 1, 48000, 0, 1, 946684800)
        self.assertEqual(hashes[0], profile)
        self.assertEqual(states[0], states[2])
        self.assertEqual(hashes[4], domain_hash("p6/config/floppy-drives/v1", profile, bytes([4])))


if __name__ == "__main__":
    unittest.main()
