"""Fixture-isolation regressions: no Waterbox runtime or emulator execution required."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RecordingHarness:
    def __init__(self, prefix):
        self.tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.dir = Path(self.tmp.name)
        self.commands = []
        self.closed = False

    def close(self):
        self.closed = True
        self.tmp.cleanup()

    def script(self, name, lines):
        return str(self.dir / name)

    def core(self, name, **kwargs):
        return f"core {name}"

    def session(self, lines, **kwargs):
        self.commands.extend(lines)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def units(self, *args):
        return []

    def digests(self, *args):
        return {"end": "digest"}


class MovieSetupTests(unittest.TestCase):
    def setUp(self):
        harness = ModuleType("harness")
        for number, name in enumerate("B1 B2 CAPS ESC JOYSTICK KEY_Q KEY_W LEFT MOUSE RIGHT UP".split(), 1):
            setattr(harness, name, number)
        harness.AVAILABLE = True
        harness.Harness = RecordingHarness
        harness.line = lambda **kwargs: "input"
        spec = importlib.util.spec_from_file_location("isolated_movie_playback", ROOT / "tests/bizhawk-movies/test_movie_playback.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"harness": harness}):
            spec.loader.exec_module(self.module)

    def prepare(self, cls):
        self.addCleanup(cls.doClassCleanups)
        cls.setUpClass()
        return [int(line.split()[2]) for line in cls.h.commands if line.startswith("run ")]

    def test_short_setup_prepares_only_two_720_unit_movies(self):
        self.assertEqual(self.prepare(self.module.MoviePlaybackTests), [720, 720])

    def test_long_setup_still_records_ten_thousand_units_without_short_fixtures(self):
        self.assertEqual(self.prepare(self.module.LongMoviePlaybackTests), [10000])
        self.assertEqual(self.module.LONG_UNITS, 10000)
        self.assertEqual((self.module.SHORT_UNITS, self.module.STATE_EVERY, self.module.CHECK_EVERY), (720, 60, 20))

    def test_discovery_preserves_all_six_behavioural_tests(self):
        loader = unittest.TestLoader()
        short = loader.getTestCaseNames(self.module.MoviePlaybackTests)
        long = loader.getTestCaseNames(self.module.LongMoviePlaybackTests)
        self.assertEqual((len(short), len(long)), (5, 1))
        self.assertEqual(len(set(short + long)), 6)
        self.assertIn("test_a_movie_of_ten_thousand_units_played_twice_at_once_by_new_processes_is_the_one_that_was_recorded", long)
        self.assertIn("test_a_movie_that_ends_before_the_core_does_is_an_error", short)

    def test_documented_short_selection_never_prepares_the_long_fixture(self):
        loader = unittest.TestLoader()
        loader.testNamePatterns = ["*.MoviePlaybackTests.*"]
        suite = loader.loadTestsFromModule(self.module)
        classes = {type(test) for group in suite for test in group}
        self.assertEqual(suite.countTestCases(), 5)
        self.assertEqual(classes, {self.module.MoviePlaybackTests})
        for cls in classes:
            self.assertEqual(self.prepare(cls), [720, 720])

    def test_setup_failure_still_cleans_up_the_harness(self):
        for cls in (self.module.MoviePlaybackTests, self.module.LongMoviePlaybackTests):
            with self.subTest(fixture=cls.__name__), \
                    patch.object(RecordingHarness, "session", side_effect=RuntimeError("fixture failed")):
                try:
                    with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                        cls.setUpClass()
                finally:
                    cls.doClassCleanups()
                self.assertTrue(cls.h.closed)
                self.assertFalse(cls.h.dir.exists())


if __name__ == "__main__":
    unittest.main()
