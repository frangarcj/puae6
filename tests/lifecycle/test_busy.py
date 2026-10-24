"""LIFE01: the single native instance. A call made while another is running is refused with BUSY and changes nothing.

There are no threads in the profile, so the only way to meet a call in progress is from inside one: the step probe
(P6_NESTED=1) calls every entry point from its boundary observer, which runs in the middle of p6_run_frame. A second init
or load after the image is there is BAD_STATE, which test_cycles covers.
"""
from pathlib import Path
import re
import tempfile
import unittest

from tests.host.test_bridge_lifecycle import ROOT
from tests.step.test_step import TOOLS, builder, configure_trace_tree, run_probe

BUSY = 3        # P6_ERROR_BUSY


@unittest.skipUnless(TOOLS, "toolchain unavailable")
class BusyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = configure_trace_tree()
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-busy-")
        cls.rom = Path(cls.tmp.name) / "boot.rom"
        cls.rom.write_bytes(builder.build((ROOT / "tests/fixtures/microrom/boot.s").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def execute(self, env=None):
        code, out = run_probe(self.exe, self.rom, 4, "steps", 1, timeout=600, extra=["pal"], env={"P6_DUMP_LOG": "1", **(env or {})})
        self.assertEqual(code, 0, out[-600:])
        return out

    def test_every_entry_point_called_from_inside_a_call_is_busy_and_the_run_is_untouched(self):
        plain = self.execute()
        nested = self.execute({"P6_NESTED": "1"})
        calls = re.findall(r"^nested init=(\d+) load=(\d+) unload=(\d+) shutdown=(\d+) reset=(\d+) run_frame=(\d+)$", nested, re.M)
        self.assertGreaterEqual(len(calls), 4)                                  # at every boundary, swallowed or not
        for call in calls:
            self.assertEqual(list(map(int, call)), [BUSY] * 6)
        self.assertEqual(re.sub(r"^nested .*\n", "", nested, flags=re.M), plain)   # nothing of the machine moved


if __name__ == "__main__":
    unittest.main()
