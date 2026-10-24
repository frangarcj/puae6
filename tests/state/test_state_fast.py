"""T49: the native state of a machine with Zorro II Fast RAM (an autoconfig board) and DET02 on it.

What is decided here (docs/adr/0009-state-format.md, validation = hash + identity + structure):
  * a config whose memory is the preset's own is the preset in every identity: the same configuration hash and the same state, byte for byte, as without the three memory fields. A config
    with other memory has a hash of its own (the sizes travel in the flags word of the hash, see bridge/config.cpp identity_flags) and a state of one memory is INCOMPATIBLE in another.
  * the memory chunk (P6RM) of a machine with a Fast board appends to the body of the others: the board's size and bytes, and the autoconfig chain (the card the guest is at, the
    half-written base, the nibbles the card presents, the base the guest gave the board). Nothing else changes, and the machines without Fast RAM keep their bodies by the byte.
  * the bound grows with the board, and the same before the first unit and after it (the board is allocated when the image is loaded).
  * every field of the autoconfig block is range-checked and cross-checked before anything is applied.
  * DET02: the program walks the chain slowly (a delay between the steps), so that the unit boundaries fall before the card is read, between the reads, with the low byte of the base
    written and the high one not, after the board is configured, and after it is shut up; the state put back gives the future of the original in this process and in a new one.
"""
import unittest

from tests.state import envelope as e
from tests.state import test_det02 as d
from tests.state.harness import FieldTable, ProbeCase, parse_chunks

RAM = 2
CORRUPT, INCOMPATIBLE = 9, 10
EXPANSION_BYTES = 22 + 256          # the fields of visit_expansion in bridge/state.cpp
SEED, CLOCK = 1, 946684800

PRELUDE = """; PUAE6 test program: the Zorro II autoconfig chain walked slowly, one step per delay, then the board used.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
"""


def delay(n, count):
    return f"    MOVE.L #{count},D0\ndelay{n}:\n    .word $5380                 ; SUBQ.L #1,D0\n    .word $66FC                 ; BNE.S delay{n}\n"


def chain(first, last, low=0x00, high=0x20, shut_up=False, count=20000):
    """The program: read the card, give the board its base (the low byte of it, then the high one; or shut it up), and use it. `first` and `last` are the addresses of the first and the
    last long word of the board."""
    steps = [
        "    .word $1039,$00E8,$0000     ; MOVE.B $E80000.L,D0: the type of the card\n    .word $13C0,$0000,$0200     ; MOVE.B D0,$200.L\n",
        "    .word $1039,$00E8,$0004     ; the product\n    .word $13C0,$0000,$0201\n    .word $1039,$00E8,$0008\n    .word $13C0,$0000,$0202\n",
        f"    MOVE.B #${low:02X},$E8004A.L       ; the low byte of the base\n",
        "    MOVE.B #$00,$E8004C.L       ; shut up\n" if shut_up else f"    MOVE.B #${high:02X},$E80048.L       ; the high byte: the board is configured\n",
        "    .word $1039,$00E8,$0000     ; what the window holds now\n    .word $13C0,$0000,$0210\n",
        f"    MOVE.L #$12345678,${first:X}.L\n" + f"    MOVE.L #$9ABCDEF0,${last:X}.L\n",
        f"    .word $2039,${first >> 16:04X},${first & 0xffff:04X}     ; MOVE.L first,D0\n    .word $23C0,$0000,$0220\n"
        + f"    .word $2039,${last >> 16:04X},${last & 0xffff:04X}\n    .word $23C0,$0000,$0224\n",
    ]
    text = PRELUDE
    for n, step in enumerate(steps):
        text += delay(n, count) + step
    return text + "loop:\n    ADDQ.L #1,D1\n    BRA.S loop\n"


FAST8, FAST2, FAST1 = ["--fast", "8192"], ["--fast", "2048"], ["--fast", "1024"]
A600_PUAE5 = ["--chip", "2048", "--fast", "8192"]
ROMS = {
    "chain8": (chain(0x200000, 0x9FFFFC), None, None),
    "chain2": (chain(0x200000, 0x3FFFFC), None, None),
    "chain1": (chain(0x230000, 0x32FFFC, low=0x30), None, None),      # a base that uses the low byte: $230000
    "shutup8": (chain(0x200000, 0x9FFFFC, shut_up=True), None, None),
    "vsync": ("vsync", None, None),
}


def identity_flags(chip, slow, fast, flags=0):
    """The flags word of config_hash for a memory that is not the preset's: p6_config.flags in bits 0-7, fast/1024 at 8, slow/64 at 12, chip/64 at 17, bit 31 set."""
    return flags | 0x80000000 | (chip // 64) << 17 | (slow // 64) << 12 | (fast // 1024) << 8


class FastCase(ProbeCase):
    """Helpers: a state is taken at `unit` of `rom` on `machine` with `extra` arguments, and its RAM chunk is read."""

    ROMS = ROMS
    MACHINE = "a1200"

    def saved(self, name, machine, rom, unit, extra):
        path = self.dir / f"{name}.state"
        run = self.probe_on(machine, rom, "--units", str(unit), "--save-at", str(unit), str(path), *extra)
        self.assertEqual([], run.save_errors)
        return path.read_bytes(), run

    def ram_fields(self, name, machine, rom, unit, extra):
        path = self.dir / f"{name}.state"
        run = self.probe_on(machine, rom, "--units", str(unit), "--save-fields-at", str(unit), str(path), *extra)
        return path.read_bytes(), FieldTable(path.read_bytes(), RAM, run.fields)


class LayoutTests(FastCase):
    def test_the_ram_chunk_appends_the_board_and_its_chain_and_the_other_chunks_do_not_change(self):
        plain, _ = self.saved("plain", "a1200", "vsync", 3, [])
        fast, _ = self.saved("fast", "a1200", "vsync", 3, FAST8)
        mine, theirs = parse_chunks(fast), parse_chunks(plain)
        self.assertEqual([(tag, version) for tag, version, _ in theirs], [(tag, version) for tag, version, _ in mine])
        self.assertEqual(len(theirs[RAM][2]) + 4 + (8 << 20) + EXPANSION_BYTES, len(mine[RAM][2]))
        self.assertEqual(theirs[RAM][2], mine[RAM][2][:len(theirs[RAM][2])], "the body of the preset is the head of the longer one")
        self.assertEqual(8 << 20, int.from_bytes(mine[RAM][2][len(theirs[RAM][2]):][:4], "little"))
        for index, (tag, _, body) in enumerate(mine):
            if index not in (RAM,):
                self.assertEqual(len(theirs[index][2]), len(body), tag)

    def test_the_memory_of_the_preset_is_the_preset_in_every_identity(self):
        # The same machine said with its own sizes, and with the explicit "none" for what it does not have: the state is the one of the config without the fields, byte for byte.
        plain, _ = self.saved("plain", "a1200", "vsync", 3, [])
        for name, extra in (("chip", ["--chip", "2048"]), ("none", ["--slow", "none", "--fast", "0"]), ("all", ["--chip", "2048", "--slow", "none"])):
            same, _ = self.saved(name, "a1200", "vsync", 3, extra)
            self.assertEqual(plain, same, name)
        plain500, _ = self.saved("plain500", "a500", "vsync", 3, [])
        same500, _ = self.saved("same500", "a500", "vsync", 3, ["--chip", "512", "--slow", "512"])
        self.assertEqual(plain500, same500)

    def test_other_memory_has_its_own_configuration_hash_and_the_sizes_are_in_it(self):
        cases = (("a1200", FAST8, 3, identity_flags(2048, 0, 8192)), ("a1200", ["--fast", "1024"], 3, identity_flags(2048, 0, 1024)),
                 ("a600", A600_PUAE5, 2, identity_flags(2048, 0, 8192)), ("a500", ["--chip", "1024"], 1, identity_flags(1024, 512, 0)),
                 ("a1200", ["--slow", "1024"], 3, identity_flags(2048, 1024, 0)))
        seen = set()
        for machine, extra, number, flags in cases:
            blob, _ = self.saved("hash", machine, "vsync", 3, extra)
            want = e.config_hash(number, 1, 48000, flags, SEED, CLOCK)
            self.assertEqual(want, blob[176:208], f"{machine} {extra}")
            seen.add(blob[176:208])
        self.assertEqual(len(cases), len(seen))

    def test_a_state_of_other_memory_is_incompatible(self):
        plain, _ = self.saved("plain", "a1200", "vsync", 3, [])
        fast, _ = self.saved("fast", "a1200", "vsync", 3, FAST8)
        fast4, _ = self.saved("fast4", "a1200", "vsync", 3, ["--fast", "4096"])
        run = self.refusals([("the state without Fast RAM", plain, INCOMPATIBLE, "config_hash: differs from the loaded machine"),
                             ("the state of a 4 MiB board", fast4, INCOMPATIBLE, "config_hash: differs from the loaded machine")], rom="vsync", extra=FAST8)
        self.assertEqual([(6, INCOMPATIBLE), (6, INCOMPATIBLE)], [(unit, code) for unit, code, _ in run.loads])
        # A state that carries a board the machine has not is longer than any state it can hold: the length is refused before the identity is looked at.
        run = self.refusals([("the state with Fast RAM", fast, CORRUPT, "payload_length")], rom="vsync", extra=())
        self.assertEqual([(6, CORRUPT)], [(unit, code) for unit, code, _ in run.loads])

    def test_the_bound_follows_the_configuration_and_is_the_same_before_the_first_unit_and_after_it(self):
        bounds = {}
        for name, extra in (("plain", []), ("fast8", FAST8), ("fast1", FAST1)):
            run = self.probe_on("a1200", "vsync", "--units", "3", "--public-at", "0", "--public-at", "3", *extra)
            before, after = (size[1] for size in run.public_size)
            self.assertEqual(before, after, name)
            bounds[name] = after
        self.assertEqual(4 + (8 << 20) + EXPANSION_BYTES, bounds["fast8"] - bounds["plain"])
        self.assertEqual(4 + (1 << 20) + EXPANSION_BYTES, bounds["fast1"] - bounds["plain"])

    def test_the_state_is_saved_again_byte_for_byte_after_a_load_into_a_new_process(self):
        blob, _ = self.saved("own", "a1200", "chain8", 5, FAST8)
        again = self.dir / "again.state"
        run = self.probe_on("a1200", "chain8", "--units", "0", "--load-at", "0", str(self.dir / "own.state"), "--save-at", "0", str(again), *FAST8)
        self.assertEqual([(0, 0, "")], run.loads)
        self.assertEqual(blob, again.read_bytes())


class AutoconfigBodyTests(FastCase):
    """The block of the chain, field by field: what a state may say and what it may not."""

    def test_the_fields_of_a_board_that_has_been_configured_and_of_one_that_has_been_shut_up(self):
        _, configured = self.ram_fields("configured", "a1200", "chain8", 12, FAST8)
        self.assertEqual((1, 1, 0x200000), (configured.get("ram.fast_ecard"), configured.get("ram.fast_cardno"), configured.get("ram.fast_start")))
        self.assertEqual(8 << 20, configured.get("ram.fast_size"))
        _, shut = self.ram_fields("shut", "a1200", "shutup8", 12, FAST8)
        self.assertEqual((1, 0xffffffff), (shut.get("ram.fast_ecard"), shut.get("ram.fast_start")))
        self.assertEqual(1, configured.get("ram.fast_mapped"))
        self.assertEqual(0, shut.get("ram.fast_mapped"))
        _, early = self.ram_fields("early", "a1200", "chain8", 1, FAST8)
        self.assertEqual((0, 0, 0), (early.get("ram.fast_ecard"), early.get("ram.fast_map_set"), early.get("ram.fast_mapped")))

    def test_a_field_outside_its_range_or_in_conflict_with_another_is_refused_before_anything_is_applied(self):
        blob, table = self.ram_fields("good", "a1200", "chain8", 12, FAST8)
        attempts = []
        for label, values, text in (
                ("a base that is not on a 64 KiB step", {"ram.fast_start": 0x210001}, "address it cannot have"),
                ("a base below the Zorro II space", {"ram.fast_start": 0x100000}, "answers at an address"),
                ("a base that puts the board past the Zorro II space", {"ram.fast_start": 0x300000}, "answers at an address"),
                ("a board that answers at no base", {"ram.fast_start": 0}, "answers at an address"),
                ("a board that answers after a shut-up", {"ram.fast_start": 0xffffffff}, "answers at an address"),
                ("a mapped flag of 2", {"ram.fast_mapped": 2}, "range"),
                ("a card past the chain", {"ram.fast_ecard": 2}, "range"),
                ("a finished chain that still has a map function", {"ram.fast_ecard": 1, "ram.fast_map_set": 1}, "still has a card"),
                ("a finished chain with a current bank", {"ram.fast_ecard": 1, "ram.fast_bank_current": 1}, "still has a card"),
                ("a second card", {"ram.fast_cardno": 2}, "range"),
                ("the size of another board", {"ram.fast_size": 4 << 20}, "range"),
                ("a chip-done flag of 2", {"ram.fast_chipdone": 2}, "range"),
                ("autoconfig mode on", {"ram.fast_autoconfig_mode": 1}, "range")):
            attempts.append((label, table.with_values(values), CORRUPT, text))
        run = self.refusals(attempts, rom="chain8", extra=FAST8, unit=12, units=16)
        self.assertEqual(len(attempts), len(run.loads))

    def test_a_chain_that_is_between_two_steps_is_a_good_state(self):
        # The low byte of the base is written and the high one is not yet: the card is still the first (ecard 0), its map function has been taken up and the byte is kept.
        _, table = self.ram_fields("half", "a1200", "chain1", 4, FAST1)
        self.assertEqual((0, 1, 1, 0x30, 0), (table.get("ram.fast_ecard"), table.get("ram.fast_cardno"), table.get("ram.fast_map_set"), table.get("ram.fast_lo"), table.get("ram.fast_mapped")))
        _, done = self.ram_fields("done", "a1200", "chain1", 6, FAST1)
        self.assertEqual((1, 0x230000, 1), (done.get("ram.fast_ecard"), done.get("ram.fast_start"), done.get("ram.fast_mapped")))


def scenario(rom, extra, points, modes):
    return (rom, extra, points, modes)


# Where the steps fall (tests/state/test_state_fast.py runs them): on the A1200 the whole chain is walked in units 1 to 5 (the low byte of the base is written in 4 and the board is
# mapped in 5); on the 68000 machines the same loop is slower, and the chain takes until unit 11.
SCENARIOS_A1200 = {
    "chain8": scenario("chain8", FAST8, (1, 2, 3, 4, 5, 6, 9, 14), d.CORE),
    "chain1": scenario("chain1", FAST1, (3, 4, 5), (d.SAME_BEAM, d.NEW)),
    "shutup8": scenario("shutup8", FAST8, (3, 4, 5, 8), (d.SAME_BEAM, d.NEW)),
}
SCENARIOS_A600 = {
    "chain8-a600": scenario("chain8", A600_PUAE5, (3, 6, 9, 11, 13), (d.SAME_BEAM, d.NEW)),
}
SCENARIOS_A500 = {
    "chain2-a500": scenario("chain2", FAST2, (4, 8, 10, 12), (d.SAME_BEAM, d.NEW)),
    "chain1-a500-slow": scenario("chain1", ["--fast", "1024", "--slow", "1024"], (7, 8, 9, 10, 12), (d.SAME_BEAM, d.NEW)),
}


class FastDet02Case(d.Det02Case):
    ROMS = ROMS
    MACHINE = "a1200"


class Det02FastA1200Tests(FastDet02Case):
    MACHINE = "a1200"


class Det02FastA600Tests(FastDet02Case):
    MACHINE = "a600"


class Det02FastA500Tests(FastDet02Case):
    MACHINE = "a500"


d.populate(Det02FastA1200Tests, SCENARIOS_A1200, {})
d.populate(Det02FastA600Tests, SCENARIOS_A600, {})
d.populate(Det02FastA500Tests, SCENARIOS_A500, {})


if __name__ == "__main__":
    unittest.main()
