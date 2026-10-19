"""T66d1: patch the fixed DiscSystem copy and exercise actual BIN/WAV files."""
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
CACHE = Path.home() / ".nuget/packages"
BASE = Path("src/BizHawk.Emulation.DiscSystem/DiscFormats/Blobs")
PATCH = ROOT / "integration/bizhawk/patches/0002-disc-blob-exact-reads.patch"


@unittest.skipUnless(DOTNET and CACHE.is_dir(), "dotnet and the local NuGet cache are required")
class DiscBlobReadTests(unittest.TestCase):
    def test_real_truncated_files_throw_but_declared_padding_survives(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="disc-reads-", dir=ROOT / "build") as directory:
            work = Path(directory)
            blobs = work / BASE
            blobs.mkdir(parents=True)
            manifest = json.loads((ROOT / "references/machawk.manifest.json").read_text())
            hashes = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
            for name in ("IBlob.cs", "Blob_RawFile.cs", "Blob_WaveFile.cs", "Blob_ZeroPadAdapter.cs", "RiffMaster.cs"):
                relative = (BASE / name).as_posix()
                data = (ROOT / "references/machawk" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), hashes[relative])
                (blobs / name).write_bytes(data)
            # Same patch application as build_tree; the reference snapshot remains untouched.
            for options in ([], ["-R", "--dry-run"]):
                subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", *options, "-i", str(PATCH)],
                               cwd=work, check=True, capture_output=True, timeout=30)
            project = work / "DiscReads.csproj"
            shutil.copyfile(ROOT / "tests/media/csharp-disc-reads/DiscReads.csproj", project)
            env = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"),
                   "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "DOTNET_NOLOGO": "1",
                   "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_ROLL_FORWARD": "Major"}
            result = subprocess.run([DOTNET, "build", str(project), "--nologo", "-m:1", "-v:q",
                                     f"-p:Puae6Root={ROOT}", f"-p:DiscBlobTree={blobs}", f"-p:RestoreSources={CACHE}"],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-6000:])
            result = subprocess.run([DOTNET, str(work / "bin/Debug/net8.0/DiscReads.dll"), str(work)],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
