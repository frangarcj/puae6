"""T48e: the -O2 probe that the state tests run is the -O0 probe, byte for byte.

The native-state suites run p6_state_probe from build/state-opt (-O2 -g, assertions on, no sanitizers) because the Debug -O0 probe of build/link-audit costs ~165 ms per unit. That is
only a valid substitute if the compiler's freedom does not show: a difference between the two builds is undefined behaviour or a read of memory nobody initialized, that is, a
determinism failure of the engine, and it is never relaxed here (docs/testing.md).

Every command line below is run on both builds, and both must print the same stdout (the digest of every subsystem of every unit, the PCM, the chipset groups, the dump) and write
the same bytes into every file `--save-at` names. The lines are the ones the suites drive: a run with `--every 1`; a state saved and loaded back in the same image; a state loaded
in a new process; and DET02 scenarios (tests/state/test_det02.py), one at least for each of its modes, whose original, same-image, restarted-image and new-process runs go through
the same comparison. `Det02Case.probe` is the only thing replaced, so what a scenario runs is exactly what test_det02 runs.

P6_STATE_PROBE_BUILD does not matter here: both trees are always built and compared. With P6_KICKSTART_ROM set, a Kickstart scenario joins them.
"""
import os
from pathlib import Path
import subprocess
import unittest

from tests.state import test_det02 as d
from tests.state import test_det02_kickstart as kickstart
from tests.state.harness import Run, TOOLS, built

# One program for each way the CPU and the chipset run (a subset of the micro-ROMs of test_det02, built from the same sources).
PROGRAMS = ("vsync", "copper_irq", "input_all", "long_ops", "busy", "tone", "stop", "trace", "user")
ROMS = {name: d.Det02Tests.ROMS[name] for name in (*PROGRAMS, "blt-aud")}
# (scenario of test_det02.SCENARIOS, unit the state is taken in, mode): every mode of DET02, on programs that exercise different things.
DET02 = (("vsync", 3, d.SAME), ("vsync", 3, d.SAME_BEAM), ("vsync", 3, d.RESET_BEAM), ("vsync", 3, d.NEW), ("vsync", 3, d.NEW_BEAM),
         ("busy", 5, d.SAME_BEAM), ("busy-disk", 8, d.RESET_BEAM), ("blit+audio", 2, d.SAME_BEAM), ("tone", 3, d.NEW),
         ("busy-ntsc", 2, d.NEW), ("disk-change", 12, d.SAME_BEAM))


def saved_files(arguments):
    """The files a probe command line writes: the path after each `--save-at N` / `--save-fields-at N`."""
    return [arguments[i + 2] for i, word in enumerate(arguments) if word in ("--save-at", "--save-fields-at") and i + 2 < len(arguments)]


def difference(first, second):
    """None when two probe results ((stdout, {file: bytes})) are equal, else what differs first."""
    if first[0] != second[0]:
        lines = list(zip(first[0].splitlines(), second[0].splitlines()))
        at = next((i for i, (a, b) in enumerate(lines) if a != b), len(lines))
        pair = lines[at] if at < len(lines) else ("(output ends)", "(output ends)")
        return f"stdout differs at line {at + 1}: {pair[0][:120]!r} against {pair[1][:120]!r}"
    for path in first[1]:
        if first[1][path] != second[1].get(path):
            other = second[1].get(path, b"")
            at = next((i for i, (a, b) in enumerate(zip(first[1][path], other)) if a != b), min(len(first[1][path]), len(other)))
            return f"{Path(path).name} differs: {len(first[1][path])} and {len(other)} bytes, first different byte at {at}"
    return None


class ParityLogic(unittest.TestCase):
    """The comparison itself: it has to see a seeded difference, in the text and in a state file."""

    def test_equal_results_agree_and_any_difference_is_reported(self):
        same = ("digest 0 cpu aa\ndigest 1 cpu bb\n", {"/x/a.state": b"\x00\x01\x02"})
        self.assertIsNone(difference(same, ("digest 0 cpu aa\ndigest 1 cpu bb\n", {"/x/a.state": b"\x00\x01\x02"})))
        self.assertIn("line 2", difference(same, ("digest 0 cpu aa\ndigest 1 cpu bc\n", same[1])))
        self.assertIn("line 3", difference(same, ("digest 0 cpu aa\ndigest 1 cpu bb\nextra\n", same[1])))
        self.assertIn("first different byte at 1", difference(same, (same[0], {"/x/a.state": b"\x00\x09\x02"})))
        self.assertIn("first different byte at 3", difference(same, (same[0], {"/x/a.state": b"\x00\x01\x02\x03"})))
        self.assertIn("a.state", difference(same, (same[0], {})))

    def test_saved_files_are_those_after_the_save_operations(self):
        self.assertEqual(["/p/s"], saved_files(["--units", "8", "--save-at", "3", "/p/s", "--load-at", "6", "/p/s"]))
        self.assertEqual(["/p/f"], saved_files(["--save-fields-at", "3", "/p/f"]))
        self.assertEqual([], saved_files(["--units", "8", "--load-at", "6", "/p/s"]))


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BuildParityTests(d.Det02Case):
    ROMS = ROMS

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exes = {"-O0": built("p6_state_probe", "debug"), "-O2": built("p6_state_probe", "optimized")}
        assert cls.exes["-O0"] != cls.exes["-O2"], "the parity test needs two different builds"

    @classmethod
    def probe(cls, rom, *arguments):
        """Det02Case's probe, on both builds: the same command line must give the same stdout and the same state files. The -O2 build runs last, so that the files left
        behind (a later `--load-at` reads them) are the optimized build's, which the comparison has just shown to be the Debug build's."""
        results = {}
        for label, exe in cls.exes.items():
            result = subprocess.run([str(exe), str(cls.roms[rom]), *arguments], capture_output=True, text=True, timeout=1800)
            assert result.returncode == 0, f"{label}: {result.stderr[-400:]}"
            results[label] = (result.stdout, {path: Path(path).read_bytes() for path in saved_files(arguments) if Path(path).is_file()})
        found = difference(results["-O0"], results["-O2"])
        assert found is None, f"-O0 and -O2 differ on `{rom} {' '.join(arguments)}`: {found}"
        assert results["-O0"][0], "the probe printed nothing"
        return Run(results["-O2"][0])

    def test_every_unit_of_each_rom(self):
        for rom in PROGRAMS:
            with self.subTest(rom=rom):
                run = self.probe(rom, "--units", "12", "--every", "1", "--dump", "chipset")
                self.assertEqual(13, len({unit for unit, _ in run.digests}))

    def test_state_saved_and_loaded_in_the_same_image(self):
        for rom in PROGRAMS:
            with self.subTest(rom=rom):
                path = self.dir / f"parity-{rom}.state"
                run = self.probe(rom, "--units", "10", "--every", "1", "--save-at", "4", str(path), "--load-at", "7", str(path))
                self.assertEqual([(7, 0, "")], run.loads)
                self.assertEqual(1, len(run.saves))

    def test_state_loaded_in_a_new_process(self):
        for rom in PROGRAMS:
            with self.subTest(rom=rom):
                path = self.dir / f"parity-new-{rom}.state"
                self.probe(rom, "--units", "5", "--save-at", "4", str(path))
                run = self.probe(rom, "--units", "6", "--every", "1", "--load-at", "0", str(path))
                self.assertEqual([(0, 0, "")], run.loads)

    def test_det02_scenarios_in_every_mode(self):
        for scenario, n, mode in DET02:
            with self.subTest(scenario=scenario, unit=n, mode=mode):
                rom, extra, points, modes = d.SCENARIOS[scenario]
                self.assertIn((n, mode), {(p, m) for p in points for m in modes})
                self.first_divergence(rom, extra, n, mode)

    @unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
    def test_det02_on_the_kickstart(self):
        rom, extra, _, _ = kickstart.SCENARIOS["kickstart"]
        for mode in (d.SAME_BEAM, d.NEW):
            with self.subTest(mode=mode):
                self.first_divergence(rom, extra, 60, mode)


if __name__ == "__main__":
    unittest.main()
