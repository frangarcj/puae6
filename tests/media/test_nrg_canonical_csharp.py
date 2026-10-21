"""Opt-in pinned DiscSystem TAO canonical150; no public loader or Waterbox claim."""
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from tests.media import test_cd_source_csharp as source, test_nrg_metadata as meta, test_cd_nrg as nrg, test_cd_chd as chd
from tests.media.test_chd_canonical_csharp import subq


class NrgCanonicalCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='nrg-canonical-',dir=source.ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); cls.dll,cls.env=source.build_disc_source(cls.work)

    def run_nrg(self,path,mode='nrg-tao',other=None,same=True):
        result=subprocess.run([source.DOTNET,str(self.dll),mode,str(path)]+([] if other is None else [str(other),'same' if same else 'other']),
                              env=self.env,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
        return result.stdout.splitlines()

    def fixture(self,name,modes=(0,2,3),*,wide=False,payload=0):
        lengths=(3,4,5); tail,directory,_=meta.etnf(modes,lengths,etn2=wide)
        data=bytearray(); wire=bytearray(); records=[]; cursor=0; rows=[]
        for n,(kind,count) in enumerate(zip(modes,lengths),1):
            stride=2048 if kind in (0,2) else 2336 if kind==3 else 2352
            one=cursor+(150 if n>1 else 0); end=one+count; mode=0 if kind==7 else 1 if kind==0 else 2; control=0 if kind==7 else 4
            records.append((cursor,one,end,mode,control,0,int(n>1),0,0,7 if n>1 else 3,mode if n>1 else 0,control if n>1 else 0))
            rows.append(f'track {n} {one}')
            for lba in range(cursor,one):
                body=bytes(2352) if kind==7 else chd.mode1(bytes(2048),lba) if kind==0 else nrg.mode2_header(lba)+bytes(2336)
                wire+=body+subq(n,0,lba,one,control)
            for k in range(count):
                raw=bytes((n*43+k+i+payload)&255 for i in range(stride)); data+=raw
                body=chd.mode1(raw,one+k) if kind==0 else nrg.mode2_header(one+k)+(nrg.form1_body(raw) if kind==2 else raw) if kind in (2,3) else raw
                wire+=body+subq(n,1,one+k,one,control)
            cursor=end
        self.assertEqual(len(data),directory); path=self.work/(name+'.nrg'); path.write_bytes(data+tail)
        metadata=b'P6CD2448\0'+struct.pack('<3I',2,cursor,len(modes))+b''.join(struct.pack('<12I',*t) for t in records)
        return path,bytes(wire),records,[hashlib.sha256(metadata+wire).hexdigest().upper(),*rows,f'leadout {cursor}']

    def test_etnf_etn2_four_modes_mixed_stride_toc_q_stream_and_identity(self):
        for modes in ((0,0,0),(2,2,2),(3,3,3),(7,7,7),(0,2,3),(7,0,7)):
            ids=[]
            for wide in (False,True):
                path,wire,records,expected=self.fixture('mixed'+str(modes)+str(wide),modes,wide=wide)
                self.assertEqual(self.run_nrg(path),expected)
                self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
                descriptor=Path(str(path)+'.descriptor').read_bytes()
                self.assertEqual(struct.unpack_from('<II',descriptor),(2,4808))
                self.assertEqual(descriptor[8:40].hex().upper(),expected[0]); ids.append(expected[0])
                self.assertEqual(struct.unpack_from('<QII',descriptor,40),(len(wire),3,len(wire)//2448))
                for n,record in enumerate(records): self.assertEqual(struct.unpack_from('<12I',descriptor,56+n*48),record)
                self.assertEqual(descriptor[56+3*48:],bytes(96*48))
                self.assertTrue(self.run_nrg(path,'nrg-default-refuse')[0].startswith('rejected '))
            self.assertEqual(ids[0],ids[1]) # Same canonical materialization, independent of ETN record width.

    def test_rebind_and_malformed_inputs_close_sources_transactionally(self):
        original,wire,_,expected=self.fixture('original')
        same=self.fixture('renamed',wide=True)[0]; other=self.fixture('changed',payload=1)[0]
        self.assertEqual(self.run_nrg(original,other=same),expected)
        self.assertEqual(self.run_nrg(original,other=other,same=False),expected)
        self.assertEqual(Path(str(original)+'.actual').read_bytes(),wire)
        for wide in (False,True):
            path=self.fixture('badbase'+str(wide),wide=wide)[0]; data=path.read_bytes(); at=struct.unpack_from('>Q' if wide else '>I',data,len(data)-(8 if wide else 4))[0]
            width=8 if wide else 4; record=32 if wide else 20
            cases=[(at+8+width*2,struct.pack('>I',5)),(at+8+width*2+4,struct.pack('>I',150)),
                   (at+8+width*2+8,b'\1'),(at+8+record,struct.pack('>Q' if wide else '>I',1)),
                   (at+8+width,struct.pack('>Q' if wide else '>I',1)),(at+8+3*record+8,struct.pack('>I',2)),
                   (at+4,struct.pack('>I',65537))]
            if wide: cases += [(at+8,struct.pack('>Q',2**32)),(at+16,struct.pack('>Q',2**32+3*2048))]
            for n,(offset,value) in enumerate(cases):
                bad=self.work/f'bad-{wide}-{n}.nrg'; blob=bytearray(data); blob[offset:offset+len(value)]=value; bad.write_bytes(blob)
                self.assertTrue(self.run_nrg(bad,'nrg-refuse')[0].startswith('rejected '))
            for tag in ('TINF','CUEX'):
                bad=self.work/(tag+str(wide)+'.nrg'); blob=bytearray(data); blob[at:at+4]=tag.encode(); bad.write_bytes(blob)
                self.assertTrue(self.run_nrg(bad,'nrg-refuse')[0].startswith('rejected '))
        missing=self.work/'missing.nrg'; self.assertTrue(self.run_nrg(missing,'nrg-refuse')[0].startswith('rejected '))

    def test_dao_v1_v2_default_and_opt_in_preserve_bytes_and_identity(self):
        for version in (1,2):
            tail,directory,_=meta.nrg(version,modes=(0,),sectors=3)
            data=bytes(i%251 for i in range(directory)); path=self.work/f'dao{version}.nrg'; path.write_bytes(data+tail)
            before=self.run_nrg(path,'nrg-default'); wire=Path(str(path)+'.actual').read_bytes()
            expected=b''.join(chd.mode1(data[(150+n)*2048:(151+n)*2048],n)+subq(1,1,n,0,4) for n in range(3))
            self.assertEqual(wire,expected); self.assertEqual(self.run_nrg(path),before)
            self.assertEqual(Path(str(path)+'.actual').read_bytes(),expected)

    def test_real_discsystem_target_and_analyzers(self):
        spec=importlib.util.spec_from_file_location('nrg_recipe',source.ROOT/'integration/bizhawk/patches/build_tree.py')
        recipe=importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
        recipe.MACHAWK=Path(os.environ.get('P6_MACHAWK_REFERENCE',str(recipe.MACHAWK))); recipe.git=functools.partial(recipe.git,cwd=recipe.MACHAWK)
        tree=self.work/'real-discsystem'; recipe.check_patches(); recipe.export_tree(tree); recipe.apply_patches(tree)
        receipt=Path(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT',str(source.ROOT/'build/waterbox/puae6/puae6-receipt.json')))
        if not receipt.is_file(): raise RuntimeError('BLOCKED: missing coordinated receipt SHA build parameter')
        sha=json.loads(receipt.read_text())['guest']['sha256'] # Compile parameter only, no execution claim.
        project=tree/'src/BizHawk.Emulation.DiscSystem/BizHawk.Emulation.DiscSystem.csproj'
        result=subprocess.run([source.DOTNET,'build',str(project),'-m:1','-v:q',f'-p:Puae6GuestSha256={sha}',
                               f'-p:RestoreSources={source.CACHE}','-p:InstallGitHooks=false'],cwd=tree,env=self.env,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout[-5000:]+result.stderr[-5000:])
        self.assertTrue((project.parent/'bin/Debug/BizHawk.Emulation.DiscSystem.dll').is_file())
