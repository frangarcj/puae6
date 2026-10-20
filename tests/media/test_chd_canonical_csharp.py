"""Real DiscSystem mount/synths over decompressed units; no CHD decoder or guest."""
import binascii
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from tests.media import test_cd_source_csharp as source, test_cd_chd as chd, test_cd_nrg as nrg


def mode(kind): return 0 if kind==7 else 1 if kind in (0,1) else 2

def subq(track,index,lba,one,control):
    def bcd(n): return n//10*16+n%10
    def clock(n): return bytes(map(bcd,(n//4500,n//75%60,n%75)))
    q=bytes((control*16+1,bcd(track),index))+clock(abs(lba-one))+b'\0'+clock(lba+150)
    return (b'\0' if index==1 else b'\xff')*12+q+(binascii.crc_hqx(q,0)^0xffff).to_bytes(2,'big')+bytes(72)


@unittest.skipUnless(source.DOTNET and source.CACHE.is_dir(), 'local dotnet/cache required')
class ChdCanonicalCSharpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='chd-canonical-',dir=source.ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); cls.dll,cls.env=source.build_disc_source(cls.work)

    def fixture(self,name,specs,*,xa=False,padding=0,mds=False,contradictory_q=False):
        units=[]; tracks=[]; regions=[]; sectors={}; old={}; cursor=0
        def region(start,end,n,index,kind,sub,synthetic):
            if start<end: regions.append([start,end,n,index,mode(kind),0 if kind==7 else 4,int(synthetic),kind,sub])
        def synthetic(kind,lba):
            return bytes(2352) if kind==7 else chd.mode1(bytes(2048),lba) if mode(kind)==1 else nrg.mode2_header(lba)+(chd.form2_body(bytes(2324)) if xa else bytes(2336))
        for n,(kind,sub,frames,gap,pg,pgsub,stored,post) in enumerate(specs,1):
            tracks.append([kind,sub,frames,gap,pg,pgsub,int(stored),post]); one=cursor+gap; end=one+frames-(gap if stored else 0)
            region(cursor,one,n,0,pg,pgsub if stored else 2,not stored)
            region(one,end,n,1,kind,sub,False); region(end,end+post,n,2,kind,2,True)
            if not stored:
                for lba in range(cursor,one): sectors[lba]=synthetic(pg,lba)+subq(n,0,lba,one,0 if pg==7 else 4)
            for k in range(frames):
                lba=(cursor if stored else one)+k; selected,st=(pg,pgsub) if stored and k<gap else (kind,sub)
                size=(2048,2352,2336,2048,2324,2336,2352,2352)[selected]
                raw=bytes((n*31+k*7+i)&255 for i in range(size)); body=raw
                if selected==7:
                    samples=[(n*317+k*211+i*13)%65536-32768 for i in range(1176)]
                    raw=struct.pack('>1176h',*samples); body=struct.pack('<1176h',*samples)
                elif selected in (1,6): raw=raw[:15]+bytes([mode(selected)])+raw[16:]; body=raw
                elif selected==0: body=chd.mode1(raw,lba)
                else: body=nrg.mode2_header(lba)+(raw if size==2336 else chd.form2_body(raw) if size==2324 else nrg.form1_body(raw))
                pw=bytes((n*11+k+i*13)&255 for i in range(96)); index=0 if lba<one else 1
                if contradictory_q: pw=subq(n,1,lba,one,0 if selected==7 else 4) # Valid CRC, wrong INDEX01 in stored pregaps.
                sectors[lba]=body+(pw if st!=2 else subq(n,index,lba,one,0 if selected==7 else 4))
                if selected==4:
                    wrong=chd.form2_body(raw,b'\0\0\x20\0\0\x20\0\0')
                    old[lba]=nrg.mode2_header(lba)+wrong+sectors[lba][2352:]
                unit=raw+(nrg.interleave(pw) if st==1 else pw) if st!=2 else raw
                units.append(unit.ljust(2448,b'\x9d'))
            units += [bytes([(n*17+padding)&255])*2448]*((-frames)&3)
            for lba in range(end,end+post): sectors[lba]=synthetic(kind,lba)+subq(n,2,lba,one,0 if kind==7 else 4)
            cursor=end+post
        path=self.work/(name+'.json'); path.write_text(json.dumps({'tracks':tracks,'xaGap':xa})); path.with_suffix('.units').write_bytes(b''.join(units))
        path.with_suffix('.expected').write_bytes(b''.join(sectors[lba] for lba in range(cursor)))
        rows=['region '+' '.join(map(str,r)) for r in regions]
        digest=lambda b: hashlib.sha256(b).hexdigest().upper()
        expected=rows+[f'sector {lba} {digest(sectors[lba])}' for lba in range(cursor)]
        legacy=rows+[f'sector {lba} {digest(old.get(lba,sectors[lba]))}' for lba in range(cursor)]
        if mds:
            payload=bytes((i*37+11)&255 for i in range(2324)); body=chd.form2_body(payload)
            path.with_suffix('.mds').write_bytes(body[8:]) # MDS stores payload+EDC, not the synthesized subheader.
            expected+=['mds '+digest(nrg.mode2_header(4350)+body)]
            legacy+=['mds '+digest(nrg.mode2_header(4350)+b'\0\0\x20\0\0\x20\0\0'+body[8:])]
        return path,expected,legacy

    def run_fixture(self,path,dll=None):
        result=subprocess.run([source.DOTNET,str(dll or self.dll),'chd-units',str(path)],env=self.env,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout[-1000:]+result.stderr)
        return result.stdout.splitlines()

    def test_regional_descriptor_materialization_identity_and_rejections(self):
        specs=[(7,2,8,0,0,2,False,1),(0,0,5,2,7,1,True,1),
               (7,2,5,3,0,2,False,2),(6,1,5,2,1,0,True,1)]
        path,expected,_=self.fixture('regional',specs)
        result=subprocess.run([source.DOTNET,str(self.dll),'chd-regional',str(path)],env=self.env,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout[-1000:]+result.stderr)
        self.assertEqual(result.stdout.splitlines(),expected)
        actual=path.with_suffix('.actual').read_bytes(); self.assertEqual(actual,path.with_suffix('.expected').read_bytes())
        wire=path.with_suffix('.descriptor').read_bytes(); self.assertEqual(len(wire),4808)
        records=[]; cursor=0
        for kind,sub,frames,gap,pg,pgsub,stored,post in specs:
            one=cursor+gap; end=one+frames-(gap if stored else 0)+post
            records.append((cursor,one,end,mode(kind),0 if kind==7 else 4,0,int(gap!=0),0,post,
                            3+(4 if gap else 0)+(56 if post else 0),mode(pg) if gap else 0,0 if not gap or pg==7 else 4))
            cursor=end
        self.assertEqual(struct.unpack_from('<II',wire),(2,4808))
        self.assertEqual(struct.unpack_from('<QII',wire,40),(cursor*2448,len(specs),cursor))
        for n,record in enumerate(records): self.assertEqual(struct.unpack_from('<12I',wire,56+n*48),record)
        self.assertEqual(wire[56+len(specs)*48:],bytes((99-len(specs))*48))
        metadata=b'P6CD2448\0'+struct.pack('<3I',2,cursor,len(specs))+b''.join(struct.pack('<12I',*t) for t in records)
        self.assertEqual(wire[8:40],hashlib.sha256(metadata+actual).digest())
        previous=wire
        path,expected,_=self.fixture('regional-valid-q',specs,contradictory_q=True)
        result=subprocess.run([source.DOTNET,str(self.dll),'chd-regional',str(path)],env=self.env,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout[-1000:]+result.stderr)
        self.assertEqual(result.stdout.splitlines(),expected)
        actual=path.with_suffix('.actual').read_bytes(); self.assertEqual(actual,path.with_suffix('.expected').read_bytes())
        wire=path.with_suffix('.descriptor').read_bytes()
        self.assertEqual(wire[:8]+wire[40:],previous[:8]+previous[40:]) # Metadata comes from regions, even with usable contradictory Q.
        self.assertEqual(wire[8:40],hashlib.sha256(metadata+actual).digest()); self.assertNotEqual(wire[8:40],previous[8:40])

    def test_typed_regions_offsets_padding_audio_subchannels_and_postgap(self):
        specs=[(7,2,66,0,0,2,False,0),(0,2,5,2,7,1,True,1),(7,0,5,151,0,0,False,2),
               (2,1,5,2,6,2,True,1),(4,2,5,2,3,0,True,2),(3,0,5,200,4,1,False,1),(6,1,5,2,1,0,True,1)]
        for xa in (False,True):
            first=self.fixture('regions'+str(xa),specs,xa=xa)
            self.assertEqual(self.run_fixture(first[0]),first[1])
            other=self.fixture('padding'+str(xa),specs,xa=xa,padding=71)
            self.assertEqual(first[1],other[1]); self.assertEqual(self.run_fixture(other[0]),other[1])

    def test_cooked_mode2_xa_headers_edc_and_parity_match_independent_oracles(self):
        specs=[(7,2,4348,0,0,2,False,0)]+[(kind,sub,5,2,kind,sub,True,0) for kind in (0,2,3,4,5,1,6,7) for sub in (2,0,1)]
        path,expected,legacy=self.fixture('cooked',specs,mds=True)
        baseline=self.work/'legacy'; baseline.mkdir(); dll,_=source.build_disc_source(baseline,legacy_xa=True)
        before=self.run_fixture(path,dll); raw=path.with_suffix('.actual').read_bytes(); correct=path.with_suffix('.expected').read_bytes()
        form1=set()
        for row in map(str.split,expected):
            if row[0]=='region' and row[8]=='3' and row[7]=='0': form1.update(range(int(row[1]),int(row[2])))
        self.assertTrue(form1); self.assertEqual(len(before),len(legacy))
        for actual,wanted in zip(before,legacy):
            fields=actual.split()
            if fields[0]=='sector' and int(fields[1]) in form1:
                at=int(fields[1])*2448; old=raw[at:at+2448]; new=correct[at:at+2448]
                self.assertEqual(old[:2076],new[:2076]); self.assertEqual(old[2352:],new[2352:])
                self.assertNotEqual(old[2076:2352],new[2076:2352]) # Original Form1 includes MSF/mode in P/Q.
            else: self.assertEqual(actual,wanted) # Original Form2 [5] and its exact EDC; all other bytes unchanged.
        self.assertNotEqual(before,expected)
        self.assertEqual(self.run_fixture(path),expected)
        self.assertEqual(path.with_suffix('.actual').read_bytes(),correct)
