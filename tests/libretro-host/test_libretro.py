"""LR01 (and LIFE01, STEP01, AV01, AV02 through the core): the libretro adapter, run by a minimal frontend that loads it like a real one.

tests/libretro-host/host.cpp dlopens build/link-audit/puae6_libretro and enforces the contract on every retro_run (one input poll,
exactly one picture, whole stereo audio frames, a serialization size that does not grow). The p6_headless runner is the other
adapter over the same p6_run_frame: the same ROM, seed and input must give the same ticks, the same picture and the same audio
unit for unit. Run: python3 -m unittest discover -s tests/libretro-host
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.host.test_bridge_lifecycle import TOOLS, build_probe, builder  # noqa: E402

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
EXPORTS = [line[1:] for line in (ROOT / "adapters/libretro/exports.txt").read_text().split()]
RUN = re.compile(r"^run (\d+) video=(\d+)x(\d+) pitch=(\d+) dupe=(\d) hash=(\w+) audio=(\d+) polls=(\d+) states=(\d+)$", re.M)
UNIT = re.compile(r"^unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=(\w+) audio=(\d+)$", re.M)
TICKS = re.compile(r"^log \d+ \[PUAE6\] unit=(\d+) ticks=(\d+) ports=(\S+)$", re.M)
CYCLE = re.compile(r"^cycle (\d+) end runs=(\d+) audio_frames=(\d+) audio_hash=(\w+)$", re.M)


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class LibretroTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frontend(self, rom="boot", *options, expect=0):
        result = subprocess.run([str(self.host), str(self.core), str(self.roms[rom]), *map(str, options)],
                                capture_output=True, text=True, timeout=600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(result.returncode, expect, result.stdout[-800:] + result.stderr[-800:])
        if expect == 0:
            self.assertNotIn("VIOLATION", result.stdout)
        return result

    def headless_units(self, rom, units):
        result = subprocess.run([str(self.headless), "--run", str(self.roms[rom]), "--units", str(units)], capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {int(m[1]): {"ticks": int(m[2]), "video": int(m[3]), "size": (int(m[4]), int(m[5])), "hash": m[6], "audio": int(m[7])}
                for m in UNIT.finditer(result.stdout)}

    @staticmethod
    def runs(text):
        return [{"n": int(m[1]), "size": (int(m[2]), int(m[3])), "pitch": int(m[4]), "dupe": int(m[5]), "hash": m[6], "audio": int(m[7]),
                 "polls": int(m[8]), "states": int(m[9])} for m in RUN.finditer(text)]

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
        self.assertTrue(all(r["polls"] == 1 and r["states"] == 14 for r in runs))             # 7 buttons on each of the 2 ports
        self.assertTrue(all(r["size"] == (756, 574) and r["pitch"] >= 756 * 4 for r in runs))
        av = re.search(r"^av base=(\d+)x(\d+) max=(\d+)x(\d+) aspect=([\d.]+) fps=([\d.]+) rate=(\d+) region=(\d+)$", out, re.M)
        self.assertEqual((av[1], av[2], av[3], av[4], av[7], av[8]), ("756", "574", "760", "576", "48000", "1"))   # region 1 is PAL
        self.assertAlmostEqual(float(av[6]), 3546895 / 70824, places=4)
        self.assertAlmostEqual(float(av[5]), 756 * 26 / (574 * 25), places=4)
        self.assertRegex(out, r"(?m)^core name=PUAE6 version=0\.1\.0-winuae-[0-9a-f]{12}-p\d+ extensions=rom\|bin need_fullpath=0$")
        self.assertIn("10", re.search(r"environment=([\d,]+)", out)[1].split(","))            # the pixel format was negotiated

    def test_the_input_of_the_frontend_reaches_the_bridge_as_the_contract_maps_it(self):
        # libretro joypad ids: B 0, A 8, X 9, UP 4, RIGHT 7 -> button 1, button 2, button 3, up, right.
        joy = self.frontend("boot", "--runs", 2, "--joy", "0:391", "--joy", "1:40").stdout
        ports = TICKS.search(joy)[3]
        self.assertEqual(ports, "1:4f:0:0,1:20:0:0")              # BUTTON_1|2|3|UP|RIGHT = 0x4f; port 1 has LEFT (id 6) = 0x20
        mouse = self.frontend("boot", "--runs", 2, "--mouse", "5,-3,5").stdout
        self.assertEqual(TICKS.search(mouse)[3], "2:5:5:-3,1:0:0:0")   # left + middle = BUTTON_1|BUTTON_3; port 1 stays a joystick

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

    def test_the_serialization_size_is_zero_because_no_state_is_implemented(self):
        self.assertNotIn("VIOLATION", self.frontend("boot", "--runs", 3).stdout)                # host.cpp checks size == 0 after every run


if __name__ == "__main__":
    unittest.main()
