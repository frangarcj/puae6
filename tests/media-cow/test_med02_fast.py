"""MED02 and STATE01 with Zorro II Fast RAM (T49 over T27): the A1200 with 8 MiB of Fast RAM and writable floppies.

Everything tests/media-cow/test_med02.py asks of the write overlay is asked again of a machine that also has the 8 MiB board (the guest writes, overwrites and reads back whole tracks; the base
keeps its hash; the state carries the overlay and a load takes the writes back, in this process and in a new one; the states of a session with writes and of one without do not load into each
other), and the two additions to the state add up: the bound of the machine with both is the bound of the machine with neither plus the board (4 + 8 MiB + the 278 bytes of its autoconfig
chain) plus the overlay (its count, the pool of 3520 sectors and the 16 raw track slots), and the configuration hash separates all four combinations (flag bit 0 of p6_config.flags and the
memory bits above it do not collide).
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import test_med02 as m  # noqa: E402
from tests.state import envelope as e  # noqa: E402
from tests.state.test_state_fast import identity_flags  # noqa: E402

FAST = ["--fast", "8192"]
BOARD = 4 + (8 << 20) + 22 + 256        # ram.fast_size, the board and the autoconfig block of P6RM (bridge/state.cpp visit_expansion)


class Med02FastTests(m.Med02Tests):
    """The whole MED02 scenario on the A1200 with 8 MiB of Fast RAM."""

    MACHINE = "a1200"

    @classmethod
    def args_for(cls, writable=True, poke=True):
        return [*m.Med02Case.args_for.__func__(cls, writable, poke), *FAST]

    @classmethod
    def run_text(cls, *arguments):
        """The probe runs of the scenario (test_med02 starts them without a machine, which is the A500) on the machine of this class."""
        return m.Med02Case.run_text.__func__(cls, "--machine", cls.MACHINE, *arguments)

    def bound_of(self, *arguments):
        run = self.probe_on(self.MACHINE, "med02", "--units", "1", "--public-at", "1", *arguments)
        return run.public_size[0][1]

    def test_the_two_additions_to_the_bound_add_up(self):
        plain = self.bound_of(*m.Med02Case.args_for.__func__(type(self), False, False))
        writes = self.bound_of(*m.Med02Case.args_for.__func__(type(self), True, False))
        fast = self.bound_of(*m.Med02Case.args_for.__func__(type(self), False, False), *FAST)
        both = self.bound_of(*m.Med02Case.args_for.__func__(type(self), True, False), *FAST)
        overlay = m.OVERLAY_FIXED + m.POOL * m.SLOT_BYTES + m.RAW_BOUND
        self.assertEqual(writes - plain, overlay)
        self.assertEqual(fast - plain, BOARD)
        self.assertEqual(both - plain, overlay + BOARD)

    def test_the_configuration_hash_tells_the_four_combinations_apart(self):
        hashes = {}
        for label, writable, fast in (("neither", False, False), ("writes", True, False), ("fast", False, True), ("both", True, True)):
            args = [*m.Med02Case.args_for.__func__(type(self), writable, False), *(FAST if fast else [])]
            path = self.dir / f"{label}.state"
            self.probe_on(self.MACHINE, "med02", "--units", "2", "--save-at", "2", str(path), *args)
            hashes[label] = path.read_bytes()[176:208]
        flags = {"neither": 0, "writes": 1, "fast": identity_flags(2048, 0, 8192), "both": identity_flags(2048, 0, 8192, flags=1)}
        for label, wanted in flags.items():
            self.assertEqual(e.config_hash(3, 1, 48000, wanted, 1, 946684800), hashes[label], label)
        self.assertEqual(4, len(set(hashes.values())))
        self.assertEqual(1, flags["both"] & 1, "the p6_config flag is bit 0 and the memory sits above bit 7")

    def test_the_state_of_the_machine_with_both_does_not_load_into_one_with_only_the_board_or_only_the_writes(self):
        both = self.state_of("both.state", "med02", 5, *self.args_for(poke=False))
        (self.dir / "both.state").write_bytes(both)
        for label, arguments in (("only the board", [*m.Med02Case.args_for.__func__(type(self), False, False), *FAST]), ("only the writes", m.Med02Case.args_for.__func__(type(self), True, False))):
            text, run = self.run_text("--units", "6", "--every", "6", *arguments, "--load-at", "5", str(self.dir / "both.state"))
            self.assertEqual(run.loads[0][0], 5, label)
            self.assertNotEqual(run.loads[0][1], 0, f"{label}: the state of both is refused")


if __name__ == "__main__":
    unittest.main()
