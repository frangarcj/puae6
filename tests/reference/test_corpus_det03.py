"""DET03 over the differential corpus: a state saved through the PUBLIC ABI (p6_state_bound, p6_state_save) in one process continues, in a new process configured the same way that loads
it through p6_state_load, as the original did (tests/state/test_det03.py has the machinery, the corpus has the scenarios). One state per scenario, at the first unit its DET02 entry names;
the new process is a fresh instance (a fresh instance that booted with the first medium for the swap scenario, whose history of media events cannot be replayed)."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.reference import test_corpus_det02 as base  # noqa: E402
from tests.scenarios.corpus import corpus  # noqa: E402
from tests.state import test_det02 as d  # noqa: E402

# "scenario@N/mode" -> divergence, as in DET02: the ones that DET02 has, if the public path shows them too (the ring scenario of the A600 needs a machine with history, and DET03 loads into a
# fresh one, which is the mode that matches).
DIVERGENCES = {key: row for key, row in base.DIVERGENCES.items() if key.endswith("/new-process") and key.split("@")[1].split("/")[0] == "4"}


def scenarios(machine):
    out = {}
    for entry in corpus.ENTRIES:
        if entry.machine != machine:
            continue
        mode = d.BOOT_A if entry.extra == "swap" else d.NEW
        out[entry.id] = (entry.program, entry.extra if isinstance(entry.extra, str) else list(entry.args), entry.points[-1:], (mode,))
    return out


class CorpusDet03A500Tests(base.CorpusCase):
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"
    ROMS = base.roms("a500")


class CorpusDet03A600Tests(base.CorpusCase):
    MACHINE = "a600"
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"
    ROMS = base.roms("a600")


class CorpusDet03A1200Tests(base.CorpusCase):
    MACHINE = "a1200"
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"
    ROMS = base.roms("a1200")


base.populate(CorpusDet03A500Tests, scenarios("a500"), DIVERGENCES)
base.populate(CorpusDet03A600Tests, scenarios("a600"), DIVERGENCES)
base.populate(CorpusDet03A1200Tests, scenarios("a1200"), DIVERGENCES)


@unittest.skipUnless(os.environ.get("P6_RELEASE_GATE"), "release gate: set P6_RELEASE_GATE=1 (the closure of T32 does); open divergences found by the corpus are listed in DIVERGENCES")
class OpenDivergenceGate(unittest.TestCase):
    """The release gate of the corpus for the public path (the closure of T32 runs it)."""

    def test_no_scenario_of_the_corpus_diverges_through_the_public_abi(self):
        self.assertEqual({}, {key: row[2:] for key, row in DIVERGENCES.items()})

if __name__ == "__main__":
    unittest.main()
