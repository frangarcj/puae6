"""MED02 (T27) with the floppy drive at every speed (T62): the guest of med02.py writes AmigaDOS tracks to a writable disk and reads them back; what the disk holds at the end must not depend
on how fast the drive hands the bits over.

At 200 %, 400 % and 800 % the controller moves the words faster and flushes its FIFO at once; at turbo DSKLEN_2 does the whole transfer, a read or a write, in one go (the write goes through
drive_write_data into the overlay of T27 as it does at the real speed). So at every speed: every marker of the program is reached, the disk exported at the end is the independent MFM model's
image of the two writes (tests/media-cow/med02.py), the base of the disk is the registered image, a disk that was not written is untouched, and the state taken in the middle of the
guest's work takes the later writes back (same image) and is the machine it was saved from (new process).
Run: python3 -m unittest discover -s tests/media-cow -p "test_med02_speed.py"
"""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med02  # noqa: E402
from test_med02 import Med02Case, overlay_of, sha  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402

SPEEDS = {"200": 2, "400": 3, "800": 4, "turbo": 5}


def source_with_dma_off_before_each_write():
    """The program of med02.py with DSKLEN = $4000 before each pair of writes that starts a transfer, as trackdisk does. The controller starts a transfer at the second write with the
    DMA bit set and compares a write with the previous one even after the last transfer ended (dsklen2 survives disk_dmafinished), so a pair that follows a finished transfer starts
    it at the FIRST write; at the real speed that costs a few cycles, but at turbo the first write moves the whole transfer at once and, for a write, reads the track into the very buffer
    the program is about to write. That is upstream's turbo mode, not this port's; the guest of MED02 is a trackdisk in miniature and does what trackdisk does here."""
    out = []
    for line in med02.rom_source().split("\n"):
        if "; DSKLEN: DMAEN" in line:
            out.append("    MOVE.W #$4000,$DFF024.L      ; DSKLEN: DMA off before the pair (T62)")
        out.append(line)
    return "\n".join(out)


class Med02SpeedTests(Med02Case):
    ROMS = {"med02": (source_with_dma_off_before_each_write(), None, None)}

    @classmethod
    def at_speed(cls, speed):
        if speed not in cls.cache:
            d = cls.dir
            number = str(SPEEDS[speed])
            ops = ["--floppy-speed", number,
                   "--export-at", "300", "0", str(d / f"e300-{speed}.bin"), "--export-at", "300", "1", str(d / f"eB-{speed}.bin"),
                   "--status-at", "300", "0", "--status-at", "300", "1", "--ramout-at", "300", hex(disk.MARK_BASE), "16", str(d / f"marks300-{speed}.bin"),
                   "--save-at", "105", str(d / f"s105-{speed}.state"), "--save-at", "300", str(d / f"s300-{speed}.state")]
            for name, address in med02.CAPTURE.items():
                ops += ["--ramout-at", "300", hex(address), str(2 * med02.CAPTURE_WORDS), str(d / f"{name}-{speed}.bin")]
            cls.cache[speed] = cls.run_text("--units", str(med02.END_UNIT), "--every", "100", *cls.args_for(), *ops)
        return cls.cache[speed]

    def test_the_guest_writes_and_reads_back_the_same_disk_at_every_speed(self):
        for speed in SPEEDS:
            with self.subTest(speed=speed):
                text, _ = self.at_speed(speed)
                d = self.dir
                status, export = self.statuses(text), self.exports(text)
                self.assertEqual(med02.marker_words((d / f"marks300-{speed}.bin").read_bytes()), list(med02.MARKERS), "the program did not reach its end")
                self.assertEqual((d / f"e300-{speed}.bin").read_bytes(), med02.expected_image(self.a, ["buf2", "buf3"]))
                self.assertEqual((status[300, 0]["used"], status[300, 0]["medium"], status[300, 0]["refused"]), (22, 22, 0))
                self.assertEqual(export[300, 1]["sha"], sha(self.b))
                self.assertEqual(status[300, 0]["base"], sha(self.a))
                for name, wanted in (("c1", "buf1"), ("c2", "buf3"), ("c3", "buf2")):
                    good, found = med02.sectors_of((d / f"{name}-{speed}.bin").read_bytes(), wanted)
                    self.assertGreaterEqual(good, 10, f"{speed} {name}: {good} of {found} sectors decode to {wanted}")
                    self.assertEqual(good, found)

    def test_the_state_in_the_middle_takes_the_later_writes_back_at_every_speed(self):
        for speed in SPEEDS:
            with self.subTest(speed=speed):
                self.at_speed(speed)
                d = self.dir
                refused, slots, _, _ = overlay_of((d / f"s105-{speed}.state").read_bytes())
                self.assertEqual((refused, sorted((m, s) for m, s, _ in slots)), (0, [(0, s) for s in range(11)]), "only write 1 is in the overlay of the state of unit 105")
                late, slots300, _, _ = overlay_of((d / f"s300-{speed}.state").read_bytes())
                self.assertEqual((late, sorted((m, s) for m, s, _ in slots300)), (0, [(0, s) for s in range(22)]))
                # loaded into the same image 20 units on, and into a new process: the disk is the one write 1 left, and the guest goes on to write 2a, 2b and read back
                same = self.probe("med02", "--units", "130", "--every", "1", *self.args_for(), "--floppy-speed", str(SPEEDS[speed]), "--load-at", "125", str(d / f"s105-{speed}.state"),
                                  "--export-at", "125", "0", str(d / f"loaded-{speed}.bin"))
                self.assertEqual(same.loads[0][:2], (125, 0), same.loads)
                self.assertEqual((d / f"loaded-{speed}.bin").read_bytes(), med02.expected_image(self.a, ["buf1"]))


if __name__ == "__main__":
    unittest.main()
