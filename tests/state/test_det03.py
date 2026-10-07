"""DET03 (T15 closure): a state saved through the PUBLIC ABI in one instance continues, in an equivalent fresh instance, as the original did.

DET02 (tests/state/test_det02.py) proves the future of a state through the registry of the bridge, which takes the test options. A host calls p6_state_bound, p6_state_save and p6_state_load,
in a process of its own, and that is the path the product has to get right: here the original process saves with `--public-save-at` (the public bound and the public save, to a file), a new process
configured the same way (the same ROM, media manifest, standard and seed) loads the file with `--public-load-at` at its unit 0, and the units that follow in both are compared digest by digest
(cpu, scheduler, ram, rtc, cia, the chipset by group, audio, input, video, bridge) and sample by sample (the PCM of every unit), with the helpers of DET02 (Det02Case.differing). The scenarios
are those of DET02 that matter for a transport between instances: a program that waits for the vertical blank, a tone, the busy chipset, a disk read in flight, a swap of media counting down
(loaded into machines that booted with either medium), the mouse, held keys, the CIAs, NTSC and, when P6_KICKSTART_ROM names an authorized Kickstart (never copied or hashed here), the real firmware.

Also here: the state the public save writes is, byte for byte, the one the registry writes at the same unit; an instance that is not equivalent (another standard, other media) refuses the state through
the public load as INCOMPATIBLE and goes on as if it had not been asked; and the capability is announced.
"""
import os
import unittest

from tests.state import test_det02 as d
from tests.state import test_state_input as input_tests

# name -> (rom name, extra probe arguments, units N at which the state is taken, modes). The machinery and the names of the modes are DET02's: NEW is a fresh instance that ran its
# reset head, BOOT_A and BOOT_B fresh instances that booted with the first or the second medium.
SCENARIOS = {
    "vsync": ("vsync", [], (3,), (d.NEW,)),
    "tone": ("tone", [], (3,), (d.NEW,)),
    "tone-modulated": ("tone-mod", [], (4,), (d.NEW,)),
    "busy": ("busy", [], (2, 5), (d.NEW,)),
    "busy-disk": ("busy", "disk", (8,), (d.NEW,)),
    "disk-dma": ("disk-dma", "disk", (7, 12), (d.NEW,)),
    "disk-dma-bytr": ("disk-dma-bytr", "disk", (3,), (d.NEW,)),
    "swap-countdown": ("busy", "swap", (12,), (d.BOOT_A, d.BOOT_B)),
    "swap-landed": ("busy", "swap", (130,), (d.BOOT_A,)),
    "copper_irq": ("copper_irq", [], (2,), (d.NEW,)),
    "mouse": ("input_all", ["--port", "0:2:1:40:-25", "--port", "1:1:65:0:0"], (4,), (d.NEW,)),
    "keys": ("vsync", input_tests.SCENARIOS["keys"][3], (6,), (d.NEW,)),
    "cias": ("cias", [], (8,), (d.NEW,)),
    "clock": ("clock", [], (4,), (d.NEW,)),
    "busy-ntsc": ("busy", ["--ntsc"], (2,), (d.NEW,)),
}
KICKSTART = {
    "kickstart": ("kickstart", input_tests.KICKSTART_INPUT, (60,), (d.NEW,)),
    "kickstart-handshake": ("kickstart", input_tests.KICKSTART_INPUT, (107,), (d.NEW,)),
}


class Det03Case(d.Det02Case):
    SAVE_OP = "--public-save-at"
    LOAD_OP = "--public-load-at"
    ROMS = d.Det02Tests.ROMS


class Det03Tests(Det03Case):
    def test_the_public_save_is_the_state_the_registry_writes(self):
        for rom, extra, n in (("vsync", [], 3), ("busy", "disk", 8), ("tone", [], 3)):
            with self.subTest(rom=rom, n=n):
                internal, public = self.dir / f"det03-internal-{rom}.state", self.dir / f"det03-public-{rom}.state"
                run = self.probe(rom, "--units", str(n), *self.arguments(extra), "--save-at", str(n), str(internal), "--public-save-at", str(n), str(public))
                self.assertEqual(internal.read_bytes(), public.read_bytes())
                self.assertEqual(2, len(run.saves))
                self.assertEqual(run.saves[0][1:], run.saves[1][1:], "the same size, hash and bound")

    def test_an_instance_that_is_not_equivalent_refuses_the_state_and_goes_on_as_if_nothing_had_happened(self):
        path = self.dir / "det03-foreign.state"
        self.probe("vsync", "--units", "3", "--save-at", "3", str(path))
        control = self.probe("vsync", "--units", "8", "--every", "1", "--ntsc")
        other_standard = self.probe("vsync", "--units", "8", "--every", "1", "--ntsc", "--public-load-at", "5", str(path))
        other_media = self.probe("vsync", "--units", "8", "--every", "1", "--adf", str(self.adf), "--public-load-at", "5", str(path))
        for run, text in ((other_standard, "config_hash"), (other_media, "media_manifest_hash")):
            (unit, code, message), = run.loads
            self.assertEqual((5, 10), (unit, code), message)          # P6_ERROR_INCOMPATIBLE
            self.assertIn(text, message)
        self.assertEqual(control.unit(5), other_standard.after[0], "a refused state changed the machine")
        for unit in range(6, 9):
            self.assertEqual(control.unit(unit), other_standard.unit(unit), f"unit {unit}: the machine did not go on")

    def test_the_capability_is_announced_by_the_image_that_carries_the_functions(self):
        run = self.probe("vsync", "--units", "1", "--public-at", "1")
        self.assertEqual("1", run.public[0][1])


d.populate(Det03Tests, SCENARIOS, {})


@unittest.skipUnless(os.environ.get("P6_KICKSTART_ROM"), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart image")
class Det03KickstartTests(Det03Case):
    pass


d.populate(Det03KickstartTests, KICKSTART, {})


if __name__ == "__main__":
    unittest.main()
