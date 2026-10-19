"""T66d2: real pinned DiscSystem parsers and sector synthesizers, no guest."""
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


@unittest.skipUnless(DOTNET and CACHE.is_dir(), "dotnet and the existing local NuGet cache are required")
class DiscSourceCSharpTests(unittest.TestCase):
    def test_iso_cue_offset_identity_and_transactional_rebind(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="disc-source-", dir=ROOT / "build") as directory:
            work = Path(directory)
            manifest = json.loads((ROOT / "references/machawk.manifest.json").read_text())
            for entry in manifest["files"]:
                relative = entry["path"]
                if not relative.endswith(".cs") or not relative.startswith(("src/BizHawk.Emulation.DiscSystem/", "ExternalProjects/iso-parser/")):
                    continue
                data = (ROOT / "references/machawk" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                target = work / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            for name in ("0002-disc-blob-exact-reads.patch", "0003-disc-chd-cross-hunk.patch"):
                subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", str(ROOT / "integration/bizhawk/patches" / name)],
                               cwd=work, check=True, capture_output=True, timeout=30)
            project = work / "DiscSource.csproj"
            shutil.copyfile(ROOT / "tests/media/csharp-disc/DiscSource.csproj", project)
            env = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"),
                   "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "DOTNET_NOLOGO": "1",
                   "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_ROLL_FORWARD": "Major"}
            result = subprocess.run([DOTNET, "build", str(project), "--nologo", "-m:1", "-v:q", f"-p:Puae6Root={ROOT}",
                                     f"-p:DiscTree={work}", f"-p:RestoreSources={CACHE}"], env=env, cwd=ROOT,
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout[-7000:] + result.stderr[-7000:])
            result = subprocess.run([DOTNET, str(work / "bin/Debug/net8.0/DiscSource.dll"), str(work)],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
