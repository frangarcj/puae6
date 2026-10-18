"""SAFE01 for the differential corpus (T32c): every scenario of tests/scenarios/corpus runs in the ASan + UBSan build of the probe (no recover: a finding ends the
process), with its inputs and media, and then once more with a state saved at its first DET02 point and loaded back one unit later, so that the paths of the native state
(the registry, the bodies of the engine, the BEAM words of T15n) run under the sanitizers too. A process that exits non-zero, or whose stderr names a runtime error or a
sanitizer, fails the scenario. The portability matrix runs the same corpus under the sanitizers in the five rows (`san` of tests/portability/differential.golden.json);
this is the direct, per-scenario form on the machine the tests run on."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.build_support import build_targets  # noqa: E402
from tests.reference import corpus_run  # noqa: E402
from tests.scenarios.corpus import corpus  # noqa: E402

ENV = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "ASAN_OPTIONS": "detect_leaks=0"}


def clean(result):
    return result.returncode == 0 and "runtime error" not in result.stderr and "Sanitizer" not in result.stderr


class CorpusSanitizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = build_targets(ROOT / "build/native-debug", ["p6_state_probe"], sanitizers=True)["p6_state_probe"]
        cls.directory = tempfile.TemporaryDirectory(prefix="p6-corpus-san-")
        directory = Path(cls.directory.name)
        cls.assets = corpus_run.Assets(directory)
        cls.roms = {}
        for name, text in corpus.programs().items():
            path = directory / f"{name}.rom"
            path.write_bytes(corpus.build_rom(text))
            cls.roms[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_probe(self, entry, *extra, units=None):
        arguments = corpus_run.probe_arguments(entry, self.assets, units)
        return subprocess.run([str(self.probe), str(self.roms[entry.program]), *arguments, "--every", "4", *extra], capture_output=True, text=True, env=ENV, timeout=3600)

    def test_every_scenario_runs_clean_under_the_sanitizers(self):
        for entry in corpus.ENTRIES:
            with self.subTest(entry=entry.id):
                result = self.run_probe(entry)
                self.assertTrue(clean(result), result.stderr[-600:])

    def test_a_state_saved_at_the_first_point_of_every_scenario_loads_clean_under_the_sanitizers(self):
        state = Path(self.directory.name) / "san.state"
        for entry in corpus.ENTRIES:
            with self.subTest(entry=entry.id):
                n = entry.points[0]
                units = n + 4
                result = self.run_probe(entry, "--save-at", str(n), str(state), "--load-at", str(n + 2), str(state), units=units)
                self.assertTrue(clean(result), result.stderr[-600:])
                self.assertIn(f"load {n + 2} 0", result.stdout, "the state was refused")


if __name__ == "__main__":
    unittest.main()
