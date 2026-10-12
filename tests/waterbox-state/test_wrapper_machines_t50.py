"""T50: WB03 and the identity of a state and of a movie on the A500 OG, the A500+, the A1000, the A2000 OG and the A2000, in BizHawk's Waterbox host (the real sandbox snapshot,
never the native serializer).

tests/waterbox-state/test_wrapper_machine.py has the tests; this module runs them on the five machines that came after the A500, the A600 and the A1200 (the machine is the
`Machine` sync setting of the core, a restart setting) with the matrix of identity over all eight:

WB03  save at N, go on M units, load, go on M again: the same units and the same digest; a state of one sandbox runs in a new one (WB04) and in another process (WB15).
ID    a state or a movie of one machine is refused by the core of each of the other seven, with a message that names both, before the sandbox is touched (the instance that refused
      it goes on as if never asked), and each machine takes its own; a movie of each plays back to the units of its recording.

Opt-in with the owner's firmware (a path in an environment variable, read-only, nothing copied, hashed or recorded):
    P6_KICKSTART_ROM  the Kickstart 1.3 .rom (a raw file): WB03/WB04/WB15 with the real ROM on the A500 OG, the A1000 and the A2000 OG.
The Kickstart 2.04 (A500+) and the 3.1 r40.063 (A2000) have no authorized copy: those two machines run micro-ROMs only.

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_machines_t50.py"
"""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_wrapper_machine as base  # noqa: E402

NEW = ("a500og", "a500plus", "a1000", "a2000og", "a2000")
KICKSTART_13 = os.environ.get("P6_KICKSTART_ROM")


@unittest.skipUnless(base.runtime_available() and base.TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6)")
class NewMachineWrapperTests(base.WrapperMachineTests):
    MACHINES = NEW
    UNIVERSE = base.MACHINES + NEW
    MOVIE_PLAYBACK = NEW
    SAVE_POINTS = (10, 85)

    # The tests of the three machines of T23e and T24f stay there; the real-firmware ones of the A500, the A600 and the A1200 and the "two machines are not one" test are theirs.
    test_the_two_machines_are_two_machines_not_one_with_two_names = None
    test_wb03_with_the_real_kickstart_205_on_the_a600 = None
    test_wb03_with_the_real_kickstart_13_on_the_a500 = None
    test_wb03_with_the_real_kickstart_31_on_the_a1200 = None
    test_wb03_a_snapshot_in_the_middle_of_an_aga_demo_continues_in_a_new_sandbox_and_in_another_process = None

    def test_the_eight_machines_are_eight_machines_not_one_with_eight_names(self):
        # The same micro-ROM, the same disks, the same input: the snapshot of the sandbox says which machine it is (memory, chipset, clock).
        names = list(self.UNIVERSE)
        lines = [self.core(chr(ord("A") + i), machine) for i, machine in enumerate(names)]
        lines += [f"run {chr(ord('A') + i)} 30" for i in range(len(names))]
        lines += [f"digest {chr(ord('A') + i)} {chr(ord('a') + i)} machine" for i in range(len(names))]
        out = self.session(lines)
        found = [self.digests(out, chr(ord("A") + i))[chr(ord("a") + i)] for i in range(len(names))]
        # The A500, the A500 OG and the A2000 OG draw the same OCS picture from the same program but not the same snapshot: their memory, their clock and their TOD differ.
        self.assertEqual(len(names), len(set(found)), dict(zip(names, found)))

    @unittest.skipUnless(KICKSTART_13 and os.path.isfile(KICKSTART_13 or ""), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3 .rom")
    def test_wb03_with_the_real_kickstart_13_on_the_a500_og_the_a1000_and_the_a2000_og(self):
        rom = str(Path(KICKSTART_13).resolve())
        for machine in ("a500og", "a1000", "a2000og"):
            with self.subTest(machine=machine):
                self.real_firmware_roundtrip(machine, rom)


if __name__ == "__main__":
    unittest.main()
