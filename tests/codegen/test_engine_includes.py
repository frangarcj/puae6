"""Include closure of the A500 engine units; it does not run an emulator.

Each selected unit is preprocessed for its user-header dependencies (-MM). Every file
must live in the portable layer, the generated sources or the materialized WinUAE
tree, and the only od-win32 files allowed are the 68k arithmetic helpers. Adding a
host header to the profile, or a patch that starts including a Win32 file, fails here.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

from tests.codegen.test_engine_warnings import engine_commands

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "build/upstream"
ALLOWED_ROOTS = (ROOT / "ports/portable", ROOT / "build/generated", UPSTREAM)
ALLOWED_OD_WIN32 = {"od-win32/machdep/m68k.cpp", "od-win32/machdep/m68k.h"}


def dependencies(entry):
    cleaned, skip = [], 0
    for arg in shlex.split(entry["command"]):
        if skip:
            skip -= 1
        elif arg in ("-o", "-MF", "-MT", "-MQ"):
            skip = 1
        elif arg not in ("-MD", "-MMD"):
            cleaned.append(arg)
    result = subprocess.run([*cleaned, "-MM"], cwd=entry["directory"], capture_output=True,
                            text=True, timeout=300)
    assert result.returncode == 0, result.stderr[:300]
    tokens = result.stdout.replace("\\\n", " ").split()[1:]
    return entry["file"], [Path(os.path.realpath(t)) for t in tokens if not t.endswith(":")]


@unittest.skipUnless(all(shutil.which(t) for t in ("cmake", "clang++")), "toolchain unavailable")
class EngineIncludeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=300)
        with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as pool:
            cls.closure = dict(pool.map(dependencies, engine_commands()))

    def test_every_dependency_is_inside_an_allowed_tree(self):
        self.assertEqual(len(self.closure), 37)   # + fpp.cpp, fpp_softfloat.cpp, softfloat_decimal.cpp (T54)
        for unit, paths in self.closure.items():
            for path in paths:
                with self.subTest(unit=Path(unit).name, header=path.name):
                    self.assertTrue(any(root in path.parents for root in ALLOWED_ROOTS), str(path))

    def test_only_the_68k_helpers_come_from_od_win32(self):
        found = {str(p.relative_to(UPSTREAM)) for paths in self.closure.values() for p in paths
                 if (UPSTREAM / "od-win32") in p.parents}
        self.assertEqual(found, ALLOWED_OD_WIN32)

    def test_no_desktop_third_party_or_frontend_trees(self):
        forbidden = {"caps", "floppybridge", "tinyxml2", "sdl", "qt", "libretro", "od-win32/dinput",
                     "od-win32/sound", "od-win32/win32", "od-win32/gui", "od-win32/parser"}
        for unit, paths in self.closure.items():
            for path in paths:
                relative = str(path.relative_to(ROOT)).lower()
                with self.subTest(unit=Path(unit).name, header=path.name):
                    self.assertFalse(any(f"/{f}/" in relative or relative.endswith(f"/{f}.h")
                                         for f in forbidden), relative)


if __name__ == "__main__":
    unittest.main()
