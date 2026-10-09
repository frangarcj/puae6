"""MED02 (T27): a guest writes a floppy; the base stays what it was and the writes are machine state.

Through the real bridge and engine (tests/state/harness.py runs p6_state_probe): the micro-ROM of med02.py writes whole AmigaDOS tracks to a writable disk, overwrites
one, writes another side, and reads them back after moving the head away. What the disk then holds is compared with the independent MFM model; the state taken in
the middle takes the later writes back (same image and a new process), the state saved after the load is the state that was loaded, the base of the disk has its
original hash all along, and a disk that was not written is untouched.
"""
import hashlib
import re
import subprocess
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import med02  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from tests.state import envelope as e  # noqa: E402
from tests.state.harness import ProbeCase, Run, parse_chunks, reforge  # noqa: E402

STATUS = re.compile(r"^status (\d+) (-?\d+) (\d+) capacity=(\d+) used=(\d+) medium=(\d+) refused=(\d+) (?:raw=(\d+) )?base=(\w+) ?(.*)$", re.M)
EXPORT = re.compile(r"^export (\d+) (-?\d+) (\d+) ([0-9a-f]{64}) (\d+) ?(.*)$", re.M)
POOL = 3520
SLOT_BYTES = 1 + 2 + 512
OVERLAY_FIXED = 4 + 8                                   # count u32, refused u64
RAW_SLOTS, RAW_WORDS = 16, 6392
RAW_BOUND = 4 + RAW_SLOTS * (1 + 1 + 4 + 2 * RAW_WORDS)    # count, then per track medium, track, bits and the words: the longest track in every slot


def sha(data):
    return hashlib.sha256(data).hexdigest()


def overlay_of(state):
    """(refused, [(medium, sector, bytes)], offset of the overlay in the P6DK body, body) of a saved state: eight bytes of tag and length start each sub-body of the chunk,
    the overlay follows the last of them and the raw tracks follow the sectors (raw_of)."""
    body = [c for c in parse_chunks(state) if c[0] == b"P6DK"][0][2]
    at = 0
    for _ in range(10):                                 # DSK0..3, DSD0..3, DISK, DSKX
        length = struct.unpack_from("<I", body, at + 4)[0]
        at += 8 + length
    count, refused = struct.unpack_from("<IQ", body, at)
    slots = []
    for i in range(count):
        o = at + OVERLAY_FIXED + i * SLOT_BYTES
        medium, sector = struct.unpack_from("<BH", body, o)
        slots.append((medium, sector, bytes(body[o + 3:o + 3 + 512])))
    return refused, slots, at, body


def raw_of(state):
    """[(medium, track, bits, bytes of the words)] of the raw tracks of a saved state, and the offset in the P6DK body where they start."""
    refused, slots, at, body = overlay_of(state)
    start = at + OVERLAY_FIXED + len(slots) * SLOT_BYTES
    count = struct.unpack_from("<I", body, start)[0]
    o = start + 4
    tracks = []
    for _ in range(count):
        medium, track, bits = struct.unpack_from("<BBI", body, o)
        n = (bits + 15) // 16 * 2
        tracks.append((medium, track, bits, bytes(body[o + 6:o + 6 + n])))
        o += 6 + n
    assert o == len(body), (o, len(body))
    return tracks, start


def raw_bytes(tracks):
    return struct.pack("<I", len(tracks)) + b"".join(struct.pack("<BBI", m, t, bits) + data for m, t, bits, data in tracks)


def with_overlay(state, slots, refused=0, tracks=None):
    """The state with its overlay replaced (and its raw tracks, when given), every checksum made right again."""
    _, _, at, body = overlay_of(state)
    old_tracks, _ = raw_of(state)
    new = bytes(body[:at]) + struct.pack("<IQ", len(slots), refused) + b"".join(struct.pack("<BH", m, s) + d for m, s, d in slots) + raw_bytes(old_tracks if tracks is None else tracks)

    def edit(chunks):
        for chunk in chunks:
            if chunk[0] == b"P6DK":
                chunk[2] = new
    return reforge(state, edit)


class Med02Case(ProbeCase):
    ROMS = {"med02": (med02.rom_source(), None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = disk.make_adf(med02.LABEL_A)
        cls.b = disk.make_adf(med02.LABEL_B)
        (cls.dir / "a.adf").write_bytes(cls.a)
        (cls.dir / "b.adf").write_bytes(cls.b)
        for name in med02.BUF_ADDR:
            (cls.dir / f"{name}.bin").write_bytes(med02.buffer_bytes(name))
        cls.cache = {}

    @classmethod
    def args_for(cls, writable=True, poke=True):
        """The probe arguments of the scenario: disk A (boot, DF0) and disk B, writable or not, and the three track buffers poked into Chip RAM at unit 1."""
        args = ["--writable"] if writable else []
        args += ["--adf", str(cls.dir / "a.adf"), "--adf", str(cls.dir / "b.adf"), "--event-at", "0", "0", "0", "1"]
        if poke:
            for name, address in med02.BUF_ADDR.items():
                args += ["--ram-at", "1", hex(address), str(cls.dir / f"{name}.bin")]
        return args

    @classmethod
    def run_text(cls, *arguments):
        """(the text, the parsed Run) of one probe process."""
        result = subprocess.run([str(cls.exe), str(cls.roms["med02"]), *arguments], capture_output=True, text=True, timeout=1800)
        assert result.returncode == 0, result.stderr[-400:]
        return result.stdout, Run(result.stdout)

    @staticmethod
    def statuses(text):
        return {(int(m[1]), int(m[3])): dict(code=int(m[2]), capacity=int(m[4]), used=int(m[5]), medium=int(m[6]), refused=int(m[7]), raw=int(m[8] or 0), base=m[9]) for m in STATUS.finditer(text)}

    @staticmethod
    def exports(text):
        return {(int(m[1]), int(m[3])): dict(code=int(m[2]), sha=m[4], written=int(m[5])) for m in EXPORT.finditer(text)}


class Med02Tests(Med02Case):
    @classmethod
    def writes(cls):
        """The run of the scenario: the disk is written, saved in the middle of the guest's work, and read at the end."""
        if "writes" not in cls.cache:
            d = cls.dir
            ops = ["--export-at", "1", "0", str(d / "e1.bin"), "--status-at", "1", "0",
                   "--ramout-at", "105", hex(disk.MARK_BASE), "16", str(d / "marks105.bin"),
                   "--export-at", "105", "0", str(d / "e105.bin"), "--status-at", "105", "0", "--save-at", "105", str(d / "s105.state"),
                   "--ramout-at", "300", hex(disk.MARK_BASE), "16", str(d / "marks300.bin"),
                   "--ramout-at", "300", hex(disk.LOG_BASE), "300", str(d / "log300.bin")]
            for name, address in med02.CAPTURE.items():
                ops += ["--ramout-at", "300", hex(address), str(2 * med02.CAPTURE_WORDS), str(d / f"{name}.bin")]
            ops += ["--export-at", "300", "0", str(d / "e300.bin"), "--export-at", "300", "1", str(d / "eB.bin"),
                    "--status-at", "300", "0", "--status-at", "300", "1", "--save-at", "300", str(d / "s300.state"), "--save-at", "255", str(d / "s255.state"),
                    "--public-at", "300"]
            text, run = cls.run_text("--units", str(med02.END_UNIT), "--every", "100", *cls.args_for(), *ops)
            cls.cache["writes"] = (text, run)
        return cls.cache["writes"]

    def test_the_guest_writes_overwrites_and_reads_back_and_the_base_is_untouched(self):
        text, run = self.writes()
        d = self.dir
        status, export = self.statuses(text), self.exports(text)
        # Before the guest wrote anything the disk is the base, and the base is what was registered.
        self.assertEqual(export[1, 0]["sha"], sha(self.a))
        self.assertEqual((status[1, 0]["used"], status[1, 0]["medium"]), (0, 0))
        # In the middle: write 1 is on the disk, nothing else.
        self.assertEqual(med02.marker_words((d / "marks105.bin").read_bytes()), ["w1", "r1"])
        self.assertEqual(export[105, 0]["sha"], sha(med02.expected_image(self.a, ["buf1"])))
        self.assertEqual((status[105, 0]["used"], status[105, 0]["medium"], status[105, 0]["refused"]), (11, 11, 0))
        # At the end: write 2a replaced write 1 in place (the same eleven sectors), 2b added the eleven of the other side, and disk B was never touched.
        self.assertEqual(med02.marker_words((d / "marks300.bin").read_bytes()), list(med02.MARKERS))
        self.assertEqual((d / "e300.bin").read_bytes(), med02.expected_image(self.a, ["buf2", "buf3"]))
        self.assertEqual((status[300, 0]["used"], status[300, 0]["medium"], status[300, 0]["capacity"]), (22, 22, POOL))
        self.assertEqual(export[300, 1]["sha"], sha(self.b))
        self.assertEqual((status[300, 1]["medium"], status[300, 1]["used"]), (0, 22))
        # Whatever the guest wrote, the base the blob store holds is the image that was registered (its content id).
        for key in ((1, 0), (105, 0), (300, 0)):
            self.assertEqual(status[key]["base"], sha(self.a), key)
        self.assertEqual(status[300, 1]["base"], sha(self.b))
        # What the guest read back (after the head left the track, so from the disk and not from the track buffer) decodes to the sectors it wrote.
        for name, wanted in (("c1", "buf1"), ("c2", "buf3"), ("c3", "buf2")):
            good, found = med02.sectors_of((d / f"{name}.bin").read_bytes(), wanted)
            self.assertGreaterEqual(good, 10, f"{name}: {good} of {found} sectors decode to {wanted}")
            self.assertEqual(good, found, f"{name}: a sector that is not the one written")

    def test_the_drive_reports_a_writable_disk(self):
        self.writes()
        log = (self.dir / "log300.bin").read_bytes()
        states = [disk.pra_state(b) for b in log[med02.SPINUP + 2:med02.SPINUP + 20]]
        self.assertTrue(all(not s.write_protected for s in states), "/WPRO is low on a disk that can be written")

    def test_the_state_carries_the_overlay_and_the_bound_does_not_grow(self):
        text, run = self.writes()
        refused, slots, _, _ = overlay_of((self.dir / "s300.state").read_bytes())
        image = (self.dir / "e300.bin").read_bytes()
        self.assertEqual(refused, 0)
        self.assertEqual(sorted((m, s) for m, s, _ in slots), [(0, s) for s in range(22)])
        for medium, sector, data in slots:
            self.assertEqual(data, image[sector * 512:sector * 512 + 512])
        early = overlay_of((self.dir / "s105.state").read_bytes())[1]
        self.assertEqual(sorted((m, s) for m, s, _ in early), [(0, s) for s in range(11)])
        # STATE01: the bound is the same at every unit, covers every state, and includes the whole pool.
        bounds = {bound for _, _, _, bound in run.saves}
        self.assertEqual(len(bounds), 1, run.saves)
        bound = bounds.pop()
        self.assertTrue(all(size <= bound for _, size, _, _ in run.saves))
        read_only = self.probe_on(self.MACHINE, "med02", "--units", "1", "--save-at", "1", str(self.dir / "ro.state"), *self.args_for(writable=False, poke=False))
        self.assertEqual(bound - read_only.saves[0][3], OVERLAY_FIXED + POOL * SLOT_BYTES + RAW_BOUND)
        # The public functions of p6.h agree: the bound, and a save that fits it.
        public = run.public[0]
        self.assertEqual((public[1], int(public[2]), int(public[3]), int(public[4])), ("1", 0, 0, 0))
        self.assertEqual(run.public_size[0][1], bound)

    def test_rewinding_to_the_state_takes_the_writes_back_and_the_future_repeats(self):
        d = self.dir
        self.writes()
        ops = ["--export-at", "300", "0", str(d / "pre.bin"), "--load-at", "300", str(d / "s105.state"), "--export-at", "300", "0", str(d / "post.bin"),
               "--status-at", "300", "0", "--save-at", "300", str(d / "resave.state"), "--save-at", "450", str(d / "s450.state"), "--export-at", "450", "0", str(d / "e450.bin")]
        text, run = self.run_text("--units", "450", "--every", "150", *self.args_for(), *ops)
        self.assertEqual([(300, 0, "")], [(u, c, m) for u, c, m in run.loads])
        status = self.statuses(text)
        self.assertEqual((d / "pre.bin").read_bytes(), med02.expected_image(self.a, ["buf2", "buf3"]))
        self.assertEqual((d / "post.bin").read_bytes(), med02.expected_image(self.a, ["buf1"]), "the overlay went back to the state's")
        self.assertEqual(status[300, 0]["used"], 11)
        self.assertEqual(status[300, 0]["base"], sha(self.a))
        # The state saved right after the load is the state that was loaded (the overlay, the drive and everything else round trip).
        self.assertEqual((d / "resave.state").read_bytes(), (d / "s105.state").read_bytes())
        # 150 units after the load the machine is the original 150 units after the state: bit for bit, the overlay included.
        self.assertEqual((d / "s450.state").read_bytes(), (d / "s255.state").read_bytes())
        self.assertEqual((d / "e450.bin").read_bytes(), (d / "e300.bin").read_bytes())

    def test_a_new_process_loads_the_state_and_goes_on_as_the_original_did(self):
        d = self.dir
        self.writes()
        ops = ["--load-at", "0", str(d / "s105.state"), "--export-at", "0", "0", str(d / "n0.bin"), "--status-at", "0", "0", "--save-at", "150", str(d / "n150.state"),
               "--export-at", "150", "0", str(d / "n150.bin")]
        text, run = self.run_text("--units", "150", "--every", "150", *self.args_for(poke=False), *ops)
        self.assertEqual([(0, 0, "")], run.loads[:1])
        self.assertEqual((d / "n0.bin").read_bytes(), med02.expected_image(self.a, ["buf1"]))
        self.assertEqual((d / "n150.state").read_bytes(), (d / "s255.state").read_bytes())
        self.assertEqual((d / "n150.bin").read_bytes(), (d / "e300.bin").read_bytes())
        self.assertEqual(self.statuses(text)[0, 0]["base"], sha(self.a))

    def test_the_state_of_a_session_without_writes_does_not_load_in_one_with_them_and_back(self):
        d = self.dir
        self.writes()
        self.probe_on(self.MACHINE, "med02", "--units", "5", "--save-at", "5", str(d / "ro5.state"), *self.args_for(writable=False, poke=False))
        text, run = self.run_text("--units", "6", "--every", "6", *self.args_for(poke=False), "--load-at", "5", str(d / "ro5.state"))
        self.assertEqual(run.loads[0][:2], (5, 10), run.loads)
        self.assertIn("config", run.loads[0][2])
        text, run = self.run_text("--units", "106", "--every", "106", *self.args_for(writable=False, poke=False), "--load-at", "106", str(d / "s105.state"))
        self.assertEqual(run.loads[0][:2], (106, 10), run.loads)

    def test_without_the_flag_the_disk_is_protected_as_before(self):
        d = self.dir
        ops = ["--export-at", "300", "0", str(d / "ro-export.bin"), "--status-at", "300", "0", "--ramout-at", "300", hex(disk.LOG_BASE), "300", str(d / "ro-log.bin"),
               "--ramout-at", "300", hex(med02.CAPTURE["c1"]), str(2 * med02.CAPTURE_WORDS), str(d / "ro-c1.bin")]
        text, run = self.run_text("--units", "300", "--every", "300", *self.args_for(writable=False), *ops)
        self.assertEqual(self.exports(text)[300, 0]["code"], 4)           # UNSUPPORTED: no overlay was reserved
        self.assertEqual(self.statuses(text)[300, 0]["code"], 4)
        log = (d / "ro-log.bin").read_bytes()
        self.assertTrue(all(disk.pra_state(b).write_protected for b in log[med02.SPINUP + 2:med02.SPINUP + 20]))
        # The write attempts changed nothing: the read after them still shows disk A.
        good, found = med02.sectors_of((d / "ro-c1.bin").read_bytes(), "buf1")
        self.assertEqual(good, 0)
        words = struct.unpack(">%dH" % med02.CAPTURE_WORDS, (d / "ro-c1.bin").read_bytes())
        sectors = disk.decode_capture(words)
        self.assertGreaterEqual(len(sectors), 10)
        for (track, sector), payload in sectors.items():
            self.assertEqual(payload, disk.adf_sector(self.a, track, sector))


class Med02PoolTests(Med02Case):
    """A pool with little room: the drive says the disk is protected before a write could be lost, and the guest's write does not happen."""

    def crafted(self, free):
        """The state of unit 105 (eleven sectors written) with the rest of the pool filled so that `free` slots remain: disk B whole, then the sectors of disk A from
        the end back, each with the base's own bytes (a slot may hold what the base holds)."""
        state = self.state_of("s105.state", "med02", 105, *self.args_for())
        _, slots, _, _ = overlay_of(state)
        have = {(m, s) for m, s, _ in slots}
        more = [(1, s, self.b[s * 512:s * 512 + 512]) for s in range(1760)]
        s = 1759
        while len(slots) + len(more) < POOL - free:
            if (0, s) not in have and not (free >= 11 and 11 <= s <= 21):
                more.append((0, s, self.a[s * 512:s * 512 + 512]))
            s -= 1
        return with_overlay(state, slots + more)

    def finish(self, free):
        d = self.dir
        (d / f"free{free}.state").write_bytes(self.crafted(free))
        ops = ["--load-at", "0", str(d / f"free{free}.state"), "--status-at", "0", "0",
               "--export-at", "200", "0", str(d / f"free{free}.bin"), "--status-at", "200", "0", "--ramout-at", "200", hex(disk.LOG_BASE), "300", str(d / f"free{free}.log"),
               "--ramout-at", "200", hex(disk.MARK_BASE), "16", str(d / f"free{free}.marks")]
        text, run = self.run_text("--units", "200", "--every", "200", *self.args_for(poke=False), *ops)
        self.assertEqual([(0, 0, "")], run.loads[:1], run.loads)
        return text, run

    def test_with_room_for_a_track_the_write_lands_and_the_drive_then_reports_protection(self):
        text, run = self.finish(12)
        status = {int(m[1]): m for m in STATUS.finditer(text)}
        self.assertEqual((int(status[0][5]), int(status[0][4])), (POOL - 12, POOL))
        # The unit-105 state is in the middle of the hold: write 2a (in place) and 2b (the eleven sectors of the other side, 11 of the 12 free) both happen.
        self.assertEqual(int(status[200][5]), POOL - 1)
        image = (self.dir / "free12.bin").read_bytes()
        self.assertEqual(image[:22 * 512], med02.expected_image(self.a, ["buf2", "buf3"])[:22 * 512])
        log = (self.dir / "free12.log").read_bytes().rstrip(b"\xff")       # what the guest logged, one PRA byte per wait; Chip RAM beyond it is the power-on pattern
        flags = [disk.pra_state(b).write_protected for b in log]
        self.assertFalse(flags[med02.SPINUP + 2], "writable while a whole track fits")
        self.assertTrue(flags[-1], "protected when fewer than a track's sectors are free: after write 2b one sector is left")

    def test_without_room_the_drive_is_protected_and_the_writes_do_not_happen(self):
        text, run = self.finish(10)
        status = {int(m[1]): m for m in STATUS.finditer(text)}
        self.assertEqual((int(status[200][5]), int(status[200][7])), (POOL - 10, 0))            # nothing was added, nothing was refused: the drive refused the write
        image = (self.dir / "free10.bin").read_bytes()
        self.assertEqual(image[:22 * 512], med02.expected_image(self.a, ["buf1"])[:22 * 512])        # write 1 is the state's, writes 2a and 2b did not happen
        log = (self.dir / "free10.log").read_bytes().rstrip(b"\xff")
        self.assertFalse(disk.pra_state(log[med02.SPINUP + 2]).write_protected, "the first part of the log is the original history of the state, taken while it had room")
        self.assertTrue(all(disk.pra_state(b).write_protected for b in log[-20:]), "from the load on the drive reports the disk protected")


class Med02HostileStateTests(Med02Case):
    """A state whose overlay no machine could have written is refused before anything is touched."""

    def test_overlays_that_cannot_be_are_refused_with_the_machine_untouched(self):
        state = self.state_of("good.state", "med02", 105, *self.args_for())
        _, slots, at, body = overlay_of(state)
        data = bytes(512)
        attempts = [
            ("a sector named twice", with_overlay(state, slots + [slots[0]]), 9, "named twice"),
            ("a medium beyond the manifest", with_overlay(state, slots + [(2, 0, data)]), 9, "manifest"),
            ("a medium beyond sixteen", with_overlay(state, slots + [(16, 0, data)]), 9, "range"),
            ("a sector beyond the disk", with_overlay(state, slots + [(0, 1760, data)]), 9, "range"),
        ]
        def too_many(chunks):
            for chunk in chunks:
                if chunk[0] == b"P6DK":
                    chunk[2] = bytes(chunk[2][:at]) + struct.pack("<IQ", POOL + 1, 0) + bytes(chunk[2][at + 12:])
        attempts.append(("more slots than the pool", reforge(state, too_many), 9, "bound"))
        def cut(chunks):
            for chunk in chunks:
                if chunk[0] == b"P6DK":
                    chunk[2] = bytes(chunk[2][:-100])
        attempts.append(("an overlay cut short", reforge(state, cut), 9, "body"))
        def padded(chunks):
            for chunk in chunks:
                if chunk[0] == b"P6DK":
                    chunk[2] = bytes(chunk[2]) + b"\0"
        attempts.append(("bytes after the overlay", reforge(state, padded), 9, "after"))
        self.refusals(attempts, rom="med02", extra=self.args_for(), unit=105, units=108)


if __name__ == "__main__":
    unittest.main()
