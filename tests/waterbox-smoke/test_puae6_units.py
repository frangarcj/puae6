"""T19c1: what puae6.wbx does unit after unit inside Waterbox, against the two other adapters over the same p6_run_frame.

WB02  10000 units with a fixed input, twice, each with the same ticks, picture and audio as p6_headless and as the libretro core, and the same
      snapshot digests between the two Waterbox runs (the hardware state, which the other adapters cannot show).
WB07  guard bands around every buffer the guest is handed, calls the guest must refuse without advancing the machine, and a memory limit
      that fails without taking the sandbox down.
WB08  render and audio switched off for stretches of units change nothing of what comes after.
STEP01 the ticks, the picture and the audio of every unit are the ones of the other adapters.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom) and skips without them.
The full run takes several minutes; P6_TEST_WB_UNITS=N shortens the two long comparisons (the acceptance is 10000).
    python3 -m unittest discover -s tests/waterbox-smoke -p "test_puae6_units.py"
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import build_probe, builder  # noqa: E402
from tests.scenarios.a500.display import Display, rom_source  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_puae6 import runtime_available  # noqa: E402

BUILD = ROOT / "build/link-audit"
FIXTURES = ROOT / "tests/fixtures/microrom"
UNITS = int(os.environ.get("P6_TEST_WB_UNITS", "10000"))
RUN = re.compile(r"^run (\d+) video=(\d+)x(\d+) pitch=(\d+) dupe=(\d) hash=(\w+) px=\w+ audio=(\d+) polls=(\d+) states=(\d+) .*$", re.M)
TICKS = re.compile(r"^log \d+ \[PUAE6\] unit=(\d+) ticks=(\d+) ports=(\S+) keys=\w+$", re.M)
CYCLE = re.compile(r"^cycle (\d+) end runs=(\d+) audio_frames=(\d+) audio_hash=(\w+)$", re.M)
UNIT = re.compile(r"^unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) parity=(\d) .* hash=(\w+) audio=(\d+)$")
LACE = Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)   # both fields, a picture that changes every unit


def fields(line):
    m = UNIT.match(line)
    return {"unit": int(m[1]), "ticks": int(m[2]), "video": int(m[3]), "size": (int(m[4]), int(m[5])), "parity": int(m[6]), "hash": m[7], "audio": int(m[8])}


@unittest.skipUnless(runtime_available(), "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WaterboxUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = build_probe(BUILD, False, "p6_headless")
        cls.host = build_probe(BUILD, False, "p6_libretro_host")
        cls.core = next(Path(build_probe(BUILD, False, "puae6_libretro")).parent.glob("puae6_libretro.*"))
        build_waterbox.build_managed()                      # once, before anything runs in parallel
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-wb-units-")
        cls.roms = {"lace": Path(cls.tmp.name) / "lace.rom", "input_all": Path(cls.tmp.name) / "input_all.rom"}
        cls.roms["lace"].write_bytes(builder.build(rom_source(LACE)))
        cls.roms["input_all"].write_bytes(builder.build((FIXTURES / "input_all.s").read_text(encoding="utf-8")))
        # Port 0 is "Joystick only" (--device 0:257) in libretro(), as in Waterbox: the core's default since T17c is "Joystick + Mouse".
        # The libretro core finds its Kickstart in the system directory under a known name (T17a), so each micro-ROM gets a directory of its own.
        cls.systems = {}
        for name, rom in cls.roms.items():
            system = Path(cls.tmp.name) / f"system-{name}"
            system.mkdir()
            (system / "kick34005.A500").write_bytes(rom.read_bytes())
            cls.systems[name] = system

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def wb(self, rom, units, **options):
        return build_waterbox.run_puae6(self.roms[rom], units, **options)

    def native(self, rom, units):
        result = subprocess.run([str(self.headless), "--run", str(self.roms[rom]), "--units", str(units)], capture_output=True, text=True, timeout=3600)
        self.assertEqual(0, result.returncode, result.stderr)
        return [line for line in result.stdout.splitlines() if line.startswith("unit ")]

    def libretro(self, rom, units, *options):
        result = subprocess.run([str(self.host), str(self.core), "--system", str(self.systems[rom]), "--device", "0:257", "--runs", str(units), *map(str, options)],
                                capture_output=True, text=True, timeout=3600, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        self.assertEqual(0, result.returncode, result.stdout[-800:] + result.stderr[-800:])
        self.assertNotIn("VIOLATION", result.stdout)
        runs = {int(m[1]): {"size": (int(m[2]), int(m[3])), "dupe": int(m[5]), "hash": m[6], "audio": int(m[7])} for m in RUN.finditer(result.stdout)}
        ticks = {int(m[1]): (int(m[2]), m[3]) for m in TICKS.finditer(result.stdout)}
        return runs, ticks, CYCLE.search(result.stdout).groups()

    def compare_with_libretro(self, lines, result, runs, ticks, cycle, ports):
        self.assertEqual(len(lines), len(runs))
        self.assertEqual(len(lines), len(ticks))
        for line in lines:
            unit = fields(line)
            n = unit["unit"]
            self.assertEqual(unit["ticks"], ticks[n][0], f"ticks of unit {n}")
            self.assertEqual(ports, ticks[n][1], f"the input the core handed over in unit {n}")
            self.assertEqual(unit["audio"], runs[n]["audio"], f"audio frames of unit {n}")
            if unit["video"]:
                self.assertEqual((unit["size"], unit["hash"]), (runs[n]["size"], runs[n]["hash"]), f"picture of unit {n}")
        self.assertEqual(int(cycle[2]), result["audio"]["frames"])
        self.assertEqual(cycle[3], result["audio"]["fnv"], "every PCM byte the core delivered is the one Waterbox delivered")

    # --- WB02 and STEP01 ----------------------------------------------------------------------------------------------------------------

    def test_ten_thousand_units_twice_equal_headless_and_libretro_with_the_same_hardware_state_each_time(self):
        every = max(UNITS // 10, 1)
        with ThreadPoolExecutor(4) as pool:
            first = pool.submit(self.wb, "lace", UNITS, digest_every=every)
            second = pool.submit(self.wb, "lace", UNITS, digest_every=every)
            native = pool.submit(self.native, "lace", UNITS)
            retro = pool.submit(self.libretro, "lace", UNITS)
            (lines_a, a), (lines_b, b), headless, (runs, ticks, cycle) = first.result(), second.result(), native.result(), retro.result()
        self.assertEqual(UNITS, len(lines_a))
        self.assertEqual(lines_a, lines_b, "two runs of the same machine give the same ticks, picture and audio")
        self.assertEqual(a["state_digests"], b["state_digests"], "and the same hardware state, checked in 10 places and at the end")
        self.assertEqual(a["audio"], b["audio"])
        self.assertEqual(headless, lines_a, "every unit is the one p6_headless prints: ticks, picture, parity, cadence and audio frames")
        self.compare_with_libretro(lines_a, a, runs, ticks, cycle, "1:0:0:0,1:0:0:0")
        self.assertGreater(len({fields(line)["hash"] for line in lines_a}), 100, "the pictures are not one repeated frame")
        self.assertEqual({0, 1, 2}, {fields(line)["parity"] for line in lines_a}, "both fields and progressive units were compared")

    def test_ten_thousand_units_twice_with_a_fixed_input_equal_the_libretro_core_and_the_input_reaches_the_machine(self):
        ports = ("0:1:1:0:0", "1:1:73:0:0")            # port 0: fire; port 1: fire, up, right (P6_BUTTON_1 | UP | RIGHT)
        every = max(UNITS // 10, 1)
        with ThreadPoolExecutor(4) as pool:
            first = pool.submit(self.wb, "input_all", UNITS, ports=ports, digest_every=every)
            second = pool.submit(self.wb, "input_all", UNITS, ports=ports, digest_every=every)
            # libretro port 0 is the Amiga joystick port and port 1 the mouse port (PUAE5's crossing, T17c): the masks go crossed so that each Amiga port gets what Waterbox gives it.
            retro = pool.submit(self.libretro, "input_all", UNITS, "--joy", "0:91", "--joy", "1:1")
            idle = pool.submit(self.wb, "input_all", UNITS, digest_every=every)
            (lines_a, a), (lines_b, b), (runs, ticks, cycle), (lines_idle, idle_result) = (first.result(), second.result(), retro.result(), idle.result())
        self.assertEqual(lines_a, lines_b)
        self.assertEqual(a["state_digests"], b["state_digests"])
        self.assertEqual(a["audio"], b["audio"])
        self.assertEqual(["1:1:0:0", "1:73:0:0"], a["ports"])
        self.compare_with_libretro(lines_a, a, runs, ticks, cycle, "1:1:0:0,1:49:0:0")      # the core logs the buttons in hex: 0x49 is 73
        self.assertEqual(UNITS, len(lines_idle))
        self.assertEqual([fields(line)["ticks"] for line in lines_idle], [fields(line)["ticks"] for line in lines_a], "input does not move the clock")
        # The micro-ROM logs the joystick registers and the fire buttons into Chip RAM: that is where the input shows, and only in the state.
        self.assertNotEqual(idle_result["state_digests"]["final"], a["state_digests"]["final"], "the held buttons changed what the machine saw")

    # --- WB08 ---------------------------------------------------------------------------------------------------------------------------

    def test_render_and_audio_switched_off_for_stretches_change_nothing_of_the_units_that_follow(self):
        units = max(min(UNITS, 900), 150)
        off = f"{units // 6}-{units // 3}:0,{units // 3 + 1}-{units // 2}:2,{units // 2 + 1}-{2 * units // 3}:1"
        with ThreadPoolExecutor(2) as pool:
            always = pool.submit(self.wb, "lace", units, digest_every=units // 6)
            cut = pool.submit(self.wb, "lace", units, render=off, digest_every=units // 6)
            (reference, ref), (lines, result) = always.result(), cut.result()
        self.assertEqual(units, len(lines))
        stretches = [(int(s.split("-")[0]), int(s.split("-")[1].split(":")[0]), int(s.split(":")[1])) for s in off.split(",")]
        flags = lambda n: next((f for a, b, f in stretches if a <= n <= b), 3)        # noqa: E731
        for line, expected in zip(lines, reference):
            unit, wanted = fields(line), fields(expected)
            n = unit["unit"]
            self.assertEqual(wanted["ticks"], unit["ticks"], f"render flags {flags(n)} moved the clock at unit {n}")
            self.assertEqual((wanted["parity"], wanted["size"]), (unit["parity"], unit["size"]), f"unit {n}")
            if flags(n) & 1:
                self.assertEqual(wanted["hash"], unit["hash"], f"the picture of unit {n}, after {n - 1} units of which some were not drawn")
            else:
                self.assertEqual(0, unit["video"], f"unit {n} delivered a picture that nobody asked for")
            if flags(n) & 2:
                self.assertEqual(wanted["audio"], unit["audio"], f"audio frames of unit {n}")
                self.assertEqual(ref["pcm"][n], result["pcm"][n], f"the PCM of unit {n}: the filter phases went on while nothing was delivered")
            else:
                self.assertEqual(0, unit["audio"], f"unit {n} delivered audio that nobody asked for")
        self.assertEqual(ref["state_digests"]["final"], result["state_digests"]["final"],
                         "after the last unit the whole machine, pictures buffers included, is what an always-rendering run has")
        quiet = [key for key in ref["state_digests"] if key != "final" and (int(key) < stretches[0][0] or int(key) >= stretches[-1][1] + 4)]
        self.assertGreaterEqual(len(quiet), 2, "digests before the first stretch and after the last one are compared")
        for key in quiet:
            self.assertEqual(ref["state_digests"][key], result["state_digests"][key], f"the whole machine at unit {key}")

    # --- WB07 ---------------------------------------------------------------------------------------------------------------------------

    def test_guard_bands_hold_and_refused_calls_do_not_advance_the_machine(self):
        lines, result = self.wb("lace", 12, bad_calls_at=5)
        self.assertEqual(self.native("lace", 12), lines, "six refused calls before unit 5 and every unit is still the one of a run without them")
        refused = {entry["Name"]: entry for entry in result["refusals"]}
        self.assertEqual({"wrong frame abi_version", "wrong frame struct_size", "wrong input struct_size", "unknown render flag", "missing input",
                          "missing output"}, set(refused))
        self.assertTrue(all(entry["result"] == 1 for entry in refused.values()), "each is BAD_ARGUMENT")
        self.assertGreater(result["canary_checks"], 12 * 2, "the bands around p6_input, p6w_frame and p6w_init were checked on every call")

    def test_memory_and_state_limits_fail_with_a_code_and_a_message_and_the_sandbox_still_runs(self):
        lines, result = self.wb("lace", 1, limits=True)
        outcomes = {entry["name"]: (entry["result"], entry["message"]) for entry in result["limits"]}
        self.assertEqual(7, outcomes["a firmware bigger than the sealed heap"][0])
        self.assertEqual(1, outcomes["more media than the contract allows"][0])
        self.assertEqual(1, outcomes["no firmware"][0])
        self.assertEqual(2, outcomes["a unit before init"][0])
        self.assertEqual(2, outcomes["a second init"][0])
        self.assertEqual(self.native("lace", 1), lines, "after four refused inits and a call before init, a good init runs the first unit as headless does")


if __name__ == "__main__":
    unittest.main()
