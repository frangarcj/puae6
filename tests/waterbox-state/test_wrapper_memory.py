"""T49: WB03 and the identity of a state and of a movie on machines with other memory (Zorro II Fast RAM, Chip and Slow RAM), in BizHawk's Waterbox host (the real sandbox
snapshot, never the native serializer).

The memory is three sync settings of the core (ChipMemory, SlowMemory, FastMemory, restart settings): the wrapper gives them to the guest in p6_config, and the mmap heap of the sandbox
grows with the memory above what the machine comes with (Puae6Core.Heaps(settings), twice the extra memory, measured in manifests/waterbox-memory.json); the config hash of the state
envelope and the identity of a movie include them when they are not the stock ones.

WB03  the A1200 with 8 MiB of Fast RAM, the A600 of PUAE5 (2 MiB Chip + 8 MiB Fast) and the A500 with 2 MiB of Fast RAM and 1 MiB of Slow RAM: save at N, go on M units, load, go on M
      again: the same units and the same digest; a state runs in a new sandbox (WB04) and in another process (WB15); with the owner's Kickstarts, where supplied (opt-in), the
      Kickstart configures the board and the snapshot restores a machine that is using it.
AREAS the Fast RAM domain has the size of the board, reads and writes through the guest (also to pages that nothing has written since the seal), and a load takes the writes back.
ID    a state or a movie of one memory is refused by a core with other memory, before the sandbox is touched, and the instance that refused it goes on as if never asked.
HEAP  the heaps of the core for a configuration are the base ones plus twice the extra memory (the same rule as the smoke harness and the manifest), and are the base ones for stock memory.

Opt-in with the owner's firmware (as tests/waterbox-state/test_wrapper_machine.py): P6_KICKSTART_A1200_ROM, P6_KICKSTART_A600_ZIP, P6_KICKSTART_ROM and P6_AGA_ADF.
Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_memory.py"
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/waterbox-smoke"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS  # noqa: E402
from tests.step import test_kickstart_a600 as ks205  # noqa: E402
from test_puae6 import MEMORY_CONFIGS, extra_memory_kib, runtime_available  # noqa: E402
import test_wrapper_machine as base  # noqa: E402

A1200_FAST8 = ("a1200", {"fast": "MB8"})
A600_PUAE5 = ("a600", {"chip": "MB2", "fast": "MB8"})
A500_FAST2 = ("a500", {"fast": "MB2", "slow": "MB1"})
CONFIGS = (A1200_FAST8, A600_PUAE5, A500_FAST2)
# The settings of the cores above as p6_config sizes (the units of the rule of the manifest).
KIB = {"MB1": 1024, "MB2": 2048, "MB4": 4096, "MB8": 8192}


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperMemoryTests(unittest.TestCase):
    # The helpers of the machine tests (a session of the FFI host, its output, the held input), without their tests.
    @classmethod
    def setUpClass(cls):
        base.WrapperMachineTests.setUpClass.__func__(cls)
        base.WrapperMachineTests.count = 0          # the helper numbers its session programs in the class it was written for

    tearDownClass = classmethod(base.WrapperMachineTests.tearDownClass.__func__)
    rom, adfs, script, held_keys, session = (base.WrapperMachineTests.rom, base.WrapperMachineTests.adfs, base.WrapperMachineTests.script,
                                              base.WrapperMachineTests.held_keys, base.WrapperMachineTests.session)
    units, digests = staticmethod(base.WrapperMachineTests.units), staticmethod(base.WrapperMachineTests.digests)
    core = base.WrapperMachineTests.core

    def memcore(self, name, config, **options):
        machine, memory = config
        return self.core(name, machine, **{**memory, **options})

    # --- WB03, WB04 and WB15 -------------------------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_the_same_machine(self):
        script = self.held_keys()
        for config in CONFIGS:
            for n in (10, 55):
                with self.subTest(machine=config[0], memory=config[1], saved_at=n):
                    m = 60
                    out = self.session([self.memcore("A", config), f"run A {n} script={script} press={base.SWAP}", "save A s", f"run A {m} script={script} press={base.SWAP}",
                                        "digest A first", "load A s", f"run A {m} script={script} press={base.SWAP}", "digest A second"])
                    units = self.units(out, "A")
                    self.assertEqual(n + 2 * m, len(units))
                    self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]], "every unit after the load is the one that followed the save")
                    digests = self.digests(out, "A")
                    self.assertEqual(digests["first"], digests["second"], "and so is the machine at the end (hardware and wrapper)")

    def test_a_state_runs_in_a_new_sandbox_and_in_another_process_as_in_the_first(self):
        script = self.held_keys()
        n, m = 40, 50
        for config in CONFIGS:
            with self.subTest(machine=config[0], memory=config[1]):
                state = self.dir / f"memory-{config[0]}.state"
                out = self.session([self.memcore("A", config), f"run A {n} script={script} press={base.SWAP}", "save A s", f"savefile A {state}",
                                    self.memcore("B", config), "load B s", f"run A {m} script={script} press={base.SWAP}", f"run B {m} script={script} press={base.SWAP}",
                                    "digest A a", "digest B b"])
                a, b = ([u[1:] for u in self.units(out, name)] for name in "AB")
                self.assertEqual(a[n:n + m], b[:m])
                self.assertEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])
                later = self.session([self.memcore("C", config), f"loadfile C {state}", f"run C {m} script={script} press={base.SWAP}", "digest C c"])
                self.assertEqual(a[n:n + m], [u[1:] for u in self.units(later, "C")], "and in another process")
                self.assertEqual(self.digests(out, "A")["a"], self.digests(later, "C")["c"])

    def test_the_memory_makes_another_machine_of_the_same_model(self):
        out = self.session([self.core("A", "a1200"), self.memcore("B", A1200_FAST8), self.core("C", "a600"), self.memcore("D", A600_PUAE5), "run A 30", "run B 30", "run C 30", "run D 30",
                            "digest A a machine", "digest B b machine", "digest C c machine", "digest D d machine"])
        found = [self.digests(out, name)[name.lower()] for name in "ABCD"]
        self.assertEqual(4, len(set(found)))

    # --- the memory domains --------------------------------------------------------------------------------------------------------

    def test_the_fast_ram_domain_reads_and_writes_through_the_guest_and_a_load_takes_the_writes_back(self):
        for config, chip_bytes in ((A1200_FAST8, 2 << 20), (A600_PUAE5, 2 << 20)):
            with self.subTest(machine=config[0]):
                out = self.session([self.memcore("A", config), "run A 5", "peek A Fast_RAM 0 8", "poke A Fast_RAM 0 DEADBEEF01020304", "poke A Fast_RAM 7FFFF8 A5A5A5A5A5A5A5A5",
                                    "peek A Fast_RAM 0 8", "peek A Fast_RAM 7FFFF8 8", "save A s", "poke A Fast_RAM 100 11223344", "peek A Fast_RAM 100 4", "load A s",
                                    "peek A Fast_RAM 100 4", "peek A Fast_RAM 0 8", f"peek A Chip_RAM {chip_bytes - 8:x} 8"])
                peeks = [(domain, at, value.upper()) for domain, at, value in re.findall(r"^peek A (\S+) (\S+) (\w+)$", out, re.M)]
                self.assertEqual(("Fast_RAM", "0", "0000000000000000"), peeks[0], "a new board is zero")
                self.assertEqual(("Fast_RAM", "0", "DEADBEEF01020304"), peeks[1])
                self.assertEqual(("Fast_RAM", "7FFFF8", "A5A5A5A5A5A5A5A5"), peeks[2], "the last bytes of the 8 MiB board")
                self.assertEqual(("Fast_RAM", "100", "11223344"), peeks[3])
                self.assertEqual(("Fast_RAM", "100", "00000000"), peeks[4], "the load took the later write back")
                self.assertEqual(("Fast_RAM", "0", "DEADBEEF01020304"), peeks[5], "and kept the earlier ones")
                self.assertEqual(8, len(peeks[6][2]) // 2)

    # --- the identity of a state and of a movie ------------------------------------------------------------------------------------

    def test_a_state_of_other_memory_is_refused_and_the_instance_goes_on_as_if_never_asked(self):
        script = self.held_keys()
        cases = (("a1200 with 8 MiB made, stock loaded", A1200_FAST8, ("a1200", {})), ("stock made, a1200 with 8 MiB loaded", ("a1200", {}), A1200_FAST8),
                 ("8 MiB made, 4 MiB loaded", A1200_FAST8, ("a1200", {"fast": "MB4"})), ("chip 2 MiB made on the A600, stock loaded", A600_PUAE5, ("a600", {})),
                 # the same extra memory (512 KiB) and so the same heaps, in other places: only the configuration tells them apart
                 ("1 MiB of Chip made, 1 MiB of Slow loaded", ("a500", {"chip": "MB1"}), ("a500", {"slow": "MB1"})))
        for label, made, loaded in cases:
            with self.subTest(label):
                out = self.session([self.memcore("M", made), f"run M 40 script={script} press={base.SWAP}", "save M s",
                                    self.memcore("T", loaded), f"run T 12 script={script} press={base.SWAP}", "load T s",
                                    f"run T 25 script={script} press={base.SWAP}", "digest T after", self.memcore("W", loaded), f"run W 12 script={script} press={base.SWAP}",
                                    f"run W 25 script={script} press={base.SWAP}", "digest W twin"])
                # The mmap heap is part of what a snapshot is compatible with and it grows with the memory: a state of another size is refused as OtherHeaps, one of the same size as OtherConfig.
                refusal = re.search(r"^rejected T s reason=(OtherConfig|OtherHeaps) message=(.*)$", out, re.M)
                self.assertIsNotNone(refusal, out[-1500:])
                same_heaps = self.heaps(made[0].upper(), **{k: v for k, v in made[1].items()}) == self.heaps(loaded[0].upper(), **{k: v for k, v in loaded[1].items()})
                self.assertEqual("OtherConfig" if same_heaps else "OtherHeaps", refusal[1], refusal[2])
                t, w = [u[1:] for u in self.units(out, "T")], [u[1:] for u in self.units(out, "W")]
                self.assertEqual(w, t, "the instance that refused the state is the instance that never saw it")
                self.assertEqual(self.digests(out, "W")["twin"], self.digests(out, "T")["after"])

    def test_a_movie_of_other_memory_is_refused_and_its_own_is_accepted(self):
        script = self.held_keys()
        movie = self.dir / "fast.bk2"
        out = self.session([self.memcore("R", A1200_FAST8), f"run R 40 script={script} press={base.SWAP} record", f"endmovie R {movie}"])
        self.assertIn(f"movie {movie} frames=40", out)
        out = self.session([self.core("P", "a1200"), f"moviecheck P {movie}", self.memcore("Q", A1200_FAST8), f"moviecheck Q {movie}"])
        self.assertEqual(1, len(re.findall(r"^movie rejected P reason=OtherConfig message=(.*)$", out, re.M)), out[-1500:])
        self.assertEqual(1, out.count("movie accepted Q"))

    def test_a_movie_with_fast_ram_plays_back_to_the_units_of_its_recording(self):
        script = self.held_keys()
        movie = self.dir / "fast-play.bk2"
        recorded = self.session([self.memcore("R", A1200_FAST8), f"run R 90 script={script} press={base.SWAP} record", f"endmovie R {movie}", "digest R end"])
        played = self.session([self.memcore("P", A1200_FAST8), f"run P 90 movie={movie}", "digest P end"])
        self.assertEqual([u[1:] for u in self.units(recorded, "R")], [u[1:] for u in self.units(played, "P")])
        self.assertEqual(self.digests(recorded, "R")["end"], self.digests(played, "P")["end"])

    # --- the heaps -----------------------------------------------------------------------------------------------------------------

    def heaps(self, machine="A500", **memory):
        args = ["--machine", machine]
        for name, value in memory.items():
            args += [f"--{name}", value]
        result = build_waterbox.run_ffi("heaps", *args)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return [int(v) for v in re.search(r"^heaps (.*)$", result.stdout, re.M)[1].split()]

    def test_the_heaps_are_the_base_ones_for_stock_memory_and_grow_by_twice_the_extra_memory_otherwise(self):
        manifest = json.loads((ROOT / "manifests/waterbox-memory.json").read_text())
        layout, factor = manifest["layout"], manifest["memory_budget"]["mmap_extra_factor"]
        base_kib = [layout[name] // 1024 for name in ("sbrk", "sealed", "invisible", "plain", "mmap")]
        for machine in ("A500", "A600", "A1200"):
            self.assertEqual(base_kib, self.heaps(machine), f"{machine}: stock memory keeps the heaps")
        self.assertEqual(base_kib, self.heaps("A500", chip="KB512", slow="KB512"), "naming the machine's own sizes is the same machine")
        cases = ((("A1200", {"fast": "MB8"}), (3, 0, 0, 8192)), (("A600", {"chip": "MB2", "fast": "MB8"}), (2, 2048, 0, 8192)),
                 (("A1200", {"slow": "MB1_5", "fast": "MB8"}), (3, 0, 1536, 8192)), (("A500", {"fast": "MB2", "slow": "MB1"}), (1, 0, 1024, 2048)),
                 (("A500", {"chip": "MB1"}), (1, 1024, 0, 0)), (("A500", {"slow": "None"}), (1, 0, "none", 0)))
        for (machine, settings), (_, chip, slow, fast) in cases:
            with self.subTest(machine=machine, settings=settings):
                extra = extra_memory_kib(machine, chip, slow, fast)
                self.assertEqual(base_kib[:4] + [base_kib[4] + factor * extra], self.heaps(machine, **settings))
        for label, _, machine, memory in MEMORY_CONFIGS:       # and the configurations whose measurements are in the manifest are the ones the smoke harness ran
            self.assertEqual(0, extra_memory_kib(machine, **memory) % 16, label)

    # --- real firmware (opt-in) ----------------------------------------------------------------------------------------------------

    def real(self, config, rom, disk=None, n=200):
        machine, memory = config
        script = self.held_keys()
        m = 40
        state = self.dir / f"real-fast-{machine}.state"
        options = "".join(f" {k}={v}" for k, v in memory.items())
        core = lambda name: f"core {name} adf={disk} machine={machine}{options}" if disk else self.memcore(name, config, adf="")
        out = self.session([core("A"), f"run A {n} script={script}", "save A s", f"savefile A {state}", f"run A {m} script={script}", "digest A first",
                            "load A s", f"run A {m} script={script}", "digest A second", core("B"), "load B s", f"run B {m} script={script}", "digest B b"], firmware=rom)
        a, b = [u[1:] for u in self.units(out, "A")], [u[1:] for u in self.units(out, "B")]
        self.assertEqual(a[n:n + m], a[n + m:], "the real Kickstart: every unit after the load is the one that followed the save")
        self.assertEqual(a[n:n + m], b, "and in a new sandbox")
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual({digests["first"]}, {digests["first"], digests["second"], digests["b"]})
        later = self.session([core("C"), f"loadfile C {state}", f"run C {m} script={script}", "digest C c"], firmware=rom)
        self.assertEqual(a[n:n + m], [u[1:] for u in self.units(later, "C")], "and in another process")
        self.assertEqual(digests["first"], self.digests(later, "C")["c"])

    @unittest.skipUnless(base.KICKSTART_A1200 and os.path.isfile(base.KICKSTART_A1200 or ""), "BLOCKED: set P6_KICKSTART_A1200_ROM to an authorized Kickstart 3.1 r40.68 .rom (A1200)")
    def test_wb03_with_the_real_kickstart_31_and_8_mib_of_fast_ram(self):
        # The Kickstart configures the board by unit ~22 and the snapshot (unit 200) restores a system that keeps its memory list in it.
        self.real(A1200_FAST8, str(Path(base.KICKSTART_A1200).resolve()))

    @unittest.skipUnless(base.KICKSTART_A600_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP to a zip with an authorized Kickstart 2.05")
    def test_wb03_with_the_real_kickstart_205_on_the_puae5_a600(self):
        with tempfile.TemporaryDirectory(prefix="p6-kickstart-") as owner:
            rom = Path(owner) / "kickstart-2.05.rom"
            rom.write_bytes(ks205.member(base.KICKSTART_A600_ZIP, ".rom", 524288))
            self.real(A600_PUAE5, str(rom), n=200)

    @unittest.skipUnless(base.KICKSTART_A500 and os.path.isfile(base.KICKSTART_A500 or ""), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3 .rom")
    def test_wb03_with_the_real_kickstart_13_and_fast_ram_on_the_a500(self):
        self.real(("a500", {"fast": "MB2"}), str(Path(base.KICKSTART_A500).resolve()), n=200)


if __name__ == "__main__":
    unittest.main()
