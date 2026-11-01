"""T105b: validated compatibility/chipset overrides have canonical identity and cold restore."""
import unittest

from tests.state.harness import ProbeCase


class ChipsetStateTests(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}
    MACHINE = "a600"

    def saved(self, name, *extra):
        path = self.dir / f"{name}.state"
        run = self.probe("vsync", "--units", "4", "--save-at", "4", str(path), *extra)
        self.assertEqual([], run.save_errors)
        return path, path.read_bytes()

    def test_matching_explicit_selectors_are_the_profile_and_overrides_restore_cold(self):
        _, auto = self.saved("auto")
        _, matching = self.saved("matching", "--chipset-compatible", "a600", "--chipset", "ecs")
        self.assertEqual(auto, matching, "explicit spellings of the profile keep its historical identity and bytes")

        for name, extra in (("compatible", ("--chipset-compatible", "a500")), ("denise", ("--chipset", "ecs-denise"))):
            with self.subTest(name=name):
                path, blob = self.saved(name, *extra)
                self.assertNotEqual(auto[176:208], blob[176:208])
                again = self.dir / f"{name}-again.state"
                run = self.probe("vsync", "--units", "0", "--load-at", "0", str(path), "--save-at", "0", str(again), *extra)
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(blob, again.read_bytes())


if __name__ == "__main__":
    unittest.main()

