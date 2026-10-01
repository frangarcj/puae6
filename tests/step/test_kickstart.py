"""Boot with an authorized Kickstart: a separate scenario that only runs where the owner supplies the image.

Nothing here is evidence of Kickstart compatibility until it has run with a real image. The descriptor
(tests/scenarios/a500/kickstart-authorized.json) lists what stays unproven.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "tests/scenarios/a500/kickstart-authorized.json"
ROM = os.environ.get("P6_KICKSTART_ROM")
HEADLESS = ROOT / "build/link-audit/p6_headless"


class KickstartScenarioDescriptorTests(unittest.TestCase):
    def test_the_scenario_is_declared_blocked_and_lists_what_is_unproven(self):
        data = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "BLOCKED")
        self.assertGreaterEqual(len(data["unproven_until_it_runs"]), 4)
        self.assertGreaterEqual(len(data["what_it_will_check"]), 3)
        self.assertIn("P6_KICKSTART_ROM", data["run"])

    def test_no_firmware_image_is_committed(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        for name in tracked:
            self.assertFalse(name.lower().endswith((".rom", ".adf")), name)
            if name.startswith(("tests/", "plan/", "docs/")) and Path(ROOT / name).stat().st_size in (262144, 524288):
                self.fail("a file of Kickstart size is tracked: " + name)


@unittest.skipUnless(ROM and Path(ROM).is_file(), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
class KickstartBootTests(unittest.TestCase):
    """It checks running, not compatibility. Run once with a Kickstart 1.3 supplied by the owner (2026-10-01): see local_run in the descriptor."""

    def run_rom(self, units, seed=1):
        return subprocess.run([str(HEADLESS), "--run", ROM, "--units", str(units), "--seed", str(seed)],
                              capture_output=True, text=True, timeout=1800)

    def test_the_image_runs_for_300_units_and_shows_a_picture(self):
        first = self.run_rom(300)
        self.assertEqual(first.returncode, 0, first.stderr)
        rows = [re.fullmatch(r"unit (\d+) ticks=(\d+) video=(\d) active=(\d+)x(\d+) .* hash=([0-9a-f]{16})(?: audio=\d+)?", line)
                for line in first.stdout.splitlines()[:-1]]
        self.assertTrue(all(rows))
        self.assertEqual(len(rows), 300)
        self.assertTrue(all(r[3] == "1" for r in rows), "a unit was delivered without a picture")
        self.assertGreater(len({r[6] for r in rows}), 1, "the picture never changed")
        self.assertEqual(first.stdout, self.run_rom(300).stdout)


if __name__ == "__main__":
    unittest.main()
