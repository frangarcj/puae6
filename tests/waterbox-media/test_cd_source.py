"""Private CD transport on real Waterbox; this does not boot a CD32."""
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
sys.path.insert(0, str(ROOT))
import build_waterbox as wb
from tests.media.test_cd_source_csharp import build_disc_source
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
    names = ("disc.iso", "stored.cue")
    extra_args = ()
    def test_iso_cue_callback_abi_restore_and_rebind_across_processes(self):
        with tempfile.TemporaryDirectory(prefix="cd-wbx-", dir=ROOT / "build") as tmp:
            work = Path(tmp)
            assembly, env = build_disc_source(work)
            runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())["runtime_dylib"]["path"]
            sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
            for name in self.names:
                for mode in self.modes:
                    result = subprocess.run(["dotnet", str(assembly), str(work), str(work / name), runtime, str(GUEST), sha, mode, *self.extra_args], env=env, capture_output=True, text=True, timeout=180)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

class Cd32WaterboxTests(CdTransportTests):
    modes = ("cd32save", "cd32load")
    def setUp(self):
        main, ext = os.environ.get('P6_CD32_ROM_MAIN'), os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext:
            self.skipTest('BLOCKED: authorized CD32 main/ext ROMs required')
        wb.check_guest_matches_trees(RECEIPT)
        self.names = (os.environ.get('P6_CD_CUE_FILE', 'device.iso'),)
        self.extra_args = (main, ext)

class Cd32CatalogWaterboxTests(Cd32WaterboxTests):
    modes = ("cd32catalogsave", "cd32catalogload")
    def setUp(self):
        super().setUp()
        self.names = ("device.iso",) # Two synthetic payloads, never a game campaign.

class Cd32RegionalWaterboxTests(Cd32CatalogWaterboxTests):
    modes = ("cd32regionalsave", "cd32regionalload", "cd32regionalcatalogsave", "cd32regionalcatalogload")

class Cd32PublicCoreTests(Cd32WaterboxTests):
    public_modes = ('publicsave', 'publicload')
    def test_iso_cue_callback_abi_restore_and_rebind_across_processes(self):
        sys.path.insert(0, str(ROOT / 'integration/bizhawk/patches'))
        import build_tree
        build_tree.check_patches()
        if build_tree.export_tree():
            build_tree.apply_patches()
            build_tree.install_sources()
        sha = json.loads(RECEIPT.read_text())['guest']['sha256']
        assembly = build_tree.build(sha)
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']['path']
        with tempfile.TemporaryDirectory(prefix='cd-core-', dir=ROOT / 'build') as tmp:
            work = Path(tmp)
            project = work / 'Cd32Loader.csproj'
            shutil.copyfile(ROOT / 'tests/media/csharp-disc/Cd32Loader.csproj', project)
            env = {**os.environ, 'NUGET_PACKAGES': str(ROOT / 'build/bizhawk-core/nuget'), 'DOTNET_NOLOGO': '1', 'DOTNET_ROLL_FORWARD': 'Major'}
            result = subprocess.run(['dotnet', 'build', str(project), '-m:1', '-v:q', f'-p:Puae6Root={ROOT}',
                                     f'-p:CoreAssemblyDir={assembly.parent}', f'-p:RestoreSources={Path.home() / ".nuget/packages"}'],
                                    env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = self.names[0]
            if path == 'device.iso':
                path = str(work / path); Path(path).write_bytes(bytes(512 * 2048))
            for mode in self.public_modes:
                result = subprocess.run(['dotnet', str(work / 'bin/Debug/net8.0/Cd32Loader.dll'), str(work), path,
                                         runtime, str(GUEST), sha, mode, *self.extra_args],
                                        env=env, capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
                self.assertIn('Public BizHawk CD32', result.stdout)

class Cd32CatalogCoreTests(Cd32PublicCoreTests):
    public_modes = ('catalogsave', 'catalogload')
    def setUp(self):
        super().setUp()
        self.names = ('device.iso',)

class Cd32FastCoreTests(Cd32PublicCoreTests):
    public_modes = ('frsave', 'frload')

class Cd32GameCoreTests(Cd32PublicCoreTests):
    public_modes = ('gamesave', 'gameload')
    def setUp(self):
        super().setUp()
        content = os.environ.get('P6_CD32_GAME_CUE')
        if not content:
            self.skipTest('BLOCKED: authorized Brian the Lion P6_CD32_GAME_CUE required')
        self.names = (content,)
