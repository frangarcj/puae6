"""T52: DET02 with the clock of the CPU at every multiplier (p6_config.cpu_multiplier: 1, 2, 4, 8, 10, 12 and 16, PUAE5's values): a state taken in the middle of a program comes back to the
machine of the original whatever the clock the CPU runs at.

The multiplier is upstream's cpu_clock_multiplier: one CPU clock lasts 1/N of a colour clock pair, so every instruction and every bus access costs fewer or more emulated colour clocks
and the instants at which the CPU meets the chipset (a DMA slot, an interrupt, the end of a blit) move. Nothing of it is a number of the host: the cycle unit (cpucycleunit) is derived
from the configuration when the machine starts, so a state needs nothing of it, and the configuration hash carries the multiplier when it is not the machine's own (a state of one clock is
INCOMPATIBLE in another: tests/state/test_state_cpu010.py). What is decided here, with the machinery, the modes and the divergence tables of tests/state/test_det02.py (nothing is relaxed):
  * the 68000 of the A500 at 3.55 MHz, 28 MHz and 56.75 MHz runs the programs of the A500 (vertical blank, the copper, audio, the busy bus with every DMA, the disk, the CIAs) and its state
    comes back, in the same image, in a restarted one and in a new process;
  * the 68010 does the same with its loop mode, its vector base and its address error frame;
  * the 68EC020 of the A1200 at 3.55, 28.4 and 56.75 MHz runs the 68020 programs (the instruction cache, the exception frames, the 020 instructions) and the AGA ones.
"""
import unittest

from tests.state import test_det02 as d
from tests.state import test_det02_a1200 as a1200
from tests.state import test_det02_cpu010 as c10

FAST, SLOW = 16, 1

BASE = {
    "vsync": ("vsync", [], (3,), d.CORE),
    "copper_irq": ("copper_irq", [], (2,), d.CORE),
    "tone": ("tone", [], (3,), d.CORE),
    "busy": ("busy", [], (2, 5), d.CORE),
    "busy-disk": ("busy", "disk", (8,), d.CORE),
    "stop": ("stop", [], (3,), d.SHAPES),
    "cias": ("cias", [], (8,), d.SHAPES),
}
C010 = {name: scenario for name, scenario in c10.SCENARIOS.items() if name.startswith("c10-")}
A1200_SCENARIOS = {name: a1200.SCENARIOS[name] for name in ("vsync", "tone", "busy", "cache020", "exceptions", "ops020", "aga-8planes", "aga-sprites")}

# Where DET02 diverges with another clock and not with the machine's own: none.
DIVERGENCES = {}


def make(name, machine, roms, scenarios, multiplier, cpu=None):
    arguments = ["--cpu-multiplier", str(multiplier)] + (["--cpu", cpu] if cpu else [])

    class Case(d.Det02Case):
        MACHINE = machine
        ROMS = roms

        @classmethod
        def probe_on(cls, machine, rom, *given):
            extra = [] if "--cpu-multiplier" in given else arguments
            return super().probe_on(machine, rom, *given, *extra)

    Case.__name__ = Case.__qualname__ = name
    d.populate(Case, scenarios, DIVERGENCES)
    return Case


Det02A500Clock1Tests = make("Det02A500Clock1Tests", "a500", c10.ROMS, BASE, 1)
Det02A500Clock16Tests = make("Det02A500Clock16Tests", "a500", c10.ROMS, BASE, 16)
Det02A500Clock8Tests = make("Det02A500Clock8Tests", "a500", c10.ROMS, {k: BASE[k] for k in ("vsync", "busy", "tone")}, 8)
Det02Cpu010Clock1Tests = make("Det02Cpu010Clock1Tests", "a500", c10.ROMS, C010, 1, cpu="68010")
Det02Cpu010Clock16Tests = make("Det02Cpu010Clock16Tests", "a500", c10.ROMS, C010, 16, cpu="68010")
Det02A1200Clock1Tests = make("Det02A1200Clock1Tests", "a1200", a1200.ROMS, A1200_SCENARIOS, 1)
Det02A1200Clock2Tests = make("Det02A1200Clock2Tests", "a1200", a1200.ROMS, {k: A1200_SCENARIOS[k] for k in ("vsync", "cache020", "ops020", "aga-8planes")}, 2)
Det02A1200Clock8Tests = make("Det02A1200Clock8Tests", "a1200", a1200.ROMS, {k: A1200_SCENARIOS[k] for k in ("vsync", "busy", "exceptions", "aga-sprites")}, 8)
Det02A1200Clock16Tests = make("Det02A1200Clock16Tests", "a1200", a1200.ROMS, A1200_SCENARIOS, 16)


if __name__ == "__main__":
    unittest.main()
