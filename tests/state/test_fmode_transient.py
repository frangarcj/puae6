"""T24 closure: the FMODE that is still in flight at the unit boundary (the transient T24c found after a load) cannot be seen.

What T24c found: after a state is loaded, fetchmode_fmode_bpl / fetchmode_fmode_spr (Agnus) and fmode_denise / denise_sprfmode / denise_bplfmode (Denise) of the loaded machine
already hold the FMODE of the registers, a few colour clocks before the original's: the original changes them when the delayed event setup_fmodes_delayed fires (2 CCK after the
write; event id 12 in the state) and when Denise's line pass consumes the FMODE slot of its ring. The state is carried, so it converges in less than a line, and no DET02 scenario saw it.

Why nothing can see it (measured with lldb on build/state-opt/p6_state_probe, A1200, this program, unit 3 saved and loaded into a new process):
  * a unit ends in the vertical blank, at vpos 4 and hpos 3 or 4. The original fires the delayed event at (4, 4) and its Denise takes the new FMODE at the pass of line 4 that runs
    at (4, 22); the loaded machine has the same values from the load. Agnus has no consumer of fetchmode_fmode_* in between: bitplane DMA starts at CCK $18 or later, sprite DMA does
    not run above line 25 (a sprite at vstart 5 never appears: its picture is that of the program without sprites), audio DMA only reads its own registers.
  * Denise is driven only by its ring: the first thing it does after a load is the pass of line 4 (draw_denise_line at beam (4, 22)), and that pass consumes the FMODE slot first
    (do_denise_cck -> expand_fmode), then the sprite slots written after the boundary (SPRxPOS/SPRxCTL arrive in the same pass through lts_unaligned_aga), in the same order as in
    the original. The ring is part of the state (DRNG, T24c), and so is the event.
So the scenarios below put the worst case on the boundary: the Copper writes FMODE at line 3, CCK $DE (sprite variants: $E0), so that the event is pending at the boundary (checked below, not assumed), with
fetch modes of 16, 32 and 64 bits in both directions, and (sprite variants) arms a manual sprite through the Copper in the CCKs right after the boundary, where the loaded machine
would differ if the transient mattered. They match in every DET02 mode (same image, restarted image, new process, with and without the beam set).

    python3 -m unittest tests.scenarios.aga.test_fmode_transient      # ~25 s with a built build/state-opt
"""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.state import test_det02 as d  # noqa: E402
from tests.state import test_det02_a1200 as a  # noqa: E402

HANDLER_SETUP_FMODES_DELAYED = 12     # the id a state gives setup_fmodes_delayed (bridge/state_cpu.cpp)
EV_MAX, EV2_MAX = 4, 16               # eventtab and eventtab2 of events.h: the layout of the probe's "scheduler" digest (bridge/state_probe.cpp scheduler())


def pending_handlers(scheduler):
    """The ids of the handlers of the active secondary events in the probe's scheduler dump (two cycle counters, then eventtab, then eventtab2)."""
    at = 16
    for _ in range(EV_MAX):
        active = scheduler[at]
        at += 1 + (8 if active else 0) + 8
    found = []
    for _ in range(EV2_MAX):
        active = scheduler[at]
        at += 1
        if active:
            found.append(struct.unpack_from("<qQB", scheduler, at)[2])
            at += 17
    return found


def program(*, fm_a, fm_b, sprite, wait):
    """Eight planes and the whole palette, FMODE = fm_b from the CPU and, every frame, `fm_a` from the Copper after a WAIT for line 3, CCK `wait` (the write that is in flight at the unit boundary) and `fm_b` again at
    line $60. With `sprite`, the Copper arms sprite 0 by hand (POS, CTL, DATB, DATA) right after the FMODE write: the writes land in the CCKs after the boundary."""
    t = ["; T24 closure: FMODE in flight at the unit boundary (tests/scenarios/aga/test_fmode_transient.py)\n", a.PROLOGUE, a.palette(), a.planes(8)]
    t.append("    MOVE.W #$0F0F,$DFF1A2.L\n")
    words = [0x0300 | wait | 1, 0xFFFE, 0x01FC, fm_a]
    if sprite:
        words += [0x0140, 0x0040, 0x0142, 0x0000, 0x0146, 0x0FF0, 0x0144, 0xF0F0]
    words += [0x6001, 0xFFFE, 0x01FC, fm_b]
    if sprite:
        words += [0x0142, 0x0000]
    words += [0xFFFF, 0xFFFE]
    for i in range(0, len(words), 2):
        t.append("    MOVE.L #$%08X,$%X.L\n" % (words[i] << 16 | words[i + 1], 0x120000 + 2 * i))
    t.append("    MOVE.L #$120000,$DFF080.L\n")
    t.append("    MOVE.W #$%04X,$DFF1FC.L\n    MOVE.W #$0210,$DFF100.L\n" % fm_b)
    t.append("    MOVE.W #$0011,$DFF10C.L\n    MOVE.W #$0024,$DFF104.L\n    MOVE.W #$0018,$DFF092.L\n    MOVE.W #$00A0,$DFF094.L\n")
    t.append("    MOVE.W #$0481,$DFF08E.L\n    MOVE.W #$04C1,$DFF090.L\n    MOVE.W #$0000,$DFF108.L\n    MOVE.W #$0000,$DFF10A.L\n")
    for p in range(8):
        t.append("    MOVE.L #$%X,$DFF%03X.L\n" % (0x100000 + p * 0x4000, 0xE0 + 4 * p))
    t.append("    MOVE.L #h,$6C.L\n    MOVE.W #$C020,$DFF09A.L\n    MOVE.W #$8380,$DFF096.L\n    MOVE.W #$2000,SR\nloop:\n    ADDQ.L #1,D0\n    BRA.S loop\n")
    t.append("h:\n    MOVE.W #$0020,$DFF09C.L\n")
    for p in range(8):
        t.append("    MOVE.L #$%X,$DFF%03X.L\n" % (0x100000 + p * 0x4000, 0xE0 + 4 * p))
    t.append("    RTE\n")
    return "".join(t)


# (FMODE of the Copper at the boundary, FMODE the rest of the frame): 16 -> 64 bits, 64 -> 16, 32 -> 16 and 16 -> 32 for the bitplanes and the sprites at once, and for each separately.
VARIANTS = {"f0": (0x000F, 0x0000), "0f": (0x0000, 0x000F), "c0": (0x000C, 0x0000), "03": (0x0003, 0x0000), "3c": (0x0003, 0x000C)}
ROMS, SCENARIOS = {}, {}
for tag, (fm_a, fm_b) in VARIANTS.items():
    for sprite in (False, True):
        name = "fmode-flight-%s%s" % (tag, "-sprite" if sprite else "")
        # The WAIT that puts the write of FMODE just before the boundary depends on the program (the Copper of the sprite variant is one CCK later) and the boundary alternates with
        # the long and short frame: the units listed end with the event pending, which test_the_event_is_pending... checks for each of them.
        wait, points = (0xE0, (2, 4, 6)) if sprite else (0xDE, (3, 4, 6))
        ROMS[name] = (program(fm_a=fm_a, fm_b=fm_b, sprite=sprite, wait=wait), None, None)
        SCENARIOS[name] = (name, [], points, d.CORE)


class FmodeTransientTests(d.Det02Case):
    MACHINE = "a1200"
    ROMS = ROMS

    def test_the_event_is_pending_at_the_boundary_so_the_scenarios_are_not_vacuous(self):
        for name in ROMS:
            with self.subTest(name):
                run = self.probe(name, "--units", "7", "--every", "1", "--dump", "scheduler")
                for unit in SCENARIOS[name][2]:
                    self.assertIn(HANDLER_SETUP_FMODES_DELAYED, pending_handlers(run.dumps[(unit, "scheduler")]), f"no setup_fmodes_delayed pending at the end of unit {unit}")


d.populate(FmodeTransientTests, SCENARIOS, {})

if __name__ == "__main__":
    unittest.main()
