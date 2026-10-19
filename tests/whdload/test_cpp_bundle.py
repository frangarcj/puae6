"""T65b1: ASan/UBSan standalone builder, byte parity with the T65a recipe."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from tests.whdload import lha_fixture  # adds tools/ to the path
from whdload import bundle, lha

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/whdload-builder"
TOOL = BUILD / "bundle_probe"
WHDLOAD, GAME = (os.environ.get(n) for n in ("P6_WHDLOAD_LHA", "P6_FATALMISSION_LHA"))


def manifest(files):
    return "".join(f"{'D' if b is None else 'F'} {p.encode('latin-1').hex()} {(b or b'').hex() or '-'}\n" for p, b in files.items())


@unittest.skipUnless(shutil.which("clang++"), "BLOCKED: clang++ unavailable")
class CppBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        sources = [ROOT / "tests/whdload/bundle_probe.cpp", *sorted((ROOT / "adapters/libretro").glob("*.h"))]
        if not TOOL.exists() or TOOL.stat().st_mtime < max(p.stat().st_mtime for p in sources):
            subprocess.run(["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                            "-fno-sanitize-recover=all", "-I" + str(ROOT / "references/libretro-api/include"), str(sources[0]), "-o", str(TOOL)],
                           check=True, capture_output=True, text=True, timeout=180)

    def compare(self, files, *, buttonwait=False, kicks=None, archive=None, program=b"program", vfs=b"vfs", reject=False):
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            work = Path(temp)
            for name, contents in (("tree", manifest(files).encode()), ("roms", manifest(kicks or {}).encode()), ("program", program), ("vfs", vfs)):
                (work / name).write_bytes(contents)
            source, mode = work / "tree", "tree"
            if archive:
                source, mode = archive
            out = subprocess.run([str(TOOL), mode, str(source), str(work / "program"), str(work / "vfs"), str(work / "roms"), str(work) + "/", str(int(buttonwait))],
                                 capture_output=True, text=True, timeout=60, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            if reject:
                self.assertEqual(2, out.returncode, out.stderr)
                self.assertFalse(list(work.glob("*.hdf")))
                return
            self.assertEqual(0, out.returncode, out.stderr)
            expected = bundle.build({**{p: b for p, b in files.items() if b is not None}, "C/WHDLoad": program}, vfs, buttonwait=buttonwait, kickstarts=kicks)
            for name, image in expected.items():
                self.assertEqual(image, (work / name).read_bytes(), name)

    def test_three_roles_root_nested_latin1_and_buttonwait(self):
        for prefix in ("", "Game/"):
            for buttonwait in (False, True):
                with self.subTest(prefix=prefix, buttonwait=buttonwait):
                    self.compare({prefix + "A.slave": b"slave", prefix + "donn\xe9es/level": bytes(range(256)), "Empty": None,
                                  "S/Startup-Sequence": b"ignore", "Devs/Kickstarts/kick40068.A1200.RTB": b"rtb"},
                                 buttonwait=buttonwait, kicks={"kick40068.A1200": b"private fixture"})

    def test_invalid_input_has_no_partial_bundle(self):
        for files in ({}, {"A.slave": b"a", "B.slave": b"b"}, {"A.slave": b"a", "../escape": b"x"},
                      {"A.slave": b"a", "Data/x": b"x", "data/y": b"y"}, {"A.slave": b"a", "Data": b"x", "Data/y": b"y"},
                      {"A.slave": b"a", **{f"x{n}": b"" for n in range(4096)}}):
            with self.subTest(paths=list(files)[:4]):
                self.compare(files, reject=True)
        self.compare({"A.slave": b"a"}, kicks={"../kick": b"bad"}, reject=True)

    def test_generated_data_is_current(self):
        subprocess.run(["python3", "tools/generate_whdload.py", "--check"], cwd=ROOT, check=True, capture_output=True, timeout=30)

    @unittest.skipUnless(WHDLOAD and GAME, "BLOCKED: set P6_WHDLOAD_LHA and P6_FATALMISSION_LHA; no ROM required")
    def test_real_lha_and_zip_match_recipe_without_rom(self):
        provider = {e.name: e.data for e in lha.read_archive(Path(WHDLOAD).read_bytes()) if not e.is_dir}
        game = {e.name: e.data for e in lha.read_archive(Path(GAME).read_bytes()) if not e.is_dir}
        program, vfs = (provider["WHDLoad/C/" + n] for n in ("WHDLoad", "WHDLoad.VFS"))
        self.assertEqual(bundle.VFS_SHA256, hashlib.sha256(vfs).hexdigest())
        self.compare(game, archive=(GAME, "lha"), program=program, vfs=vfs)
        with tempfile.TemporaryDirectory(dir=BUILD) as temp:
            path = Path(temp) / "game.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in game.items():
                    archive.writestr(name, data)
            self.compare(game, archive=(path, "zip"), program=program, vfs=vfs, buttonwait=True)
