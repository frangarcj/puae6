"""T55c7/c8: real FRESTORE040 on independent MC68040UM/FPSP frames."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.build_support import build_targets
from tests.host.test_cpu_fpu import ROOT, builder


class Frestore040Tests(unittest.TestCase):
    def test_unimp_fields_exception_priority_bus_and_following_arithmetic(self):
        tree = ROOT / "build/state-opt"
        build_targets(tree, ["p6_state_probe"], optimized=True)
        with tempfile.TemporaryDirectory(prefix="p6-frestore040-", dir=ROOT / "build") as tmp:
            exe, rom = Path(tmp) / "probe", Path(tmp) / "test.rom"
            rom.write_bytes(builder.build("entry:\n MOVE.W #$2700,SR\n MOVE.B #3,$BFE201.L\n MOVE.B #2,$BFE001.L\nloop:\n BRA.S loop\n"))
            includes = ["-I" + str(ROOT / p) for p in ("ports/portable", "build/generated", "build/upstream/include", "build/upstream", "bridge/include", "bridge")]
            objects = sorted(str(p) for p in (tree / "CMakeFiles/p6_engine.dir").rglob("*.o"))
            subprocess.run(["clang++", "-std=c++17", "-UNDEBUG", "-O1", "-g", "-fno-fast-math", "-ffp-contract=off", *includes,
                            str(ROOT / "tests/host/frestore040_probe.cpp"), *objects, str(tree / "libp6_bridge.a"),
                            str(tree / "libp6_portable.a"), "-o", str(exe)], check=True, timeout=300)
            result = subprocess.run([str(exe), str(rom)], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(86, result.stdout.count(" PASS"), result.stdout)
            print(result.stdout, end="")
