"""MED01: what a guest sees of read-only disks, through the real bridge and engine.

A micro-ROM (tests/scenarios/disk/disk.py) reads track 0 of DF0 by disk DMA, watches the drive status lines of CIA-A while the
host mounts another disk, reads again, attempts a write and reads back. The expected words come from an MFM model written
from the AmigaDOS format alone, not from engine output. Disks and ROM are generated here; nothing binary is committed.
Set P6_TEST_SANITIZERS=1 to also run one PAL scenario under ASan/UBSan.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT, TOOLS, build_probe, builder
from tests.scenarios.disk import disk

STANDARDS = ("pal", "ntsc")
LABEL_A, LABEL_B = 0x41, 0x42


def run_scenario(exe, directory, standard, seed, adfs=None):
    directory = Path(directory)
    rom = directory / "disk.rom"
    rom.write_bytes(builder.build(disk.rom_source()))
    paths = []
    for name, label in (("A", LABEL_A), ("B", LABEL_B)):
        path = directory / f"{name}.adf"
        path.write_bytes(disk.make_adf(label))
        paths.append(str(path))
    dump = directory / f"chip-{standard}-{seed}.bin"
    result = subprocess.run([str(exe), str(rom), standard, str(seed), str(disk.RUN_UNITS), str(disk.MOUNT_UNIT), *paths, str(dump)],
                            capture_output=True, text=True, timeout=600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    ram = dump.read_bytes() if dump.exists() else b""
    return result, ram


def verified(words):
    """{(track, sector): payload} of the sectors whose header and data checksums hold."""
    return disk.decode_capture(words)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class Med01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_probe(ROOT / "build/link-audit", False, "p6_med01_probe")
        cls.adf = {"A": disk.make_adf(LABEL_A), "B": disk.make_adf(LABEL_B)}
        cls.runs = {}
        cls.temp = tempfile.TemporaryDirectory(prefix="p6-med01-")
        for standard in STANDARDS:
            for seed in (1, 2):
                result, ram = run_scenario(cls.exe, cls.temp.name, standard, seed)
                cls.runs[standard, seed] = (result, ram)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def scenarios(self):
        for (standard, seed), (result, ram) in self.runs.items():
            with self.subTest(standard=standard, seed=seed):
                self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
                yield standard, seed, result, ram

    def assert_shows(self, words, wanted, other, what):
        """At least 10 of the 11 sectors read verify and equal disk `wanted` (a read starts at a random sector, and the
        engine's jitter can cost the first sync); none belongs to `other`; every body word equals the model's."""
        found = [f for f in disk.find_sectors(words) if f.header_ok and f.data_ok]
        sectors = verified(words)
        self.assertGreaterEqual(len(sectors), 10, what)
        for (track, sector), payload in sectors.items():
            self.assertEqual(track, 0, what)
            self.assertEqual(payload, disk.adf_sector(self.adf[wanted], track, sector), f"{what}: sector {sector}")
            self.assertNotEqual(payload, disk.adf_sector(self.adf[other], track, sector), what)
        for f in found:
            model = disk.mfm_words_for_sector(self.adf[wanted], f.track, f.sector)[4:]
            self.assertEqual(disk.body_words(words, f), model, f"{what}: MFM words of sector {f.sector}")

    def test_the_guest_reads_the_boot_disk_then_the_swapped_disk_and_a_write_changes_nothing(self):
        for standard, seed, result, ram in self.scenarios():
            self.assertTrue(all(disk.markers(ram).values()), disk.markers(ram))
            self.assert_shows(disk.capture_words(ram, "boot"), "A", "B", "boot read")
            self.assert_shows(disk.capture_words(ram, "swapped"), "B", "A", "read after the swap")
            self.assert_shows(disk.capture_words(ram, "readback"), "B", "A", "read after the write attempt")
            pattern = disk.WRITE_PATTERN * (2 * disk.WRITE_WORDS // len(disk.WRITE_PATTERN))
            self.assertEqual(ram[disk.WRITE_BUFFER:disk.WRITE_BUFFER + 2 * disk.WRITE_WORDS], pattern)   # the DMA only read it

    def test_the_drive_lines_show_a_disk_change_and_a_write_protected_disk(self):
        for standard, seed, result, ram in self.scenarios():
            log = disk.pra_log(ram)
            states = [disk.pra_state(b) for b in log]
            timeline = disk.swap_timeline(log)
            self.assertNotIn(None, timeline.values(), timeline)
            self.assertLess(timeline["ready"], timeline["ejected"] < timeline["reinserted"] and timeline["reinserted"])
            gap = timeline["reinserted"] - timeline["ejected"]
            low, high = disk.swap_gap_bounds(standard)
            self.assertTrue(low <= gap <= high, (standard, gap, low, high))
            self.assertTrue(states[timeline["ejected"]].changed and not states[timeline["ejected"]].ready)
            self.assertFalse(states[timeline["acknowledged"]].changed)             # released by the step pulse, not by the swap
            ready = [s for s in states[timeline["ready"]:] if s.ready]
            self.assertTrue(all(s.write_protected for s in states[timeline["ready"]:]))     # /WPRO, also with the drive empty
            self.assertTrue(all(s.track0 for s in states))                          # the head never left cylinder 0
            self.assertTrue(ready)

    def test_media_are_never_written_and_two_processes_replay_the_swap_exactly(self):
        for standard, seed, result, ram in self.scenarios():
            self.assertIn("adf A buffer_unchanged=1 blob_equal=1", result.stdout)
            self.assertIn("adf B buffer_unchanged=1 blob_equal=1", result.stdout)
        with tempfile.TemporaryDirectory(prefix="p6-med01-replay-") as directory:
            again, ram_again = run_scenario(self.exe, directory, "pal", 1)
        first, ram = self.runs["pal", 1]
        self.assertEqual(again.stdout, first.stdout)                                # every unit, register and digest
        self.assertEqual(ram_again, ram)
        final = lambda text: next(line for line in text.splitlines() if line.startswith("final digest="))
        self.assertNotEqual(final(self.runs["pal", 2][0].stdout), final(first.stdout))   # the digest is sensitive to the seed

    @unittest.skipUnless(os.environ.get("P6_TEST_SANITIZERS") == "1", "opt-in: full engine build with sanitizers")
    def test_same_scenario_is_clean_under_asan_and_ubsan(self):
        exe = build_probe(ROOT / "build/native-debug", True, "p6_med01_probe")
        with tempfile.TemporaryDirectory(prefix="p6-med01-san-") as directory:
            result, ram = run_scenario(exe, directory, "pal", 1)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr)
        self.assertNotIn("runtime error", result.stderr)
        self.assertTrue(all(disk.markers(ram).values()))


if __name__ == "__main__":
    unittest.main()
