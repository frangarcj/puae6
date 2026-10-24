"""T66d3: real derived Blob_CHD, recording provider; does not test CHD decoding."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DOTNET = shutil.which("dotnet")
BASE = Path("src/BizHawk.Emulation.DiscSystem/DiscFormats/Blobs")


@unittest.skipUnless(DOTNET, "local dotnet SDK is required")
class ChdBlobReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="chd-reads-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        blobs = cls.work / BASE
        blobs.mkdir(parents=True)
        manifest = json.loads((ROOT / "references/machawk.manifest.json").read_text())
        hashes = {f["path"]: f["sha256"] for f in manifest["files"]}
        for name in ("IBlob.cs", "Blob_CHD.cs"):
            relative = (BASE / name).as_posix()
            data = (ROOT / "references/machawk" / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != hashes[relative]:
                raise AssertionError("Pinned DiscSystem source differs: " + relative)
            (blobs / name).write_bytes(data)
        for name in ("Program.cs", "ChdReads.csproj"):
            shutil.copyfile(ROOT / "tests/media/csharp-chd-reads" / name, cls.work / name)
        cls.env = {**os.environ, "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"),
                   "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_ROLL_FORWARD": "Major"}
        cls.dll = cls.work / "bin/Debug/net8.0/ChdReads.dll"
        cls.build()
        cls.before = cls.run_case("cross")
        patch = ROOT / "integration/bizhawk/patches/0003-disc-chd-cross-hunk.patch"
        for options in ([], ["-R", "--dry-run"]):
            subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", *options, "-i", str(patch)],
                           cwd=cls.work, check=True, capture_output=True, timeout=30)
        cls.build()

    @classmethod
    def build(cls):
        result = subprocess.run([DOTNET, "build", str(cls.work / "ChdReads.csproj"), "--nologo", "-m:1", "-v:q",
                                 "-p:RestoreSources=" + str(Path.home() / ".nuget/packages")],
                                env=cls.env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout[-4000:] + result.stderr[-4000:])

    @classmethod
    def run_case(cls, case):
        return subprocess.run([DOTNET, str(cls.dll), case], env=cls.env, capture_output=True, text=True, timeout=30)

    def test_cross_hunk_reproducer_fails_on_pinned_source_and_passes_with_patch(self):
        self.assertNotEqual(0, self.before.returncode)
        self.assertIn("Cross-hunk payload differs", self.before.stderr)
        result = self.run_case("cross")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_exact_boundaries_empty_reads_cache_reverse_seek_and_dispose(self):
        result = self.run_case("cache")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_provider_failure_propagates_and_failed_hunk_is_not_cached(self):
        result = self.run_case("error")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
