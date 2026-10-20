"""T55c10/LR01: A4040 selectors, internal FPU, Z3 and portable restore."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from test_memory import PEEK, descriptors
from tests.build_support import build_targets
from tests.state import test_a4040 as a4040


@unittest.skipUnless(lr.TOOLS, "toolchain unavailable")
class A4040FrontendTests(lr.LibretroHarness):
    compare_with_headless = lr.LibretroTests.compare_with_headless

    @classmethod
    def setUpClass(cls):
        built = build_targets(lr.ROOT / "build/state-opt", ["p6_libretro_host", "puae6_libretro", "p6_headless"], optimized=True)
        cls.host, cls.core, cls.headless = (built[k] for k in ("p6_libretro_host", "puae6_libretro", "p6_headless"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="a4040-lr-", dir=lr.ROOT / "build")
        cls.counter, cls.roms = 0, {}
        for name, (source, _, _) in a4040.A4040Tests.ROMS.items():
            cls.roms[name] = Path(cls.tmp.name) / (name + ".rom")
            cls.roms[name].write_bytes(lr.builder.build(source))

    @staticmethod
    def views(out):
        return [(r["unit"], r["ticks"], r["audio"], r["ahash"], r["hash"], r["px"]) for r in lr.LibretroHarness.runs(out)]

    def test_cpu_fpu_and_z3_oracles_and_same_new_process_restore(self):
        for cpu in a4040.CPU:
            for rom, fpu, extra, address, expected in (
                    ("cpu-" + cpu, "None", (), "0x2010", "0f967f0a"),
                    ("fpu", "M" + cpu, (), "0x2020", "3ffd0000aaaaaaaaaaaaaaab"),
                    ("z3", "None", ("--set", "puae6_z3_memory=MB16"), "0x22000000", "11223344"),
                    ("cpu-" + cpu, "None", ("--set", "puae6_fast_size=None"), "0x2010", "0f967f0a")):
                with self.subTest(cpu=cpu, rom=rom, extra=extra):
                    # Exercise the preset and the individual selectors; FPU None overrides no preset here.
                    options = ("--set", "puae6_machine=A4040", "--set", "puae6_cpu_compatibility=normal",
                               "--set", "puae6_cpu_model=" + cpu, "--set", "puae6_fpu_model=" + fpu, *extra)
                    n, m = (12, 3) if rom == "z3" else (3, 4)
                    state = Path(self.tmp.name) / "restore.state"
                    out = self.frontend(rom, "--runs", n + 2*m, *options, "--do", f"{n}:save:s",
                                        "--do", f"{n}:savefile:{state}", "--do", f"{n+m}:load:s",
                                        "--do", f"{n}:peek:{address}:{len(expected)//2}", name="kick40068.A4000").stdout
                    self.assertIn("state load s exact ok=1", out)
                    self.assertIn(expected, [p[3] for p in PEEK.finditer(out)])
                    future = self.views(out)[n:n+m]
                    self.assertEqual(future, self.views(out)[n+m:])
                    fresh = self.frontend(rom, "--runs", m, *options, "--do", f"0:loadfile:{state}", name="kick40068.A4000").stdout
                    self.assertIn(" exact ok=1", fresh)
                    self.assertEqual(future, self.views(fresh))
                    self.assertEqual(re.search(r"state final .*hash=(\w+)", out)[1], re.search(r"state final .*hash=(\w+)", fresh)[1])
                    flags = ("--machine", "a4040", "--cpu", cpu, "--cpu-compatibility", "normal", "--fpu", "0" if fpu == "None" else cpu)
                    flags += ("--z3", "16") if rom == "z3" else ("--fast", "none") if extra else ()
                    plain = self.frontend(rom, "--runs", n, *options, name="kick40068.A4000").stdout
                    self.compare_with_headless(plain, rom, n, *flags)
                    if rom == "z3":
                        self.assertEqual((0x22000000, 16 << 20), (descriptors(out)[0]["Z3"]["start"], descriptors(out)[0]["Z3"]["len"]))
                    if fpu != "None":
                        preset = "a4040_normal" if cpu == "68040" else "a4060_normal"
                        bundled = self.frontend(rom, "--runs", n, "--set", "puae6_preset=" + preset, name="kick40068.A4000").stdout
                        self.assertEqual(self.views(plain), self.views(bundled))
                    state.unlink()

    def test_invalid_pairs_and_foreign_cpu_fpu_states_are_refused(self):
        base = ("--set", "puae6_machine=A4040", "--set", "puae6_cpu_compatibility=normal")
        for cpu, fpu in (("68040", "M68060"), ("68060", "M68040"), ("68040", "M68882")):
            out = self.frontend("cpu-68040", *base, "--set", "puae6_cpu_model=" + cpu, "--set", "puae6_fpu_model=" + fpu, name="kick40068.A4000", expect=3).stdout
            self.assertIn("FPU" if fpu == "M68882" else "matching A4040 internal CPU", out)
        out = self.frontend("cpu-68040", "--set", "puae6_machine=A4040", name="kick40068.A4000", expect=3).stdout
        self.assertIn("normal CPU accuracy", out)
        state = Path(self.tmp.name) / "identity.state"
        self.frontend("cpu-68040", "--runs", 3, *base, "--do", f"3:savefile:{state}", name="kick40068.A4000")
        for change in ("puae6_cpu_model=68060", "puae6_fpu_model=M68040", "puae6_z3_memory=MB16"):
            out = self.frontend("cpu-68040", "--runs", 4, *base, "--set", change, "--do", f"3:loadfile:{state}", name="kick40068.A4000").stdout
            self.assertIn(" exact ok=0", out)
        state.unlink()


if __name__ == "__main__":
    unittest.main()
