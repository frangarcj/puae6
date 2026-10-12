"""T30b, DET02: a state taken while the real Fatal Mission runs from its WHDLoad volume goes back the same (native engine, A1200 with the owner's Kickstart 3.1).

The machinery, the modes and what a divergence table is are in tests/state/test_det02.py; nothing is relaxed here. The machine boots DH0: (the prepared image of tests/whdload/support.py, with no floppy),
its script runs WHDLoad, the game loads its data through WHDLoad's resload and takes the keyboard and the joystick; the states are taken inside the boot, while the data loads, with the title on the
screen, with the game started by fire (port 1) and after F10 has quit WHDLoad and the script has written to the volume (a real write: the overlay holds chunks).

Opt-in (see tests/whdload/support.py): the free downloads and the owner's Kickstart. The probe is the one P6_STATE_PROBE_BUILD selects.
"""
import unittest

from tests.state import test_det02 as d
from tests.whdload import support

# Where DET02 diverges with the real game (found by T30b; the engine is out of this task's reach, so the table pins it and T15m owns it): with the game started (fire at unit 400) and its sound playing, the
# machine after a load matches the original in every digest (CPU, RAM, chipset, audio, picture) and the PCM it hands over differs from the first unit after the load and stays different: the waveform of
# the steady tone the game plays is another one. The boot, the title, the volume's writes and the end of the game (after F10 the sound is off) match.
CAUSE = "the PCM of a game with Paula DMA playing is another waveform from the first unit after the load, with every digest equal: audio state that neither P6AU nor AUDP carries"
DIVERGENCES = {f"whdload-game@{n}/{mode}": (1, ("pcm",), "T15m", CAUSE) for n in (420, 700) for mode in (d.SAME_BEAM, d.NEW)}

# name -> (rom, probe arguments, units N at which the state is taken, modes). The history before N is part of the arguments; the units above N are the input the loaded machine receives.
SCENARIOS = {
    "whdload-boot": ("kickstart", "image", (120, 240), (d.SAME_BEAM, d.NEW)),
    "whdload-title": ("kickstart", "image", (330,), (d.SAME_BEAM, d.NEW)),
    "whdload-game": ("kickstart", "image+fire", (420, 700), (d.SAME_BEAM, d.NEW)),
    "whdload-quit": ("kickstart", "image+fire+quit", (1502,), (d.SAME_BEAM, d.NEW)),
}


@unittest.skipUnless(support.AVAILABLE, support.SKIP)
class Det02WhdloadTests(d.Det02Case):
    MACHINE = "a1200"

    @classmethod
    def firmware(cls):
        from pathlib import Path
        return Path(support.KICKSTART)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        image, cls.manifest = support.prepare()
        cls.hdf = cls.dir / "fatal-mission.hdf"
        cls.hdf.write_bytes(image)

    def arguments(self, extra):
        if isinstance(extra, str) and extra.startswith("image"):
            args = ["--hdf", str(self.hdf)]
            if "fire" in extra:
                args += support.fire()
            if "quit" in extra:
                args += support.quit_key()
            return args
        return super().arguments(extra)


d.populate(Det02WhdloadTests, SCENARIOS, DIVERGENCES)


if __name__ == "__main__":
    unittest.main()
