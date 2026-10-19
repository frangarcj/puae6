"""T65c1: actual C# builder vs T65a byte for byte; no engine, guest or ROM."""
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

from tests.whdload import lha_fixture
from whdload import bundle, ffs, lha

ROOT = Path(__file__).resolve().parents[2]
DOTNET = shutil.which("dotnet")
CACHE = Path.home() / ".nuget/packages"


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            output.writestr(name + "/" if data is None else name, data or b"")
    return stream.getvalue()


@unittest.skipUnless(DOTNET and CACHE.is_dir(), "dotnet and the existing local NuGet cache are required")
class BundleCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="whd-csharp-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.env = {**os.environ, "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "DOTNET_ROLL_FORWARD": "Major",
                   "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"), "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
        built = subprocess.run([DOTNET, "build", str(ROOT / "tests/ffi/Ffi.csproj"), "--nologo", "-m:1",
                                f"-p:RestoreSources={CACHE}", "-p:P6ManagedTestSuffix=-whdload"],
                               cwd=ROOT, env=cls.env, capture_output=True, text=True, timeout=180)
        if built.returncode:
            raise AssertionError(built.stdout[-5000:] + built.stderr[-5000:])
        cls.dll = ROOT / "build/ffi-whdload/bin/Debug/net8.0/Ffi.dll"

    def run_bundle(self, game, program=b"synthetic program", vfs=b"synthetic plugin", *, wait=False, kickstarts=None, provider=None, success=True):
        (self.work / "game.arc").write_bytes(game)
        args = ["whdbundle", "--archive", str(self.work / "game.arc"), "--buttonwait", str(wait).lower()]
        for name, data in (("program", program), ("vfs", vfs), ("kickstarts", archive(kickstarts) if kickstarts else None), ("provider", provider)):
            if data is not None:
                (self.work / name).write_bytes(data)
                args += ["--" + name, str(self.work / name)]
        out = self.work / ("output-" + str(len(list(self.work.glob("output-*")))))
        result = subprocess.run([DOTNET, str(self.dll), *args, "--out", str(out)], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        if not success:
            self.assertFalse(out.exists(), "a refusal published a partial bundle")
            return result.stderr
        self.assertEqual(set(bundle.NAMES), {p.name for p in out.iterdir()})
        self.assertEqual(game, (self.work / "game.arc").read_bytes())
        return {p.name: p.read_bytes() for p in out.iterdir()}

    def test_ascii_latin1_paths_lha_zip_and_buttonwait_match_every_hdf_byte(self):
        for high in (False, True):
            game = {"Game/A.slave": b"slave", "Game/data/level": bytes(range(256)) * 150, "Game/empty": b"", "Game/unused": None,
                    "Devs/Kickstarts/kick40068.A1200.RTB": b"explicit relocation data"}
            if high:
                game["Game/donn\xe9es/\xf1.dat"] = b"Latin1 data"
            for wait in (False, True):
                expected = bundle.build({"C/WHDLoad": b"synthetic program", **{p: b for p, b in game.items() if b is not None}}, b"synthetic plugin", buttonwait=wait,
                                        kickstarts={"kick40068.A1200": b"synthetic ROM fixture"})
                for image in (archive(game), lha_fixture.archive(*(lha_fixture.member(p.encode("latin-1"), b) for p, b in game.items() if b is not None))):
                    with self.subTest(high=high, wait=wait, lha=image[2:3] == b"-"):
                        actual = self.run_bundle(image, wait=wait, kickstarts={"kick40068.A1200": b"synthetic ROM fixture"})
                        self.assertEqual(expected, actual)

    def test_unsafe_ambiguous_conflicting_and_oversized_inputs_publish_nothing(self):
        cases = [{"A.slave": b"a", "B.slave": b"b"}, {"x": b"no slave"}, {"../A.slave": b"x"}, {"A.slave": b"x", "Dir/file": b"x", "dir/other": b"x"}]
        for files in cases:
            self.assertTrue(self.run_bundle(archive(files), success=False))
        oversized = bytearray(archive({"A.slave": b"x"}))
        central = oversized.index(b"PK\x01\x02")
        struct.pack_into("<I", oversized, central + 24, 48 * 1024 * 1024 + 1)
        self.assertIn("larger", self.run_bundle(oversized, success=False))
        self.assertIn("Kickstart", self.run_bundle(archive({"A.slave": b"x"}), kickstarts={"bad/name": b"x"}, success=False))

    def test_default_volume_writer_keeps_its_previous_bytes(self):
        files = {"S/Startup-Sequence": b"Echo hi\n", "Dir/test": b"data"}
        source, out = self.work / "volume.zip", self.work / "volume.hdf"
        source.write_bytes(archive(files))
        result = subprocess.run([DOTNET, str(self.dll), "volume", "--archive", str(source), "--out", str(out)], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        tree = ffs.build_tree(files)
        self.assertEqual(ffs.write_volume(tree, "PUAE6", blocks=ffs.volume_blocks(tree, free_blocks=2048)), out.read_bytes())

    @unittest.skipUnless(os.environ.get("P6_WHDLOAD_LHA") and os.environ.get("P6_FATALMISSION_LHA"), "authorized WHDLoad and game archives are required (no ROM needed)")
    def test_real_provider_and_game_match_t65a_and_wrong_plugin_is_refused(self):
        provider = Path(os.environ["P6_WHDLOAD_LHA"]).read_bytes()
        game = Path(os.environ["P6_FATALMISSION_LHA"]).read_bytes()
        tools = {f.name: f.data for f in lha.read_archive(provider) if not f.is_dir}
        files = {f.name: f.data for f in lha.read_archive(game) if not f.is_dir}
        program, vfs = tools["WHDLoad/C/WHDLoad"], tools["WHDLoad/C/WHDLoad.VFS"]
        expected = bundle.build({"C/WHDLoad": program, **files}, vfs)
        self.assertEqual(expected, self.run_bundle(game, provider=provider))
        for name in ("WHDLoad", "WHDLoad.VFS"):
            bad = {"WHDLoad/C/WHDLoad": program, "WHDLoad/C/WHDLoad.VFS": vfs}
            member = "WHDLoad/C/" + name
            bad[member] = bad[member][:-1] + bytes([bad[member][-1] ^ 1])
            self.assertIn("pinned", self.run_bundle(game, provider=archive(bad), success=False))
