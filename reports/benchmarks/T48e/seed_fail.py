"""Seeded difference: the -O0 probe's stdout (or its state file) is altered after it ran; the parity test has to fail."""
import subprocess, sys, unittest
from pathlib import Path
sys.path.insert(0, ".")
from tests.state import test_build_parity as t
real = subprocess.run
mode = sys.argv[1]
def seeded(args, *a, **k):
    r = real(args, *a, **k)
    if "link-audit" in str(args[0]):
        if mode == "stdout":
            r.stdout = r.stdout.replace("digest 3 cpu ", "digest 3 cpu 0", 1)
        else:
            for i, w in enumerate(args):
                if w == "--save-at":
                    p = Path(args[i + 2]); b = bytearray(p.read_bytes()); b[100] ^= 1; p.write_bytes(bytes(b))
    return r
t.subprocess.run = seeded
suite = unittest.defaultTestLoader.loadTestsFromName("tests.state.test_build_parity.BuildParityTests.test_state_saved_and_loaded_in_the_same_image")
res = unittest.TextTestRunner(verbosity=0).run(suite)
print("SEEDED", mode, "failures", len(res.failures) + len(res.errors))
