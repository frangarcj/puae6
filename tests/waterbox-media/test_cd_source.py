"""Private CD transport on real Waterbox; this does not boot a CD32."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_waterbox as wb
GUEST = wb.BUILD / "puae6/release/puae6.wbx"
RECEIPT = wb.BUILD / "puae6/puae6-receipt.json"

class CdLayoutTests(unittest.TestCase):
    def test_private_c_and_managed_layout_without_changing_abi1(self):
        subprocess.run(["clang++", "-std=c++17", "-x", "c++", "-fsyntax-only", "-I" + str(ROOT / "adapters/waterbox"), "-"],
                       input='#include "p6w_cd.h"\n#include <stddef.h>\nstatic_assert(sizeof(p6w_cd_info)==2432 && offsetof(p6w_cd_info,bytes)==40 && offsetof(p6w_cd_info,tracks)==56);', text=True, check=True)
        env = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"), "DOTNET_ROLL_FORWARD": "Major", "DOTNET_NOLOGO": "1"}
        result = subprocess.run(["dotnet", "build", str(ROOT / "tests/ffi/Ffi.csproj"), "-m:1", "-v:q", f"-p:RestoreSources={Path.home() / '.nuget/packages'}"], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        subprocess.run(["dotnet", str(ROOT / "build/ffi/bin/Debug/net8.0/Ffi.dll"), "cdlayout"], env=env, check=True, timeout=30)

@unittest.skipUnless(GUEST.is_file() and wb.runtime_receipt_path().is_file() and shutil.which("dotnet"), "built guest/runtime and dotnet required")
class CdTransportTests(unittest.TestCase):
    modes = ("save", "load")
    def test_iso_cue_callback_abi_restore_and_rebind_across_processes(self):
        with tempfile.TemporaryDirectory(prefix="cd-wbx-", dir=ROOT / "build") as tmp:
            work = Path(tmp)
            for entry in json.loads((ROOT / "references/machawk.manifest.json").read_text())["files"]:
                name = entry["path"]
                if name.endswith(".cs") and name.startswith(("src/BizHawk.Emulation.DiscSystem/", "ExternalProjects/iso-parser/")):
                    data = (ROOT / "references/machawk" / name).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                    target = work / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            for name in ("0002-disc-blob-exact-reads.patch", "0003-disc-chd-cross-hunk.patch"):
                subprocess.run(["patch", "-p1", "-i", str(ROOT / "integration/bizhawk/patches" / name)], cwd=work, check=True, capture_output=True)
            project = work / "DiscSource.csproj"; shutil.copyfile(ROOT / "tests/media/csharp-disc/DiscSource.csproj", project)
            env = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"), "DOTNET_NOLOGO": "1", "DOTNET_ROLL_FORWARD": "Major", "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home")}
            result = subprocess.run(["dotnet", "build", str(project), "-m:1", "-v:q", f"-p:Puae6Root={ROOT}", f"-p:DiscTree={work}", f"-p:RestoreSources={Path.home() / '.nuget/packages'}"], env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runtime = json.loads(wb.runtime_receipt_path().read_text())["runtime_dylib"]["path"]
            sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
            for name in ("disc.iso", "stored.cue"):
                for mode in self.modes:
                    result = subprocess.run(["dotnet", str(work / "bin/Debug/net8.0/DiscSource.dll"), str(work), str(work / name), runtime, str(GUEST), sha, mode], env=env, capture_output=True, text=True, timeout=120)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
