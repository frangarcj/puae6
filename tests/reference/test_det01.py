"""DET01: two new processes, the same inputs, 10000 video units: every digest the probe prints is the same, byte for byte.

Each case runs p6_state_probe twice AT THE SAME TIME (two processes that share nothing but the files they read), prints the digest of every
subsystem every FIVE units and the audio and the picture of EVERY unit, and the two outputs must be identical. A difference is reported by
tools/compare_runs.py (first boundary, subsystem, inputs applied before it), not as "outputs differ".

The inputs are scripted: the pseudo-random ones come from the generator below with its seed in the case, never from the host.
P6_TEST_DET01_UNITS shortens the run (default 10000) for iteration; a shortened run is not DET01 and is reported as skipped by the dispatcher."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import compare_runs as cr  # noqa: E402
from tests.portability import corpus  # noqa: E402
from tests.reference import builds  # noqa: E402

UNITS = 10000
EVERY = 5
SEED = 32


def lcg(seed):
    """x -> (1103515245 x + 12345) mod 2^31: the generator of the scripted inputs; the seed is in the case."""
    x = seed
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x >> 8


def scripted_input(units, seed):
    """Mouse and joystick packets and a few keys spread over the run, as probe arguments."""
    random = lcg(seed)
    args = ["--port", "0:2:0:0:0", "--port", "1:1:0:0:0"]
    for unit in range(7, units, 37):
        draw = next(random)
        if draw % 3 == 0:
            args += ["--port-at", str(unit), f"0:2:{draw % 3}:{(draw >> 3) % 61 - 30}:{(draw >> 9) % 41 - 20}"]
        else:
            args += ["--port-at", str(unit), f"1:1:{(draw >> 3) % 8}:0:0"]
    for unit in range(11, units, 301):
        code = 0x40 + next(random) % 40
        args += ["--key", str(unit), hex(code), "down", "--key", str(unit + 3), hex(code), "up"]
    return args


def cases(units):
    return {
        "a500-vsync-pal": ("vsync", []),
        "audio-tone_all-48000": ("audio-tone_all", []),
        "a500-flip-lace-pal": ("a500-flip-lace", []),
        "input-scripted": ("input_all", scripted_input(units, SEED)),
        "a1200-copper-irq-pal": ("copper_irq", ["--machine", "a1200"]),
    }


class Det01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = int(os.environ.get("P6_TEST_DET01_UNITS", UNITS))
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-det01-")
        corpus.write_corpus(cls.directory.name)
        cls.probe = builds.product_probe()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def start(self, rom, args, units):
        """(command, process, stdout file): the output goes to a file so that neither process waits for the other to be read (a pipe fills at 64 KiB)."""
        command = [str(self.probe), str(Path(self.directory.name) / f"{rom}.rom"), "--units", str(units), "--every", str(EVERY), *args]
        out = tempfile.TemporaryFile()
        process = subprocess.Popen(command, stdout=out, stderr=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"})
        return command, process, out

    @staticmethod
    def finish(process, out):
        process.wait()
        out.seek(0)
        text = out.read().decode()
        out.close()
        return text

    def test_two_new_processes_print_the_same_boundaries(self):
        if self.units != UNITS:
            self.skipTest(f"P6_TEST_DET01_UNITS={self.units}: DET01 is {UNITS} units")
        for name, (rom, args) in cases(self.units).items():
            with self.subTest(case=name):
                (command, first, file1), (_, second, file2) = self.start(rom, args, self.units), self.start(rom, args, self.units)
                out1, out2 = self.finish(first, file1), self.finish(second, file2)
                self.assertEqual((0, 0), (first.returncode, second.returncode))
                run1, run2 = cr.parse_probe(out1), cr.parse_probe(out2)
                self.assertEqual(self.units + 1, len(run1.units), "every unit printed its audio and its picture")
                report = cr.compare(run1, run2)
                self.assertTrue(report["equal"], cr.render(report, cr.previous_inputs(cr.inputs_from_probe_args(command[2:]), report.get("first_divergent_boundary", 0), 8)))
                self.assertEqual(out1, out2, "the comparison keys miss something the probe prints")

    def test_the_scripted_inputs_are_a_function_of_the_seed(self):
        self.assertEqual(scripted_input(2000, SEED), scripted_input(2000, SEED))
        self.assertNotEqual(scripted_input(2000, SEED), scripted_input(2000, SEED + 1))
        args = scripted_input(2000, SEED)
        self.assertIn("--port-at", args)
        self.assertIn("--key", args)
        units = [int(args[i + 1]) for i, a in enumerate(args) if a in ("--port-at", "--key")]
        self.assertTrue(all(0 < u < 2000 for u in units))

    def test_a_shortened_run_still_checks_the_machinery(self):
        # Always runs: 30 units of every case through two processes; the full length is test_two_new_processes_print_the_same_boundaries.
        for name, (rom, args) in cases(30).items():
            with self.subTest(case=name):
                (_, first, file1), (_, second, file2) = self.start(rom, args, 30), self.start(rom, args, 30)
                out1, out2 = self.finish(first, file1), self.finish(second, file2)
                self.assertEqual((0, 0), (first.returncode, second.returncode))
                self.assertEqual(out1, out2)
                self.assertEqual(31, len(cr.parse_probe(out1).units))


if __name__ == "__main__":
    unittest.main()
