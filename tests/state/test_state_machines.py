"""T50: the native state of the A500 OG, A500+, A1000, A2000 OG and A2000, and the line between every pair of machines.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure): the state of each new machine has the eleven chunks of the A500's, in the same order and
with the same versions. What follows the machine, not the chunk list:
  * P6RT (the clock) is the 31-byte body of the MSM6242B where the machine has the chip (A500+, A2000 OG, A2000, as the A500) and EMPTY where it has none (A500 OG, A1000, as the A600);
  * P6CH carries one more sub-body, CTOD (patch 0240), exactly on the machines whose CIA-A TOD counts the mains (A1000, A2000 OG, A2000): 8 bytes and its header, so the bytes of every other
    machine's state, the A500, the A600 and the A1200 among them, are what they were;
  * the memory chunk follows the preset (512 KiB or 1 MiB of Chip, 0 or 512 KiB of Slow).
A state of one machine is INCOMPATIBLE in every other by its configuration hash, before anything is applied: the matrix is the A500, the A600 and the A1200 (whose files fix their layouts)
and the five new ones, each against each; and a state loaded into a new process is saved again byte for byte.
"""
import itertools
import unittest

from tests.state import envelope as e
from tests.state.harness import ProbeCase, parse_chunks

RTC, VIDEO = 3, 5
INCOMPATIBLE = 10
MACHINES = ("a500", "a600", "a1200", "a500og", "a500plus", "a1000", "a2000og", "a2000")
NEW = ("a500og", "a500plus", "a1000", "a2000og", "a2000")
CLOCK = {"a500og": False, "a500plus": True, "a1000": False, "a2000og": True, "a2000": True}
MAINS_TOD = {"a1000", "a2000og", "a2000"}
CHIP_SLOW = {"a500og": (1 << 19, 0), "a500plus": (1 << 20, 0), "a1000": (1 << 19, 0), "a2000og": (1 << 19, 1 << 19), "a2000": (1 << 20, 0)}


class StateMachinesCase(ProbeCase):
    ROMS = {"vsync": ("vsync", None, None)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.states = {}
        for machine in MACHINES:
            path = cls.dir / f"{machine}.state"
            cls.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path))
            cls.states[machine] = path.read_bytes()


class LayoutTests(StateMachinesCase):
    def test_each_new_machine_has_the_chunks_of_the_a500_and_the_body_its_hardware_gives_them(self):
        control = parse_chunks(self.states["a500"])
        for machine in NEW:
            chunks = parse_chunks(self.states[machine])
            with self.subTest(machine=machine):
                self.assertEqual([(tag, version) for tag, version, _ in control], [(tag, version) for tag, version, _ in chunks])
                self.assertEqual(31 if CLOCK[machine] else 0, len(chunks[RTC][2]), "P6RT: the clock of the MSM6242B, or nothing")
                chip, slow = (int.from_bytes(chunks[2][2][at:at + 4], "little") for at in (0, 4))
                self.assertEqual(CHIP_SLOW[machine], (chip, slow))

    def test_the_mains_tod_adds_one_body_to_the_chipset_chunk_and_nothing_else_moves(self):
        control = parse_chunks(self.states["a500"])
        for machine in NEW:
            chunks = parse_chunks(self.states[machine])
            plain = [len(body) for _, _, body in chunks]
            with self.subTest(machine=machine):
                self.assertEqual(MAINS_TOD >= {machine}, b"CTOD" in self.states[machine], "the CTOD body")
                for index, (tag, _, body) in enumerate(control):
                    if tag in (b"P6RT", b"P6VD", b"P6RM"):         # the clock, the picture rows and the memory sizes follow the machine
                        continue
                    if tag == b"P6CH":
                        # 8 bytes of CTOD and its 8-byte header (tag and length) on a machine whose TOD counts the mains
                        self.assertEqual(len(body) + (16 if machine in MAINS_TOD else 0), plain[index], tag)
                    else:
                        self.assertEqual(len(body), plain[index], tag)

    def test_the_machines_that_count_the_vertical_sync_have_no_ctod_body(self):
        for machine in ("a500", "a600", "a1200", "a500og", "a500plus"):
            with self.subTest(machine=machine):
                self.assertNotIn(b"CTOD", self.states[machine])


class MatrixTests(StateMachinesCase):
    def test_a_state_of_one_machine_is_incompatible_in_every_other(self):
        for own, foreign in itertools.permutations(MACHINES, 2):
            if own not in NEW and foreign not in NEW:
                continue                        # the A500, A600 and A1200 matrix is tests/waterbox-state/test_wrapper_machine.py and tests/state/test_state_a1200.py
            path = self.dir / f"{foreign}-for-{own}.state"
            path.write_bytes(self.states[foreign])
            run = self.probe_on(own, "vsync", "--units", "3", "--load-at", "3", str(path))
            with self.subTest(own=own, foreign=foreign):
                self.assertEqual([(3, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_a_state_loaded_into_a_new_process_is_saved_again_byte_for_byte(self):
        for machine in NEW:
            again = self.dir / f"{machine}-again.state"
            source = self.dir / f"{machine}.state"
            run = self.probe_on(machine, "vsync", "--units", "0", "--load-at", "0", str(source), "--save-at", "0", str(again))
            with self.subTest(machine=machine):
                self.assertEqual([(0, 0, "")], run.loads)
                self.assertEqual(self.states[machine], again.read_bytes())


class RewoundCase(ProbeCase):
    """The state of a machine that went back to an earlier state and ran a unit, against the state of the one that never went back (the whole bytes, which no DET02 digest sees)."""

    ROMS = {"vsync": ("vsync", None, None)}

    def differences(self):
        arguments = ("--port", "0:2:1:3:-2", "--key", "0", "0x20", "down")
        self.probe("vsync", "--units", "6", *arguments, "--save-at", "3", str(self.dir / "g.state"), "--save-at", "4", str(self.dir / "natural.state"),
                   "--load-at", "5", str(self.dir / "g.state"), "--save-at", "6", str(self.dir / "rewound.state"))
        natural, rewound = (parse_chunks((self.dir / f"{name}.state").read_bytes()) for name in ("natural", "rewound"))
        return [(tag, [i for i in range(len(body)) if body[i] != rewound[k][2][i]][:4]) for k, (tag, _, body) in enumerate(natural) if body != rewound[k][2]]


def rewound(machine):
    class Case(RewoundCase):
        MACHINE = machine

        def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
            self.assertEqual([], self.differences())
    Case.__name__ = Case.__qualname__ = f"Rewound{machine.upper()}Tests"
    return Case


RewoundA500OGTests = rewound("a500og")
RewoundA500PlusTests = rewound("a500plus")
RewoundA1000Tests = rewound("a1000")
RewoundA2000OGTests = rewound("a2000og")
RewoundA2000Tests = rewound("a2000")

del StateMachinesCase, RewoundCase


if __name__ == "__main__":
    unittest.main()
