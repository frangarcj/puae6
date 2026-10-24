"""Opt-in pinned DiscSystem TAO canonical150; no public loader or Waterbox claim."""
import binascii
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


def nrg_fixture(modes=(0,2,3),*,wide=False,payload=0,lengths=(3,4,5)):
    tail,directory,_=meta.etnf(modes,lengths,etn2=wide)
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
    assert len(data)==directory
    metadata=b'P6CD2448\0'+struct.pack('<3I',2,cursor,len(modes))+b''.join(struct.pack('<12I',*t) for t in records)
    return bytes(data)+tail,bytes(wire),records,[hashlib.sha256(metadata+wire).hexdigest().upper(),*rows,f'leadout {cursor}']


def sbi_fixture(path, wire, entries, lbas, *, mednafen=True):
    """Write real SBI records and return independently patched deinterleaved2448 bytes.

    entries=(LBA, record_type, payload); lbas describes wire order, including negative INDEX00.
    """
    patched=bytearray(wire); positions={lba:n*2448+2364 for n,lba in enumerate(lbas)}; file=bytearray(b'SBI\0')
    for lba,kind,payload in entries:
        aba=lba+150; msf=(aba//4500,aba//75%60,aba%75)
        file+=bytes((v//10*16+v%10 for v in msf))+bytes([kind])+payload
        at=positions[lba]; start,count={1:(0,10),2:(3,3),3:(7,3)}[kind]; assert len(payload)==count
        patched[at+start:at+start+count]=payload
        if mednafen: # SynthChecksum writes complemented CRC; Mednafen inverts it again.
            patched[at+10:at+12]=binascii.crc_hqx(patched[at:at+10],0).to_bytes(2,'big')
    path.write_bytes(file)
    return bytes(patched)

def dao_fixture(work,name,version=2,modes=(7,0,6),padded=False,groups=None,prefix=0,reordered=False):
    seed=tuple(7 if k==16 else 0 if k==15 else 6 if k==17 else k for k in modes)
    if groups is None: groups=((-150,11,((1,seed[0],-150,0,4),(2,seed[1],4,6,11)),0,1,b'1234567890123'),
            (20,39,((3,seed[2],30,32,39),),32,7,b'ABCDEFGHIJKLM'))
    tail,_,_,blocks=meta.nrg_sessions(version,groups=groups); tail=bytearray(tail)
    payload=bytearray([0x6d]*prefix); wire=bytearray(); rows=[]; ordinal=0; width=8 if version==2 else 4
    sessions=[f'session {i} {g[2][0][0]} {g[2][-1][0]} {g[3]} {g[0]} {g[1]} {g[4]} {g[5].hex()}' for i,g in enumerate(groups,1)]
    for group,g in enumerate(groups):
        for local,(number,_,zero,one,end) in enumerate(g[2]):
            kind=modes[ordinal]; ordinal+=1; control=0 if kind in (7,16) else 4
            size=2448 if kind>=15 else 2048 if kind in (0,2) else 2336 if kind==3 else 2352
            stride=2448 if padded and kind in (0,5,6,7) else size
            offset=len(payload); start=offset+(one-zero)*stride; finish=start+(end-one)*stride
            at=blocks[group]['DAO']+30+local*(18+3*width)
            struct.pack_into('>H',tail,at+12,stride); tail[at+14]=kind
            struct.pack_into('>QQQ' if version==2 else '>III',tail,at+18,offset,start,finish)
            rows.append(f'track {number} {offset} {start} {finish} {stride} {kind} {zero} {one} {end} 1 {control} '+b'US1234567890'.hex())
            for lba in range(zero,end):
                raw=bytearray((number*53+lba-zero+k)&255 for k in range(size))
                if kind in (5,6,15,17): raw[15]=1 if kind in (5,15) else 2
                if kind>=15:
                    pw=bytes((number*31+lba-zero+k)&255 for k in range(96)); raw[2352:]=nrg.interleave(pw); body=bytes(raw[:2352])+pw
                else:
                    body=chd.mode1(bytes(raw),lba) if kind==0 else nrg.mode2_header(lba)+(nrg.form1_body(bytes(raw)) if kind==2 else raw) if kind in (2,3) else bytes(raw)
                    body+=subq(number,0 if lba<one else 1,lba,one,control)
                payload+=raw+bytes([0xa5])*(stride-size); wire+=body
    directory=len(payload)+123; struct.pack_into('>Q' if version==2 else '>I',tail,len(tail)-width,directory)
    if len(groups)>2 or reordered: # SINF and CUE families precede DAO: the second DAO appears at chunk200.
        tail=b''.join(tail[b[k]:b[k]+8+struct.unpack_from('>I',tail,b[k]+4)[0]] for k in ('SIN','CUE','DAO') for b in blocks)+tail[-12-width:]
    path=Path(work)/(name+'.nrg'); path.write_bytes(payload+bytes([0x9c])*123+tail)
    return path,bytes(wire),sessions+rows

def dao_metadata(rows):
    sessions=[]; records=[]; extents=[]; isrc=[]
    for row in rows:
        fields=row.split()[1:]
        if row.startswith('session '): sessions.append((tuple(map(int,fields[1:7])),bytes.fromhex(fields[7])))
        else:
            number,off0,off1,endoff,stride,kind,zero,one,end,adr,control=map(int,fields[:11])
            mode=0 if kind in (7,16) else 1 if kind in (0,5,15) else 2; start=max(0,zero); pg=one>start
            records.append((start,one,end,mode,control,0,int(pg),0,0,3|(4 if pg else 0),mode if pg else 0,control if pg else 0))
            extents.append((zero,end)); isrc.append(bytes.fromhex(fields[11]))
    metadata=b'P6CD2448\0'+struct.pack('<4I',3,sessions[-1][0][4],len(records),len(sessions))
    for fields,catalog in sessions: metadata+=struct.pack('<3IiIB',*fields)+catalog
    for record,raw in zip(records,isrc): metadata+=struct.pack('<12I',*record)+raw
    metadata+=struct.pack('<I',len(extents))+b''.join(struct.pack('<iiQ',a,b,(b-a)*2448) for a,b in extents)
    return metadata,records,sessions


def dao_identity(wire,rows):
    return hashlib.sha256(dao_metadata(rows)[0]+wire).hexdigest().upper()


def global_fixture(work, name, *, shift=0, payload=False):
    from tests.fixtures.nrg_etnf_cdi import converted_fixture
    raw,programs=converted_fixture(); raw=bytearray(raw); directory=1411104
    if shift: struct.pack_into('>I',raw,directory+40+20,11702+shift)
    if payload: raw[710304+17]^=1; programs=(programs[0],bytes(raw[710304:directory]))
    wire=bytearray(); rows=[]
    for n,(one,count,stride,kind,off) in enumerate(((0,302,2352,7,0),(11702+shift,300,2336,3,710304)),1):
        zero=one-150; end=one+count; control=0 if kind==7 else 4
        rows.append(f'session {n} {n} {n} {0 if kind==7 else 32} {zero} {end} 0 '+bytes(13).hex())
        rows.append(f'track {n} {off} {off} {off+count*stride} {stride} {kind} {zero} {one} {end} 1 {control} '+bytes(12).hex())
        for lba in range(zero,end):
            index=int(lba>=one)
            body=(bytes(2352) if kind==7 else nrg.mode2_header(lba)+bytes(2336)) if not index else programs[n-1][(lba-one)*stride:(lba-one+1)*stride]
            if index and kind==3: body=nrg.mode2_header(lba)+body
            wire+=body+subq(n,index,lba,one,control)
    path=Path(work)/(name+'.nrg'); path.write_bytes(raw)
    metadata,records,sessions=dao_metadata(rows)
    identity=hashlib.sha256(metadata.replace(b'P6CD2448\0',b'P6ETGM2448\0',1)+wire).hexdigest().upper()
    return path,bytes(wire),[r for r in rows if r.startswith("session ")]+[r for r in rows if r.startswith("track ")],records,sessions,identity


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
        image,wire,records,expected=nrg_fixture(modes,wide=wide,payload=payload)
        path=self.work/(name+'.nrg'); path.write_bytes(image)
        return path,wire,records,expected

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

    def sparse_fixture(self,name,version=2,modes=(7,0,6),**options):
        return dao_fixture(self.work,name,version,modes,**options)

    def sparse(self,path,mode='nrg-sparse',faults=False):
        result=subprocess.run([source.DOTNET,str(self.dll),mode,str(path)]+(['faults'] if faults else []),env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
        return result.stdout.splitlines()

    def test_dao_multisession_sparse_offsets_tocs_and_stored_subchannels(self):
        for version in (1,2):
            for modes in ((7,0,6),(2,3,5),(16,15,17)):
                path,wire,rows=self.sparse_fixture(f'sparse{version}-{modes}',version,modes)
                self.assertEqual(self.sparse(path),rows); self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
        path,wire,rows=self.sparse_fixture('padded',padded=True)
        self.assertEqual(self.sparse(path),rows); self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
        groups=tuple((-150 if n==0 else n*3-1,n*3+2,
                      ((n+1,0,-150 if n==0 else n*3,n*3+1 if n else 0,n*3+2),),
                      0,255,bytes((0,255))+b'12345678901') for n in range(99))
        path,wire,rows=self.sparse_fixture('max-sessions',modes=(0,)*99,groups=groups)
        self.assertEqual(self.sparse(path),rows); self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)

    def test_dao_multisession_gaps_faults_and_owner_cleanup(self):
        for version in (1,2):
            path,wire,rows=self.sparse_fixture('faults'+str(version),version)
            self.assertEqual(self.sparse(path,faults=True),rows); self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
        path,_,rows=self.sparse_fixture('sbi')
        path.with_suffix('.sbi').write_bytes(b'SBI\0')
        self.assertEqual(self.sparse(path),rows) # An empty valid companion is accepted without changing sectors.
        path.with_suffix('.sbi').write_bytes(b'bad!')
        self.assertEqual(self.sparse(path),rows) # Invalid signature retains the historical ignored-file behavior.

    def test_sbi_sparse_sessions_q_precedence_offsets_and_interleave(self):
        entries=((-1,1,bytes(range(10))),(6,2,b'\x98\x76\x54'),(32,3,b'\x12\x34\x56'),(-1,2,b'\x99\x88\x77'))
        lbas=[*range(-150,11),*range(30,39)]
        for modes in ((7,0,6),(16,15,17)):
            path,wire,_=self.sparse_fixture('sbi-wire'+str(modes),modes=modes)
            patch=self.work/'records.sbi'
            for mednafen in (False,True):
                expected=sbi_fixture(patch,wire,entries,lbas,mednafen=mednafen)
                result=subprocess.run([source.DOTNET,str(self.dll),'nrg-sbi',str(path),str(patch),str(mednafen).lower()],env=self.env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(Path(str(path)+'.actual').read_bytes(),expected)
                interleaved=b''.join(expected[n:n+2352]+nrg.interleave(expected[n+2352:n+2448]) for n in range(0,len(expected),2448))
                self.assertEqual(Path(str(path)+'.interleaved').read_bytes(),interleaved)
            # Public mount finalization discovers the real companion and applies the default policy.
            path.with_suffix('.sbi').write_bytes(patch.read_bytes())
            result=subprocess.run([source.DOTNET,str(self.dll),'nrg-sbi',str(path)],env=self.env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(Path(str(path)+'.actual').read_bytes(),expected)

    def test_sbi_preflight_read_failure_and_owner_cleanup_transactionally(self):
        path,wire,rows=self.sparse_fixture('sbi-faults',modes=(16,15,17))
        result=subprocess.run([source.DOTNET,str(self.dll),'nrg-sbi-faults',str(path)],env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        companion=path.with_suffix('.sbi')
        for contents,error in ((b'SBI\0\x00\x02\x11\x02abc','InvalidDataException'),(b'SBI\0\x00','SBIParseException')):
            companion.write_bytes(contents)
            self.assertEqual(self.sparse(path,'nrg-sparse-refuse'),['rejected '+error])
        companion.unlink()
        self.assertEqual(self.sparse(path),rows) # Both failed mounts released their backing files.
        self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)

    def normalized(self,path,other=None,kind='same'):
        result=subprocess.run([source.DOTNET,str(self.dll),'nrg-normalized',str(path)]+([] if other is None else [str(other),kind]),
                              env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
        return result.stdout.strip()

    def test_dao_multisession_descriptor_v3_and_canonical_identity(self):
        for version,modes in ((1,(7,0,6)),(2,(2,3,5)),(2,(16,15,17))):
            path,wire,rows=dao_fixture(self.work,f'normalized{version}-{modes}',version,modes)
            self.assertEqual(self.normalized(path),dao_identity(wire,rows)); self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
            descriptor=Path(str(path)+'.descriptor').read_bytes(); metadata,records,sessions=dao_metadata(rows)
            self.assertEqual(len(descriptor),6792); self.assertEqual(struct.unpack_from('<II',descriptor),(3,6792))
            self.assertEqual(descriptor[8:40].hex().upper(),dao_identity(wire,rows)); self.assertEqual(struct.unpack_from('<QII',descriptor,40),(39*2448,3,39))
            for n,record in enumerate(records): self.assertEqual(struct.unpack_from('<12I',descriptor,56+n*48),record)
            self.assertEqual(descriptor[56+3*48:4808],bytes(96*48)); self.assertEqual(struct.unpack_from('<I',descriptor,4808),(2,))
            for n,(fields,catalog) in enumerate(sessions): self.assertEqual(struct.unpack_from('<3IiI',descriptor,4812+n*20),fields[:5])
            self.assertEqual(descriptor[4812+40:],bytes(97*20))

    def test_dao_multisession_rebind_lease_and_transactional_failures(self):
        path,wire,rows=dao_fixture(self.work,'identity-base'); expected=dao_identity(wire,rows)
        same,wire2,rows2=dao_fixture(self.work,'relocated-reordered',1,padded=True,prefix=37,reordered=True)
        self.assertEqual(wire,wire2); self.assertEqual(expected,dao_identity(wire2,rows2))
        self.assertEqual(self.normalized(path,same),expected)
        self.assertEqual(self.normalized(path,same,'fault'),expected) # Failed candidate hashing preserves the previous reader.
        data=path.read_bytes(); directory=struct.unpack_from('>Q',data,len(data)-8)[0]; blocks=meta.nrg_sessions()[3]
        changes=[(0,b'\xff'), # Backed negative prefix is invisible to ReadAt but belongs to identity.
                 (int(rows[-1].split()[3]),b'\xff'),
                 (directory+blocks[1]['CUE']+12,struct.pack('>i',21)),
                 (directory+blocks[0]['DAO']+12,b'X'),
                 (directory+blocks[0]['DAO']+27,b'\x02'),
                 (directory+blocks[1]['DAO']+30,b'Z')]
        for n,(at,value) in enumerate(changes):
            alien=self.work/f'identity-alien{n}.nrg'; altered=bytearray(data); altered[at:at+len(value)]=value; alien.write_bytes(altered)
            self.assertEqual(self.normalized(path,alien,'other'),expected)

    def metadata(self,data,base,fault=''):
        path=self.work/'metadata.nrg'; path.write_bytes(data)
        result=subprocess.run([source.DOTNET,str(self.dll),'nrg-metadata',str(path),str(base),fault],
                              env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
        return result.stdout.splitlines()

    def test_dao_multisession_metadata_exact_groups_and_global_tracks(self):
        for version, prefix in ((1,0),(2,2**32+7)):
            data,base,expected,blocks=meta.nrg_sessions(version,prefix)
            expected += ['isrc '+b'US1234567890'.hex()]*3
            self.assertEqual(self.metadata(data,base),expected)
            # Ordinal within each family binds sessions; physical interleaving is immaterial.
            reordered=[]
            for kind in ('SIN','CUE','DAO'):
                for block in blocks:
                    at=block[kind]; length=struct.unpack_from('>I',data,at+4)[0]
                    reordered.append(data[at:at+8+length])
            self.assertEqual(self.metadata(b''.join(reordered)+data[data.index(b'END!'):],base),expected)
        groups=tuple((-150 if n==0 else (n-1)*10+2,n*10+2,
                      ((n+1,0,-150 if n==0 else n*10,0 if n==0 else n*10+1,n*10+2),),
                      0,255,bytes((0,255))+b'12345678901') for n in range(99))
        data,base,expected,blocks=meta.nrg_sessions(groups=groups)
        data=bytearray(data); raw=bytes(range(12)); at=blocks[-1]['DAO']+30; data[at:at+12]=raw
        expected += ['isrc '+b'US1234567890'.hex()]*98+['isrc '+raw.hex()]
        self.assertEqual(self.metadata(data,base),expected) # 298 chunks, hints are opaque, raw bytes survive ASCII strings.

    def test_dao_multisession_metadata_invalid_and_no_payload(self):
        for version in (1,2):
            data,base,_,blocks=meta.nrg_sessions(version); first,second=blocks
            invalid=[data[:-1],data+b'x',bytes(65536)+data,data.replace(b'END!'+bytes(4),meta.chunk('END!',b'x'))]
            def change(at,value,fmt='I'):
                bad=bytearray(data); struct.pack_into('>'+fmt,bad,at,value); invalid.append(bad)
            change(first['SIN']+8,3)
            # Existing parser accepts this mismatched SINF; the new metadata seam must reject it.
            self.assertTrue(self.metadata(invalid[-1],base,'legacy')[0].startswith('nrg '))
            change(second['DAO']+28,4,'B'); change(second['DAO']+29,4,'B'); change(first['DAO']+28,0,'B')
            for value in (9,31):
                at=second['CUE']+12
                if version==2: change(at,value)
                else:
                    bad=bytearray(data); bad[at:at+4]=bytes((0,0,2,value)); invalid.append(bad)
            for index in (0,1): # Missing CUE0 or A2.
                at=first['CUE']; length=struct.unpack_from('>I',data,at+4)[0]; entry=0 if index==0 else length-8
                bad=bytearray(data); struct.pack_into('>I',bad,at+4,length-8); del bad[at+8+entry:at+16+entry]; invalid.append(bad)
            for offset,value in ((first['CUE']+9,0x9a),(first['CUE']+10,1),(first['CUE']+8,0),
                                 (first['CUE']+26,0),(first['CUE']+24,0x41)):
                change(offset,value,'B')
            at=second['SIN']; invalid.append(data[:at]+data[at+12:])
            width=8 if version==2 else 4; record=second['DAO']+30
            for field,value in ((18,376544-2352),(18+width,381248+1),(18+2*width,2**(width*8)-1)):
                change(record+field,value,'Q' if version==2 else 'I')
            change(first['DAO']+30+12,0,'H'); change(first['CUE']+4,65537)
            bad=bytearray(data); bad[first['DAO']:first['DAO']+4]=b'DAOI' if version==2 else b'DAOX'; invalid.append(bad)
            end=data.index(b'END!'); at=first['DAO']; length=struct.unpack_from('>I',data,at+4)[0]
            invalid.append(data[:end]+data[at:at+8+length]+data[end:])
            invalid.append(data[:end]+meta.etnf()[0][:68]+data[end:])
            invalid.append(data[:-width]+bytes([255])*width)
            for bad in invalid:
                self.assertTrue(self.metadata(bad,base)[0].startswith('rejected '))
            self.assertEqual(self.metadata(data,base,'io'),['rejected IOException'])
            self.assertEqual(self.metadata(data,base,'short'),['rejected NRGParseException'])

    def test_etnf_global_producer_sparse_wire_descriptor_and_identity(self):
        path,wire,rows,records,sessions,identity=global_fixture(self.work,'global')
        self.assertEqual(self.run_nrg(path,'nrg-sparse'),rows)
        self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
        self.assertEqual(self.normalized(path),identity)
        self.assertEqual(Path(str(path)+'.actual').read_bytes(),wire)
        descriptor=Path(str(path)+'.descriptor').read_bytes()
        self.assertEqual(struct.unpack_from('<II',descriptor),(3,6792)); self.assertEqual(len(descriptor),6792)
        self.assertEqual(descriptor[8:40].hex().upper(),identity)
        self.assertEqual(struct.unpack_from('<QII',descriptor,40),(12002*2448,2,12002))
        for i,record in enumerate(records): self.assertEqual(struct.unpack_from('<12I',descriptor,56+i*48),record)
        self.assertEqual(descriptor[152:4808],bytes(97*48)); self.assertEqual(struct.unpack_from('<I',descriptor,4808),(2,))
        for i,(fields,_) in enumerate(sessions): self.assertEqual(struct.unpack_from('<3IiI',descriptor,4812+i*20),fields[:5])
        self.assertEqual(descriptor[4852:],bytes(97*20))
        self.assertEqual(self.metadata(path.read_bytes()[1411104:],1411104),[
            'global 1 1 0 -150 302','global 2 2 32 11552 12002',
            'extent 1 0 710304 2352 7 -150 0 302','extent 2 710304 1411104 2336 3 11552 11702 12002'])
        patched=sbi_fixture(path.with_suffix('.sbi'),wire,[(11702,2,b'\x12\x34\x56')],list(range(-150,302))+list(range(11552,12002)))
        self.run_nrg(path,'nrg-sbi'); self.assertEqual(Path(str(path)+'.actual').read_bytes(),patched)
        path.with_suffix('.sbi').unlink()

    def test_etnf_global_rebind_invalid_metadata_and_ownership(self):
        path,wire,_,_,_,identity=global_fixture(self.work,'global-original')
        same=global_fixture(self.work,'global-renamed')[0]
        self.assertEqual(self.normalized(path,same),identity)
        for name,kwargs in (('coordinates',{'shift':1}),('payload',{'payload':True})):
            other,changed,*_=global_fixture(self.work,name,**kwargs)
            self.assertNotEqual(wire,changed); self.assertEqual(self.normalized(path,other,'other'),identity)
        self.assertEqual(self.normalized(path,same,'fault'),identity)
        data=path.read_bytes(); directory=1411104
        for at,value in ((directory,b'ETN2'),(directory+4,struct.pack('>I',19)),(directory+20,struct.pack('>I',1)),
            (directory+28+8,struct.pack('>I',0)),(directory+40+8,struct.pack('>I',710305)),
            (directory+40+20,struct.pack('>I',400)),(directory+40+16,struct.pack('>I',5)),
            (directory+40+24,struct.pack('>I',1)),(directory+80,b'JUNK')):
            broken=bytearray(data); broken[at:at+len(value)]=value
            self.assertTrue(self.metadata(broken[directory:],directory)[0].startswith('rejected '))
            invalid=self.work/'global-invalid.nrg'; invalid.write_bytes(broken)
            self.assertTrue(self.run_nrg(invalid,'nrg-sparse-refuse')[-1].startswith('rejected '))
        for fault in ('io','short'):
            self.assertTrue(self.metadata(data[directory:],directory,fault)[0].startswith('rejected '))

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
