"""T23b: the native state on the A600 profile (ECS, 1 MiB Chip, no Slow RAM, no clock chip) and the line between the two machines.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure): the A600 state has the same eleven chunks, in the same order and with the same
versions as the A500's, and one body is different, P6RT: the machine has no clock chip (cs_rtc == 0), so there is no clock for the chunk to carry and its body is EMPTY. The chunk is
not left out because the table is the same for every machine (a missing chunk is refused), and the live machine, not the state, says which of the two bodies it has: a clock body in a
machine without the chip and an empty one in a machine with it are both refused by their length, before anything is applied. The layout of the A500 is not touched (its own tests, which
fix it byte by byte, do not change). The memory chunk follows the machine too (1 MiB Chip and 0 Slow against 512 KiB and 512 KiB) and a state of one machine is INCOMPATIBLE in the other
by its configuration hash, before anything is applied; with the hash forged so that the identity matches, the chunks refuse it the same way.
"""
import unittest

from tests.state import envelope as e
from tests.state.harness import ProbeCase, parse_chunks, reforge

RAM, RTC, VIDEO = 2, 3, 5  # indices of the chunks in the state
CONFIG_HASH = slice(176, 208)
INCOMPATIBLE = 10
CORRUPT = 9


class CrossMachineCase(ProbeCase):
    """A machine and the state of the other one. `OTHER` names the other; the states are taken at unit 3 of the vsync program."""

    ROMS = {"vsync": ("vsync", None, None)}
    OTHER = ""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own = cls.state_at(cls.MACHINE, "own")
        cls.foreign = cls.state_at(cls.OTHER, "foreign")

    @classmethod
    def state_at(cls, machine, name):
        path = cls.dir / f"{name}.state"
        cls.probe_on(machine, "vsync", "--units", "3", "--save-at", "3", str(path))
        return path.read_bytes()

    def test_a_state_of_the_other_machine_is_incompatible_and_the_machine_is_left_alone(self):
        run = self.refusals([("the state of the other machine", self.foreign, INCOMPATIBLE, "config_hash: differs from the loaded machine")])
        self.assertEqual([(6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_identity_of_the_machine_does_not_let_the_state_of_the_other_one_through(self):
        # With the configuration hash of this machine written into the other one's header (and the header checksum made right), the identity matches and the chunks refuse it: the
        # clock chunk (31 bytes against none) and the memory sizes are the other machine's.
        forged = reforge(self.foreign, header={CONFIG_HASH.start: self.own[CONFIG_HASH]})
        self.refusals([("the other machine's state with this machine's hash", forged, CORRUPT, "chunk")])


class A600AgainstA500StateTests(CrossMachineCase):
    MACHINE, OTHER = "a600", "a500"

    def test_the_a600_state_has_the_chunks_of_the_a500_and_an_empty_clock_chunk(self):
        mine, theirs = parse_chunks(self.own), parse_chunks(self.foreign)
        self.assertEqual([(tag, version) for tag, version, _ in theirs], [(tag, version) for tag, version, _ in mine])
        self.assertEqual(11, len(mine))
        self.assertEqual((b"P6RT", 1, b""), tuple(mine[RTC]))
        self.assertEqual(31, len(theirs[RTC][2]), "the A500 clock body is what it was")
        for index, (tag, _, body) in enumerate(mine):
            if index not in (RTC, VIDEO):           # the video chunk follows the picture of the machine (its ECS rows are more of them)
                self.assertEqual(len(theirs[index][2]), len(body), tag)
        chip, slow = (int.from_bytes(mine[RAM][2][at:at + 4], "little") for at in (0, 4))
        self.assertEqual((1 << 20, 0), (chip, slow))
        self.assertEqual(((1 << 19), (1 << 19)), tuple(int.from_bytes(theirs[RAM][2][at:at + 4], "little") for at in (0, 4)))
        self.assertEqual(e.HEADER_SIZE + sum(16 + len(body) for _, _, body in mine), len(self.own))

    def test_an_a600_state_loaded_into_a_new_process_is_saved_again_byte_for_byte(self):
        again = self.dir / "again.state"
        source = self.dir / "own.state"
        run = self.probe("vsync", "--units", "0", "--load-at", "0", str(source), "--save-at", "0", str(again))
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(self.own, again.read_bytes())

    def test_a_clock_body_in_a_machine_without_the_chip_is_refused_by_its_length(self):
        clock = parse_chunks(self.foreign)[RTC][2]
        attempts = [("the clock body of the A500", reforge(self.own, lambda c: c[RTC].__setitem__(2, clock)), CORRUPT, "chunk_length"),
                    ("one byte of clock", reforge(self.own, lambda c: c[RTC].__setitem__(2, b"\0")), CORRUPT, "chunk_length")]
        self.refusals(attempts)


class A500AgainstA600StateTests(CrossMachineCase):
    MACHINE, OTHER = "a500", "a600"

    def test_an_empty_clock_body_in_a_machine_with_the_chip_is_refused(self):
        attempts = [("no clock body", reforge(self.own, lambda c: c[RTC].__setitem__(2, b"")), CORRUPT, "chunk rtc: the body ends before its last field")]
        self.refusals(attempts)


class RewoundStateCase(ProbeCase):
    """The state of a machine that went back to an earlier state and ran a unit, against the state of the one that never went back (the whole bytes, which no DET02 digest sees)."""

    ROMS = {"vsync": ("vsync", None, None)}

    def differences(self):
        arguments = ("--port", "0:2:1:3:-2", "--key", "0", "0x20", "down")
        self.probe("vsync", "--units", "6", *arguments, "--save-at", "3", str(self.dir / "g.state"), "--save-at", "4", str(self.dir / "natural.state"),
                   "--load-at", "5", str(self.dir / "g.state"), "--save-at", "6", str(self.dir / "rewound.state"))
        natural, rewound = (parse_chunks((self.dir / f"{name}.state").read_bytes()) for name in ("natural", "rewound"))
        return [(tag, [i for i in range(len(body)) if body[i] != rewound[k][2][i]][:4]) for k, (tag, _, body) in enumerate(natural) if body != rewound[k][2]]


class A500RewoundStateTests(RewoundStateCase):
    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        self.assertEqual([], self.differences())


class A600RewoundStateTests(RewoundStateCase):
    """T23b: on the A600 alone the rewound machine used to save 288 in BEAM's current_linear_vpos_visible (word 19) where the natural one saved 287: restore_custom sets
    programmed_register_accessed_h on an ECS Agnus, and the programmed-beam checks it switches on shifted the copy by a line. Patch 0104 carries the two flags in BEAM."""

    MACHINE = "a600"

    def test_a_unit_after_a_rewind_saves_the_state_of_the_machine_that_never_went_back(self):
        self.assertEqual([], self.differences())


del CrossMachineCase                # only the two machines' classes run


if __name__ == "__main__":
    unittest.main()
