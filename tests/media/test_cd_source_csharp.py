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


def build_disc_source(work, *, legacy_xa=False, legacy_first_pregap=False):
    manifest = json.loads((ROOT / "references/machawk.manifest.json").read_text())
    for entry in manifest["files"]:
        relative = entry["path"]
        if not relative.endswith(".cs") or not relative.startswith(("src/BizHawk.Emulation.DiscSystem/", "ExternalProjects/iso-parser/")):
            continue
        data = (ROOT / "references/machawk" / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # 0014 extends the official DiscMountJob after0011; the other0011 paths are outside this source-only harness.
    public_patch=(ROOT/'integration/bizhawk/patches/0011-public-puae6-chd.patch').read_bytes()
    block=next(b for b in public_patch.split(b'diff --git ')[1:] if b.split(b'\n',1)[0].endswith(b'b/src/BizHawk.Emulation.DiscSystem/DiscMountJob.cs'))
    subprocess.run(['patch','-p1','--fuzz=0','--no-backup-if-mismatch'],input=b'diff --git '+block,cwd=work,check=True,capture_output=True,timeout=30)
    subprocess.run(['patch','-p1','--fuzz=0','-R','--dry-run'],input=b'diff --git '+block,cwd=work,check=True,capture_output=True,timeout=30)
    for name in ("0002-disc-blob-exact-reads.patch", "0003-disc-chd-cross-hunk.patch", "0008-disc-chd-regions-and-xa.patch", "0010-disc-chd-host.patch", "0012-chd-first-track-pregap.patch", "0014-canonical-nrg-tao.patch", "0018-canonical-nrg-dao-metadata.patch", "0019-canonical-nrg-dao-sessions.patch", "0020-provider-sbi-overlay.patch", "0023-canonical-nrg-global-etnf.patch"):
        if legacy_first_pregap and name == "0012-chd-first-track-pregap.patch": continue
        subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", str(ROOT / "integration/bizhawk/patches" / name)],
                       cwd=work, check=True, capture_output=True, timeout=30)
    if legacy_xa:
        # Exact pre-fix XA baseline, retaining the new typed-loader seam.
        synth=work/'src/BizHawk.Emulation.DiscSystem/Internal/SynthUtils.cs'
        data=synth.read_bytes(); fixed=b'buffer8[offset + 6] = 0x20; // XA repeats four bytes: submode at2 and6.'
        assert data.count(fixed)==1
        form1=(b'var mode = buf2352[offset + 15];\r\n'
               b'\t\t\tbuf2352[offset + 15] = 0; // ECM zeroSectorAddress clears only MSF, not mode.\r\n'
               b'\t\t\tECM.ECC_Populate(buf2352, offset, buf2352, offset, true);\r\n'
               b'\t\t\tbuf2352[offset + 15] = mode;')
        assert data.count(form1)==1
        synth.write_bytes(data.replace(fixed,b'buffer8[offset + 5] = 0x20;').replace(form1,b'ECM.ECC_Populate(buf2352, offset, buf2352, offset, false);'))
    project = work / "DiscSource.csproj"
    shutil.copyfile(ROOT / "tests/media/csharp-disc/DiscSource.csproj", project)
    env = {**os.environ, "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"),
           "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "DOTNET_NOLOGO": "1",
           "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_ROLL_FORWARD": "Major"}
    result = subprocess.run([DOTNET, "build", str(project), "--nologo", "-m:1", "-v:q", f"-p:Puae6Root={ROOT}",
                             f"-p:DiscTree={work}", f"-p:RestoreSources={CACHE}"], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=180)
    if result.returncode: raise AssertionError(result.stdout[-7000:] + result.stderr[-7000:])
    return work / "bin/Debug/net8.0/DiscSource.dll", env


@unittest.skipUnless(DOTNET and CACHE.is_dir(), "dotnet and the existing local NuGet cache are required")
class DiscSourceCSharpTests(unittest.TestCase):
    def test_iso_cue_offset_identity_and_transactional_rebind(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="disc-source-", dir=ROOT / "build") as directory:
            work=Path(directory); dll,env=build_disc_source(work)
            result=subprocess.run([DOTNET,str(dll),str(work)],env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
