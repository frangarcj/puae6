"""Pinned parsemds export against independent LE descriptor fixtures; no MDF or engine."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from tests.media import test_ccd_metadata as ccd_ref, test_nrg_metadata as nrg_ref
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import materialize_upstream as mat


def mds(modes=((0xaa,2048,0),(0xa9,2352,0),(0xab,2448,8)), *, name='*.mdf', wide=False, base=0, absent=False):
    # WinUAE MDS structs and fixed DiscSystem MDS_Format offsets, not parser output.
    count=len(modes); end=count*100+50; table=112; extras=table+80*(count+3); footers=extras+8*count; names=footers+16*count
    data=bytearray(names); data[:16]=b'MEDIA DESCRIPTOR'; data[16:18]=bytes((1,5))
    struct.pack_into('<HH',data,18,0,1); struct.pack_into('<I',data,80,88)
    struct.pack_into('<iiHBBHHII',data,88,-150,end,1,count+3,3,1,count,0,table)
    expected=[f'mds 1 5 0 1 1 {count} -150 {end} {count}',f'name {int(absent or name.lower()=="*.mdf")} {"*.mdf" if absent else name}']
    for n,point in enumerate((0xa0,0xa1,0xa2)):
        pmin,psec,pframe=(1,0x20,0) if n==0 else (count,0,0) if n==1 else ((end+150)//4500,(end+150)//75%60,(end+150)%75)
        toc=(0x14,0,point,0,0,0,0,pmin,psec,pframe); data[table+80*n+2:table+80*n+12]=bytes(toc)
        expected.append('lead '+' '.join(map(str,toc)))
    for n,(mode,stride,sub) in enumerate(modes):
        track=table+80*(n+3); offset=base+123+n*1000000; pregap=150 if n==0 else 10+n; sectors=50+n
        toc=(0x10 if mode==0xa9 else 0x14,0,n+1,0,0,0,0,(100*n+150)//4500,(100*n+150)//75%60,(100*n+150)%75)
        data[track:track+2]=bytes((mode,sub)); data[track+2:track+12]=bytes(toc)
        struct.pack_into('<IH',data,track+12,extras+8*n,stride); struct.pack_into('<iQII',data,track+36,100*n,offset,1,footers+16*n)
        struct.pack_into('<II',data,extras+8*n,pregap,sectors); struct.pack_into('<II',data,footers+16*n,0 if absent else names,int(wide))
        expected.append(f'track {100*n} {offset} {pregap} {sectors} {stride} {mode} {sub} {int(wide)} '+' '.join(map(str,toc)))
    data+=(name.encode('utf-16le')+b'\0\0') if wide else name.encode('ascii')+b'\0'
    return data,expected,dict(track=table+240,extra=extras,footer=footers,name=names)


@unittest.skipUnless(shutil.which('clang++'),'clang++ unavailable')
class MdsMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'build').mkdir(exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(prefix='mds-metadata-',dir=ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); target=cls.work/'blkdev_cdimage.cpp'; target.write_bytes((ROOT/'upstream/winuae/blkdev_cdimage.cpp').read_bytes())
        entries=json.loads((ROOT/'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0356-cue-metadata-only','0357-ccd-metadata-only','0361-nrg-metadata-only','0388-mds-metadata-only'):
            entry=next(x for x in entries if x['id']==identifier)
            patch=(ROOT/entry['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest()==entry['files'][0]['before_sha256']
            assert hashlib.sha256(patch).hexdigest()==entry['sha256']
            mat._apply(patch,cls.work,True); mat._apply(patch,cls.work,False)
            assert hashlib.sha256(target.read_bytes()).hexdigest()==entry['files'][0]['after_sha256']
        for kind in ('mds','cue','ccd','nrg'):
            command=['clang++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fno-exceptions','-fno-rtti',
                     '-fsanitize=address,undefined','-fno-sanitize-recover=all','-DP6_'+kind.upper()+'_METADATA',
                     '-I'+str(ROOT/'ports/portable/media'),str(target),str(ROOT/f'tests/media/{kind}_metadata_probe.cpp'),'-o',str(cls.work/kind)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
            if result.returncode: raise AssertionError(result.stderr)

    def decode(self,data,*,error=None,fault=0,bytes_=None):
        path=self.work/'fixture.mds'; path.write_bytes(data)
        result=subprocess.run([str(self.work/'mds'),str(path),str(len(data) if bytes_ is None else bytes_),str(fault)],capture_output=True,text=True,timeout=10)
        self.assertEqual(0 if error is None else 2,result.returncode,result.stderr+result.stdout)
        if error is not None: self.assertEqual(f'error={error}',result.stdout.strip())
        return result.stdout.splitlines()

    def test_raw_toc_extras_geometry_names_and_64bit_offsets(self):
        modes=((0xaa,2048,0),(0xaa,2352,0),(0xaa,2448,8),(0xa9,2352,0),(0xa9,2448,8),(0xab,2352,0),(0xab,2448,8))
        for options in ({'modes':modes,'base':2**32+7},{'name':'track.mdf'},{'wide':True,'name':'Disco-ñ-😀.mdf'},
                        {'wide':True,'name':'*.MDF'},{'absent':True},{'modes':((0xa9,2352,0),)*99}):
            data,expected,_=mds(**options); self.assertEqual(expected,self.decode(data))
        data,expected,at=mds(); self.assertEqual(expected,self.decode(data,bytes_=2**32+len(data)))
        first=bytes(data[at['track']:at['track']+80]); second=bytes(data[at['track']+80:at['track']+160])
        data[at['track']:at['track']+160]=second+first; self.assertEqual(expected,self.decode(data))

    def test_truncation_invalid_ranges_duplicates_endian_and_callback_are_transactional(self):
        data,_,at=mds(modes=((0xaa,2048,0),))
        for cut in range(len(data)): self.decode(data[:cut],error=1)
        mutations=[(0,b'X'),(108,struct.pack('>I',112)),(80,b'\xff'*4),(88,b'\xff'*8),
                   (112+4,b'\xa1'),(112+10,b'\x3c'),(at['track']+11,b'\x4b'),(112+240+4,b'\x00'),(at['track']+12,b'\xff'*4),(at['track']+52,b'\xff'*4),
                   (at['extra']+4,b'\0'*4),(at['track']+40,struct.pack('<Q',2**64-1)),(at['footer'],b'\xff'*4),
                   (84,struct.pack('<I',at['name']+2))]
        for position,value in mutations:
            bad=bytearray(data); bad[position:position+len(value)]=value; self.decode(bad,error=1)
        for fault in (1,2,3,5,9): self.decode(data,fault=fault,error=4)
        data,_,at=mds(wide=True,name='a.mdf')
        for cut in (1,2): self.decode(data[:-cut],error=1)
        for value in (b'\x00\xdc',b'\x00\xd8x\0'):
            bad=bytearray(data); bad[at['name']:at['name']+len(value)]=value; self.decode(bad,error=1)

    def test_unsupported_variants_and_fixed_read_budget(self):
        data,_,at=mds(modes=((0xaa,2048,0),))
        for position,value in ((16,b'\x02'),(18,b'\x10\0'),(20,b'\x02\0'),(96,b'\x02\0'),(100,b'\x02\0'),
                               (at['track'],b'\xac'),(at['track']+1,b'\x04'),(at['track']+16,struct.pack('<H',2336)),
                               (at['track']+48,struct.pack('<I',2)),(at['footer']+4,struct.pack('<I',2)),(at['name'],b'\x80')):
            bad=bytearray(data); bad[position:position+len(value)]=value; self.decode(bad,error=5)
        data,_,at=mds(); other=len(data); data+=b'other.mdf\0'
        struct.pack_into('<I',data,at['footer']+16,other); self.decode(data,error=5)
        for name in ('*.bin','bad?.mdf'):
            data,_,_=mds(name=name); self.decode(data,error=5)
        data,_,_=mds(name='a'*1024); self.decode(data,error=3)
        data,_,_=mds(modes=((0xa9,2352,0),)*99,name='a'*600); self.decode(data,error=3)

    def test_existing_endpoints_still_compile_and_parse(self):
        path=self.work/'old.txt'
        for kind,text in (('cue','FILE mixed.bin BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00'),('ccd',ccd_ref.ccd()[0])):
            path.write_text(text); result=subprocess.run([str(self.work/kind),str(path),'0'],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr+result.stdout)
        data,base,expected=nrg_ref.nrg(); path.write_bytes(data)
        result=subprocess.run([str(self.work/'nrg'),str(path),str(base),'0'],capture_output=True,text=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr); self.assertEqual(expected,result.stdout.splitlines())
