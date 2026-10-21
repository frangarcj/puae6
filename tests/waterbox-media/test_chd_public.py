"""Explicit public CD selection and real Waterbox restore; CHD/NRG share ownership checks."""
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))
import build_waterbox as wb
from tests.media import test_cd_source_csharp as source, test_chd_reader as reader, test_cd_chd as chd
from tests.media.test_chd_canonical_csharp import subq
from tests.media.cdtv_boot_image import image as original_amiga_iso
from tests.media.test_nrg_canonical_csharp import nrg_fixture, dao_fixture, dao_metadata, dao_identity, sbi_fixture


def prepare_public_cd(cls, *, guest_required):
    receipt = Path(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT', str(wb.BUILD / 'puae6/puae6-receipt.json')))
    guest = wb.BUILD / 'puae6/release/puae6.wbx'
    main, ext = os.environ.get('P6_CD32_ROM_MAIN'), os.environ.get('P6_CD32_ROM_EXT')
    if not source.DOTNET or not receipt.is_file(): raise RuntimeError('BLOCKED: local dotnet/receipt compile parameter required')
    sha = json.loads(receipt.read_text())['guest']['sha256']
    runtime = None
    if guest_required:
        if not all((main, ext, guest.is_file(), wb.runtime_receipt_path().is_file())):
            raise RuntimeError('BLOCKED: verified guest/runtime and authorized CD32 main/ext required')
        wb.check_guest_matches_trees(receipt)
        if wb.sha256(guest)!=sha: raise AssertionError('guest SHA differs from receipt')
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
        if wb.sha256(Path(runtime['path']))!=runtime['sha256']: raise AssertionError('runtime SHA differs from receipt')
    work,env,output=build_public_cd(sha)
    if guest_required:
        (work / 'dll').mkdir(exist_ok=True); shutil.copy2(guest, work / 'dll/puae6.wbx')
    cls.prepared=(work,env,guest,sha,runtime,main,ext,output)


@functools.lru_cache(maxsize=1)
def build_public_cd(sha):
    spec = importlib.util.spec_from_file_location('public_chd_recipe', ROOT / 'integration/bizhawk/patches/build_tree.py')
    recipe = importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
    recipe.MACHAWK = Path(os.environ.get('P6_MACHAWK_REFERENCE', str(recipe.MACHAWK)))
    recipe.git = functools.partial(recipe.git, cwd=recipe.MACHAWK)
    temporary = tempfile.TemporaryDirectory(prefix='public-chd-wbx-', dir=ROOT / 'build')
    unittest.addModuleCleanup(build_public_cd.cache_clear)
    unittest.addModuleCleanup(temporary.cleanup)
    work = Path(temporary.name); tree = work / 'client-common'
    recipe.check_patches(); recipe.export_tree(tree, recipe.EXPORT_EMUHAWK)
    recipe.apply_patches(tree); recipe.install_sources(tree)
    assembly = recipe.build(sha, tree, 'src/BizHawk.Client.Common/BizHawk.Client.Common.csproj',
                            'src/BizHawk.Client.Common/bin/Debug/BizHawk.Client.Common.dll')
    project = work / 'Cd32Loader.csproj'
    shutil.copy2(ROOT / 'tests/media/csharp-disc/Cd32Loader.csproj', project)
    env = {**os.environ, 'BIZHAWK_HOME': str(work), 'NUGET_PACKAGES': str(ROOT / 'build/bizhawk-core/nuget'),
           'DOTNET_CLI_HOME': str(ROOT / 'build/waterbox/dotnet-home'), 'DOTNET_NOLOGO': '1',
           'DOTNET_ROLL_FORWARD': 'Major', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'}
    for name in ('DYLD_LIBRARY_PATH', 'LD_LIBRARY_PATH'): env.pop(name, None)
    result = subprocess.run([source.DOTNET, 'build', str(project), '-m:1', '-v:q', f'-p:Puae6Root={ROOT}',
                             f'-p:CoreAssemblyDir={assembly.parent}', '-p:DefineConstants=PUBLIC_CHD',
                             f'-p:RestoreSources={source.CACHE}'], env=env, capture_output=True, text=True, timeout=180)
    if result.returncode: raise AssertionError(result.stdout+result.stderr)
    output = work / 'bin/Debug/net8.0'
    library = 'libp6_chd_host.dylib' if sys.platform == 'darwin' else 'libp6_chd_host.so'
    shutil.copy2(assembly.parent / library, output / library)
    return work,env,output



class ChdPublicWaterboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare_public_cd(cls, guest_required=True)

    def scenario(self, htoa=False, direct=False, nrg=False):
        base,env,guest,sha,runtime,main,ext,output=self.prepared
        work=base/(('nrg-' if nrg else '')+('direct' if direct else 'htoa' if htoa else 'legacy')); work.mkdir()
        extension='.nrg' if nrg else '.chd'
        if nrg:
            data,wire,_,_=nrg_fixture(); (work/'disc.nrg').write_bytes(data); (work/'wire.bin').write_bytes(wire)
            (work/'other.nrg').write_bytes(nrg_fixture(payload=1)[0])
            shifted=bytearray(nrg_fixture(lengths=(4,3,5))[0]); directory=int.from_bytes(data[-4:],'big')
            shifted[:directory]=data[:directory] # Identical raw payload; metadata moves the first two track boundaries.
            (work/'shifted.nrg').write_bytes(shifted)
        elif htoa:
            fixture,wire=chd.htoa_fixture(work,'disc',200)
            (work/'wire.bin').write_bytes(wire)
            chd.htoa_fixture(work,'other',200,payload=1)
            chd.htoa_fixture(work,'shifted',201)
        else:
            pattern = reader.dependency.load('public_chd_pattern', ROOT / 'tests/fixtures/chd/generate.py')
            fixture = ROOT / 'tests/fixtures/chd/cdzl.chd'
            hashes = json.loads((fixture.parent / 'sha256.json').read_text())
            self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), hashes[fixture.name])
            shutil.copy2(fixture, work / 'disc.chd')
            audio = pattern.audio()
            (work / 'wire.bin').write_bytes(b''.join(audio[n*2352:(n+1)*2352] + subq(1, 1, n, 0, 0) for n in range(40)))
            # A second valid v5 file with recomputed map CRC and different audio, not a corrupt container.
            changed = bytearray(pattern.expected()); changed[0] ^= 1
            metadata = b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:40 PREGAP:0 PGTYPE:AUDIO PGSUB:NONE POSTGAP:0'
            (work / 'other.chd').write_bytes(reader.metadata_chain(reader.cd_map([(4, bytes(changed))], codec=b'cdzl', sector=changed),
                                                                  [metadata], tag=0x43485432))
        for name in ('disc','other',*(['shifted'] if htoa or nrg else [])):
            (work/(name+'.xml')).write_text(f'<BizHawk-XMLGame System="Amiga" Name="legal"><LoadAssets>'
                                           f'<LoadAsset FileName="{name}{extension}"/></LoadAssets></BizHawk-XMLGame>')
        for mode in (('nrgpublicsave','nrgpublicload') if nrg else ('chdpublicsave','chdpublicload')):
            xml=work/('disc'+extension if direct else 'disc.xml')
            if (htoa or nrg) and mode.endswith('load'):
                # New absolute paths, same immutable inputs in the second process.
                renamed=work/'renamed'; renamed.mkdir()
                for name in ('disc','other','shifted'):
                    shutil.copy2(work/(name+extension),renamed/(name+extension))
                    shutil.copy2(work/(name+'.xml'),renamed/(name+'.xml'))
                if nrg: (renamed/'disc.nrg').write_bytes(nrg_fixture(wide=True)[0]) # Same materialization, new ETN width.
                xml=renamed/('disc'+extension if direct else 'disc.xml')
            result=subprocess.run([source.DOTNET,str(output/'Cd32Loader.dll'),str(work),str(xml),
                                   runtime['path'],str(guest),sha,mode,main,ext,*(['0,2,3,152,153,156,157,306,307,311'] if nrg else ['200'] if htoa else [])],env=env,
                                  capture_output=True,text=True,timeout=240)
            self.assertEqual(result.returncode,0,result.stdout[-5000:]+result.stderr[-5000:])
            self.assertIn(f'Public {extension[1:].upper()} Waterbox reads/SaveRAM/cold owner/new process/full future/FD close PASS',result.stdout)

    def test_xml_chd_reads_saveram_and_cold_restore(self):
        self.scenario()

    def test_xml_chd_htoa_saveram_and_cold_restore(self):
        self.scenario(htoa=True)

    def test_direct_chd_preference_saveram_and_cold_restore(self):
        self.scenario(direct=True)

    def test_xml_nrg_canonical_saveram_and_cold_restore(self):
        self.scenario(nrg=True)

    def test_direct_nrg_canonical_saveram_and_cold_restore(self):
        self.scenario(nrg=True,direct=True)


class NrgPublicHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare_public_cd(cls, guest_required=False)

    def test_xml_and_direct_nrg_explicit_selection_and_ownership(self):
        work,env,_,_,_,_,_,output=self.prepared
        identified_iso=work/'identified-amiga.iso'; identified_iso.write_bytes(original_amiga_iso(bytes(22152)))
        for wide in (False,True):
            data,wire,_,expected=nrg_fixture(wide=wide)
            image=work/('etn2.NRG' if wide else 'etnf.nrg'); image.write_bytes(data)
            xml=image.with_suffix('.xml'); xml.write_text(f'<BizHawk-XMLGame System="Amiga" Name="legal"><LoadAssets><LoadAsset FileName="{image.name}"/></LoadAssets></BizHawk-XMLGame>')
            result=subprocess.run([source.DOTNET,str(output/'Cd32Loader.dll'),'host-nrg',str(xml),str(identified_iso)],env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn(expected[0],result.stdout.splitlines())
            self.assertEqual(Path(str(xml)+'.actual').read_bytes(),wire)


class NrgMultiPublicWaterboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare_public_cd(cls, guest_required=True)

    def scenario(self,kind):
        base,env,guest,sha,runtime,main,ext,output=self.prepared
        pattern=reader.dependency.load('multi_public_chd_pattern',ROOT/'tests/fixtures/chd/generate.py')
        audio=pattern.audio(); chdwire=b''.join(audio[n*2352:(n+1)*2352]+subq(1,1,n,0,0) for n in range(40))
        isowire=b''.join(chd.mode1(bytes(2048),n)+subq(1,1,n,0,4) for n in range(512))
        isoid=hashlib.sha256(b'P6CD2448\0'+struct.pack('<3I5i',1,512,1,0,0,512,1,4)+isowire).hexdigest().upper()
        chdid=hashlib.sha256(b'P6CD2448\0'+struct.pack('<3I12I',2,40,1,0,0,40,0,0,0,0,0,0,3,0,0)+chdwire).hexdigest().upper()
        lbas=[*range(-150,11),*range(30,39)]; entries=((6,2,b'\x98\x76\x54'),(32,3,b'\x12\x34\x56'))
        work=base/('multi-'+kind); work.mkdir(); path,raw,rows=dao_fixture(work,'disc')
        wire=sbi_fixture(path.with_suffix('.sbi'),raw,entries,lbas)
        ids=[dao_identity(wire,rows),isoid,chdid]
        catalogid=hashlib.sha256(b'PUAE6 CD catalog/v1'+struct.pack('<I',3)+b''.join(bytes.fromhex(i) for i in ids)+struct.pack('<5I',4,0,1,2,0)).hexdigest().upper()
        (work/'ids.txt').write_text('\n'.join(ids+[catalogid])); (work/'wire.bin').write_bytes(wire)
        (work/'tracks.bin').write_bytes(b''.join(struct.pack('<12I',*r) for r in dao_metadata(rows)[1]))
        (work/'iso-wire.bin').write_bytes(isowire); (work/'chd-wire.bin').write_bytes(chdwire)
        (work/'legacy.iso').write_bytes(bytes(512*2048)); shutil.copy2(ROOT/'tests/fixtures/chd/cdzl.chd',work/'audio.chd')
        shutil.copy2(path,work/'alias.nrg'); shutil.copy2(path.with_suffix('.sbi'),work/'alias.sbi')
        names=['disc.nrg','legacy.iso','audio.chd','alias.nrg'] if kind=='catalog' else ['disc.nrg']
        def xml(directory,assets):
            (directory/'disc.xml').write_text('<BizHawk-XMLGame System="Amiga" Name="multi"><LoadAssets>'+''.join(f'<LoadAsset FileName="{name}"/>' for name in assets)+'</LoadAssets></BizHawk-XMLGame>')
        xml(work,names)
        for change in ('other','shifted','sbi',*(['order','map'] if kind=='catalog' else [])):
            other=work/change; other.mkdir()
            for name in (*names,'disc.sbi',*(['alias.sbi'] if kind=='catalog' else [])): shutil.copy2(work/name,other/name)
            data=bytearray(path.read_bytes())
            if change=='other': data[int(rows[-1].split()[3])+17]^=1 # Stored program in session2.
            if change=='shifted':
                directory=int.from_bytes(data[-8:],'big'); cue=data.index(b'CUEX',data.index(b'CUEX',directory)+4)
                struct.pack_into('>i',data,cue+12,21) # Valid CUE0, same readable wire; only metadata identity changes.
            if change in ('other','shifted'):
                (other/'disc.nrg').write_bytes(data)
                if kind=='catalog': (other/'alias.nrg').write_bytes(data)
            if change=='sbi':
                sbi_fixture(other/'disc.sbi',raw,((6,2,b'abc'),entries[1]),lbas)
                if kind=='catalog': shutil.copy2(other/'disc.sbi',other/'alias.sbi')
            assets=[names[0],names[2],names[1],names[3]] if change=='order' else names[:3]+[names[1]] if change=='map' else names
            xml(other,assets)
        renamed=work/'renamed'; renamed.mkdir()
        for name in (*names,'disc.sbi',*(['alias.sbi'] if kind=='catalog' else [])): shutil.copy2(work/name,renamed/name)
        xml(renamed,names)
        for mode,directory in (('save',work),('load',renamed)):
            content=directory/('disc.nrg' if kind=='direct' else 'disc.xml')
            result=subprocess.run([source.DOTNET,str(output/'Cd32Loader.dll'),str(work),str(content),runtime['path'],str(guest),sha,
                                   'nrgmultipublic'+mode,main,ext,kind],env=env,capture_output=True,text=True,timeout=240)
            self.assertEqual(result.returncode,0,result.stdout[-6000:]+result.stderr[-6000:])
            self.assertIn('Public NRG V3 multisession/mixed catalog/SBI/cold process/full future/FD close PASS',result.stdout)

    def test_xml_multisession_cold_restore(self):
        self.scenario('xml')

    def test_direct_multisession_cold_restore(self):
        self.scenario('direct')

    def test_mixed_catalog_cold_restore(self):
        self.scenario('catalog')
