"""T49: DET02 with the owner's Kickstarts on machines with Zorro II Fast RAM (opt-in; the firmware is read in place and never copied, hashed or kept).

    P6_KICKSTART_A1200_ROM  Kickstart 3.1 r40.68 (A1200)      P6_AGA_ADF  an AGA demo disk, mounted in DF0 at boot in the demo scenarios
    P6_KICKSTART_A600_ZIP   zip with the Kickstart 2.05 (A600)  P6_WB205_ZIP  zip with the Workbench 2.05 boot disk
    P6_KICKSTART_ROM        Kickstart 1.3 r34.5 (A500)

What the real Kickstarts add to tests/state/test_state_fast.py: the autoconfig chain walked by the expansion code of the operating system itself (exec finds the board, the Kickstart gives
it its base and adds its memory to the system list), so the boundaries around the unit in which that happens (the card presented, the base written, the board mapped) and long after it,
when the system keeps its memory lists, its task stacks and its disk buffers in the board. A test fails if the board is not configured by the Kickstart where it is expected (the
timeline of each machine was measured: the A1200 at unit 21, the A600 at 74, the A500 at 101).
"""
import os
from pathlib import Path
import unittest

from tests.state import test_det02 as d
from tests.state import test_det02_a600 as a600
from tests.state.harness import FieldTable, ProbeCase, parse_chunks

A1200_ROM = os.environ.get("P6_KICKSTART_A1200_ROM")
AGA_ADF = os.environ.get("P6_AGA_ADF")
A500_ROM = os.environ.get("P6_KICKSTART_ROM")
A600_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
WB_ZIP = os.environ.get("P6_WB205_ZIP")
RAM = 2


def memory_list(blob):
    """The MemHeaders of the system's memory list (ExecBase.MemList) in a state: [(attributes, lower, upper)]. Only RAM that the chunk P6RM holds can be read (Chip and Fast)."""
    body = parse_chunks(blob)[RAM][2]
    chip, slow = (int.from_bytes(body[at:at + 4], "little") for at in (0, 4))
    chip_bytes = body[8:8 + chip]
    after = 8 + chip + slow
    fast_bytes = body[after + 4:after + 4 + int.from_bytes(body[after:after + 4], "little")]

    def read(address, length):
        if address + length <= len(chip_bytes):
            return chip_bytes[address:address + length]
        if 0x200000 <= address and address + length - 0x200000 <= len(fast_bytes):
            return fast_bytes[address - 0x200000:address - 0x200000 + length]
        raise IndexError(hex(address))

    long = lambda address: int.from_bytes(read(address, 4), "big")
    sysbase, headers, node = long(4), [], None
    node = long(sysbase + 322)
    while node and long(node):
        headers.append((int.from_bytes(read(node + 14, 2), "big"), long(node + 20), long(node + 24)))
        node = long(node)
    return headers

FAST8 = ["--fast", "8192"]
PUAE5_A600 = ["--chip", "2048", "--fast", "8192"]
FAST2 = ["--fast", "2048"]

A1200_SCENARIOS = {
    "kickstart-autoconfig": ("kickstart", FAST8, (20, 21, 22, 23), (d.SAME_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", FAST8, (140,), (d.SAME_BEAM, d.RESET_BEAM, d.NEW)),
    "kickstart-insert-disk": ("kickstart", FAST8, (400,), (d.SAME_BEAM, d.NEW)),
}
A1200_DEMO = {"demo-boot": ("kickstart", "demo", (200, 400, 800), (d.SAME_BEAM, d.NEW))}
A600_SCENARIOS = {
    "kickstart-autoconfig": ("kickstart", PUAE5_A600, (73, 74, 75, 76, 77), (d.SAME_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", PUAE5_A600, (140,), (d.SAME_BEAM, d.NEW)),
}
A600_WORKBENCH = {"workbench-boot": ("kickstart", "workbench", (300, 700), (d.SAME_BEAM, d.NEW))}
A500_SCENARIOS = {
    "kickstart-autoconfig": ("kickstart", FAST2, (100, 101, 102, 103), (d.SAME_BEAM, d.NEW)),
    "kickstart-steady": ("kickstart", FAST2, (140,), (d.SAME_BEAM, d.NEW)),
}


class A1200Case(d.Det02Case):
    MACHINE = "a1200"

    @classmethod
    def firmware(cls):
        return Path(A1200_ROM)

    def arguments(self, extra):
        if extra == "demo":
            return ["--adf", AGA_ADF, "--event-at", "0", "0", "0", "1", *FAST8]
        return super().arguments(extra)


class A600Case(a600.Kickstart205Case):
    MACHINE = "a600"

    def arguments(self, extra):
        if extra == "workbench":
            return ["--adf", str(self.workbench), "--event-at", "0", "0", "0", "1", *PUAE5_A600]
        return super().arguments(extra)


@unittest.skipUnless(A1200_ROM and Path(A1200_ROM).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized Kickstart 3.1 (A1200)")
class Det02FastKickstart31A1200Tests(A1200Case):
    def test_the_kickstart_configures_the_board_and_uses_it(self):
        _, table = self.fields("a1200", 30, FAST8)
        self.assertEqual((1, 1, 0x200000), (table.get("ram.fast_ecard"), table.get("ram.fast_mapped"), table.get("ram.fast_start")))

    def test_the_system_has_the_board_in_its_memory_list_as_fast_memory(self):
        # What the Kickstart did with the board is visible in the system's own books: a MemHeader marked MEMF_FAST (4) that spans the board, $200000 to $A00000.
        path = self.dir / "list.state"
        self.probe_on("a1200", "kickstart", "--units", "100", "--save-at", "100", str(path), *FAST8)
        headers = memory_list(path.read_bytes())
        fast = [h for h in headers if h[0] & 4]
        self.assertTrue(fast, headers)
        self.assertEqual(0x200000, min(lower for _, lower, _ in fast) & ~0xffff)
        self.assertEqual(0xA00000, max(upper for _, _, upper in fast))
        self.assertTrue(any(h[0] & 2 for h in headers), "and Chip RAM is in the list as well")

    def fields(self, machine, unit, extra):
        path = self.dir / f"fields-{unit}.state"
        run = self.probe_on(machine, "kickstart", "--units", str(unit), "--save-fields-at", str(unit), str(path), *extra)
        return path.read_bytes(), FieldTable(path.read_bytes(), RAM, run.fields)


@unittest.skipUnless(A1200_ROM and AGA_ADF and Path(A1200_ROM).is_file() and Path(AGA_ADF).is_file(), "BLOCKED: set P6_KICKSTART_A1200_ROM and P6_AGA_ADF to run the AGA demo")
class Det02FastAgaDemoTests(A1200Case):
    pass


@unittest.skipUnless(A600_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP to a zip with an authorized Kickstart 2.05")
class Det02FastKickstart205A600Tests(A600Case):
    def test_the_kickstart_configures_the_board_of_the_puae5_a600_and_uses_it(self):
        path = self.dir / "fields.state"
        run = self.probe_on("a600", "kickstart", "--units", "80", "--save-fields-at", "80", str(path), *PUAE5_A600)
        table = FieldTable(path.read_bytes(), RAM, run.fields)
        self.assertEqual((1, 1, 0x200000), (table.get("ram.fast_ecard"), table.get("ram.fast_mapped"), table.get("ram.fast_start")))
        self.assertEqual((2 << 20, 0, 8 << 20), (table.get("ram.chip_size"), table.get("ram.slow_size"), table.get("ram.fast_size")))
        state = self.dir / "list.state"
        self.probe_on("a600", "kickstart", "--units", "100", "--save-at", "100", str(state), *PUAE5_A600)
        fast = [h for h in memory_list(state.read_bytes()) if h[0] & 4]
        self.assertTrue(fast and max(upper for _, _, upper in fast) == 0xA00000, "the system lists the 8 MiB board as Fast memory")


@unittest.skipUnless(A600_ZIP and WB_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP and P6_WB205_ZIP to run the Workbench 2.05 boot")
class Det02FastWorkbench205Tests(A600Case):
    pass


@unittest.skipUnless(A500_ROM and Path(A500_ROM).is_file(), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3")
class Det02FastKickstart13A500Tests(d.Det02Case):
    MACHINE = "a500"

    @classmethod
    def firmware(cls):
        return Path(A500_ROM)


d.populate(Det02FastKickstart31A1200Tests, A1200_SCENARIOS, {})
d.populate(Det02FastAgaDemoTests, A1200_DEMO, {})
d.populate(Det02FastKickstart205A600Tests, A600_SCENARIOS, {})
d.populate(Det02FastWorkbench205Tests, A600_WORKBENCH, {})
d.populate(Det02FastKickstart13A500Tests, A500_SCENARIOS, {})


if __name__ == "__main__":
    unittest.main()
