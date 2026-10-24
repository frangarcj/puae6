"""T62: DET02 with the floppy drive at every speed (p6_config.floppy_speed: 100 %, 200 %, 400 %, 800 % and turbo), and what the speed does to the machine.

The speed is a restart option: it is upstream's floppy_speed, which decides how many colour clocks a bit of the track takes (200 %, 400 %, 800 %: the controller reads the track faster and
flushes its FIFO at once) or, at 0 (turbo), lets DSKLEN_2 move the whole transfer at once and waits two lines (linecounter) before the DSKBLK interrupt. Nothing of it is read from the
host: the DMA counts emulated colour clocks. What is decided here:
  * the speed accelerates the disk (units until a read of $1900 words finishes, and reads finished in 60 units, by speed) and the same speed gives the same machine every time;
  * DET02 holds with the drive at each speed: a state taken before, in the middle of and after a transfer is loaded into the same image, a restarted one and a new process, and the units
    that follow are the original's, subsystem by subsystem (the scenarios of tests/state/test_det02.py that read and seek, at the speeds);
  * a state taken in the two lines that a turbo transfer waits (linecounter) is the case the speed adds to the native state (patch 0280): it is found at a boundary on purpose, DET02 holds
    for it, a copy with the counter zeroed does not (the DSKBLK interrupt never comes), and a counter beyond two lines or a DMA that is on while it counts are refused;
  * the speed is part of the machine identity (config_hash, bits 25 to 27 of the flags word): at 100 % it is the hash it always was, a state of one speed is refused at another.
"""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import unittest

from tests.state import envelope as e
from tests.state import test_det02 as d
from tests.state import test_state_upstream as upstream
from tests.state.harness import ProbeCase, parse_chunks, reforge

# p6_config.floppy_speed as the ABI number: 1 + a step (the schema's numbers; 0 and 1 are the real drive).
SPEEDS = {"200": 2, "400": 3, "800": 4, "turbo": 5}
DISK, DISK_CHUNK = 8, b"P6DK"
RAM = 2

# A note on DSKLEN. The controller starts a transfer at the second of two writes with the DMA bit set, and compares a write with the previous one even after the transfer it started has
# ended (dsklen2 is not cleared by disk_dmafinished). A program that starts a transfer with two writes and no DMA-off write before them therefore starts it at the FIRST one. At the real
# speed that costs a few cycles and nothing else; at turbo the first write already moves the whole transfer, so for a WRITE it first reads the track into the very buffer the program is
# about to write (tests/media-cow/test_med02_speed.py). That is upstream's turbo mode and not this port's; the programs here write DSKLEN = $4000 first, as trackdisk does.

# A read that goes on: $1900 words from the first unit with the disk, and from each DSKBLK interrupt another of $1900. The program logs the vertical blanks it has counted (D1) when the
# first transfer starts ($608) and at the end of each read (a list of longs from $700; D2 is how many, at $604), so that the memory of the state says when the disk finished. DMACON
# enables the disk before DSKLEN, as trackdisk does. (The assembler of the micro-ROMs knows MOVE Dn,(An)+ and MOVE #imm,abs only, hence the address registers.)
DISK_COUNT = """; PUAE6 test program (T62): disk reads of $1900 words one after the other; the memory says how many finished and in which unit.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$FF,$BFD300.L       ; CIAB DDRB: all outputs
    MOVE.B #$FF,$BFD100.L       ; deselect every drive
    MOVE.B #$7F,$BFD100.L       ; motor on
    MOVE.B #$77,$BFD100.L       ; select DF0: the motor latches on
    MOVE.L #dskblk,$64.L
    MOVE.L #vblank,$6C.L
    MOVE.W #$C022,$DFF09A.L     ; INTENA: vertical blank and disk block
    MOVE.W #$2200,$DFF100.L
    MOVE.W #$2C81,$DFF08E.L
    MOVE.W #$2CC1,$DFF090.L
    MOVEA.L #$700,A2            ; the log of the reads that finish
    MOVE.W #$2000,SR
    .nops 40000                 ; the medium is in the drive by now
    MOVEA.L #$608,A3
    MOVE.L D1,(A3)+             ; the vertical blanks counted when the first transfer starts
    MOVE.W #$7F00,$DFF09E.L     ; ADKCON: clear WORDSYNC and the rest
    MOVE.W #$9100,$DFF09E.L     ; ADKCON: SET | MFMPREC | FAST, as trackdisk
    MOVE.W #$8210,$DFF096.L     ; DMACON: SET, master and disk
    MOVE.L #$8000,$DFF020.L     ; DSKPT
    MOVE.W #$4000,$DFF024.L     ; DSKLEN: DMA off, so that the two writes below are a pair (trackdisk does it; see the note after this program)
    MOVE.W #$9900,$DFF024.L     ; DSKLEN twice: a read of $1900 words
    MOVE.W #$9900,$DFF024.L
loop:
    ADDQ.L #1,D0
    BRA.S loop
dskblk:
    MOVE.W #$0002,$DFF09C.L     ; INTREQ: clear DSKBLK
    ADDQ.L #1,D2
    MOVEA.L #$604,A3
    MOVE.L D2,(A3)+             ; the reads that finished
    MOVE.L D1,(A2)+             ; the vertical blanks counted at the end of this one
    .nops PAD
    MOVE.L #$8000,$DFF020.L
    MOVE.W #$4000,$DFF024.L
    MOVE.W #$9900,$DFF024.L     ; the next read
    MOVE.W #$9900,$DFF024.L
    RTE
vblank:
    MOVE.W #$0020,$DFF09C.L
    ADDQ.L #1,D1
    RTE
"""
DISK_COUNT_PLAIN = DISK_COUNT.replace("    .nops PAD\n", "")


def chip(blob):
    """Chip RAM of a state, as the bytes the RAM chunk holds."""
    body = parse_chunks(blob)[RAM][2]
    size = int.from_bytes(body[0:4], "little")
    return body[8:8 + size]


def long_at(blob, address):
    return int.from_bytes(chip(blob)[address:address + 4], "big")


def disk_subs(blob):
    """{tag: bytes} of the ten sub-bodies of the disk chunk (DSK0..3, DSD0..3, DISK, DSKX); a machine whose floppies are writable or that has hard files carries more after them."""
    body = next(body for tag, _, body in parse_chunks(blob) if tag == DISK_CHUNK)
    subs, at = {}, 0
    for _ in range(10):
        (length,) = struct.unpack_from("<I", body, at + 4)
        subs[body[at:at + 4].decode("ascii")] = body[at + 8:at + 8 + length]
        at += 8 + length
    return subs


def dskx(blob):
    """The words of DSKX in a state: the controller's ({name: value}) then the four drives'."""
    subs = disk_subs(blob)
    words = struct.unpack(">%di" % (len(subs["DSKX"]) // 4), subs["DSKX"])
    return dict(zip(upstream.DSKX_GLOBAL, words)), words


def with_linecounter(blob, value, length=None):
    """The state with the linecounter word of DSKX changed and, if `length` is given, the words left of the DMA in DISK (the other checksums right)."""
    offset = 4 * upstream.DSKX_GLOBAL.index("linecounter")

    def change(subs):
        for sub in subs:
            if sub[0] == "DSKX":
                sub[1] = sub[1][:offset] + struct.pack(">i", value) + sub[1][offset + 4:]
            elif sub[0] == "DISK" and length is not None:
                sub[1] = sub[1][:15] + struct.pack(">H", length) + sub[1][17:]
    return upstream.with_subs(blob, DISK_CHUNK, change)


class FloppyCase(d.Det02Case):
    """The machinery of DET02 with a drive speed in the arguments: a scenario is written "disk@turbo" and is the scenario "disk" with --floppy-speed 5."""

    ROMS = {}

    def arguments(self, extra):
        if isinstance(extra, str) and "@" in extra:
            base, speed = extra.split("@")
            return super().arguments(base) + ["--floppy-speed", str(SPEEDS[speed])]
        return super().arguments(extra)


def scenarios(names, speeds, points, modes):
    return {f"{name}@{speed}": (rom, f"{extra}@{speed}", points[speed], modes) for name, (rom, extra) in names.items() for speed in speeds}


# Where a transfer is at each boundary: the first read starts in unit 2 and takes ten units at 100 %, so the points go through the first read, its end and the reads that follow.
POINTS = {"200": (3, 6, 7, 8, 12), "400": (3, 4, 5, 6, 12), "800": (2, 3, 4, 5, 12), "turbo": (2, 3, 4, 5, 8)}
MODES = (d.SAME_BEAM, d.RESET_BEAM, d.NEW)
SCENARIOS = {
    **scenarios({"disk-dma": ("disk-dma", "disk"), "disk-dma-single": ("disk-dma-single", "disk")}, tuple(SPEEDS), POINTS, MODES),
    **scenarios({"disk-seek": ("disk-seek", "disk")}, tuple(SPEEDS), {speed: (4, 5, 9) for speed in SPEEDS}, (d.SAME_BEAM, d.NEW)),
    **scenarios({"disk-dma-bytr": ("disk-dma-bytr", "disk"), "disk-steps": ("disk-steps", "disk")}, ("turbo", "800"), {"turbo": (3, 5), "800": (3, 5)}, (d.SAME_BEAM, d.NEW)),
    **scenarios({"disk-count": ("disk-count", "disk")}, tuple(SPEEDS), POINTS, (d.SAME_BEAM, d.NEW)),
    # The boundary of unit 2 falls inside the two lines that a turbo transfer waits (LineCounterTests checks that it does): the state carries linecounter, the DSKBLK interrupt of the
    # loaded machine comes on the line the original's does.
    "turbo-linecounter": ("disk-count", "disk@turbo", (2,), d.CORE),
}


class Det02FloppyTests(FloppyCase):
    # The reading and seeking programs of tests/state/test_det02.py, and the one above that reads on and counts.
    ROMS = {**{name: spec for name, spec in d.Det02Tests.ROMS.items() if name.startswith("disk-")}, "disk-count": (DISK_COUNT_PLAIN, None, None)}


d.populate(Det02FloppyTests, SCENARIOS, {})


class FloppySpeedTests(ProbeCase):
    """What the speed does: how much faster the disk reads, that it is repeatable, and the identity."""

    ROMS = {"disk-count": (DISK_COUNT_PLAIN, None, None)}
    UNITS = 40

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adf = cls.dir / "a.adf"
        cls.adf.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))
        cls.measured = {}

    def run_at(self, speed, units, rom="disk-count"):
        """The state after `units` units with the drive at `speed` (None: no option at all)."""
        args = [] if speed is None else ["--floppy-speed", str(speed)]
        path = self.dir / f"count-{speed}-{units}.state"
        self.probe(rom, "--units", str(units), "--save-at", str(units), str(path), "--adf", str(self.adf), "--event-at", "0", "0", "0", "1", *args)
        return path.read_bytes()

    def measure(self, speed):
        """(units from the start of the first read to its end, reads finished in UNITS units) of the drive at `speed`; the vertical blanks are counted by the program."""
        if speed not in self.measured:
            blob = self.run_at(speed, self.UNITS)
            reads, start = long_at(blob, 0x604), long_at(blob, 0x608)
            self.assertGreater(reads, 0, f"speed {speed}: no read finished in {self.UNITS} units")
            self.measured[speed] = (long_at(blob, 0x700) - start, reads)
        return self.measured[speed]

    def test_the_turbo_floppy_and_the_faster_ones_accelerate_the_load(self):
        real = self.measure(None)
        self.assertGreaterEqual(real[0], 9, "a read of one track takes about ten units at the real speed")
        self.assertEqual(self.measure(1), real, "naming the real drive is the profile")
        previous = real
        for speed in (2, 3, 4):
            found = self.measure(speed)
            factor = 1 << (speed - 1)                 # 2, 4, 8
            self.assertLess(found[0], previous[0], f"speed {speed} is not faster than the one before")
            self.assertLessEqual(found[0], real[0] // factor + 1, f"speed {speed}: {found} against the real drive {real}")
            self.assertGreaterEqual(found[1], real[1] * factor * 8 // 10, f"speed {speed} finished {found[1]} reads, the real drive {real[1]}")
            previous = found
        turbo = self.measure(5)
        self.assertLessEqual(turbo[0], 1, f"the first turbo read finishes in the unit it starts in or the next: {turbo}")
        self.assertGreater(turbo[1], real[1] * 30, f"turbo finished {turbo[1]} reads in {self.UNITS} units, the real drive {real[1]}")
        self.assertGreater(turbo[1], previous[1] * 3)
        print("\nT62 floppy speed (units until the first read of $1900 words ends / reads ended in %d units): " % self.UNITS +
              ", ".join(f"{name} {self.measure(speed)[0]}/{self.measure(speed)[1]}" for name, speed in (("100%", None), ("200%", 2), ("400%", 3), ("800%", 4), ("turbo", 5))))

    def test_the_same_speed_gives_the_same_machine_every_time(self):
        for speed in (3, 5):
            with self.subTest(speed=speed):
                first = self.run_at(speed, 20)
                (self.dir / f"count-{speed}-20.state").unlink()
                self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(self.run_at(speed, 20)).hexdigest())

    def test_the_speed_is_part_of_the_identity_and_the_real_drive_is_the_hash_it_always_was(self):
        base = e.config_hash(1, 1, 48000, 0, 1, 946684800)
        hashes = {speed: self.run_at(speed, 4)[176:208] for speed in (None, 1, 2, 3, 4, 5)}
        self.assertEqual(hashes[None], base)
        self.assertEqual(hashes[1], base, "naming the real drive is the profile")
        for speed, step in ((2, 1), (3, 2), (4, 3), (5, 4)):
            self.assertEqual(hashes[speed], e.config_hash(1, 1, 48000, step << 25, 1, 946684800), f"speed {speed}")
        self.assertEqual(len(set(hashes.values())), 5)

    def test_a_state_of_one_speed_is_refused_at_another(self):
        path = self.dir / "other.state"
        for saved, loaded in ((None, 2), (None, 5), (5, None), (2, 3)):
            with self.subTest(saved=saved, loaded=loaded):
                path.write_bytes(self.run_at(saved, 6))
                args = [] if loaded is None else ["--floppy-speed", str(loaded)]
                run = self.probe("disk-count", "--units", "8", "--every", "1", *args, "--load-at", "6", str(path), "--adf", str(self.adf), "--event-at", "0", "0", "0", "1")
                self.assertEqual([(6, e.INCOMPATIBLE)], [(at, code) for at, code, _ in run.loads], run.loads)

    def test_a_speed_the_abi_does_not_have_fails_the_init(self):
        result = subprocess.run([str(self.exe), str(self.roms["disk-count"]), "--units", "2", "--floppy-speed", "6"], capture_output=True, text=True, timeout=600)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("floppy speed", result.stdout + result.stderr)


class LineCounterTests(ProbeCase):
    """The two lines a turbo transfer waits before the DSKBLK interrupt (patch 0280). The boundary of unit 2 of the turbo "turbo-linecounter" scenario of Det02FloppyTests is inside them."""

    ROMS = {"disk-count": (DISK_COUNT_PLAIN, None, None)}
    UNIT = 2

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adf = cls.dir / "a.adf"
        cls.adf.write_bytes(hashlib.sha256(b"disk a").digest() * (901120 // 32))

    def args(self, speed="5"):
        return ["--adf", str(self.adf), "--event-at", "0", "0", "0", "1", "--floppy-speed", speed]

    def turbo_state(self):
        return self.state_of("turbo.state", "disk-count", self.UNIT, *self.args())

    def test_the_boundary_of_the_scenario_is_inside_the_two_lines_and_the_state_carries_the_counter(self):
        blob = self.turbo_state()
        self.assertIn(dskx(blob)[0]["linecounter"], (1, 2))
        # the DMA is off while it counts and a turbo read leaves its length at -1 (0xffff in DISK): the state of such a boundary is a state like any other
        subs = disk_subs(blob)
        self.assertEqual(0, subs["DISK"][5], "the DMA state of DISK is off")
        self.assertEqual(0xffff, struct.unpack_from(">H", subs["DISK"], 15)[0])

    def test_the_counter_is_zero_when_nothing_is_counting(self):
        for speed in ("1", "3"):
            self.assertEqual(0, dskx(self.state_of(f"real{speed}.state", "disk-count", self.UNIT, *self.args(speed)))[0]["linecounter"], speed)
        self.assertEqual(0, dskx(self.state_of("turbo-late.state", "disk-count", 1, *self.args()))[0]["linecounter"], "no transfer has started in unit 1")

    def test_a_copy_with_the_counter_zeroed_does_not_replay_the_original(self):
        path = self.dir / "turbo.state"
        path.write_bytes(self.turbo_state())
        zeroed = self.dir / "zeroed.state"
        zeroed.write_bytes(with_linecounter(path.read_bytes(), 0, 0))       # what the machine looks like with nothing counting: DMA off, no words left
        digests = []
        for state in (path, zeroed):
            run = self.probe("disk-count", "--units", str(self.UNIT + 5), "--every", "1", *self.args(), "--load-at", str(self.UNIT), str(state))
            self.assertEqual([(self.UNIT, 0, "")], run.loads[:1])
            digests.append([run.digests[(self.UNIT + j, s)] for j in range(1, 5) for s in d.SUBSYSTEMS])
        self.assertNotEqual(digests[0], digests[1], "the counter is what ends the transfer: without it the interrupt never comes")
        self.assertNotEqual(*[hashlib.sha256(x.read_bytes()).hexdigest() for x in (path, zeroed)])

    def test_a_counter_beyond_two_lines_or_a_transfer_that_counts_with_the_dma_on_is_refused(self):
        blob = self.turbo_state()
        attempts = [("a counter of 3", with_linecounter(blob, 3), e.CORRUPT_STATE, "turbo transfer counter"),
                    ("a negative counter", with_linecounter(blob, -1), e.CORRUPT_STATE, "turbo transfer counter"),
                    ("the length of a turbo read with nothing counting", with_linecounter(blob, 0), e.CORRUPT_STATE, "no turbo transfer is counting")]
        self.refusals(attempts, rom="disk-count", extra=self.args(), unit=self.UNIT, units=self.UNIT + 4)
        # a read that is in progress at the real speed has its DMA on: nothing can be counting
        reading = self.state_of("reading.state", "disk-count", 5, *self.args("1"))
        self.refusals([("a count while the DMA is on", with_linecounter(reading, 1), e.CORRUPT_STATE, "while the DMA of the disk is on")], rom="disk-count", extra=self.args("1"), unit=5, units=8)


# --- the owner's Kickstart 1.3 and a game disk (opt-in: P6_KICKSTART_ROM and P6_GAME_ADF; read in place, never copied, hashed or committed) -------------------------------------------

KICKSTART = os.environ.get("P6_KICKSTART_ROM")
GAME = os.environ.get("P6_GAME_ADF")
REAL = bool(KICKSTART and GAME and Path(KICKSTART).is_file() and Path(GAME).is_file())
# The Kickstart loads the disk in DF0 by itself (autoboot, then the game's own loader): the head goes from cylinder 0 to the last one the game uses and the motor stops. Units sampled
# for the end of the load, by speed, from a measurement (the load starts at about unit 200: the Kickstart's own start-up).
LOAD_END = {None: range(700, 951, 10), "200": range(450, 751, 10), "400": range(300, 501, 10), "800": range(250, 451, 10), "turbo": range(200, 401, 10)}
LAST_CYLINDER = 28


def drive0(blob):
    """(cylinder, motor on) of DF0 in a state."""
    body = disk_subs(blob)["DSK0"]
    return body[5], bool(body[4] & 1)


class RealFloppyCase(FloppyCase):
    ROMS = {}

    @classmethod
    def firmware(cls):
        return Path(KICKSTART)

    def arguments(self, extra):
        if isinstance(extra, str) and extra.startswith("game"):
            base, _, speed = extra.partition("@")
            return ["--adf", GAME, "--event-at", "0", "0", "0", "1"] + (["--floppy-speed", str(SPEEDS[speed])] if speed else [])
        return super().arguments(extra)


# Boundaries inside the load of the disk, at each speed: the head stepping, a transfer in flight, the motor, the controller's own latches (a turbo transfer waits two lines in them).
REAL_SCENARIOS = {
    "kickstart-disk@200": ("kickstart", "game@200", (300, 450), (d.SAME_BEAM, d.NEW)),
    "kickstart-disk@400": ("kickstart", "game@400", (260, 340), (d.SAME_BEAM, d.NEW)),
    "kickstart-disk@800": ("kickstart", "game@800", (260, 310), (d.SAME_BEAM, d.NEW)),
    "kickstart-disk@turbo": ("kickstart", "game@turbo", (230, 250, 270, 290), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
}


@unittest.skipUnless(REAL, "BLOCKED: set P6_KICKSTART_ROM and P6_GAME_ADF to an authorized Kickstart 1.3 and a game disk")
class Det02FloppyKickstartTests(RealFloppyCase):
    pass


d.populate(Det02FloppyKickstartTests, REAL_SCENARIOS, {})


@unittest.skipUnless(REAL, "BLOCKED: set P6_KICKSTART_ROM and P6_GAME_ADF to an authorized Kickstart 1.3 and a game disk")
class KickstartLoadTimeTests(RealFloppyCase):
    """Kickstart 1.3 and a disk: the units until the load is over (the head at the last cylinder and the motor off) by speed."""

    def end_of_load(self, speed):
        units = list(LOAD_END[speed])
        args = ["--units", str(units[-1]), "--adf", GAME, "--event-at", "0", "0", "0", "1"] + (["--floppy-speed", str(SPEEDS[speed])] if speed else [])
        for unit in units:
            args += ["--save-at", str(unit), str(self.dir / f"load-{speed}-{unit}.state")]
        self.probe("kickstart", *args)
        found = None
        for unit in units:
            path = self.dir / f"load-{speed}-{unit}.state"
            cylinder, motor = drive0(path.read_bytes())
            path.unlink()
            if found is None and cylinder >= LAST_CYLINDER and not motor:
                found = unit
        return found

    def test_the_load_of_a_real_disk_ends_sooner_at_every_step_of_the_speed(self):
        ends = {speed: self.end_of_load(speed) for speed in (None, "200", "400", "800", "turbo")}
        print("\nT62 Kickstart 1.3 + a game disk, unit in which the load is over (the head at cylinder %d, the motor off), sampled every 10 units: " % LAST_CYLINDER +
              ", ".join(f"{speed or '100'} {end}" for speed, end in ends.items()))
        self.assertTrue(all(end is not None for end in ends.values()), ends)
        order = [ends[speed] for speed in (None, "200", "400", "800", "turbo")]
        self.assertEqual(sorted(order, reverse=True), order, ends)
        self.assertEqual(len(set(order)), 5, ends)
        self.assertGreater(ends[None] - ends["turbo"], 400, "the turbo drive saves hundreds of units of a load that takes the real drive 600")


if __name__ == "__main__":
    unittest.main()
