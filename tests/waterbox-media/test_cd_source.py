"""Private CD transport on real Waterbox; this does not boot a CD32."""
import json
import binascii
import hashlib
import struct
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
        downloads = Path.home() / 'Downloads'
        main = Path(os.environ.get('P6_CD32_ROM_MAIN', downloads / 'kick40060.CD32'))
        ext = Path(os.environ.get('P6_CD32_ROM_EXT', downloads / 'kick40060.CD32.ext'))
        if not main.is_file() or not ext.is_file():
            self.skipTest('BLOCKED: authorized CD32 main/ext ROMs required')
        wb.check_guest_matches_trees(RECEIPT)
        self.names = (os.environ.get('P6_CD_CUE_FILE', 'device.iso'),)
        self.extra_args = (str(main), str(ext))

class Cd32CatalogWaterboxTests(Cd32WaterboxTests):
    modes = ("cd32catalogsave", "cd32catalogload")
    def setUp(self):
        super().setUp()
        self.names = ("device.iso",) # Two synthetic payloads, never a game campaign.

class Cd32RegionalWaterboxTests(Cd32CatalogWaterboxTests):
    modes = ("cd32regionalsave", "cd32regionalload", "cd32regionalcatalogsave", "cd32regionalcatalogload")

@unittest.skipUnless(GUEST.is_file() and wb.runtime_receipt_path().is_file() and shutil.which("dotnet"), "built guest/runtime and dotnet required")
class Cd32SessionWaterboxTests(unittest.TestCase):
    setUp=Cd32WaterboxTests.setUp

    def test_private_v3_cold_snapshot_and_rebind(self):
        with tempfile.TemporaryDirectory(prefix='cd-v3-',dir=ROOT/'build') as temporary:
            work=Path(temporary); assembly,env=build_disc_source(work)
            runtime=wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']['path']
            sha=json.loads(RECEIPT.read_text())['guest']['sha256']; path=work/'sessions'
            def fixture(variant='',metadata=False,payload=False):
                info=bytearray(6792); wire=bytearray(39*2448)
                struct.pack_into('<II32sQII',info,0,3,len(info),bytes(32),len(wire),3,39)
                def bcd(n): return n//10*16+n%10
                def clock(n): return bytes(map(bcd,(n//4500,n//75%60,n%75)))
                for n,(start,one,end,mode) in enumerate(((0,0,4,0),(4,6,11,1),(30,32,39,2))):
                    control=0 if mode==0 else 4; pre=start<one
                    struct.pack_into('<12I',info,56+n*48,start,one,end,mode,control,0,1,0,0,7 if pre else 3,mode if pre else 0,control if pre else 0)
                    for lba in range(start,end):
                        raw=bytearray((bytes((n*53+lba+k)&255 for k in range(256))*10)[:2352])
                        if mode: raw[:16]=b'\0'+b'\xff'*10+b'\0'+clock(lba+150)+bytes((mode,))
                        if mode==2: raw[16:24]=bytes((1,2,0,0))*2
                        q=bytes((control*16+1,n+1,int(lba>=one)))+clock(abs(lba-one))+b'\0'+clock(lba+150)
                        sub=(b'\xff' if lba<one else b'\0')*12+q+(binascii.crc_hqx(q,0)^0xffff).to_bytes(2,'big')+bytes(72)
                        wire[lba*2448:(lba+1)*2448]=raw+sub
                struct.pack_into('<I',info,4808,2)
                struct.pack_into('<IIIiI',info,4812,1,2,0,-150,11)
                struct.pack_into('<IIIiI',info,4832,3,3,0x10 if metadata else 0x20,20,39)
                if payload: wire[32*2448+24]^=1
                info[8:40]=hashlib.sha256(b'WBSESS3'+info+wire[:11*2448]+wire[30*2448:]).digest()
                Path(str(path)+variant+'.v3').write_bytes(info); Path(str(path)+variant+'.wire').write_bytes(wire)
            fixture(); fixture('-meta',metadata=True); fixture('-data',payload=True)
            for shape in ('','catalog'):
                pids=[]
                for action in ('save','load'):
                    command=['dotnet',str(assembly),str(work),str(path),runtime,str(GUEST),sha,'cd32sessions'+shape+action,*self.extra_args]
                    with subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
                        pids.append(process.pid)
                        try: stdout,stderr=process.communicate(timeout=180)
                        except subprocess.TimeoutExpired:
                            process.kill(); stdout,stderr=process.communicate(); self.fail(stdout[-2000:]+stderr[-4000:])
                        self.assertEqual(0,process.returncode,stdout[-3000:]+stderr[-4000:])
                        self.assertIn('Private V3 sessions single/catalog cold snapshot/full future/identity/rebind PASS',stdout)
                self.assertNotEqual(*pids)

@unittest.skipUnless(GUEST.is_file() and wb.runtime_receipt_path().is_file() and shutil.which("dotnet"), "built guest/runtime and dotnet required")
class IdeMixedWaterboxTests(unittest.TestCase):
    def test_catalog_with_adf_two_hdfs_and_prepared_volume_restores_everywhere(self):
        from tests.hdf import hdf_image
        from tests.media import ide_cd_rom
        with tempfile.TemporaryDirectory(prefix="ide-mixed-wbx-", dir=ROOT / "build") as tmp:
            work = Path(tmp)
            assembly, env = build_disc_source(work)
            runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())["runtime_dylib"]["path"]
            sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
            files = {
                "ide-mixed.rom": ide_cd_rom.state_rom(),
                "alongside.adf": bytes(901120),
                "first.hdf": hdf_image.rdb_image(cylinders=2),
                "second.hdf": bytes(b"DOS\0") + bytes(12 * 512 - 4),
                # A frontend-prepared volume reaches p6w as an HDF image. Its
                # own archive conversion is already covered by T64b.
                "volume.hdf": bytes(b"DOS\1") + bytes(16 * 512 - 4),
            }
            for name, data in files.items():
                (work / name).write_bytes(data)
            before = {name: (work / name).read_bytes() for name in files}
            base = ["dotnet", str(assembly), str(work), str(work / "ide-mixed-a.iso"), runtime, str(GUEST), sha]
            extra = [str(work / name) for name in ("ide-mixed.rom", "alongside.adf", "first.hdf", "second.hdf", "volume.hdf")]
            pids = []
            for mode in ("idemixedsave", "idemixedload"):
                with subprocess.Popen([*base, mode, *extra], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                    pids.append(process.pid)
                    stdout, stderr = process.communicate(timeout=300)
                    self.assertEqual(process.returncode, 0, stdout[-4000:] + stderr[-6000:])
                    self.assertIn("Waterbox IDE mixed CD+ADF+2HDF+volume local/new-sandbox/new-process restore PASS", stdout)
            self.assertNotEqual(*pids)
            self.assertEqual(before, {name: (work / name).read_bytes() for name in files})

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
            guest_dir = work / 'bin/Debug/net8.0/dll'
            guest_dir.mkdir(exist_ok=True)
            shutil.copy2(GUEST, guest_dir / 'puae6.wbx')
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

class Cd32FmvCoreTests(Cd32PublicCoreTests):
    public_modes = ('fmvsave', 'fmvload')
    def setUp(self):
        downloads = Path.home() / 'Downloads'
        main = Path(os.environ.get('P6_CD32_ROM_MAIN', downloads / 'kick40060.CD32'))
        ext = Path(os.environ.get('P6_CD32_ROM_EXT', downloads / 'kick40060.CD32.ext'))
        fmv = Path(os.environ.get('P6_CD32_FMV_ROM', downloads / 'FMV.CD32-2.rom'))
        content = Path(os.environ.get('P6_CANNON_FODDER_CUE',
                                      downloads / 'Cannon Fodder (Europe)' / 'Cannon Fodder (Europe).cue'))
        if not all(path.is_file() for path in (main, ext, fmv, content)):
            self.skipTest('BLOCKED: authorized CD32/FMV ROMs and P6_CANNON_FODDER_CUE required')
        wb.check_guest_matches_trees(RECEIPT)
        self.names = (str(content),)
        self.extra_args = (str(main), str(ext), str(fmv))
