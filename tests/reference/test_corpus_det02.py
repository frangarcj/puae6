"""DET02 over the differential corpus (tests/scenarios/corpus): every scenario, on its own machine, saved at the units it names and loaded back into the same image, an image that
restarted its program, a fresh process or an image with other media, and compared with the original subsystem by subsystem and sample by sample. The machinery, the modes and the
form of a divergence table are those of tests/state/test_det02.py, which this reuses unchanged; the scenarios here are the ones the corpus adds (the register-ring programs in
particular) or repeats on the three machines, so that the corpus gives its own DET02 verdict per scenario.

A scenario that diverges is a test that EXPECTS to fail next to a test that pins where, with the owner and what is known of the cause (DIVERGENCES): it blocks the release until
its owner task closes it, and the day it matches its entry leaves the table and nothing else changes."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.reference import corpus_run  # noqa: E402
from tests.scenarios.corpus import corpus  # noqa: E402
from tests.state import test_det02 as d  # noqa: E402

# "scenario@N/mode" -> (units after the load at which the first difference shows, what differs there, owner, cause). See tests/state/test_det02.py.
# Empty: the two divergences the corpus found (T32b) were closed by T15n. ecs-ring-cpu-pal (a CPU loop that rewrites BPLCON0/1/2, BPLCON3, DIWHIGH and the colours on the A600 diverged one
# unit after the load, in the same image and in a restarted one) lost the bitplane DMA of the first display line because the bitplane sequencer of Agnus (bprun, ddf_stopping...) was not
# in the state: patch 0261 puts it in BEAM. ecs-beam-var-wide-pal (a programmed beam loaded into a fresh process) kept the derived lengths of the PAL reset: patch 0260 runs
# init_beamcon0 when the derived values are rebuilt.
DIVERGENCES = {}


def scenarios(machine):
    return {entry.id: (entry.program, entry.extra if isinstance(entry.extra, str) else list(entry.args), entry.points, entry.modes)
            for entry in corpus.ENTRIES if entry.machine == machine}


def roms(machine):
    texts = corpus.programs()
    return {entry.program: (texts[entry.program], None, None) for entry in corpus.ENTRIES if entry.machine == machine}


class CorpusCase(d.Det02Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assets = corpus_run.Assets(cls.dir)

    def arguments(self, extra):
        if isinstance(extra, str) and extra in corpus.MEDIA and extra not in ("disk", "swap"):
            return self.assets.expand(extra)
        return super().arguments(extra)


def populate(cls, table, divergences):
    """One DET02 test per scenario, boundary and mode. A divergence the table lists is NOT an expected failure here: its test checks that it is where the table says (the first unit
    after the load and the subsystems that differ there), so that a divergence that moves, or that goes away, is seen; and OpenDivergenceGate fails for as long as the table is not empty,
    which is what blocks the release (T32). A scenario that matches is an ordinary test."""
    for name, (rom, extra, points, modes) in table.items():
        for n in points:
            for mode in modes:
                key = f"{name}@{n}/{mode}"
                slug = key.replace("@", "_at_").replace("/", "_").replace("-", "_").replace("+", "_")

                def det02(self, rom=rom, extra=extra, n=n, mode=mode, key=key):
                    found = self.first_divergence(rom, extra, n, mode)
                    self.assertIsNone(found, f"{key}: not the original {found and found[0]} units after the load, in {found and found[1]}")

                def pin(self, rom=rom, extra=extra, n=n, mode=mode, key=key):
                    found = self.first_divergence(rom, extra, n, mode)
                    unit, subsystems, owner, cause = divergences[key]
                    self.assertEqual((unit, list(subsystems)), found and found[:2], f"{key}: the divergence moved ({owner}: {cause})")

                if key in divergences:
                    setattr(cls, f"test_det02_pinned_divergence_{slug}", pin)
                else:
                    setattr(cls, f"test_det02_{slug}", det02)


class CorpusA500Tests(CorpusCase):
    ROMS = roms("a500")


class CorpusA600Tests(CorpusCase):
    MACHINE = "a600"
    ROMS = roms("a600")


class CorpusA1200Tests(CorpusCase):
    MACHINE = "a1200"
    ROMS = roms("a1200")


populate(CorpusA500Tests, scenarios("a500"), DIVERGENCES)
populate(CorpusA600Tests, scenarios("a600"), DIVERGENCES)
populate(CorpusA1200Tests, scenarios("a1200"), DIVERGENCES)


@unittest.skipUnless(os.environ.get("P6_RELEASE_GATE"), "release gate: set P6_RELEASE_GATE=1 (the closure of T32 does); open divergences found by the corpus are listed in DIVERGENCES")
class OpenDivergenceGate(unittest.TestCase):
    """The release gate of the corpus (the closure of T32 runs it; T32b does not): no scenario of the corpus may diverge."""

    def test_no_scenario_of_the_corpus_diverges_in_det02(self):
        self.assertEqual({}, {key: row[2:] for key, row in DIVERGENCES.items()}, "open divergences found by the corpus: each blocks the release until its owner task closes it")


if __name__ == "__main__":
    unittest.main()
