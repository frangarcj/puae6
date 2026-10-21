"""Compressed CHD -> host ABI -> real DiscSystem/regions/source; no guest."""
import ctypes
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from tests.media import test_cd_source_csharp as source, test_chd_reader as reader, test_cd_chd as chd, test_cd_nrg as nrg
from tests.media.test_chd_canonical_csharp import subq


class ChdHostCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not source.DOTNET or not source.CACHE.is_dir(): raise RuntimeError('BLOCKED: local dotnet/cache required')
        cls.temp=tempfile.TemporaryDirectory(prefix='chd-host-',dir=source.ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); cls.dll,cls.env=source.build_disc_source(cls.work)
        for name in ('DYLD_LIBRARY_PATH','LD_LIBRARY_PATH'): cls.env.pop(name,None)
        spec=importlib.util.spec_from_file_location('chd_host_recipe',source.ROOT/'integration/bizhawk/patches/build_tree.py')
        recipe=importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe); cls.recipe=recipe
        cls.library=recipe.build_chd_host(cls.dll.parent)
        cls.pattern=reader.dependency.load('chd_host_pattern',source.ROOT/'tests/fixtures/chd/generate.py')

    @classmethod
    def public_target(cls):
        if hasattr(cls,'public_assembly'): return cls.public_assembly
        recipe=cls.recipe; recipe.MACHAWK=Path(os.environ.get('P6_MACHAWK_REFERENCE',str(recipe.MACHAWK)))
        recipe.git=functools.partial(recipe.git,cwd=recipe.MACHAWK)
        receipt=Path(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT',str(source.ROOT/'build/waterbox/puae6/puae6-receipt.json')))
        sha=json.loads(receipt.read_text())['guest']['sha256'] # Compile parameter, never a guest execution claim.
        tree=cls.work/'real-client-common'; recipe.check_patches()
        recipe.export_tree(tree,recipe.EXPORT_EMUHAWK); recipe.apply_patches(tree); recipe.install_sources(tree)
        cls.public_assembly=recipe.build(sha,tree,'src/BizHawk.Client.Common/BizHawk.Client.Common.csproj',
            'src/BizHawk.Client.Common/bin/Debug/BizHawk.Client.Common.dll')
        (tree/'build.log').unlink(); return cls.public_assembly

    def test_real_client_common_target_and_analyzers(self):
        self.assertTrue(self.public_target().is_file())

    def public_runner(self):
        if hasattr(type(self),'public_output'): return self.public_output
        assembly=self.public_target(); work=self.work/'public-host'; work.mkdir()
        project=work/'Cd32Loader.csproj'; shutil.copy2(source.ROOT/'tests/media/csharp-disc/Cd32Loader.csproj',project)
        result=subprocess.run([source.DOTNET,'build',str(project),'-m:1','-v:q',f'-p:Puae6Root={source.ROOT}',
            f'-p:CoreAssemblyDir={assembly.parent}','-p:DefineConstants=PUBLIC_CHD',f'-p:RestoreSources={source.CACHE}'],
            env=self.env,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        output=work/'bin/Debug/net8.0'; shutil.copy2(self.library,output/self.library.name)
        type(self).public_output=output
        return output

    def test_xml_amiga_public_selector_xa_and_parameters(self):
        output=self.public_runner(); work=output.parents[2]
        # One independent Mode2 synthetic gap checks the explicit XA policy; four legal codec fixtures check routing.
        units=[(bytes((i+n*11)&255 for i in range(2048))+bytes(400)) for n in range(3)]+[bytes(2448)]
        units += [bytes((i+n*19)&255 for i in range(2336))+bytes(112) for n in range(3)]+[bytes(2448)]
        metadata=[b'TRACK:1 TYPE:MODE1 SUBTYPE:NONE FRAMES:3 PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0',
                  b'TRACK:2 TYPE:MODE2 SUBTYPE:NONE FRAMES:3 PREGAP:2 PGTYPE:MODE2 PGSUB:NONE POSTGAP:0']
        gap=work/'gap.chd'; gap.write_bytes(reader.metadata_chain(reader.cd_map([(4,b''.join(units))],sector=bytes(19584)),metadata,tag=[0x43485432]*2))
        expected=b''.join(chd.mode1(units[n][:2048],n)+subq(1,1,n,0,4) for n in range(3))
        expected+=b''.join(nrg.mode2_header(n)+bytes(2336)+subq(2,0,n,5,4) for n in (3,4))
        expected+=b''.join(nrg.mode2_header(n)+units[n-1][:2336]+subq(2,1,n,5,4) for n in (5,6,7))
        fixtures=[(gap,expected)]
        audio=self.pattern.audio(); wire=b''.join(audio[n*2352:(n+1)*2352]+subq(1,1,n,0,0) for n in range(40))
        for codec in self.pattern.CODECS:
            image=work/(codec+'.chd'); shutil.copy2(source.ROOT/'tests/fixtures/chd'/(codec+'.chd'),image); fixtures.append((image,wire))
        for image,wanted in fixtures:
            xml=image.with_suffix('.xml'); xml.write_text(f'<BizHawk-XMLGame System="Amiga" Name="legal"><LoadAssets><LoadAsset FileName="{image.name}"/></LoadAssets></BizHawk-XMLGame>')
            result=subprocess.run([source.DOTNET,str(output/'Cd32Loader.dll'),'host-chd',str(xml)],env=self.env,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(Path(str(xml)+'.actual').read_bytes(),wanted)

    def test_real_discsystem_target_and_analyzers(self):
        recipe=self.recipe
        recipe.MACHAWK=Path(os.environ.get('P6_MACHAWK_REFERENCE',str(recipe.MACHAWK)))
        recipe.git=functools.partial(recipe.git,cwd=recipe.MACHAWK)
        receipt=Path(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT',str(source.ROOT/'build/waterbox/puae6/puae6-receipt.json')))
        if not receipt.is_file(): raise RuntimeError('BLOCKED: missing guest receipt SHA build parameter')
        guest=json.loads(receipt.read_text())['guest']['sha256'] # Compile parameter only; no guest execution/validation.
        tree=self.work/'real-discsystem'; recipe.check_patches()
        self.assertTrue(recipe.export_tree(tree)); recipe.apply_patches(tree)
        assembly=recipe.build(guest,tree,'src/BizHawk.Emulation.DiscSystem/BizHawk.Emulation.DiscSystem.csproj',
            'src/BizHawk.Emulation.DiscSystem/bin/Debug/BizHawk.Emulation.DiscSystem.dll')
        self.assertTrue(assembly.is_file()); (tree/'build.log').unlink()

    def test_four_codecs_real_mount_regions_audio_subq_crossings_and_ownership(self):
        audio=self.pattern.audio(); expected=b''.join(audio[n*2352:(n+1)*2352]+subq(1,1,n,0,0) for n in range(40))
        record=(0,0,40,0,0,0,0,0,0,3,0,0)
        metadata=b'P6CD2448\0'+struct.pack('<3I',2,40,1)+struct.pack('<12I',*record)
        identity=hashlib.sha256(metadata+expected).hexdigest().upper()
        ids=[]
        hashes=json.loads((source.ROOT/'tests/fixtures/chd/sha256.json').read_text())
        for codec in self.pattern.CODECS:
            with self.subTest(codec=codec):
                original=source.ROOT/'tests/fixtures/chd'/(codec+'.chd')
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),hashes[original.name])
                path=self.work/(codec+'-á.chd'); shutil.copy2(original,path)
                result=subprocess.run([source.DOTNET,str(self.dll),'chd-host',str(path)],env=self.env,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                rows=result.stdout.splitlines(); ids.append(rows[0]); self.assertEqual(rows[0],identity)
                self.assertEqual(rows[1:],['0 40 1 1 0 0 False 7 2'])
                self.assertEqual(Path(str(path)+'.actual').read_bytes(),expected)
                self.assertEqual(Path(str(path)+'.units').read_bytes(),self.pattern.expected()[19577:19577+2448])
                wire=Path(str(path)+'.descriptor').read_bytes()
                self.assertEqual(len(wire),4808); self.assertEqual(struct.unpack_from('<II',wire),(2,4808))
                self.assertEqual(wire[8:40].hex().upper(),identity)
                self.assertEqual(struct.unpack_from('<QII12I',wire,40),(40*2448,1,40,*record))
                self.assertEqual(wire[104:],bytes(98*48))
        self.assertEqual(len(set(ids)),1) # Codec/path are not canonical media identity.

    def test_native_layout_version_range_and_failed_open_are_transactional(self):
        class Info(ctypes.Structure):
            _fields_=[('version',ctypes.c_uint32),('size',ctypes.c_uint32),('unit',ctypes.c_uint32),('hunk',ctypes.c_uint32),
                      ('tracks',ctypes.c_uint32),('reserved',ctypes.c_uint32),('bytes',ctypes.c_uint64)]
        lib=ctypes.CDLL(str(self.library)); lib.p6_chd_host_open.argtypes=[ctypes.c_char_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(Info)]
        lib.p6_chd_host_read.argtypes=[ctypes.c_void_p,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_uint32]
        lib.p6_chd_host_get_track.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]; lib.p6_chd_host_close.argtypes=[ctypes.c_void_p]
        info=Info(1,32); owner=ctypes.c_void_p(0x1234); before=bytes(info)
        descriptors=len(os.listdir('/dev/fd'))
        for data in (b'',b'MComprHD'+bytes(116),(source.ROOT/'tests/fixtures/chd/cdzl.chd').read_bytes()[:100]):
            bad=self.work/'bad.chd'; bad.write_bytes(data)
            self.assertNotEqual(lib.p6_chd_host_open(str(bad).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
            self.assertEqual((owner.value,bytes(info)),(0x1234,before))
            self.assertEqual(len(os.listdir('/dev/fd')),descriptors)
        path=source.ROOT/'tests/fixtures/chd/cdzl.chd'
        info.version=2; self.assertNotEqual(lib.p6_chd_host_open(str(path).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
        info.version=1; self.assertEqual(lib.p6_chd_host_open(str(path).encode(),ctypes.byref(owner),ctypes.byref(info)),0)
        try:
            self.assertEqual(len(os.listdir('/dev/fd')),descriptors+1)
            self.assertEqual((ctypes.sizeof(info),Info.bytes.offset,info.unit,info.hunk,info.tracks,info.bytes),(32,24,2448,19584,1,97920))
            out=(ctypes.c_ubyte*2449)(*([0xa5]*2449)); before=bytes(out)
            for offset,count in ((0,2449),(97920,1),(2**64-1,1)):
                self.assertNotEqual(lib.p6_chd_host_read(owner,offset,out,count),0); self.assertEqual(bytes(out),before)
            track=(ctypes.c_uint32*24)(); track[0]=1; track[1]=96; before=bytes(track)
            self.assertNotEqual(lib.p6_chd_host_get_track(owner,1,track),0); self.assertEqual(bytes(track),before)
            self.assertEqual(lib.p6_chd_host_get_track(owner,0,track),0)
            self.assertEqual(list(track[:8]),[1,96,1,40,0,0,0,0]); self.assertEqual(bytes(track)[48:54],b'AUDIO\0')
        finally: lib.p6_chd_host_close(owner)
        self.assertEqual(len(os.listdir('/dev/fd')),descriptors)

    def test_first_stored_pregap_public_v2_identity_and_ownership(self):
        output=self.public_runner()
        for gap in (1,149,150,151,200):
            for sub in ('NONE','RW_RAW'):
                fixture,wire=chd.htoa_fixture(self.work,f'public-htoa-{gap}-{sub}',gap,sub=sub)
                image=fixture[0]; xml=image.with_suffix('.xml')
                xml.write_text(f'<BizHawk-XMLGame System="Amiga" Name="HTOA"><LoadAssets><LoadAsset FileName="{image.name}"/></LoadAssets></BizHawk-XMLGame>')
                result=subprocess.run([source.DOTNET,str(output/'Cd32Loader.dll'),'host-chd',str(xml)],env=self.env,capture_output=True,text=True,timeout=90)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(Path(str(xml)+'.actual').read_bytes(),wire)
                records=[(0,gap,gap+3,0,0,0,1,0,0,7,0,0),(gap+3,gap+3,gap+12,1,4,0,0,0,0,3,0,0)]
                metadata=b'P6CD2448\0'+struct.pack('<3I',2,gap+12,2)+b''.join(struct.pack('<12I',*t) for t in records)
                self.assertIn(hashlib.sha256(metadata+wire).hexdigest().upper(),result.stdout.splitlines())
