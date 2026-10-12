"""T52: the clock of the CPU (p6_config.cpu_multiplier) changes how fast the cycle-exact CPU runs and nothing else, whatever the host.

The multiplier is upstream's cpu_clock_multiplier (N << 8): one CPU clock lasts CYCLE_UNIT / N, so N = 2 is the 7.09 MHz of a 68000 (the A500's, and the default of every 68000 machine) and N = 4 the
14.19 MHz of the A1200's 68EC020. The values are PUAE5's `puae_cpu_multiplier`: 1, 2, 4, 8, 10, 12 and 16 (3.55 to 56.75 MHz); zero is the machine's own.

What is derived here, from the MC68000 manual and from nothing the engine printed:
  * a 68000 running TST.W (An)+ from ROM and DBRA 2000 times pays 8 + 10 clocks a pass (the last one 8 + 14): 36004 clocks. CIA-A timer A counts E clocks, one every ten clocks of a
    7.09 MHz CPU, i.e. every 5 colour clocks; at multiplier N one CPU clock lasts CYCLE_UNIT / N of a colour clock pair, and upstream's cycle unit is an integer (cpucycleunit = 512 // N), so
    the timer counts 36004 * (512 // N) / 2560: 3600 at the default (N = 2), 7200 / N when N divides 512 and a little fewer at 10 and 12 (the unit is 51 and 42, not 51.2 and 42.67). The one or
    two E clocks the start and the stop of the timer cost through the E clock synchronisation (the CIA bus cycle is the chipset's, not the CPU's) are the slack. The multiplier changes the time
    the program takes and not what it computes.
  * the 68010 runs the same program in loop mode: its time scales with the clock the same way, so time x multiplier is the same for every multiplier (to the slack and the unit's rounding).
  * the 68EC020 of the A1200 (default 4) never runs slower with a faster clock, and the clock buys it a good part of the speed it names but not all: the instruction cache runs at the CPU
    clock while the prefetches from ROM wait for the bus, so time x multiplier is not constant there (measured: 3.1 times faster at 14 MHz than at 3.5, 3.6 times faster at 56 than at 14).
  * the chipset does not move: the audio frames delivered and the ticks of every unit are those of the default machine to the length of the instruction that ends a unit (a unit ends at the first
    instruction boundary after the beam wraps; tests/libretro-host/test_cpu.py compares them with the default's).
The probe is tests/host/cpu_010_probe.cpp, which loads the machine with the CPU and the multiplier it is told; nothing reads the clock of the host.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.build_support import build_targets
from tests.host import ensure_generated
from tests.host import test_cpu_010 as t10

ROOT = t10.ROOT
TREE = t10.TREE
MULTIPLIERS = (1, 2, 4, 8, 10, 12, 16)
PASSES = t10.PASSES
CYCLE_UNIT = 512                # upstream's sysdeps.h: the unit in which cpucycleunit counts a CPU clock (CYCLE_UNIT / N)
CHIP_CLOCKS_PER_E = 2560        # one E clock is 5 colour clocks, 5 * CYCLE_UNIT
CLOCKS = PASSES * 8 + (PASSES - 1) * 10 + 14
SLACK = 6                       # E clocks the start and the stop of the timer can add: the CIA synchronisation is a clock or two each way, more at a high multiplier


class CPUClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_generated()
        build_targets(TREE, ["p6_lifecycle_probe"])
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-cpuclock-")
        cls.exe = t10.CPU010Tests.build_probe(Path(cls.tmp.name))
        cls.rom = Path(cls.tmp.name) / "loop.rom"
        cls.rom.write_bytes(t10.builder.build(t10.CPU010Tests.LOOP_ONLY))
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, *options):
        key = tuple(options)
        if key not in self.cache:
            result = subprocess.run([str(self.exe), str(self.rom), "20", *options], capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [line.split() for line in result.stdout.splitlines()]
            header = next(line for line in lines if line[0] == "cpu")
            self.assertEqual(next(line for line in lines if line[0] == "units")[3], "600df00d", "the program did not reach its end: " + " ".join(options))
            results = {int(line[1]): int(line[2], 16) for line in lines if line[0] == "result"}
            self.cache[key] = (header, 0xFFFF - (results[1] << 8 | results[0]), next(int(line[2]) for line in lines if line[:2] == ["state", "instructions"]))
        return self.cache[key]

    def test_the_header_says_which_clock_and_cpu_the_machine_has(self):
        header, _, _ = self.run_probe("--cpu", "68010", "--multiplier", "8")
        self.assertEqual(header[1], "68010")
        self.assertEqual(header[header.index("clock_multiplier") + 1], str(8 << 8))
        header, _, _ = self.run_probe("--cpu", "68000")
        self.assertEqual(header[header.index("clock_multiplier") + 1], str(2 << 8))        # the machine's own, upstream's built_in_prefs
        header, _, _ = self.run_probe("--machine", "a1200")
        self.assertEqual((header[1], header[header.index("clock_multiplier") + 1]), ("68020", str(4 << 8)))

    def test_a_68000_pays_the_cycles_of_the_manual_at_every_multiplier(self):
        for multiplier in MULTIPLIERS:
            with self.subTest(multiplier=multiplier):
                _, elapsed, _ = self.run_probe("--cpu", "68000", "--multiplier", str(multiplier))
                self.assertAlmostEqual(elapsed, CLOCKS * (CYCLE_UNIT // multiplier) // CHIP_CLOCKS_PER_E, delta=SLACK)

    def test_the_default_clock_is_the_one_named_two_on_a_68000_machine_and_four_on_the_a1200(self):
        self.assertEqual(self.run_probe("--cpu", "68000")[1:], self.run_probe("--cpu", "68000", "--multiplier", "2")[1:])
        self.assertEqual(self.run_probe("--machine", "a1200")[1:], self.run_probe("--machine", "a1200", "--multiplier", "4")[1:])

    def test_a_68010_in_loop_mode_scales_with_the_clock(self):
        base = self.run_probe("--cpu", "68010", "--multiplier", "2")[1] * 2
        for multiplier in MULTIPLIERS:
            with self.subTest(multiplier=multiplier):
                _, elapsed, _ = self.run_probe("--cpu", "68010", "--multiplier", str(multiplier))
                self.assertAlmostEqual(elapsed * multiplier, base, delta=SLACK * multiplier)

    def test_the_a1200_never_runs_slower_with_a_faster_clock_and_gets_most_of_the_speed(self):
        times = {m: self.run_probe("--machine", "a1200", "--multiplier", str(m))[1] for m in MULTIPLIERS}
        ordered = [times[m] for m in MULTIPLIERS]
        self.assertEqual(ordered, sorted(ordered, reverse=True), times)
        self.assertGreater(times[1] / times[4], 2.5)
        self.assertGreater(times[4] / times[16], 3.0)
        self.assertGreater(times[1] / times[16], 8)

    def test_a_run_is_the_same_run_every_time(self):
        first = self.run_probe("--cpu", "68000", "--multiplier", "12")
        self.cache.clear()
        self.assertEqual(first, self.run_probe("--cpu", "68000", "--multiplier", "12"))


if __name__ == "__main__":
    unittest.main()
