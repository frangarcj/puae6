"""T75: the memory maps, RETRO_MEMORY_SYSTEM_RAM and the achievements flag of the libretro core (LR01), run by the minimal frontend.

RetroAchievements (rcheevos) reads the emulated machine through what the core announces: SET_MEMORY_MAPS (descriptors with the real bus addresses) and, without a
console table, RETRO_MEMORY_SYSTEM_RAM. The core offers Chip, Slow and Fast RAM (in that order, big endian, flagged SYSTEM_RAM) and the ROM (constant), with the Chip RAM as
SYSTEM_RAM like PUAE5 (adapters/libretro/memory_map.h has the decisions). What is decided here:
  * the map and its sizes for each machine and memory option (A500 512K+512K Slow, A600 1 MiB, A1200 2 MiB, with Fast, with 1.5 MiB of Slow);
  * the memory is the engine's: what a micro-ROM writes at an Amiga address shows through the pointer of the descriptor that claims that address (Chip, Slow, and the Fast board
    once the Kickstart has configured it at $200000), and a poke of the frontend through the pointer is read by the machine;
  * the pointers are the same from the load to the unload, through run, reset, save/load, rewind and run-ahead, announced once, and a state load puts the content back in place;
  * accepting or refusing the map, or the achievements flag, changes nothing of the machine, and a frontend that pokes nothing gets the headless machine unit by unit.
Run: python3 -m unittest discover -s tests/libretro-host -p "test_memory.py"
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr  # noqa: E402

# A program that writes a pattern into every RAM the machine may have and keeps the machine changing: a counter at $1010 (Chip RAM) and a copy of the long at $1004 to
# $1008, so that a poke of the frontend into Chip RAM is read by the CPU. The Zorro II board is configured by hand as the Kickstart does (low byte, then the high byte of its
# base $200000) before it is written; without the board those writes go nowhere.
MEMORY_PROGRAM = """; PUAE6 test program: patterns in Chip, Slow and Fast RAM, then a counter and a copy that the frontend can poke.
entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.L #$11223344,$1000.L
    MOVE.L #$55667788,$C00100.L
    MOVE.L #20000,D0
delay:
    .word $5380                 ; SUBQ.L #1,D0
    .word $66FC                 ; BNE.S delay
    MOVE.B #$00,$E8004A.L       ; the low byte of the base of the board
    MOVE.B #$20,$E80048.L       ; the high byte: the board is configured at $200000
    MOVE.L #20000,D0
delay2:
    .word $5380
    .word $66FC
    MOVE.L #$99AABBCC,$200100.L
loop:
    ADDQ.L #1,D1
    .word $23C1,$0000,$1010     ; MOVE.L D1,$1010.L: a counter in Chip RAM
    .word $23F9,$0000,$1004,$0000,$1008     ; MOVE.L $1004.L,$1008.L: what the frontend pokes at $1004 is read here
    BRA.S loop
"""

MAP = re.compile(r"^memory-map (\d+) count=(\d+)$", re.M)
DESC = re.compile(r"^memory-desc (\d+) name=(\w+) start=0x([0-9a-f]+) len=0x([0-9a-f]+) select=0x([0-9a-f]+) disconnect=0x([0-9a-f]+) flags=0x([0-9a-f]+) ptr=(\S+)$", re.M)
SYSTEM_RAM = re.compile(r"^memory system_ram ptr=(\S+) size=(\d+) announcements=(\d+)$", re.M)
HASHES = re.compile(r"^memory hash(.*)$", re.M)
PEEK = re.compile(r"^peek (0x[0-9a-f]+) (\d+) (\w+)$", re.M)

# libretro.h: RETRO_MEMDESC_CONST 1, BIGENDIAN 2, SYSTEM_RAM 4.
CONST, BIGENDIAN, SYSTEM_RAM_FLAG = 1, 2, 4
KIB = 1024

A1200_NAME = lr.KICKSTART_NAMES_A1200[0]
A600_NAME = lr.KICKSTART_NAMES_A600[0]
# machine -> (options, Kickstart name, headless flags)
CASES = {
    "A500": ((), lr.KICKSTART_NAMES[0], ("--machine", "a500")),
    "A600": (("--set", "puae6_machine=A600"), A600_NAME, ("--machine", "a600")),
    "A1200": (("--set", "puae6_machine=A1200"), A1200_NAME, ("--machine", "a1200")),
}


def descriptors(text):
    """The last announcement: name -> dict(start, len, select, flags, ptr), in the order announced."""
    last = text.rfind("memory-map ")
    found = {}
    order = []
    for m in DESC.finditer(text[last:]):
        found[m[2]] = {"start": int(m[3], 16), "len": int(m[4], 16), "select": int(m[5], 16), "disconnect": int(m[6], 16), "flags": int(m[7], 16), "ptr": m[8]}
        order.append(m[2])
    return found, order


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class MemoryMapTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rom = Path(cls.tmp.name) / "memory.rom"
        rom.write_bytes(lr.builder.build(MEMORY_PROGRAM))
        cls.roms["memory"] = rom

    def machine(self, machine, *options, rom="memory", **keywords):
        extra, name, _ = CASES[machine]
        return self.frontend(rom, *extra, *options, name=name, **keywords)

    def headless(self, machine, units, *flags, rom="memory"):
        return self.headless_units(rom, units, *CASES[machine][2], *flags)

    # --- the map of each machine ------------------------------------------------------------------------------------------------------

    def test_the_a500_has_512k_of_chip_and_512k_of_slow_ram_and_the_rom_window_at_the_top_of_the_bus(self):
        out = self.machine("A500", "--runs", 3).stdout
        found, order = descriptors(out)
        self.assertEqual(order, ["CHIP", "SLOW", "ROM"])
        self.assertEqual((found["CHIP"]["start"], found["CHIP"]["len"], found["CHIP"]["select"]), (0x000000, 512 * KIB, 0))
        self.assertEqual((found["SLOW"]["start"], found["SLOW"]["len"], found["SLOW"]["select"]), (0xC00000, 512 * KIB, 0))
        self.assertEqual((found["ROM"]["start"], found["ROM"]["len"]), (0xF80000, 512 * KIB))
        self.assertEqual(found["CHIP"]["flags"], SYSTEM_RAM_FLAG | BIGENDIAN)
        self.assertEqual(found["SLOW"]["flags"], SYSTEM_RAM_FLAG | BIGENDIAN)
        self.assertEqual(found["ROM"]["flags"], CONST | BIGENDIAN)
        self.assertEqual(len(MAP.findall(out)), 1, "announced once")

    def test_the_a600_and_the_a1200_have_no_slow_ram_and_1_and_2_mib_of_chip(self):
        for machine, chip in (("A600", 1024 * KIB), ("A1200", 2048 * KIB)):
            with self.subTest(machine=machine):
                found, order = descriptors(self.machine(machine, "--runs", 3).stdout)
                self.assertEqual(order, ["CHIP", "ROM"])
                self.assertEqual((found["CHIP"]["start"], found["CHIP"]["len"]), (0, chip))

    def test_the_fast_board_is_announced_at_the_address_the_kickstart_gives_the_first_board(self):
        for machine, options, chip, fast in (("A1200", ("--set", "puae6_fast_size=MB8"), 2048, 8192),
                                             ("A600", ("--set", "puae6_chip_size=MB2", "--set", "puae6_fast_size=MB8"), 2048, 8192),
                                             ("A500", ("--set", "puae6_fast_size=MB2"), 512, 2048)):
            with self.subTest(machine=machine, fast=fast):
                found, order = descriptors(self.machine(machine, "--runs", 3, *options).stdout)
                self.assertEqual(order[0], "CHIP")
                self.assertEqual(order[-1], "ROM")
                self.assertEqual(order[order.index("FAST") - 1], "CHIP" if machine != "A500" else "SLOW", order)     # Chip, Slow, Fast: the order rcheevos concatenates
                self.assertEqual((found["FAST"]["start"], found["FAST"]["len"], found["FAST"]["select"]), (0x200000, fast * KIB, 0))
                self.assertEqual(found["FAST"]["flags"], SYSTEM_RAM_FLAG | BIGENDIAN)
                self.assertEqual(found["CHIP"]["len"], chip * KIB)

    def test_a_slow_ram_that_is_not_a_power_of_two_is_the_head_of_the_block_above_it(self):
        found, _ = descriptors(self.machine("A500", "--runs", 3, "--set", "puae6_slow_size=MB1_5").stdout)
        self.assertEqual((found["SLOW"]["start"], found["SLOW"]["len"], found["SLOW"]["select"]), (0xC00000, 1536 * KIB, 0xE00000))
        found, _ = descriptors(self.machine("A1200", "--runs", 3, "--set", "puae6_slow_size=MB1_5").stdout)
        self.assertEqual((found["SLOW"]["start"], found["SLOW"]["len"]), (0xC00000, 1536 * KIB))

    def test_the_rom_window_of_every_machine_ends_at_the_end_of_the_24_bit_bus(self):
        # The micro-ROM is 256 KiB for every machine, so the 512 KiB case is the same rule on the size the engine reports: the end of the ROM is the end of the 24-bit bus.
        for machine in CASES:
            found, _ = descriptors(self.machine(machine, "--runs", 2).stdout)
            self.assertEqual(found["ROM"]["start"] + found["ROM"]["len"], 0x1000000, machine)

    def test_system_ram_is_the_chip_ram_of_the_map_and_other_memory_types_are_not_offered(self):
        for machine, chip in (("A500", 512), ("A600", 1024), ("A1200", 2048)):
            with self.subTest(machine=machine):
                out = self.machine(machine, "--runs", 3, "--do", "3:memory").stdout
                found, _ = descriptors(out)
                ram = SYSTEM_RAM.search(out)
                self.assertEqual((ram[1], int(ram[2])), (found["CHIP"]["ptr"], chip * KIB))
                self.assertIn("memory-check ok=1", out)

    def test_there_is_no_memory_before_the_load_and_after_the_unload(self):
        # Without content or Kickstart the load is refused and the map was never announced.
        result = self.frontend("boot", system=False, expect=3)
        self.assertNotIn("memory-map", result.stdout)

    def test_the_achievements_flag_is_set_at_init(self):
        self.assertIn("achievements 1", self.machine("A500", "--runs", 1).stdout)

    # --- the memory is the engine's ---------------------------------------------------------------------------------------------------

    def test_what_the_program_writes_is_read_through_the_descriptor_that_claims_its_address(self):
        out = self.machine("A1200", "--runs", 30, "--set", "puae6_fast_size=MB8",
                           "--do", "30:peek:0x1000:4", "--do", "30:peek:0x200100:4", "--do", "30:peek:0xC00100:4", "--do", "30:peek:0xF80000:4").stdout
        peeks = {m[1]: m[3] for m in PEEK.finditer(out)}
        self.assertEqual(peeks["0x1000"], "11223344")                 # Chip RAM, big endian: the byte at the lowest address is the high byte
        self.assertEqual(peeks["0x200100"], "99aabbcc")               # Fast RAM, once the program has configured the board at $200000
        self.assertEqual(peeks["0xc00100"], "unmapped")               # the A1200 has no Slow RAM
        self.assertEqual(peeks["0xf80000"], "11114ef9")               # the ROM: the micro-ROM's reset vector

    def test_the_slow_ram_of_the_a500_and_its_chip_ram_are_read_where_the_amiga_has_them(self):
        out = self.machine("A500", "--runs", 30, "--do", "30:peek:0x1000:4", "--do", "30:peek:0xC00100:4", "--do", "30:peek:0x200100:4").stdout
        peeks = {m[1]: m[3] for m in PEEK.finditer(out)}
        self.assertEqual((peeks["0x1000"], peeks["0xc00100"], peeks["0x200100"]), ("11223344", "55667788", "unmapped"))

    def test_the_counter_of_the_program_moves_in_the_chip_ram_the_frontend_reads(self):
        out = self.machine("A500", "--runs", 12, "--do", "6:peek:0x1010:4", "--do", "12:peek:0x1010:4").stdout
        first, second = (int(m[3], 16) for m in PEEK.finditer(out))
        self.assertGreater(second, first)
        self.assertGreater(first, 0)

    def test_a_poke_of_the_frontend_into_chip_ram_is_read_by_the_cpu_and_is_the_same_each_time(self):
        def run():
            return self.machine("A500", "--runs", 12, "--do", "6:poke:0x1004:cafebabe", "--do", "12:peek:0x1008:4").stdout
        first, second = run(), run()
        self.assertEqual(PEEK.search(first)[3], "cafebabe")           # the CPU copied the long to $1008 after the poke
        self.assertEqual([r["hash"] for r in self.runs(first)], [r["hash"] for r in self.runs(second)])
        self.assertEqual(HASHES.findall(first), HASHES.findall(second))

    def test_the_rom_is_declared_constant_and_a_frontend_that_respects_that_cannot_change_it(self):
        out = self.machine("A500", "--runs", 3, "--do", "3:poke:0xF80000:ffffffff", "--do", "3:peek:0xF80000:4").stdout
        self.assertIn("poke 0xf80000 0", out)
        self.assertEqual(PEEK.search(out)[3], "11114ef9")

    # --- the pointers are the same all along ------------------------------------------------------------------------------------------

    def test_the_pointers_stay_the_same_and_the_map_is_announced_once_through_run_reset_save_load_and_runahead(self):
        for machine, options in (("A500", ()), ("A1200", ("--set", "puae6_fast_size=MB8")), ("A500", ("--set", "puae6_slow_size=MB1_5"))):
            with self.subTest(machine=machine, options=options):
                out = self.machine(machine, "--runs", 20, *options, "--do", "0:memory", "--do", "5:save:a", "--do", "5:memory", "--do", "10:reset", "--do", "10:memory",
                                   "--do", "12:load:a", "--do", "12:memory", "--do", "16:memory", "--ahead", "8:3", "--ahead", "9:2").stdout
                self.assertEqual(len(MAP.findall(out)), 1, "the map was announced again")
                pointers = {m[1] for m in SYSTEM_RAM.finditer(out)}
                self.assertEqual(len(pointers), 1, pointers)
                self.assertEqual(len(SYSTEM_RAM.findall(out)), 5)
                self.assertEqual(out.count("memory-check ok=1"), 5)
                self.assertTrue(all(m[3] == "1" for m in SYSTEM_RAM.finditer(out)))

    def test_a_load_puts_the_content_back_in_the_same_buffers(self):
        out = self.machine("A1200", "--runs", 14, "--set", "puae6_fast_size=MB8", "--do", "6:save:a", "--do", "6:memory", "--do", "12:memory", "--do", "12:load:a", "--do", "12:memory").stdout
        before, later, after = HASHES.findall(out)
        self.assertEqual(before, after)                               # the machine as it was when saved
        self.assertNotEqual(before, later)                            # the counter had moved on
        pointers = {m[1] for m in SYSTEM_RAM.finditer(out)}
        self.assertEqual(len(pointers), 1)

    def test_a_poke_after_the_save_is_taken_back_by_the_load(self):
        out = self.machine("A500", "--runs", 12, "--do", "4:save:a", "--do", "4:memory", "--do", "8:poke:0x1000:deadbeef", "--do", "8:peek:0x1000:4", "--do", "12:load:a",
                           "--do", "12:peek:0x1000:4", "--do", "12:memory").stdout
        peeks = [m[3] for m in PEEK.finditer(out)]
        self.assertEqual(peeks, ["deadbeef", "11223344"])
        self.assertEqual(HASHES.findall(out)[0], HASHES.findall(out)[1])

    def test_a_reset_leaves_the_ram_where_it_was_and_the_program_writes_its_pattern_again(self):
        out = self.machine("A500", "--runs", 40, "--do", "10:poke:0x1000:00000000", "--do", "10:reset", "--do", "40:peek:0x1000:4", "--do", "40:memory").stdout
        self.assertEqual(PEEK.search(out)[3], "11223344")
        self.assertEqual(len(MAP.findall(out)), 1)
        self.assertIn("memory-check ok=1", out)

    def test_the_content_is_loaded_again_in_the_same_session_and_the_pointers_are_announced_again_for_the_new_load(self):
        out = self.machine("A500", "--runs", 3, "--cycles", 3, "--do", "3:memory").stdout
        self.assertEqual(len(MAP.findall(out)), 3)                    # one announcement per load
        self.assertEqual(out.count("memory-check ok=1"), 3)

    # --- nothing of the machine changes -------------------------------------------------------------------------------------------------

    def test_accepting_or_refusing_the_map_and_the_flag_is_the_same_machine(self):
        for machine in CASES:
            with self.subTest(machine=machine):
                taken = self.runs(self.machine(machine, "--runs", 20).stdout)
                no_map = self.runs(self.machine(machine, "--runs", 20, "--no-memory-map").stdout)
                refused = self.runs(self.machine(machine, "--runs", 20, "--no-memory-map", "--no-achievements").stdout)
                keys = lambda runs: [(r["hash"], r["audio"], r["ahash"], r["ticks"]) for r in runs]  # noqa: E731
                self.assertEqual(keys(taken), keys(no_map))
                self.assertEqual(keys(taken), keys(refused))

    def test_the_machine_with_all_the_memory_is_the_headless_machine_unit_by_unit_and_its_state_hash_is_the_same_with_or_without_the_map(self):
        options = ("--set", "puae6_fast_size=MB8")
        out = self.machine("A1200", "--runs", 16, *options, "--do", "16:save:z").stdout
        self.compare_with_headless(out, "memory", 16, "--machine", "a1200", "--fast", "8192")
        refused = self.machine("A1200", "--runs", 16, *options, "--no-memory-map", "--no-achievements", "--do", "16:save:z").stdout
        native = lambda text: re.search(r"^state native z hash=(\w+)$", text, re.M)[1]  # noqa: E731
        self.assertEqual(native(out), native(refused))                # the machine's part of the state (T31): hash of the hardware
        self.assertEqual(re.search(r"^state final .* hash=(\w+)$", out, re.M)[1], re.search(r"^state final .* hash=(\w+)$", refused, re.M)[1])

    def test_a_frontend_that_only_reads_the_memory_every_unit_leaves_the_machine_as_headless_has_it(self):
        out = self.machine("A500", "--runs", 12, *sum((["--do", f"{n}:memory"] for n in range(1, 13)), [])).stdout
        self.compare_with_headless(out, "memory", 12, "--machine", "a500")


if __name__ == "__main__":
    unittest.main()
