"""T23e: WB03 and the identity of a state and of a movie on both machines, the A500 and the A600, in BizHawk's Waterbox host (the real sandbox snapshot, never the native serializer).

The machine is a sync setting of the core (Puae6SyncSettings.Machine, a restart setting): the wrapper gives the guest the machine in p6_config, and the config hash of the state
envelope and the one of the header of a movie (PUAE6Config) include it.

WB03  on the A500 and on the A600: save at N, go on M units, load, go on M again: the same units and the same digest; a state of one sandbox runs in a new one (WB04) and in
      another process (WB15), also with a real Kickstart where the owner supplies it (opt-in, below).
ID    a state or a movie of one machine is refused by the core of the other one, with a message that names both, before the sandbox is touched (the instance that refused it goes
      on as if never asked), and each machine takes its own.
AREAS the A600 has no Slow RAM domain and its Chip RAM is 1 MiB; the A500 keeps its three.

Opt-in with the owner's firmware (read in memory from the zip, written to a temporary directory outside the repository that goes away at the end; no hash, no byte and no copy of it
is kept anywhere):
    P6_KICKSTART_A600_ZIP  zip with the Kickstart 2.05 .rom (A600, 512 KiB): WB03/WB04/WB15 with the real ROM on the A600
    P6_KICKSTART_ROM       the Kickstart 1.3 .rom of the A500 (a raw file): the same on the A500

Needs the Waterbox runtime and the guest (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom), dotnet and the local NuGet cache; skips without them.
    python3 -m unittest discover -s tests/waterbox-state -p "test_wrapper_machine.py"
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
import build_waterbox  # noqa: E402
from tests.host.test_bridge_lifecycle import TOOLS, builder  # noqa: E402
from tests.input.test_input import ESC, JOYSTICK, MOUSE, line  # noqa: E402
from tests.scenarios.disk import disk  # noqa: E402
from tests.step import test_kickstart_a600 as ks205  # noqa: E402
from test_puae6 import runtime_available  # noqa: E402

GUEST = build_waterbox.BUILD / "puae6/release/puae6.wbx"
RECEIPT = build_waterbox.BUILD / "puae6/puae6-receipt.json"
SWAP = f"{disk.MOUNT_UNIT - 1}:Next_Slot;{disk.MOUNT_UNIT}:Insert_Disk"   # underscores stand for spaces in a program line
UNIT = re.compile(r"^u (\S+) (\d+) ticks=(\d+) hash=(\w+) audio=(\d+) pcm=(\w+) lag=(\d) light=(\d) events=(\d+)$")
MACHINES = ("a500", "a600")
KICKSTART_A600_ZIP = os.environ.get("P6_KICKSTART_A600_ZIP")
KICKSTART_A500 = os.environ.get("P6_KICKSTART_ROM")


def program_text(lines):
    return "\n".join(lines) + "\n"


@unittest.skipUnless(runtime_available() and TOOLS, "Waterbox runtime or puae6.wbx not built (tools/build_waterbox.py --puae6-run build/waterbox/boot.rom)")
class WrapperMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-machine-")
        cls.dir = Path(cls.tmp.name)
        for name, source in (("disk", disk.rom_source()), ("chipcell", (ROOT / "tests/waterbox-smoke/chipcell.s").read_text(encoding="utf-8"))):
            (cls.dir / f"{name}.rom").write_bytes(builder.build(source))
        for name, label in (("A", 0x41), ("B", 0x42)):
            (cls.dir / f"{name}.adf").write_bytes(disk.make_adf(label))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # --- helpers --------------------------------------------------------------------------------------------------------------------

    def rom(self, name):
        return str(self.dir / f"{name}.rom")

    def adfs(self, names="A,B"):
        return ",".join(str(self.dir / f"{n}.adf") for n in names.split(",") if n)

    def script(self, name, lines):
        path = self.dir / f"{name}.txt"
        path.write_text(program_text(lines))
        return str(path)

    def held_keys(self):
        """A key held from unit 50 on and the mouse moving: input that a state has to carry along."""
        return self.script("held", [line(keys=[ESC] if u >= 50 else [], p0=(MOUSE, 0, 1 if u % 7 == 0 else 0, 0), p1=(JOYSTICK, 0, 0, 0)) for u in range(1, 500)])

    def session(self, lines, firmware="disk", expect=0):
        WrapperMachineTests.count += 1
        path = self.dir / f"program-{WrapperMachineTests.count}.txt"
        path.write_text(program_text(lines))
        fw = firmware if os.path.isabs(firmware) else self.rom(firmware)
        result = build_waterbox.run_ffi("session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", self.sha, "--firmware", fw, "--program", str(path))
        self.assertEqual(expect, result.returncode, f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}")
        return result.stdout

    @staticmethod
    def units(out, name):
        return [m.groups() for m in map(UNIT.match, out.splitlines()) if m and m[1] == name]

    @staticmethod
    def digests(out, name):
        return {m[1]: m[2] for m in re.finditer(rf"^digest {name} (\S*) sha=(\w+) ", out, re.M)}

    def core(self, name, machine="a500", adf="A,B", **options):
        extra = "".join(f" {k}={v}" if v is not True else f" {k}" for k, v in options.items())
        media = f" adf={self.adfs(adf)}" if adf else ""
        return f"core {name}{media} machine={machine}{extra}"

    # --- WB03, WB04 and WB15 on both machines ---------------------------------------------------------------------------------------

    def test_save_go_on_load_and_repeat_gives_the_same_units_and_the_same_machine_on_both_machines(self):
        script = self.held_keys()
        # a disk read by DMA (units 26-52), the swap pending (80-180), a read after the swap (about 190), the motor on throughout, a key held from 50
        for machine in MACHINES:
            for n in (10, 55, 130):
                with self.subTest(machine=machine, saved_at=n):
                    m = 100
                    out = self.session([self.core("A", machine), f"run A {n} script={script} press={SWAP}", "save A s", f"run A {m} script={script} press={SWAP}", "digest A first",
                                        "load A s", f"run A {m} script={script} press={SWAP}", "digest A second"])
                    units = self.units(out, "A")
                    self.assertEqual(n + 2 * m, len(units))
                    self.assertEqual([u[1:] for u in units[n:n + m]], [u[1:] for u in units[n + m:]], "every unit after the load is the one that followed the save")
                    digests = self.digests(out, "A")
                    self.assertEqual(digests["first"], digests["second"], "and so is the machine at the end (hardware and wrapper)")

    def test_a_state_runs_in_a_new_sandbox_and_in_a_third_as_in_the_first_on_both_machines(self):
        script = self.held_keys()
        n, m = 85, 60
        for machine in MACHINES:
            with self.subTest(machine=machine):
                out = self.session([self.core("A", machine), f"run A {n} script={script} press={SWAP}", "save A s",
                                    self.core("B", machine), "load B s", self.core("C", machine), "run C 17", "load C s",
                                    f"run A {m} script={script} press={SWAP}", f"run B {m} script={script} press={SWAP}", f"run C {m} script={script} press={SWAP}",
                                    "digest A a", "digest B b", "digest C c"])
                a, b, c = ([u[1:] for u in self.units(out, name)] for name in "ABC")
                self.assertEqual(a[n:n + m], b[:m])
                self.assertEqual(a[n:n + m], c[17:17 + m])
                digests = {**self.digests(out, "A"), **self.digests(out, "B"), **self.digests(out, "C")}
                self.assertEqual({digests["a"]}, {digests["a"], digests["b"], digests["c"]})

    def test_a_state_written_by_one_process_is_loaded_by_another_new_one_on_both_machines(self):
        script = self.held_keys()
        for machine in MACHINES:
            with self.subTest(machine=machine):
                state = self.dir / f"process-{machine}.state"
                first = self.session([self.core("A", machine), f"run A 85 script={script} press={SWAP}", f"savefile A {state}", f"run A 70 script={script} press={SWAP}", "digest A end"])
                second = self.session([self.core("B", machine), f"loadfile B {state}", f"run B 70 script={script} press={SWAP}", "digest B end"])
                self.assertEqual([u[1:] for u in self.units(first, "A")][85:], [u[1:] for u in self.units(second, "B")])
                self.assertEqual(self.digests(first, "A")["end"], self.digests(second, "B")["end"])

    def test_the_two_machines_are_two_machines_not_one_with_two_names(self):
        # The same micro-ROM, the same disks, the same input: Chip RAM is 512 KiB on one and 1 MiB on the other, and the snapshot of the sandbox says so.
        out = self.session([self.core("A", "a500"), self.core("B", "a600"), "run A 30", "run B 30", "digest A a machine", "digest B b machine"])
        self.assertNotEqual(self.digests(out, "A")["a"], self.digests(out, "B")["b"])

    # --- the identity of a state and of a movie ------------------------------------------------------------------------------------

    def test_a_state_of_the_other_machine_is_refused_naming_both_and_the_instance_goes_on_as_if_never_asked(self):
        script = self.held_keys()
        for made, loaded in (("a500", "a600"), ("a600", "a500")):
            with self.subTest(made=made, loaded=loaded):
                target = self.core("T", loaded)
                out = self.session([self.core("A", made), f"run A 70 script={script} press={SWAP}", "save A s",
                                    target, f"run T 12 script={script} press={SWAP}", "digest T before", "load T s", f"run T 25 script={script} press={SWAP}", "digest T after",
                                    self.core("W", loaded), f"run W 12 script={script} press={SWAP}", f"run W 25 script={script} press={SWAP}", "digest W twin"])
                refusal = re.search(r"^rejected T s reason=OtherConfig message=(.*)$", out, re.M)
                self.assertIsNotNone(refusal, out[-1500:])
                self.assertIn(made.upper(), refusal[1])
                self.assertIn(loaded.upper(), refusal[1])
                t, w = [u[1:] for u in self.units(out, "T")], [u[1:] for u in self.units(out, "W")]
                self.assertEqual(w, t, "the instance that refused the state is the instance that never saw it")
                self.assertEqual(self.digests(out, "W")["twin"], self.digests(out, "T")["after"])

    def test_a_state_of_the_same_machine_with_other_settings_is_refused_without_naming_a_machine(self):
        out = self.session([self.core("A", "a600"), "run A 20", "save A s", self.core("T", "a600", ntsc=True), "load T s"])
        refusal = re.search(r"^rejected T s reason=OtherConfig message=(.*)$", out, re.M)
        self.assertIsNotNone(refusal)
        self.assertIn("other sync settings", refusal[1])
        self.assertIn("A600", refusal[1], "the message says which Amiga this core is")

    def test_a_movie_of_the_other_machine_is_refused_naming_both_and_each_machine_accepts_its_own(self):
        script = self.held_keys()
        movies = {}
        for machine in MACHINES:
            movies[machine] = self.dir / f"{machine}.bk2"
            out = self.session([self.core("R", machine), f"run R 60 script={script} press={SWAP} record", f"endmovie R {movies[machine]}"])
            self.assertIn(f"movie {movies[machine]} frames=60", out)
        for recorded, played in (("a500", "a600"), ("a600", "a500")):
            with self.subTest(recorded=recorded, played=played):
                out = self.session([self.core("P", played), f"moviecheck P {movies[recorded]}"])
                refusal = re.search(r"^movie rejected P reason=OtherConfig message=(.*)$", out, re.M)
                self.assertIsNotNone(refusal, out[-1500:])
                self.assertIn(recorded.upper(), refusal[1])
                self.assertIn(played.upper(), refusal[1])
        for machine in MACHINES:
            self.assertIn("movie accepted P", self.session([self.core("P", machine), f"moviecheck P {movies[machine]}"]), machine)

    def test_a_movie_of_the_a600_plays_back_to_the_units_of_its_recording(self):
        script = self.held_keys()
        movie = self.dir / "a600-play.bk2"
        recorded = self.session([self.core("R", "a600"), f"run R 120 script={script} press={SWAP} record", f"endmovie R {movie}", "digest R end"])
        played = self.session([self.core("P", "a600"), f"run P 120 movie={movie}", "digest P end"])
        self.assertEqual([u[1:] for u in self.units(recorded, "R")], [u[1:] for u in self.units(played, "P")])
        self.assertEqual(self.digests(recorded, "R")["end"], self.digests(played, "P")["end"])

    # --- real firmware (opt-in) ----------------------------------------------------------------------------------------------------

    def real_firmware_roundtrip(self, machine, rom):
        """WB03, WB04 and WB15 with a real Kickstart: no disk, so the machine sits at the insert-disk screen after its boot; keys and the mouse keep the input path alive."""
        script = self.held_keys()
        n, m = 300, 60
        state = self.dir / f"real-{machine}.state"
        out = self.session([self.core("A", machine, adf=""), f"run A {n} script={script}", "save A s", f"savefile A {state}", f"run A {m} script={script}", "digest A first",
                            "load A s", f"run A {m} script={script}", "digest A second",
                            self.core("B", machine, adf=""), "load B s", f"run B {m} script={script}", "digest B b"], firmware=rom)
        a, b = [u[1:] for u in self.units(out, "A")], [u[1:] for u in self.units(out, "B")]
        self.assertEqual(a[n:n + m], a[n + m:], "the real Kickstart: every unit after the load is the one that followed the save")
        self.assertEqual(a[n:n + m], b, "and in a new sandbox")
        digests = {**self.digests(out, "A"), **self.digests(out, "B")}
        self.assertEqual({digests["first"]}, {digests["first"], digests["second"], digests["b"]})
        later = self.session([self.core("C", machine, adf=""), f"loadfile C {state}", f"run C {m} script={script}", "digest C c"], firmware=rom)
        self.assertEqual(a[n:n + m], [u[1:] for u in self.units(later, "C")], "and in another process")
        self.assertEqual(digests["first"], self.digests(later, "C")["c"])

    @unittest.skipUnless(KICKSTART_A600_ZIP, "BLOCKED: set P6_KICKSTART_A600_ZIP to a zip with an authorized Kickstart 2.05")
    def test_wb03_with_the_real_kickstart_205_on_the_a600(self):
        with tempfile.TemporaryDirectory(prefix="p6-kickstart-") as owner:
            rom = Path(owner) / "kickstart-2.05.rom"
            rom.write_bytes(ks205.member(KICKSTART_A600_ZIP, ".rom", 524288))
            self.real_firmware_roundtrip("a600", str(rom))

    @unittest.skipUnless(KICKSTART_A500 and os.path.isfile(KICKSTART_A500 or ""), "BLOCKED: set P6_KICKSTART_ROM to an authorized Kickstart 1.3 .rom")
    def test_wb03_with_the_real_kickstart_13_on_the_a500(self):
        self.real_firmware_roundtrip("a500", str(Path(KICKSTART_A500).resolve()))


if __name__ == "__main__":
    unittest.main()
