"""LR01 (and LIFE01, STEP01, AV01, AV02 through the core), T17a (content, Kickstart lookup, VFS, Disk Control), T17b (core options from the one schema):
the libretro adapter, run by a minimal frontend that loads it like a real one.

tests/libretro-host/host.cpp dlopens build/link-audit/puae6_libretro and enforces the contract on every retro_run (one input poll,
exactly one picture, whole stereo audio frames, a serialization size that does not grow). The p6_headless runner is the other
adapter over the same p6_run_frame: the same ROM, seed and input must give the same ticks, the same picture and the same audio
unit for unit; the p6_state_probe, which drives the media events by unit, is the reference for what Disk Control does to a drive.
The core finds its Kickstart in the frontend's system directory (here a micro-ROM under a Kickstart file name) and takes an ADF, an
M3U playlist of ADFs, or nothing as content. Run: python3 -m unittest discover -s tests/libretro-host
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402
from tests.scenarios.ecs import ecs  # noqa: E402
import generate_options  # noqa: E402

KICKSTART_NAMES = [
    "kick34005.A500",
    "amiga-os-130.rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
    "Kickstart v1.3 rev 34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV).rom",
    "Kickstart v1.3 r34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
]
# The Kickstart 2.05 of the A600 (T23d): PUAE5's names (libretro-core.h uae_kickstarts[]) and the file names of the collections. The A500 and the A600
# look for their own names only.
KICKSTART_NAMES_A600 = [
    "kick37350.A600",
    "amiga-os-205-a600.rom",
    "Kickstart v2.05 rev 37.350 (1992)(Commodore)(A600HD)[!].rom",
    "Kickstart v2.05 rev 37.350 (1992)(Commodore)(A600HD).rom",
    "Kickstart v2.05 r37.350 (1992-04)(Commodore)(A600HD)[!].rom",
]
# The Kickstart 3.1 r40.68 of the A1200 (T24e): PUAE5's names (libretro-core.h uae_kickstarts[A1200_KS31_ROM]: the "normal", the Amiga Forever and the two TOSEC
# ones) and the TOSEC spelling "r40.68" of the file the project's authorized copy comes in. Each machine looks for its own names only.
KICKSTART_NAMES_A1200 = [
    "kick40068.A1200",
    "amiga-os-310-a1200.rom",
    "Kickstart v3.1 rev 40.68 (1993)(Commodore)(A1200)[!].rom",
    "Kickstart v3.1 rev 40.68 (1993)(Commodore)(A1200).rom",
    "Kickstart v3.1 r40.68 (1993)(Commodore)(A1200)[!].rom",
]
A600 = ("--set", "puae6_machine=A600")
A1200 = ("--set", "puae6_machine=A1200")
# Every machine of puae6_machine: the option, the names of its Kickstart, the Kickstart version the message names and p6_headless's --machine.
MACHINES = {"A500": ((), KICKSTART_NAMES, "1.3", "a500"), "A600": (A600, KICKSTART_NAMES_A600, "2.05", "a600"),
            "A1200": (A1200, KICKSTART_NAMES_A1200, "3.1", "a1200")}
ADF_BYTES = 901120

# A program that shows what DF0 reports: it selects the drive with the motor on, pulses STEP (which clears the disk-change latch) and puts the
# CIA-A port that carries the drive's status lines into COLOR00, so that the middle of the picture tells whether a disk is in the drive.
OBSERVER = """; PUAE6 test program: the colour of the screen is the drive's status.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and the power LED are outputs
    MOVE.B #2,$BFE001.L         ; OVL off
    MOVE.B #$FF,$BFD300.L       ; DDRB: the drive select, motor and step lines are outputs
    MOVE.B #$77,$BFD100.L       ; PRB: DF0 selected, motor on
loop:
    MOVE.B #$76,$BFD100.L       ; STEP pulse
    MOVE.B #$77,$BFD100.L
    MOVEQ #0,D2
    MOVE.B $BFE001.L,D2         ; PRA: bits 2-5 are the drive's status lines
    MOVE.W D2,$DFF180.L         ; COLOR00
    BRA.S loop
"""


# A program that tells the machines apart: the ECS Denise answers DENISEID ($DFF07C) with $FFFC, the OCS one leaves the bus value there. The register goes to
# COLOR00, so the colour of the whole screen is the Denise of the machine.
IDENTIFY = """; PUAE6 test program: the colour of the screen is what DENISEID reads.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L         ; DDRA: OVL and the power LED are outputs
    MOVE.B #2,$BFE001.L         ; OVL off
loop:
    .word $3439                 ; MOVE.W $DFF07C.L,D2 (the assembler of the micro-ROMs has no absolute source)
    .long $DFF07C
    MOVE.W D2,$DFF180.L         ; COLOR00
    BRA.S loop
"""


def adf(seed):
    """An ADF whose bytes are the SHA-256 of the seed repeated: distinct seeds give distinct disks, as in tests/state/test_det02.py."""
    return hashlib.sha256(seed.encode()).digest() * (ADF_BYTES // 32)

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
EXPORTS = [line[1:] for line in (ROOT / "adapters/libretro/exports.txt").read_text().split()]
RUN = re.compile(r"^run (\d+) video=(\d+)x(\d+) pitch=(\d+) dupe=(\d) hash=(\w+) px=(\w+) audio=(\d+) polls=(\d+) states=(\d+) ahash=(\w+) unit=(\d+) ticks=(\d+)$",
                 re.M)
UNIT = re.compile(r"^unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=(\w+) audio=(\d+)$", re.M)
CADENCE = re.compile(r"^unit (\d+) .* cadence=(\d+)/(\d+) ", re.M)
AV_SET = re.compile(r"^av-set (\d+) base=(\d+)x(\d+) max=(\d+)x(\d+) aspect=([\d.]+) fps=([\d.]+) rate=(\d+)$", re.M)
TICKS = re.compile(r"^log \d+ \[PUAE6\] unit=(\d+) ticks=(\d+) ports=(\S+) keys=(\w+)$", re.M)
CYCLE = re.compile(r"^cycle (\d+) end runs=(\d+) audio_frames=(\d+) audio_hash=(\w+)$", re.M)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class LibretroHarness(unittest.TestCase):
    """The build, the micro-ROMs and one process of the minimal frontend; the tests of LibretroTests and tests/libretro-host/test_state.py stand on it."""

    @classmethod
    def setUpClass(cls):
        cls.host = build_probe(BUILD, False, "p6_libretro_host")
        cls.core = next(Path(build_probe(BUILD, False, "puae6_libretro")).parent.glob("puae6_libretro.*"))
        cls.headless = build_probe(BUILD, False, "p6_headless")
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-libretro-")
        cls.roms = {}
        for name in ("boot", "long_frame", "input_all"):
            rom = Path(cls.tmp.name) / f"{name}.rom"
            rom.write_bytes(builder.build((FIXTURES / f"{name}.s").read_text(encoding="utf-8")))
            cls.roms[name] = rom
        garbage = Path(cls.tmp.name) / "garbage.rom"
        garbage.write_bytes(bytes(range(256)) * 4)
        cls.roms["garbage"] = garbage
        observer = Path(cls.tmp.name) / "observer.rom"
        observer.write_bytes(builder.build(OBSERVER))
        cls.roms["observer"] = observer
        identify = Path(cls.tmp.name) / "identify.rom"
        identify.write_bytes(builder.build(IDENTIFY))
        cls.roms["identify"] = identify
        cls.probe = build_probe(BUILD, False, "p6_state_probe")
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def work(self):
        """A new empty directory under the test's temporary one."""
        type(self).counter += 1
        path = Path(self.tmp.name) / f"work-{self.counter}"
        path.mkdir()
        return path

    def system(self, rom, name=KICKSTART_NAMES[0]):
        """A system directory that holds the ROM under a Kickstart file name."""
        path = self.work()
        shutil.copyfile(self.roms[rom], path / name)
        return path

    def frontend(self, rom="boot", *options, content=None, name=KICKSTART_NAMES[0], system=None, expect=0):
        """One process of the minimal frontend. The Kickstart is the micro-ROM `rom` under `name` in a system directory (`system` names another
        directory, False gives the frontend none); `content` is a path handed over as the game, or None for a load without content."""
        arguments = [str(self.host), str(self.core)]
        if system is not False:
            arguments += ["--system", str(system or self.system(rom, name))]
        if content is not None:
            arguments += ["--content", str(content)]
        result = subprocess.run([*arguments, *map(str, options)], capture_output=True, text=True, timeout=600,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(result.returncode, expect, result.stdout[-800:] + result.stderr[-800:])
        if expect == 0:
            self.assertNotIn("VIOLATION", result.stdout)
        return result

    def system_for(self, rom, *names):
        """A system directory that holds the ROM under each of the names (a person's directory with a Kickstart for each machine)."""
        path = self.work()
        for name in names:
            shutil.copyfile(self.roms[rom], path / name)
        return path

    def headless_text(self, rom, units, *flags):
        result = subprocess.run([str(self.headless), "--run", str(self.roms[rom]), "--units", str(units), *flags], capture_output=True, text=True,
                                timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def headless_units(self, rom, units, *flags):
        result = subprocess.run([str(self.headless), "--run", str(self.roms[rom]), "--units", str(units), *flags], capture_output=True, text=True,
                                timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {int(m[1]): {"ticks": int(m[2]), "video": int(m[3]), "size": (int(m[4]), int(m[5])), "hash": m[6], "audio": int(m[7])}
                for m in UNIT.finditer(result.stdout)}

    def playlist(self, text, files=("a.adf", "b.adf"), name="list.m3u", encoding="utf-8", prefix=b""):
        directory = self.work()
        for file in files:
            path = directory / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(adf(file))
        (directory / name).write_bytes(prefix + text.encode(encoding))
        return directory / name

    @staticmethod
    def runs(text):
        return [{"n": int(m[1]), "size": (int(m[2]), int(m[3])), "pitch": int(m[4]), "dupe": int(m[5]), "hash": m[6], "audio": int(m[8]),
                 "px": m[7], "polls": int(m[9]), "states": int(m[10]), "ahash": m[11], "unit": int(m[12]), "ticks": int(m[13])}
                for m in RUN.finditer(text)]


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class LibretroTests(LibretroHarness):

    # --- the library -----------------------------------------------------------------------------------------------------------------

    def test_the_core_exports_the_libretro_api_and_nothing_else_and_links_no_frontend_library(self):
        exported = subprocess.run(["nm", "-gU", str(self.core)], capture_output=True, text=True).stdout.split()
        names = sorted(name.lstrip("_") for name in exported if name.startswith("_") and not name.startswith("__"))
        self.assertEqual(sorted(EXPORTS), names)
        linked = subprocess.run(["otool", "-L", str(self.core)], capture_output=True, text=True).stdout.splitlines()[1:]
        libraries = sorted(line.split()[0] for line in linked)
        self.assertFalse([lib for lib in libraries if "Framework" in lib], libraries)       # no CoreAudio, no windowing
        self.assertTrue(all(lib.startswith(("/usr/lib/", "@rpath/puae6")) for lib in libraries), libraries)

    # --- LR01: what every retro_run does ---------------------------------------------------------------------------------------------

    def test_every_run_polls_once_shows_one_picture_and_the_info_is_what_the_core_delivers(self):
        out = self.frontend("boot", "--runs", 12).stdout
        runs = self.runs(out)
        self.assertEqual([r["n"] for r in runs], list(range(1, 13)))
        # One poll, and the same few reads in every run (the keyboard table, the buttons and sticks of two ports): a bounded cost, no loop on the frontend.
        self.assertTrue(all(r["polls"] == 1 and r["states"] == runs[0]["states"] and 14 <= r["states"] <= 256 for r in runs))
        self.assertTrue(all(r["size"] == (756, 574) and r["pitch"] >= 756 * 4 for r in runs))
        av = re.search(r"^av base=(\d+)x(\d+) max=(\d+)x(\d+) aspect=([\d.]+) fps=([\d.]+) rate=(\d+) region=(\d+)$", out, re.M)
        self.assertEqual((av[1], av[2], av[3], av[4], av[7], av[8]), ("756", "574", "760", "576", "48000", "1"))   # region 1 is PAL
        self.assertAlmostEqual(float(av[6]), 3546895 / 70824, places=4)
        self.assertAlmostEqual(float(av[5]), 756 * 26 / (574 * 25), places=4)
        self.assertRegex(out, r"(?m)^core name=PUAE6 version=0\.1\.0-winuae-[0-9a-f]{12}-p\d+ extensions=adf\|adz\|dms\|zip\|m3u need_fullpath=1$")
        self.assertIn("10", re.search(r"environment=([\d,]+)", out)[1].split(","))            # the pixel format was negotiated

    def test_the_input_of_the_frontend_reaches_the_bridge_as_the_contract_maps_it(self):
        # libretro joypad ids: B 0, A 8, X 9, UP 4, RIGHT 7 -> button 1, button 2, button 3, up, right.
        joy = self.frontend("boot", "--runs", 2, "--joy", "0:391", "--joy", "1:40").stdout
        ports = TICKS.search(joy)[3]
        # Player 1 drives the joystick port (the second packet port): BUTTON_1|2|3|UP|RIGHT = 0x4f. By default it also owns the mouse port (an idle mouse), so
        # player 2's pad (LEFT, id 6, 0x20) has no port; "Joystick only" (device 257) gives it the mouse port.
        self.assertEqual(ports, "2:0:0:0,1:4f:0:0")
        alone = self.frontend("boot", "--runs", 2, "--joy", "0:391", "--joy", "1:40", "--device", "0:257").stdout
        self.assertEqual(TICKS.search(alone)[3], "1:20:0:0,1:4f:0:0")
        mouse = self.frontend("boot", "--runs", 2, "--mouse", "5,-3,5").stdout
        self.assertEqual(TICKS.search(mouse)[3], "2:5:5:-3,1:0:0:0")   # left + middle = BUTTON_1|BUTTON_3; the mouse and player 1's pad work at once

    def test_a_frontend_that_refuses_every_optional_capability_still_gets_the_same_machine(self):
        normal = self.runs(self.frontend("boot", "--runs", 8).stdout)
        refused = self.frontend("boot", "--runs", 8, "--reject-optional")
        self.assertNotIn("log ", refused.stdout)                                               # no log interface was given
        self.assertEqual([(r["hash"], r["audio"]) for r in self.runs(refused.stdout)], [(r["hash"], r["audio"]) for r in normal])

    def test_a_frontend_that_cannot_repeat_a_picture_is_handed_the_previous_one(self):
        # long_frame has units without a new picture from the tenth on: with GET_CAN_DUPE the core passes NULL, without it the picture again.
        without_picture = [n for n, unit in self.headless_units("long_frame", 13).items() if not unit["video"]]
        self.assertEqual(without_picture, [10, 11, 12, 13])
        duping = self.runs(self.frontend("long_frame", "--runs", 13).stdout)
        copying = self.runs(self.frontend("long_frame", "--runs", 13, "--no-dupe").stdout)
        self.assertEqual([r["n"] for r in duping if r["dupe"]], without_picture)
        self.assertEqual([r["n"] for r in copying if r["dupe"]], [])
        for run in (duping, copying):
            self.assertTrue(all(r["polls"] == 1 for r in run))
        self.assertEqual([r["hash"] for r in copying][9:], [copying[8]["hash"]] * 4)          # the last real picture, again

    def test_a_pixel_format_the_frontend_refuses_fails_the_load_cleanly(self):
        result = self.frontend("boot", "--bad-format", expect=3)
        self.assertIn("load refused", result.stdout)
        self.assertRegex(result.stdout, r"log 3 \[PUAE6\] the frontend does not accept XRGB8888")

    def test_a_firmware_the_bridge_refuses_fails_the_load_and_leaves_the_core_usable(self):
        result = self.frontend("garbage", expect=3)
        self.assertIn("load refused", result.stdout)
        self.assertRegex(result.stdout, r"log 3 \[PUAE6\] p6_load failed")
        self.assertRegex(result.stdout, r"(?m)^message PUAE6: the Kickstart ROM in the system directory .* is not usable: kick34005\.A500 refused")

    def test_calls_made_from_inside_a_callback_are_ignored(self):
        out = self.frontend("boot", "--runs", 6, "--reenter").stdout
        self.assertEqual(len(self.runs(out)), 6)
        self.assertEqual([int(m[1]) for m in TICKS.finditer(out)], list(range(1, 7)))         # the nested retro_run did not advance a unit
        self.assertIn("retro_run was called from inside a libretro call and is ignored", out)

    # --- STEP01: the same boundaries as the headless adapter -------------------------------------------------------------------------

    def test_n_runs_are_n_headless_units_with_the_same_ticks_picture_and_audio(self):
        for rom, units in (("boot", 20), ("long_frame", 14)):
            with self.subTest(rom=rom):
                out = self.frontend(rom, "--runs", units).stdout
                headless = self.headless_units(rom, units)
                ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
                runs = {r["n"]: r for r in self.runs(out)}
                self.assertEqual(sorted(runs), sorted(headless))
                for n, expected in headless.items():
                    self.assertEqual(ticks[n], expected["ticks"], f"unit {n}")
                    self.assertEqual(runs[n]["audio"], expected["audio"], f"unit {n}")
                    if expected["video"]:
                        self.assertEqual((runs[n]["size"], runs[n]["hash"]), (expected["size"], expected["hash"]), f"unit {n}")
                    else:
                        self.assertTrue(runs[n]["dupe"], f"unit {n} has no new picture and the core repeats the last")

    # --- AV02: nothing is lost or invented by the frontend's pace --------------------------------------------------------------------

    def test_audio_a_frontend_takes_in_pieces_or_later_is_delivered_whole_and_in_order(self):
        def final(*options):
            return CYCLE.search(self.frontend("boot", "--runs", 14, *options).stdout).groups()
        whole = final()
        self.assertEqual(final("--audio-partial", 100), whole)                                  # taken 100 frames at a time
        self.assertEqual(final("--audio-stall", "3:6"), whole)                                  # nothing taken in runs 3-6: delivered later
        self.assertEqual(final("--audio-partial", 1), whole)                                    # one frame per call

    def test_a_frontend_that_never_takes_audio_does_not_grow_the_core_without_bound(self):
        out = self.frontend("boot", "--runs", 40, "--audio-stall", "1:100").stdout
        self.assertIn("the frontend is not taking audio: the oldest frames are dropped", out)
        delivered = int(CYCLE.search(out)[3])
        self.assertEqual(delivered, 0)                                                          # it took none, and the core kept at most its bound

    # --- LIFE01: load, run, unload, again -------------------------------------------------------------------------------------------

    def test_load_run_unload_cycles_leave_nothing_behind_and_repeat_exactly(self):
        out = self.frontend("boot", "--runs", 4, "--cycles", 6).stdout
        cycles = [m.groups() for m in CYCLE.finditer(out)]
        self.assertEqual(len(cycles), 6)
        self.assertEqual(len({c[1:] for c in cycles}), 1, cycles)                               # runs, audio frames and the PCM stream are identical

    def test_the_serialization_size_is_announced_once_and_never_changes(self):
        # T18: the host checks after every run that retro_serialize_size is what it was after the load; tests/libretro-host/test_state.py does the states.
        out = self.frontend("boot", "--runs", 3, "--cycles", 2).stdout
        sizes = re.findall(r"(?m)^state size=(\d+)$", out)
        self.assertEqual(len(sizes), 2)
        self.assertGreater(int(sizes[0]), 1_000_000)
        self.assertEqual(sizes[0], sizes[1])

    # --- T17a: the Kickstart is found in the system directory, by name, and recognized by its bytes -----------------------------------

    @staticmethod
    def messages(text):
        return re.findall(r"(?m)^message (.*)$", text)

    def test_the_kickstart_is_found_under_each_known_file_name(self):
        reference = None
        for name in KICKSTART_NAMES:
            for options in ((), ("--no-vfs",)):
                with self.subTest(name=name, options=options):
                    out = self.frontend("boot", "--runs", 6, *options, name=name).stdout
                    self.assertIn(f"Kickstart {name}, read " + ("with stdio" if options else "through the frontend VFS"), out)
                    # The name finds the file and nothing else: the same bytes under any of them are the same machine.
                    runs = [(r["hash"], r["audio"]) for r in self.runs(out)]
                    reference = reference or runs
                    self.assertEqual(runs, reference)

    def test_a_file_that_is_not_under_a_known_name_is_not_a_kickstart(self):
        result = self.frontend("boot", name="kickstart.rom", expect=3)
        self.assertRegex(result.stdout, r"(?m)^message PUAE6: no Kickstart 1\.3 ROM in the system directory ")
        self.assertRegex(result.stdout, r"log 3 \[PUAE6\] no Kickstart 1\.3 ROM")

    def test_a_missing_or_refused_kickstart_fails_the_load_with_a_message_and_leaves_the_core_usable(self):
        good = self.system("boot")
        for label, bad, text in (("missing", self.work(), "no Kickstart 1.3 ROM in the system directory"),
                                 ("refused", self.system("garbage"), "is not usable: kick34005.A500 refused"),
                                 ("none", False, "the frontend has no system directory")):
            with self.subTest(label):
                failed = self.frontend("boot", system=bad, expect=3).stdout
                self.assertIn("load refused", failed)
                self.assertIn(text, " ".join(self.messages(failed)))
                # The same process loads again after the person fixes the system directory: nothing of the failed load is left.
                out = self.frontend("boot", "--runs", 4, "--retry-system", good, system=bad).stdout
                self.assertIn("load refused", out)
                self.assertEqual([r["n"] for r in self.runs(out)], [1, 2, 3, 4])

    def test_a_refused_kickstart_does_not_hide_a_good_one_under_the_next_name(self):
        directory = self.system("garbage", KICKSTART_NAMES[0])
        shutil.copyfile(self.roms["boot"], directory / KICKSTART_NAMES[1])
        out = self.frontend("boot", "--runs", 3, system=directory).stdout
        self.assertRegex(out, r"log 3 \[PUAE6\] p6_load failed")
        self.assertIn(f"Kickstart {KICKSTART_NAMES[1]},", out)
        self.assertNotIn("load refused", out)

    def test_a_kickstart_larger_than_any_rom_is_refused_without_reading_it_all(self):
        directory = self.work()
        (directory / KICKSTART_NAMES[0]).write_bytes(bytes(600 * 1024))
        out = self.frontend("boot", system=directory, expect=3).stdout
        self.assertIn("is not usable: kick34005.A500 it is too large", " ".join(self.messages(out)))

    # --- T23d: the A600 as a restart option, with the Kickstart 2.05 of its own -------------------------------------------------------------

    def compare_with_headless(self, out, rom, units, *flags):
        """Every unit of a session of the core has the ticks, audio and picture that p6_headless gives the same ROM with the same machine."""
        headless = self.headless_units(rom, units, *flags)
        ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), sorted(headless))
        for n, expected in headless.items():
            self.assertEqual((ticks[n], runs[n]["audio"]), (expected["ticks"], expected["audio"]), f"unit {n}")
            if expected["video"]:
                self.assertEqual((runs[n]["size"], runs[n]["hash"]), (expected["size"], expected["hash"]), f"unit {n}")
            else:
                self.assertTrue(runs[n]["dupe"], f"unit {n}")
        return runs

    def test_the_a600_selected_by_option_is_the_machine_headless_runs_with_machine_a600(self):
        for rom, units in (("boot", 20), ("long_frame", 14), ("identify", 8)):
            for standard in ((), ("--set", "puae6_video_standard=NTSC")):
                with self.subTest(rom=rom, standard=standard):
                    out = self.frontend(rom, "--runs", units, *A600, *standard, name=KICKSTART_NAMES_A600[0]).stdout
                    self.assertIn("machine A600 (puae6_machine)", out)
                    self.compare_with_headless(out, rom, units, "--machine", "a600", *(["--ntsc"] if standard else []))

    def test_the_a1200_selected_by_option_is_the_machine_headless_runs_with_machine_a1200(self):
        # T24e: PAL and NTSC, the three micro-ROMs, unit by unit (ticks, audio, picture hash).
        for rom, units in (("boot", 20), ("long_frame", 14), ("identify", 8)):
            for standard in ((), ("--set", "puae6_video_standard=NTSC")):
                with self.subTest(rom=rom, standard=standard):
                    out = self.frontend(rom, "--runs", units, *A1200, *standard, name=KICKSTART_NAMES_A1200[0]).stdout
                    self.assertIn("machine A1200 (puae6_machine)", out)
                    self.compare_with_headless(out, rom, units, "--machine", "a1200", *(["--ntsc"] if standard else []))

    # --- T23f: the frontend is told the rate of the beam the unit really ran --------------------------------------------------------------

    def beam_rom(self, name, standard):
        key = f"beam-{name}-{standard}"
        if key not in self.roms:
            path = Path(self.tmp.name) / f"{key}.rom"
            path.write_bytes(builder.build(ecs.rom_source(ecs.beam_display_for(name, standard))))
            self.roms[key] = path
        return key

    def test_a_guest_that_programs_the_other_standard_makes_the_core_announce_the_new_rate_once_at_the_unit_it_changes(self):
        nominal = {"pal": (3546895, 70824), "ntsc": (715909, 11921)}
        for machine, name, standard in (("A600", "to-ntsc", "pal"), ("A600", "to-pal", "ntsc"), ("A1200", "to-ntsc", "pal"), ("A1200", "to-pal", "ntsc")):
            key = self.beam_rom(name, standard)
            options = ("--set", "puae6_video_standard=NTSC") if standard == "ntsc" else ()
            flags = ("--machine", MACHINES[machine][3], *(["--ntsc"] if standard == "ntsc" else []))
            with self.subTest(machine=machine, program=name, standard=standard):
                out = self.frontend(key, "--runs", 12, *MACHINES[machine][0], *options, name=MACHINES[machine][1][0]).stdout
                self.compare_with_headless(out, key, 12, *flags)
                cadence = {int(m[1]): (int(m[2]), int(m[3])) for m in CADENCE.finditer(self.headless_text(key, 12, *flags))}
                n, d = nominal[standard]
                moved = [u for u, (cn, cd) in sorted(cadence.items()) if abs(cn * d - n * cd) * 100 > n * cd]     # more than 1 % from the announced rate
                self.assertTrue(moved, cadence)
                announced = [(int(m[1]), m) for m in AV_SET.finditer(out)]
                self.assertEqual([run for run, _ in announced], [moved[0]])                                      # exactly one, at the unit it changed
                cn, cd = cadence[moved[0]]
                m = announced[0][1]
                self.assertAlmostEqual(float(m[7]), cn / cd, places=5)
                self.assertEqual((int(m[4]), int(m[5]), int(m[8])), (760, 576, 48000))                           # the buffer announced at the load
                self.assertEqual({cadence[u] for u in moved}, {cadence[moved[0]]})                                # and it stayed there
                self.assertEqual(self.messages(out), [])

    def test_an_a500_never_has_a_rate_to_announce_whatever_the_field_does(self):
        for rom in ("boot", "long_frame", "input_all"):
            for options in ((), ("--set", "puae6_video_standard=NTSC")):
                with self.subTest(rom=rom, options=options):
                    out = self.frontend(rom, "--runs", 16, *options).stdout
                    self.assertEqual(AV_SET.findall(out), [])

    def test_a_frontend_that_refuses_the_new_rate_is_not_asked_again_for_the_same_one(self):
        key = self.beam_rom("to-ntsc", "pal")
        out = self.frontend(key, "--runs", 12, *A600, "--refuse-av-info", name=KICKSTART_NAMES_A600[0]).stdout
        self.assertEqual(len(AV_SET.findall(out)), 1)

    def test_the_option_really_changes_the_machine_the_a500_and_the_a600_differ_in_what_a_program_reads(self):
        a500 = self.frontend("identify", "--runs", 8).stdout
        a600 = self.frontend("identify", "--runs", 8, *A600, name=KICKSTART_NAMES_A600[0]).stdout
        self.assertIn("machine A500 (puae6_machine)", a500)
        self.assertNotEqual([(r["hash"], r["px"]) for r in self.runs(a500)], [(r["hash"], r["px"]) for r in self.runs(a600)])
        # Choosing the A500 by name is the default machine, bit for bit.
        named = self.frontend("identify", "--runs", 8, "--set", "puae6_machine=A500").stdout
        self.assertEqual(self.machine(named), self.machine(a500))

    def test_the_a1200_is_a_third_machine_what_the_program_reads_from_denise_is_the_aga_one(self):
        # T24e: DENISEID is $FFFC on the ECS Denise of the A600 and $00F8 on the AGA Lisa of the A1200 (tests/step/test_a1200.py), and the A500 leaves the bus value.
        pictures = {}
        for machine, (options, names, _, _) in MACHINES.items():
            out = self.frontend("identify", "--runs", 8, *options, name=names[0]).stdout
            self.assertIn(f"machine {machine} (puae6_machine)", out)
            pictures[machine] = [(r["hash"], r["px"]) for r in self.runs(out)]
        self.assertEqual(len({str(p) for p in pictures.values()}), 3, "two machines drew the same picture for a program that reads Denise's ID")
        named = self.frontend("identify", "--runs", 8, "--set", "puae6_machine=A1200", name=KICKSTART_NAMES_A1200[0]).stdout
        self.assertEqual([(r["hash"], r["px"]) for r in self.runs(named)], pictures["A1200"])

    def test_the_a600_kickstart_is_found_under_each_known_file_name(self):
        reference = None
        for name in KICKSTART_NAMES_A600:
            for options in ((), ("--no-vfs",)):
                with self.subTest(name=name, options=options):
                    out = self.frontend("boot", "--runs", 6, *A600, *options, name=name).stdout
                    self.assertIn(f"Kickstart {name}, read " + ("with stdio" if options else "through the frontend VFS"), out)
                    runs = [(r["hash"], r["audio"]) for r in self.runs(out)]
                    reference = reference or runs
                    self.assertEqual(runs, reference)

    def test_the_a1200_kickstart_is_found_under_each_known_file_name_and_the_3_0_of_puae5_is_not_one_of_them(self):
        reference = None
        for name in KICKSTART_NAMES_A1200:
            for options in ((), ("--no-vfs",)):
                with self.subTest(name=name, options=options):
                    out = self.frontend("boot", "--runs", 6, *A1200, *options, name=name).stdout
                    self.assertIn(f"Kickstart {name}, read " + ("with stdio" if options else "through the frontend VFS"), out)
                    runs = [(r["hash"], r["audio"]) for r in self.runs(out)]
                    reference = reference or runs
                    self.assertEqual(runs, reference)
        # PUAE5 also reads kick39106.A1200 (Kickstart 3.0) for its A1200; this machine boots the 3.1, so that name is not looked for.
        out = self.frontend("boot", *A1200, name="kick39106.A1200", expect=3).stdout
        self.assertIn("no Kickstart 3.1 ROM in the system directory ", " ".join(self.messages(out)))

    def test_each_machine_looks_for_the_kickstart_of_its_own_and_never_boots_with_the_one_of_the_other(self):
        for machine, (options, names, wanted, _) in MACHINES.items():
            for other, (_, present, _, _) in MACHINES.items():
                if other == machine:
                    continue
                for name in present:
                    with self.subTest(machine=machine, other=other, name=name):
                        out = self.frontend("boot", *options, name=name, expect=3).stdout
                        self.assertIn("load refused", out)
                        text = " ".join(self.messages(out))
                        self.assertIn(f"no Kickstart {wanted} ROM in the system directory ", text)
                        self.assertIn(f"(looked for {names[0]}, ...) for the {machine} chosen by puae6_machine", text)
                        self.assertNotIn("run 1 ", out)
        # A directory that holds all of them boots whichever machine is chosen, each with its own.
        everything = self.system_for("boot", *(names[0] for _, names, _, _ in MACHINES.values()))
        for options, names, _, _ in MACHINES.values():
            self.assertIn(f"Kickstart {names[0]},", self.frontend("boot", "--runs", 3, *options, system=everything).stdout)

    def test_a_refused_a1200_kickstart_is_a_clear_failure_and_does_not_fall_back_to_another_machines_names(self):
        directory = self.system_for("garbage", KICKSTART_NAMES_A1200[0])
        for name in (KICKSTART_NAMES[0], KICKSTART_NAMES_A600[0]):
            shutil.copyfile(self.roms["boot"], directory / name)                    # usable ROMs, but under the names of the other machines
        out = self.frontend("boot", *A1200, system=directory, expect=3).stdout
        self.assertIn("is not usable: kick40068.A1200 refused", " ".join(self.messages(out)))
        self.assertNotIn("run 1 ", out)
        shutil.copyfile(self.roms["boot"], directory / KICKSTART_NAMES_A1200[1])
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A1200[1]},", self.frontend("boot", "--runs", 3, *A1200, system=directory).stdout)

    def test_a_refused_a600_kickstart_is_a_clear_failure_and_does_not_fall_back_to_the_a500_names(self):
        directory = self.system_for("garbage", KICKSTART_NAMES_A600[0])
        shutil.copyfile(self.roms["boot"], directory / KICKSTART_NAMES[0])         # a usable ROM, but under the name of the other machine
        out = self.frontend("boot", *A600, system=directory, expect=3).stdout
        self.assertIn("is not usable: kick37350.A600 refused", " ".join(self.messages(out)))
        self.assertNotIn(f"Kickstart {KICKSTART_NAMES[0]},", out)
        self.assertNotIn("run 1 ", out)
        # And the next name of the A600 does serve after a refused one, as it does for the A500.
        shutil.copyfile(self.roms["boot"], directory / KICKSTART_NAMES_A600[1])
        good = self.frontend("boot", "--runs", 3, *A600, system=directory).stdout
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A600[1]},", good)

    def test_a_machine_value_the_schema_does_not_allow_fails_the_load_naming_the_key_and_the_value(self):
        for value in ("a1200", "A1200OG", "A4000", "a600", "A500+", "", "A600 ", "A1200 "):
            with self.subTest(value=value):
                result = self.frontend("boot", "--set", f"puae6_machine={value}", expect=3)
                text = f'the option puae6_machine has the value "{value}", which this core does not support (allowed: A500, A600, A1200)'
                self.assertIn(f"message PUAE6: {text}", result.stdout)
                self.assertIn("load refused", result.stdout)
                self.assertNotIn("run 1 ", result.stdout)
                self.assertNotIn("vfs open", result.stdout)                      # refused before any Kickstart was looked at, and no machine was chosen by default

    def test_a_machine_changed_while_the_machine_runs_is_reported_and_the_next_load_is_the_other_machine(self):
        both = self.system_for("identify", KICKSTART_NAMES[0], KICKSTART_NAMES_A600[0])
        base = self.frontend("identify", "--runs", 14, system=both).stdout
        changed = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A600", system=both).stdout
        self.assertEqual(self.machine(changed), self.machine(base))                           # the running machine is not touched
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_machine is now A600 but the machine runs with A500: reload the content to apply it"])
        self.assertEqual(self.messages(base), [])
        # After a reload it is the A600, and it is the one a fresh process would have.
        cycles = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A600", "--cycles", 2, system=both).stdout
        self.assertRegex(cycles, r"machine A500 \(puae6_machine\)(?s:.*)machine A600 \(puae6_machine\)")
        fresh = self.frontend("identify", "--runs", 14, *A600, system=both).stdout
        found = [m.groups() for m in CYCLE.finditer(cycles)]
        self.assertEqual(len(found), 2)
        self.assertEqual(CYCLE.search(fresh).groups()[1:], found[1][1:])
        pictures = [(r["hash"], r["px"]) for r in self.runs(cycles)]
        self.assertEqual(len(pictures), 28)
        self.assertEqual(pictures[:14], [(r["hash"], r["px"]) for r in self.runs(base)])                    # the first load is the A500 ...
        self.assertEqual(pictures[14:], [(r["hash"], r["px"]) for r in self.runs(fresh)])                   # ... and the second the A600 of a fresh process
        self.assertNotEqual(pictures[:14], pictures[14:])

    def test_a_machine_changed_to_the_a1200_while_the_machine_runs_is_reported_and_the_next_load_is_the_a1200(self):
        both = self.system_for("identify", KICKSTART_NAMES[0], KICKSTART_NAMES_A1200[0])
        base = self.frontend("identify", "--runs", 14, system=both).stdout
        changed = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A1200", system=both).stdout
        self.assertEqual(self.machine(changed), self.machine(base))
        self.assertEqual(self.messages(changed), ["PUAE6: puae6_machine is now A1200 but the machine runs with A500: reload the content to apply it"])
        cycles = self.frontend("identify", "--runs", 14, "--set-at", "6:puae6_machine=A1200", "--cycles", 2, system=both).stdout
        self.assertRegex(cycles, r"machine A500 \(puae6_machine\)(?s:.*)machine A1200 \(puae6_machine\)")
        fresh = self.frontend("identify", "--runs", 14, *A1200, system=both).stdout
        found = [m.groups() for m in CYCLE.finditer(cycles)]
        self.assertEqual(len(found), 2)
        self.assertEqual(CYCLE.search(fresh).groups()[1:], found[1][1:])
        pictures = [(r["hash"], r["px"]) for r in self.runs(cycles)]
        self.assertEqual(pictures[14:], [(r["hash"], r["px"]) for r in self.runs(fresh)])

    def test_the_same_configuration_loads_the_same_machine_every_time(self):
        out = self.frontend("boot", "--runs", 4, *A600, "--cycles", 4, name=KICKSTART_NAMES_A600[0]).stdout
        cycles = [m.groups() for m in CYCLE.finditer(out)]
        self.assertEqual(len(cycles), 4)
        self.assertEqual(len({c[1:] for c in cycles}), 1, cycles)
        sizes = re.findall(r"(?m)^state size=(\d+)$", out)
        self.assertEqual(len(set(sizes)), 1, sizes)

    # --- T17a: ADF and M3U content, through the VFS or stdio ------------------------------------------------------------------------

    def test_an_adf_as_content_is_registered_by_its_bytes_and_inserted_before_the_first_unit(self):
        directory = self.work()
        (directory / "Game Disk.adf").write_bytes(adf("game"))
        out = self.frontend("observer", "--runs", 5, content=directory / "Game Disk.adf").stdout
        self.assertIn(f"medium 0 sha256={hashlib.sha256(adf('game')).hexdigest()}", out)
        self.assertRegex(out, rf"(?m)^images count=1 index=0 eject=0\nimage 0 path={re.escape(str(directory))}/Game Disk\.adf label=Game Disk$")
        self.assertLess(out.index("DF0: medium 0 inserted"), out.index("run 1 "))              # a power-on disk: events before the first unit
        self.assertIn(f"vfs open {directory}/Game Disk.adf", out)
        with_disk = self.runs(out)
        without = self.runs(self.frontend("observer", "--runs", 5).stdout)
        self.assertNotEqual({r["px"] for r in with_disk}, {r["px"] for r in without})            # the drive reports a disk (see the observer program)
        self.assertEqual(len({r["px"] for r in with_disk}), 1)

    def test_a_load_without_content_boots_the_kickstart_with_an_empty_drive(self):
        out = self.frontend("observer", "--runs", 4).stdout
        self.assertIn("images count=0 index=0 eject=0", out)
        self.assertNotIn("DF0:", out)
        self.assertEqual(len(self.runs(out)), 4)

    def test_an_m3u_names_the_disks_with_relative_paths_labels_comments_and_any_line_end(self):
        for label, text, prefix in (("lf", "#EXTM3U\n# a comment\n\na.adf|Disk A\nb.adf|\n", b""),
                                    ("crlf", "a.adf|Disk A\r\nb.adf|\r\n", b""),
                                    ("cr", "a.adf|Disk A\rb.adf", b""),
                                    ("bom", "a.adf|Disk A\nb.adf\n", b"\xef\xbb\xbf"),
                                    ("blanks", "  a.adf | Disk A \n\t\n\tb.adf\n", b"")):
            with self.subTest(label):
                m3u = self.playlist(text, prefix=prefix)
                out = self.frontend("boot", "--runs", 2, content=m3u).stdout
                base = re.escape(str(m3u.parent))
                self.assertRegex(out, rf"(?m)^images count=2 index=0 eject=0\nimage 0 path={base}/a\.adf label=Disk A\nimage 1 path={base}/b\.adf label=b$")

    def test_an_absolute_path_and_the_same_disk_twice_in_a_playlist(self):
        elsewhere = self.work()
        (elsewhere / "far.adf").write_bytes(adf("far"))
        m3u = self.playlist(f"a.adf|first\n{elsewhere}/far.adf|far away\na.adf|again\n")
        out = self.frontend("boot", "--runs", 2, content=m3u).stdout
        self.assertEqual(out.count("log 1 [PUAE6] medium "), 2)                                # three entries, two distinct disks
        self.assertRegex(out, rf"(?m)^image 1 path={re.escape(str(elsewhere))}/far\.adf label=far away$")
        self.assertRegex(out, r"(?m)^image 2 path=.*/a\.adf label=again$")

    def test_a_playlist_that_cannot_be_loaded_fails_the_load_and_says_why(self):
        sixteen = "".join(f"d{i}.adf\n" for i in range(16))
        files = self.work()
        for name, size in (("short.adf", ADF_BYTES - 1), ("long.adf", ADF_BYTES + 1), ("huge.adf", 3 * ADF_BYTES), ("notes.txt", 1)):
            (files / name).write_bytes(bytes(size))
        cases = [
            ("seventeen", self.playlist(sixteen + "d16.adf\n", [f"d{i}.adf" for i in range(17)]), "has more than 16 disks (line 17)"),
            ("a missing file", self.playlist("a.adf\nnot-there.adf\n"), "disk 2 ("),
            ("an empty playlist", self.playlist("# nothing\n\n"), "lists no disks"),
            ("an entry without a file", self.playlist("a.adf\n|label\n"), "line 2: the entry has no file name"),
            ("a NUL byte", self.playlist("a.adf\nb\0.adf\n"), "line 2: a NUL byte is not text"),
            ("another format", self.playlist("a.adf\nb.d64\n", ["a.adf", "b.d64"]), "disk 2 ("),
            ("an IPF", self.playlist("a.adf\nb.ipf\n", ["a.adf", "b.ipf"]), "IPF images are not supported"),
            ("a path that is too long", self.playlist("a.adf\n" + "x" * 5000 + ".adf\n"), "line 2: the path is too long"),
            ("a huge playlist", self.playlist("#" * (70 * 1024) + "\na.adf\n"), "cannot be loaded: it is too large"),
            ("a short ADF", files / "short.adf", "is 901119 bytes, not an ADF of 901120"),
            ("a long ADF", files / "long.adf", "is 901121 bytes, not an ADF of 901120"),
            ("a huge ADF", files / "huge.adf", "is larger than any disk file this core reads"),
            ("a text file", files / "notes.txt", "is not content this core loads"),
            ("no such file", files / "nothing.adf", "cannot be opened"),
        ]
        for label, content, text in cases:
            with self.subTest(label):
                out = self.frontend("boot", "--runs", 2, content=content, expect=3).stdout
                self.assertIn("load refused", out)
                self.assertIn(text, " ".join(self.messages(out)))
        # Sixteen disks are the most a playlist may name.
        out = self.frontend("boot", "--runs", 2, content=self.playlist(sixteen, [f"d{i}.adf" for i in range(16)])).stdout
        self.assertIn("images count=16", out)

    def test_save_disks_in_a_playlist_are_ignored_and_the_disks_stay_read_only(self):
        m3u = self.playlist("#SAVEDISK:Save|disk.adf\na.adf|A\n")
        out = self.frontend("boot", "--runs", 2, content=m3u).stdout
        self.assertIn("log 2 [PUAE6] 1 #SAVEDISK line(s) of the playlist are ignored: the disks are read-only", out)
        self.assertIn("images count=1", out)

    def test_paths_with_unicode_and_very_long_paths_are_read_and_a_path_is_no_identity(self):
        deep = self.work()
        for _ in range(3):
            deep = deep / ("d" * 200)
        deep.mkdir(parents=True)
        unicode_dir = self.work() / "Amiga éè 日本語"
        unicode_dir.mkdir()
        (deep / "long.adf").write_bytes(adf("same"))
        (unicode_dir / "Pokémon 日本.adf").write_bytes(adf("same"))
        (unicode_dir / "list.m3u").write_text("Pokémon 日本.adf|Título 日本\n", encoding="utf-8")
        self.assertGreater(len(str(deep / "long.adf")), 600)
        seen = []
        for content in (deep / "long.adf", unicode_dir / "Pokémon 日本.adf", unicode_dir / "list.m3u"):
            for options in ((), ("--no-vfs",)):
                with self.subTest(content=content.name[:20], options=options):
                    out = self.frontend("observer", "--runs", 4, *options, content=content).stdout
                    self.assertIn(f"medium 0 sha256={hashlib.sha256(adf('same')).hexdigest()}", out)
                    seen.append([(r["hash"], r["px"], r["audio"]) for r in self.runs(out)])
        self.assertIn("label=Título 日本", self.frontend("boot", "--runs", 1, content=unicode_dir / "list.m3u").stdout)
        self.assertEqual(len({str(run) for run in seen}), 1)          # three paths, two spellings of the same bytes: one machine

    def test_the_vfs_and_stdio_paths_give_identical_runs_and_a_hostile_size_is_not_believed(self):
        m3u = self.playlist("a.adf|A\nb.adf|B\n")
        vfs = self.frontend("observer", "--runs", 30, "--disk", "10:eject", content=m3u).stdout
        stdio = self.frontend("observer", "--runs", 30, "--disk", "10:eject", "--no-vfs", content=m3u).stdout
        self.assertNotIn("vfs open", stdio)
        self.assertEqual(len(re.findall(r"(?m)^vfs open ", vfs)), 4)                         # the playlist, two disks and the Kickstart
        strip = lambda text: re.sub(r"(?m)^(vfs open .*\n|log 1 \[PUAE6\] Kickstart .*\n|final .*\n)", "", text)
        self.assertEqual(strip(vfs), strip(stdio))
        self.assertIn("read through the frontend VFS", vfs)
        self.assertIn("read with stdio", stdio)
        out = self.frontend("boot", "--vfs-claim", 1 << 40, content=m3u, expect=3).stdout          # the frontend says every file has a terabyte
        self.assertIn("load refused", out)

    def test_the_same_disk_under_any_path_or_name_is_the_same_medium(self):
        ids = []
        for name in ("one.adf", "Another Name.ADF", "sub/dir/third.adf"):
            path = self.work() / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(adf("same disk"))
            out = self.frontend("observer", "--runs", 6, content=path).stdout
            ids.append((re.search(r"medium 0 sha256=(\w+)", out)[1], [(r["hash"], r["px"], r["audio"]) for r in self.runs(out)]))
        self.assertEqual(len({str(i) for i in ids}), 1)
        other = self.work() / "one.adf"
        other.write_bytes(adf("other disk"))
        self.assertNotEqual(re.search(r"medium 0 sha256=(\w+)", self.frontend("boot", "--runs", 1, content=other).stdout)[1], ids[0][0])

    # --- T17a: Disk Control: eject, choose, insert; recorded by the callback and applied at the next unit boundary -----------------------

    def disks(self):
        return self.playlist("a.adf|Disk A\nb.adf|Disk B\nc.adf|Disk C\n", ["a.adf", "b.adf", "c.adf"])

    def disk_run(self, *options, runs=140, rom="observer", m3u=None, **kwargs):
        return self.frontend(rom, "--runs", runs, *options, content=m3u or self.disks(), **kwargs).stdout

    @staticmethod
    def drive_changes(out):
        """(run, colour) each time the colour of the middle of the picture changes: the drive's status as the observer program shows it."""
        changes, last = [], None
        for run in LibretroTests.runs(out):
            if run["px"] != last:
                changes.append((run["n"], run["px"]))
                last = run["px"]
        return changes

    def events(self, out):
        """(run, text) for each 'DF0:' line the core logged while that run was being made."""
        found, pending = [], []
        for line in out.splitlines():
            if m := re.match(r"log \d+ \[PUAE6\] (DF0: .*)$", line):
                pending.append(m[1])
            elif m := re.match(r"run (\d+) ", line):
                found += [(int(m[1]), text) for text in pending]
                pending = []
        return found

    def test_eject_choose_insert_changes_the_drive_at_the_next_boundaries(self):
        out = self.disk_run("--disk", "10:eject", "--disk", "15:index=1", "--disk", "16:insert")
        self.assertEqual(self.events(out), [(1, "DF0: medium 0 inserted"), (10, "DF0: ejected"), (16, "DF0: medium 1 inserted")])
        changes = self.drive_changes(out)
        present, empty = changes[0][1], changes[1][1]
        self.assertNotEqual(present, empty)
        # The picture of unit 1 is the program's first: it settles in unit 2. The eject is immediate at the boundary of run 10; the engine's delayed
        # change lands about a hundred units after the insert of run 16 (unit 117).
        self.assertEqual([c for c in changes if c[0] >= 3], [(10, empty), (117, present)])
        self.assertIn("disk 10 eject result=1 eject=1 index=0 count=3", out)
        self.assertIn("disk 15 index=1 result=1 eject=1 index=1 count=3", out)
        self.assertIn("disk 16 insert result=1 eject=0 index=1 count=3", out)

    def test_the_same_sequence_through_the_state_probe_lands_in_the_same_units(self):
        out = self.disk_run("--disk", "10:eject", "--disk", "15:index=1", "--disk", "16:insert")
        directory = self.work()
        for name in "abc":
            (directory / f"{name}.adf").write_bytes(adf(f"{name}.adf"))
        # The probe queues an event after unit N and the next unit applies it: the adapter's run 10 is "after unit 9".
        probe = subprocess.run([str(self.probe), str(self.roms["observer"]), "--units", "140", "--every", "1", "--adf", directory / "a.adf",
                                "--adf", directory / "b.adf", "--adf", directory / "c.adf", "--event-at", "0", "0", "0", "1",
                                "--event-at", "9", "0", "-1", "2", "--event-at", "15", "0", "1", "3"], capture_output=True, text=True, timeout=600)
        self.assertEqual(probe.returncode, 0, probe.stderr[-400:])
        pictures = {int(m[1]): m[2] for m in re.finditer(r"(?m)^digest (\d+) video (\w+) ", probe.stdout)}
        frames = {int(m[1]): int(m[2]) for m in re.finditer(r"(?m)^pcm (\d+) \w+ (\d+)$", probe.stdout)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), list(range(1, 141)))
        for n in runs:
            self.assertEqual(runs[n]["audio"], frames[n], f"unit {n}")
        # Same picture in the probe exactly where it is the same in the core (the digests are not the core's hashes, the partition is what is compared).
        for a in range(1, 141, 3):
            for b in range(2, 141, 7):
                self.assertEqual(pictures[a] == pictures[b], runs[a]["hash"] == runs[b]["hash"], f"units {a} and {b}")
        self.assertEqual([n for n in range(3, 141) if pictures[n] != pictures[n - 1]], [10, 117])

    def test_a_request_made_inside_a_callback_waits_for_the_next_boundary(self):
        between = self.disk_run("--disk", "11:eject", "--disk", "11:index=2", "--disk", "12:insert")
        inside = self.disk_run("--disk-during", "10:eject", "--disk-during", "10:index=2", "--disk", "12:insert")
        self.assertEqual(self.events(inside), self.events(between))
        self.assertEqual([(r["hash"], r["px"], r["audio"]) for r in self.runs(inside)], [(r["hash"], r["px"], r["audio"]) for r in self.runs(between)])
        self.assertIn((11, "DF0: ejected"), self.events(inside))

    def test_opening_and_closing_the_tray_on_the_same_disk_is_still_a_disk_change(self):
        out = self.disk_run("--disk", "5:eject", "--disk", "5:insert")
        self.assertEqual(self.events(out), [(1, "DF0: medium 0 inserted"), (5, "DF0: ejected"), (5, "DF0: medium 0 inserted")])
        changes = self.drive_changes(out)
        self.assertEqual([c[0] for c in changes if c[0] >= 3], [5, 106])               # empty at once, the same disk again after the delayed insert
        self.assertEqual(changes[0][1], changes[-1][1])

    def test_the_rules_of_the_tray_the_frontend_can_rely_on(self):
        out = self.disk_run("--disk", "3:index=1", "--disk", "4:insert", "--disk", "5:eject", "--disk", "5:eject", "--disk", "6:index=7",
                            "--disk", "7:insert", runs=12)
        self.assertIn("disk 3 index=1 result=0 eject=0 index=0 count=3", out)             # the image is chosen only with the tray open
        self.assertIn("disk 4 insert result=1 eject=0 index=0 count=3", out)             # closing a closed tray: nothing to do, true
        self.assertIn("disk 5 eject result=1 eject=1 index=0 count=3", out)
        self.assertIn("disk 6 index=7 result=1 eject=1 index=3 count=3", out)             # past the last image: no disk, reported as the count
        self.assertIn("disk 7 insert result=1 eject=0 index=3 count=3", out)
        self.assertEqual(self.events(out), [(1, "DF0: medium 0 inserted"), (5, "DF0: ejected")])        # and the drive stays empty
        self.assertIn("log 2 [PUAE6] the image can only be chosen while the tray is open", out)

    def test_images_can_leave_the_list_and_new_ones_come_in_at_the_end_with_the_tray_open(self):
        # Adding and replacing with a file are T17e's (tests/libretro-host/test_media_append.py); here, what the list does around them.
        elsewhere = self.work() / "new.adf"
        elsewhere.write_bytes(adf("new"))
        out = self.disk_run("--disk", "3:remove=1", "--disk", "4:eject", "--disk", "5:remove=1", "--disk", "5:replace=0:" + str(elsewhere),
                            "--disk", "5:add", "--disk", "6:remove=9", runs=8)
        self.assertIn("disk 3 remove=1 result=0 eject=0 index=0 count=3", out)             # the tray must be open
        self.assertIn("disk 5 remove=1 result=1 eject=1 index=0 count=2", out)             # c.adf is image 1 now
        self.assertIn(f"disk 5 replace=0:{elsewhere} result=1 eject=1 index=0 count=2", out)      # image 0 is now the new disk
        self.assertIn("disk 5 add result=1 eject=1 index=0 count=3", out)                  # an empty slot at the end
        self.assertIn("disk 6 remove=9 result=0 eject=1 index=0 count=3", out)
        self.assertIn("log 1 [PUAE6] image 0: medium 3, \"new\"", out)                      # registered at the end of the manifest (a, b and c came first)

    def test_the_image_the_frontend_remembers_is_the_one_that_boots(self):
        m3u = self.playlist("a.adf|A\nb.adf|B\nc.adf|C\n", ["a.adf", "b.adf", "c.adf"])
        base = str(m3u.parent)
        out = self.frontend("observer", "--runs", 3, "--initial", f"2:{base}/c.adf", content=m3u).stdout
        self.assertIn("initial result=1", out)
        self.assertIn("images count=3 index=2 eject=0", out)
        self.assertEqual(self.events(out), [(1, "DF0: medium 2 inserted")])
        for label, spec in (("another path", f"2:{base}/a.adf"), ("an index past the list", f"9:{base}/c.adf"), ("another playlist", f"1:/elsewhere/b.adf")):
            with self.subTest(label):
                out = self.frontend("observer", "--runs", 3, "--initial", spec, content=m3u).stdout
                self.assertIn("images count=3 index=0 eject=0", out)                           # ignored: the first image
                self.assertIn("is not in the content: starting with the first", out)

    def test_an_older_frontend_gets_the_legacy_interface_and_a_message_without_the_extended_one(self):
        out = self.disk_run("--legacy-disk-control", "--legacy-message", "--disk", "10:eject", "--disk", "12:index=1", "--disk", "13:insert", runs=20)
        self.assertNotIn("image 0 path=", out)                                              # the legacy interface has no paths or labels
        self.assertIn("images count=3 index=0 eject=0", out)
        self.assertIn("disk 13 insert result=1 eject=0 index=1 count=3", out)
        self.assertEqual(self.events(out)[1:], [(10, "DF0: ejected"), (13, "DF0: medium 1 inserted")])
        bad = self.frontend("boot", "--legacy-message", "--legacy-disk-control", "--initial", "0:x", content=self.work() / "missing.adf", expect=3).stdout
        self.assertIn("initial unsupported", bad)
        self.assertRegex(bad, r"(?m)^message PUAE6: disk 1 \(.*missing\.adf\): it cannot be opened$")

    def test_content_and_disk_changes_repeat_exactly_over_load_unload_cycles_and_processes(self):
        m3u = self.disks()
        steps = ("--disk", "10:eject", "--disk", "12:index=2", "--disk", "13:insert", "--cycles", 3)
        cycles = [m.groups() for m in CYCLE.finditer(self.disk_run(*steps, runs=130, m3u=m3u))]
        self.assertEqual(len(cycles), 3)
        self.assertEqual(len({c[1:] for c in cycles}), 1, cycles)
        again = [m.groups() for m in CYCLE.finditer(self.disk_run(*steps, runs=130, m3u=m3u))]
        self.assertEqual(again, cycles)


    # --- T17b: the core options come from the schema; a reboot option needs a reload, a presentation option does not -------------------

    OPTION = re.compile(r"^option (\S+) default=(\S*) values=(\S*) category=(\S*) desc=(.*)$", re.M)
    AV = re.compile(r"^av(?:-after \d+)? base=.*$", re.M)

    @staticmethod
    def schema_options():
        schema = generate_options.load_schema()
        return schema, [o for o in schema["options"] if o["libretro"]]

    def av(self, out):
        """The av line (or the lines, one per load) without the after-run prefix, so that two of them can be compared."""
        return [re.sub(r"^av(-after \d+)?", "av", line) for line in self.AV.findall(out)]

    def machine(self, out):
        """What a session did, whatever the options said: the units' ticks, pictures and audio."""
        return ([m[2] for m in TICKS.finditer(out)], [(r["hash"], r["px"], r["audio"]) for r in self.runs(out)])

    def test_the_options_are_offered_through_v2_v1_and_the_legacy_interface_as_the_schema_says(self):
        schema, exposed = self.schema_options()
        # T49: the preset is offered right after the model; it is no option of the schema (it lays the values of a preset over the model and the memory).
        preset = {"libretro": {"key": "puae6_preset", "category": "machine"}, "default": "none", "class": "reboot",
                  "values": [{"value": "none"}] + [{"value": entry["id"]} for entry in schema["presets"]]}
        exposed = exposed[:1] + [preset] + exposed[1:]
        reference = None
        for api in (2, 1, 0):
            with self.subTest(api=api):
                out = self.frontend("boot", "--runs", 3, "--options-api", api).stdout
                offered = self.OPTION.findall(out)
                self.assertEqual([o[0] for o in offered], [o["libretro"]["key"] for o in exposed])
                for (key, default, values, category, desc), option in zip(offered, exposed):
                    self.assertEqual(default, option["default"], key)
                    self.assertEqual(sorted(values.split("|")), sorted(v["value"] for v in option["values"]), key)
                    self.assertEqual(desc.endswith("(Restart)"), option["class"] == "reboot", desc)      # a reboot option says so in the menu
                    if api == 2:
                        self.assertEqual(category, option["libretro"]["category"], key)
                self.assertIn(f"options api={api} count={len(exposed)} categories={len(schema['categories']) if api == 2 else 0}", out)
                if api == 2:
                    self.assertEqual(re.findall(r"(?m)^category (\S+)$", out), [c["id"] for c in schema["categories"]])
                # The frontend holds the defaults: the same machine whichever way the options were offered.
                self.assertEqual(self.machine(out)[1], self.machine(self.frontend("boot", "--runs", 3).stdout)[1])
                reference = reference or out
        self.assertIn("rate=48000 region=1", reference)

    def test_a_frontend_without_core_options_gets_the_defaults_of_the_schema(self):
        out = self.frontend("boot", "--runs", 6, "--no-options").stdout
        self.assertNotIn("option puae6_", out)
        self.assertRegex(out, r"(?m)^av base=756x574 .* fps=50\.08\d* rate=48000 region=1$")
        self.assertEqual(self.machine(out), self.machine(self.frontend("boot", "--runs", 6).stdout))

    def test_ntsc_selected_by_option_is_the_machine_headless_runs_with_ntsc(self):
        for rom, units in (("boot", 20), ("long_frame", 14)):
            with self.subTest(rom=rom):
                out = self.frontend(rom, "--runs", units, "--set", "puae6_video_standard=NTSC").stdout
                headless = self.headless_units(rom, units, "--ntsc")
                ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
                runs = {r["n"]: r for r in self.runs(out)}
                self.assertEqual(sorted(runs), sorted(headless))
                for n, expected in headless.items():
                    self.assertEqual((ticks[n], runs[n]["audio"]), (expected["ticks"], expected["audio"]), f"unit {n}")
                    if expected["video"]:
                        self.assertEqual((runs[n]["size"], runs[n]["hash"]), (expected["size"], expected["hash"]), f"unit {n}")
                    else:
                        self.assertTrue(runs[n]["dupe"], f"unit {n}")
                self.assertEqual(runs[1]["size"], (756, 484))
        av = re.search(r"^av base=(\d+)x(\d+) max=\d+x\d+ aspect=([\d.]+) fps=([\d.]+) rate=(\d+) region=(\d+)$", out, re.M)
        self.assertEqual((av[1], av[2], av[5], av[6]), ("756", "484", "48000", "0"))          # RETRO_REGION_NTSC is 0
        self.assertAlmostEqual(float(av[4]), 715909 / 11921, places=4)
        self.assertAlmostEqual(float(av[3]), 756 * 43 / (484 * 50), places=4)
        pal = self.frontend("long_frame", "--runs", 14).stdout
        self.assertNotEqual(self.machine(out), self.machine(pal))                                  # the option really changed the machine

    def probe_frames(self, rom, units, *flags):
        result = subprocess.run([str(self.probe), str(self.roms[rom]), "--units", str(units), "--every", "1", *flags], capture_output=True, text=True,
                                timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        return {int(m[1]): int(m[2]) for m in re.finditer(r"(?m)^pcm (\d+) \w+ (\d+)$", result.stdout)}

    def test_44100_selected_by_option_is_the_audio_the_state_probe_makes_at_that_rate_and_changes_nothing_else(self):
        # p6_headless has no way to select the audio rate (it is fixed at 48000, which tests/config/test_parity.py checks against the schema), so the
        # reference for the sample count is p6_state_probe --rate, which drives the same p6_run_frame; ticks and pictures do not depend on the rate.
        out = self.frontend("boot", "--runs", 20, "--set", "puae6_audio_rate=44100").stdout
        default = self.frontend("boot", "--runs", 20).stdout
        frames = self.probe_frames("boot", 20, "--rate", "44100")
        self.assertEqual({r["n"]: r["audio"] for r in self.runs(out)}, frames)
        self.assertNotEqual([r["audio"] for r in self.runs(out)], [r["audio"] for r in self.runs(default)])
        self.assertEqual(self.machine(out)[0], self.machine(default)[0])
        self.assertEqual([(r["hash"], r["px"]) for r in self.runs(out)], [(r["hash"], r["px"]) for r in self.runs(default)])
        self.assertIn("rate=44100 region=1", out)
        # Both options together: NTSC is the headless picture and ticks, 44100 the probe's audio.
        both = self.frontend("boot", "--runs", 20, "--set", "puae6_video_standard=NTSC", "--set", "puae6_audio_rate=44100").stdout
        self.assertEqual({r["n"]: r["audio"] for r in self.runs(both)}, self.probe_frames("boot", 20, "--ntsc", "--rate", "44100"))
        headless = self.headless_units("boot", 20, "--ntsc")
        self.assertEqual({int(m[1]): int(m[2]) for m in TICKS.finditer(both)}, {n: u["ticks"] for n, u in headless.items()})
        self.assertEqual({r["n"]: r["hash"] for r in self.runs(both)}, {n: u["hash"] for n, u in headless.items()})
        self.assertIn("rate=44100 region=0", both)

    def test_a_reboot_option_changed_while_the_machine_runs_is_reported_and_changes_nothing(self):
        base = self.frontend("boot", "--runs", 14).stdout
        changed = self.frontend("boot", "--runs", 14, "--set-at", "6:puae6_video_standard=NTSC", "--set-at", "7:puae6_audio_rate=44100").stdout
        # The run, the announced av info and the region are those of the machine that is running; the host also checks after every run that
        # the serialization size did not move (the state bound hangs from the same identity).
        self.assertEqual(self.machine(changed), self.machine(base))
        self.assertEqual(CYCLE.search(changed).groups(), CYCLE.search(base).groups())
        self.assertEqual(self.av(changed), self.av(base) * 3)                                    # the line at load and the one after each change
        notes = self.messages(changed)
        self.assertEqual(notes, ["PUAE6: puae6_video_standard is now NTSC but the machine runs with PAL: reload the content to apply it",
                                 "PUAE6: puae6_audio_rate is now 44100 but the machine runs with 48000: reload the content to apply it"])
        self.assertRegex(changed, r"(?m)^log 2 \[PUAE6\] puae6_video_standard is now NTSC")
        self.assertEqual(self.messages(base), [])

    def test_a_reboot_option_takes_effect_with_the_next_load_and_then_is_the_machine_a_fresh_process_would_have(self):
        steps = ("--set-at", "6:puae6_video_standard=NTSC", "--set-at", "7:puae6_audio_rate=44100")
        out = self.frontend("boot", "--runs", 14, *steps, "--cycles", 2).stdout
        cycles = [m.groups() for m in CYCLE.finditer(out)]
        self.assertEqual(len(cycles), 2)
        self.assertNotEqual(cycles[0][1:], cycles[1][1:])
        avs = re.findall(r"(?m)^av base=.*$", out)
        self.assertEqual(len(avs), 2)
        self.assertTrue(avs[0].endswith("rate=48000 region=1") and avs[1].endswith("rate=44100 region=0"), avs)
        fresh = self.frontend("boot", "--runs", 14, "--set", "puae6_video_standard=NTSC", "--set", "puae6_audio_rate=44100").stdout
        self.assertEqual(CYCLE.search(fresh).groups()[1:], cycles[1][1:])                        # same runs, audio frames and PCM stream
        self.assertEqual(len(self.messages(out)), 2)                                             # the second load had nothing left to report

    def test_a_presentation_option_applies_at_once_and_does_not_touch_the_machine(self):
        base = self.frontend("boot", "--runs", 12).stdout
        out = self.frontend("boot", "--runs", 12, "--set-at", "5:puae6_video_aspect=4:3").stdout
        self.assertEqual(self.machine(out), self.machine(base))
        self.assertEqual(CYCLE.search(out).groups(), CYCLE.search(base).groups())
        geometry = re.findall(r"(?m)^geometry (\d+x\d+) aspect=([\d.]+)$", out)
        self.assertEqual(geometry, [("756x574", "1.369756"), ("756x574", "1.333333")])           # announced again with the first picture after the change
        self.assertLess(out.index("set 5 "), out.index("geometry 756x574 aspect=1.333333"))
        self.assertIn("aspect=1.333333 fps=50.080411 rate=48000 region=1", out)                  # av-after: the new ratio, the same timing
        self.assertEqual(self.messages(out), [])                                                 # nothing to reload
        chosen = self.frontend("boot", "--runs", 3, "--set", "puae6_video_aspect=4:3").stdout
        self.assertRegex(chosen, r"(?m)^av base=756x574 .* aspect=1\.333333 fps=50\.08")
        self.assertEqual(self.machine(chosen)[1], self.machine(base)[1][:3])

    def test_a_value_the_schema_does_not_allow_fails_the_load_naming_the_key_and_the_value(self):
        for key, value, allowed in (("puae6_video_standard", "SECAM", "PAL, NTSC"), ("puae6_video_standard", "pal", "PAL, NTSC"),
                                    ("puae6_video_standard", "", "PAL, NTSC"), ("puae6_audio_rate", "22050", "44100, 48000"),
                                    ("puae6_video_aspect", "wide", "pixel, 4:3")):
            with self.subTest(key=key, value=value):
                result = self.frontend("boot", "--set", f"{key}={value}", expect=3)
                text = f'the option {key} has the value "{value}", which this core does not support (allowed: {allowed})'
                self.assertIn(f"message PUAE6: {text}", result.stdout)
                self.assertIn(f"log 3 [PUAE6] {text}", result.stdout)
                self.assertIn("load refused", result.stdout)
                self.assertNotIn("run 1 ", result.stdout)
                self.assertNotIn("vfs open", result.stdout)                                      # refused before the Kickstart was looked at

    def test_a_stale_value_that_arrives_while_the_machine_runs_is_ignored_with_a_message(self):
        base = self.frontend("boot", "--runs", 10).stdout
        out = self.frontend("boot", "--runs", 10, "--set-at", "5:puae6_audio_rate=22050").stdout
        self.assertEqual(self.machine(out), self.machine(base))
        self.assertEqual(self.messages(out), ['PUAE6: the option puae6_audio_rate has the value "22050", which this core does not support (allowed: '
                                              '44100, 48000); it is ignored while the machine runs, and loading the content with it would fail'])


# --- the A600 with the Kickstart 2.05 and the Workbench 2.05 the owner supplies (opt-in) ---------------------------------------------------------------

KICKSTART_A600_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WORKBENCH_205_ZIP = os.environ.get("P6_WB205_ZIP")


def zip_member(path, suffix, size):
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(suffix)]
        if len(names) != 1 or archive.getinfo(names[0]).file_size != size:
            raise unittest.SkipTest(f"{path}: expected one {suffix} of {size} bytes")
        return archive.read(names[0])


@unittest.skipUnless(TOOLS and KICKSTART_A600_ZIP and WORKBENCH_205_ZIP,
                     "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP (zips with the Kickstart 2.05 .rom and the Workbench 2.05 .adf) to run the A600 with real firmware")
class RealA600Tests(LibretroHarness):
    """LR01 on the A600 with the real Kickstart 2.05: the core finds it under the name PUAE5 gives it, takes the Workbench disk as the content (the zip itself, as a
    frontend hands it over) and every unit is the one p6_headless makes with the same ROM and disk. The firmware is read in memory and written to a temporary
    directory outside the repository that is removed at the end; nothing of it is copied into the repository, hashed or printed."""

    UNITS = int(os.environ.get("P6_A600_UNITS", "1800"))      # the Workbench shell is up by unit ~1550 (tests/step/test_kickstart_a600.py)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rom_bytes = zip_member(KICKSTART_A600_ZIP, ".rom", 524288)
        cls.adf_bytes = zip_member(WORKBENCH_205_ZIP, ".adf", ADF_BYTES)
        cls.rom_file = Path(cls.tmp.name) / "a600-kickstart.rom"
        cls.rom_file.write_bytes(cls.rom_bytes)
        cls.adf_file = Path(cls.tmp.name) / "a600-workbench.adf"
        cls.adf_file.write_bytes(cls.adf_bytes)

    def a600_system(self, name=KICKSTART_NAMES_A600[0]):
        path = self.work()
        (path / name).write_bytes(self.rom_bytes)
        return path

    def headless_real(self, units, with_disk):
        arguments = [str(self.headless), "--run", str(self.rom_file), "--machine", "a600", "--units", str(units)]
        if with_disk:
            arguments += ["--disk", str(self.adf_file)]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=1800)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        return {int(m[1]): (int(m[2]), int(m[3]), m[6], int(m[7])) for m in UNIT.finditer(result.stdout)}

    def test_the_real_kickstart_2_05_is_found_by_the_name_of_puae5_and_every_unit_is_the_one_headless_makes(self):
        out = self.frontend("boot", "--runs", self.UNITS, *A600, system=self.a600_system(), content=Path(WORKBENCH_205_ZIP)).stdout
        self.assertIn("machine A600 (puae6_machine)", out)
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A600[0]}, read through the frontend VFS", out)
        expected = self.headless_real(self.UNITS, True)
        ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), sorted(expected))
        for unit, (expected_ticks, video, picture, audio) in expected.items():
            self.assertEqual((ticks[unit], runs[unit]["audio"]), (expected_ticks, audio), f"unit {unit}")
            if video:
                self.assertEqual(runs[unit]["hash"], picture, f"unit {unit}")
        self.assertGreater(len({r["hash"] for r in runs.values()}), 4, "the picture hardly changed")

    def test_without_content_the_a600_waits_for_a_disk_and_the_collection_name_of_puae5_serves_too(self):
        out = self.frontend("boot", "--runs", 60, *A600, system=self.a600_system(KICKSTART_NAMES_A600[1])).stdout
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A600[1]},", out)
        expected = self.headless_real(60, False)
        ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
        self.assertEqual(ticks, {unit: value[0] for unit, value in expected.items()})

    def test_a_state_taken_while_the_real_kickstart_loads_the_disk_replays_the_same_units(self):
        content = Path(WORKBENCH_205_ZIP)
        first = self.frontend("boot", "--runs", 360, *A600, "--do", "200:save:real", "--do", "280:load:real", system=self.a600_system(),
                              content=content).stdout
        runs = [(r["unit"], r["ticks"], r["audio"], r["ahash"], r["hash"]) for r in self.runs(first)]
        seen = {}
        for run in runs:
            seen.setdefault(run[0], []).append(run)
        for unit in range(201, 281):
            self.assertEqual(len(seen[unit]), 2, unit)
            self.assertEqual(seen[unit][0], seen[unit][1], f"unit {unit} after the load")


# --- the A1200 with the Kickstart 3.1 and an AGA demo the owner supplies (opt-in, T24e) ------------------------------------------------------------------

KICKSTART_A1200_ROM = os.environ.get("P6_KICKSTART_A1200_ROM")
AGA_ADF = os.environ.get("P6_AGA_ADF")
AGA_DMS = os.environ.get("P6_AGA_DMS")


@unittest.skipUnless(TOOLS and KICKSTART_A1200_ROM and AGA_ADF,
                     "BLOCKED: set P6_KICKSTART_A1200_ROM (the raw 512 KiB Kickstart 3.1 r40.68 of the A1200) and P6_AGA_ADF (an ADF of a free AGA demo) to run the A1200 with real firmware")
class RealA1200Tests(LibretroHarness):
    """LR01 on the A1200 with the real Kickstart 3.1: the core finds it under the TOSEC name of the project's authorized copy, takes the AGA demo as the content and
    every unit is the one p6_headless --machine a1200 makes with the same ROM and disk (tests/step/test_kickstart_a1200.py is what the machine itself shows). The
    firmware is read where it is and written, under its known name, to a temporary directory outside the repository that is removed at the end; nothing of it is
    copied into the repository, hashed or printed."""

    UNITS = int(os.environ.get("P6_A1200_UNITS", "2200"))          # the first AGA scenes of Catabasis are on the screen by unit ~2000 (tests/step/test_kickstart_a1200.py)
    DMS_UNITS = int(os.environ.get("P6_A1200_DMS_UNITS", "1500"))

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.path.getsize(KICKSTART_A1200_ROM) != 524288:
            raise unittest.SkipTest("P6_KICKSTART_A1200_ROM is not a 512 KiB image")
        if os.path.getsize(AGA_ADF) != ADF_BYTES:
            raise unittest.SkipTest("P6_AGA_ADF is not a 901120-byte ADF")
        cls.rom_bytes = Path(KICKSTART_A1200_ROM).read_bytes()
        cls.rom_file = Path(cls.tmp.name) / "a1200-kickstart.rom"
        cls.rom_file.write_bytes(cls.rom_bytes)

    def a1200_system(self, name=KICKSTART_NAMES_A1200[4]):
        path = self.work()
        (path / name).write_bytes(self.rom_bytes)
        return path

    def headless_real(self, units, disk):
        arguments = [str(self.headless), "--run", str(self.rom_file), "--machine", "a1200", "--units", str(units)]
        if disk:
            arguments += ["--disk", disk]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=3600)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        return {int(m[1]): (int(m[2]), int(m[3]), m[6], int(m[7])) for m in UNIT.finditer(result.stdout)}

    def assert_same_units(self, out, expected):
        ticks = {int(m[1]): int(m[2]) for m in TICKS.finditer(out)}
        runs = {r["n"]: r for r in self.runs(out)}
        self.assertEqual(sorted(runs), sorted(expected))
        for unit, (expected_ticks, video, picture, audio) in expected.items():
            self.assertEqual((ticks[unit], runs[unit]["audio"]), (expected_ticks, audio), f"unit {unit}")
            if video:
                self.assertEqual(runs[unit]["hash"], picture, f"unit {unit}")
        return runs

    def test_the_real_kickstart_3_1_is_found_by_its_collection_name_and_every_unit_with_the_aga_demo_is_the_one_headless_makes(self):
        out = self.frontend("boot", "--runs", self.UNITS, *A1200, system=self.a1200_system(), content=Path(AGA_ADF)).stdout
        self.assertIn("machine A1200 (puae6_machine)", out)
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A1200[4]}, read through the frontend VFS", out)
        self.assertIn("DF0: medium 0 inserted", out)
        runs = self.assert_same_units(out, self.headless_real(self.UNITS, AGA_ADF))
        self.assertGreater(len({r["hash"] for r in runs.values()}), 4, "the picture hardly changed")

    def test_without_content_the_a1200_waits_for_a_disk_and_the_name_of_puae5_serves_too(self):
        out = self.frontend("boot", "--runs", 120, *A1200, system=self.a1200_system(KICKSTART_NAMES_A1200[0])).stdout
        self.assertIn(f"Kickstart {KICKSTART_NAMES_A1200[0]},", out)
        self.assert_same_units(out, self.headless_real(120, None))

    def test_the_other_machines_do_not_boot_with_the_a1200_kickstart_and_the_a1200_does_not_boot_with_theirs(self):
        # A system directory with only the 3.1 of the A1200 (under its own name) leaves the A500 and the A600 without a Kickstart, and the other way round.
        directory = self.a1200_system(KICKSTART_NAMES_A1200[0])
        for options, wanted in (((), "1.3"), (A600, "2.05")):
            out = self.frontend("boot", *options, system=directory, expect=3).stdout
            self.assertIn(f"no Kickstart {wanted} ROM in the system directory ", " ".join(LibretroTests.messages(out)))
        wrong = self.system_for("boot", KICKSTART_NAMES[0], KICKSTART_NAMES_A600[0])
        out = self.frontend("boot", *A1200, system=wrong, expect=3).stdout
        self.assertIn("no Kickstart 3.1 ROM in the system directory ", " ".join(LibretroTests.messages(out)))

    @unittest.skipUnless(AGA_DMS, "BLOCKED: set P6_AGA_DMS to run the DMS of a free demo on the A1200")
    def test_a_dms_disk_is_opened_by_the_core_and_the_a1200_leaves_the_insert_disk_screen(self):
        system = self.a1200_system()
        out = self.frontend("boot", "--runs", self.DMS_UNITS, *A1200, system=system, content=Path(AGA_DMS)).stdout
        self.assertIn("DF0: medium 0 inserted", out)
        empty = self.frontend("boot", "--runs", self.DMS_UNITS, *A1200, system=system).stdout
        shown = {r["hash"] for r in self.runs(empty)[200:]}
        self.assertFalse({r["hash"] for r in self.runs(out)[-100:]} & shown, "the machine still shows a picture of the insert-disk screen")
        self.assertGreater(len({r["hash"] for r in self.runs(out)[-100:]}), 1, "the last 100 units show one frozen picture")


if __name__ == "__main__":
    unittest.main()
