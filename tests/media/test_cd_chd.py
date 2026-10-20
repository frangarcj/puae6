"""Private CHD provider domain: exact numeric sectors, IDs, rebind and native/VFS I/O."""
import functools
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from tests.media import test_chd_reader as reader, test_cd_mds as mds, test_cd_nrg as nrg
ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=128)
def mode1(payload, lba):
    sector = bytearray(nrg.mode2_header(lba)[:-1] + b'\1' + payload + bytes(288))
    edc = 0
    for b in sector[:2064]:
        edc ^= b
        for _ in range(8): edc = (edc >> 1) ^ (0xd8018001 if edc & 1 else 0)
    struct.pack_into('<I',sector,2064,edc)
    def mul(a,b):
        value = 0
        while b:
            if b & 1: value ^= a
            a <<= 1
            if a & 256: a ^= 0x11d
            b >>= 1
        return value
    inverse3 = next(n for n in range(256) if mul(n,3) == 1)
    def parity(row):
        total = power = 0
        for b in row: total ^= b; power = mul(power ^ b,2)
        first = mul(mul(power,2) ^ total,inverse3)
        return first, first ^ total
    for n in range(86): sector[2076+n],sector[2162+n] = parity(sector[12+n+i*86] for i in range(24))
    for n in range(52): sector[2248+n],sector[2300+n] = parity(sector[12+(n//2*86+n%2+i*88)%2236] for i in range(43))
    return bytes(sector)


class CdChdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-chd-',dir=ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup); cls.work = Path(cls.temp.name)
        entry = next(p for p in json.loads((ROOT/'patches/winuae/series.json').read_text())['patches'] if p['id'] == '0355-cdrom-mode1-sync')
        source = ROOT/'upstream/winuae/cdrom.cpp'; target = cls.work/'cdrom.cpp'; patch = (ROOT/entry['file']).read_bytes()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['files'][0]['before_sha256'] and hashlib.sha256(patch).hexdigest() == entry['sha256']
        target.write_bytes(source.read_bytes()); mds.mat._apply(patch,cls.work,True); mds.mat._apply(patch,cls.work,False)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['files'][0]['after_sha256']
        paths = [ROOT/'tests/media/cd_chd_probe.cpp',ROOT/'adapters/libretro/cd_chd.cpp',target]
        paths += [ROOT/'ports/portable/media'/name for name in ('sha256.c','cd_source.cpp','cd_wave.cpp','cd_disc.cpp','cd_read.cpp','cd_subchannel.cpp','cd_utilities.cpp')]
        (cls.work/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.19)\nproject(provider C CXX)\n'+f'include("{ROOT}/cmake/Chd.cmake")\n'+
            'add_executable(probe '+ ' '.join('"'+str(p)+'"' for p in paths)+')\n'+
            'target_link_libraries(probe PRIVATE p6_chd_reader)\ntarget_compile_options(probe PRIVATE -Wall -Wextra -Werror "$<$<COMPILE_LANGUAGE:CXX>:-include;cstring>")\n'+
            'target_compile_definitions(probe PRIVATE UAE_SYSDEPS_H)\ntarget_include_directories(probe PRIVATE '+ ' '.join('"'+str(ROOT/p)+'"' for p in ('ports/portable','references/libretro-api/include','upstream/winuae/include'))+')\n')
        flags = '-O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer'
        reader.dependency.ChdDependencyTests.run_ok(['cmake','-S',str(cls.work),'-B',str(cls.work/'out'),'-DCMAKE_C_FLAGS='+flags,'-DCMAKE_CXX_FLAGS='+flags,'-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined'])
        reader.dependency.ChdDependencyTests.run_ok(['cmake','--build',str(cls.work/'out'),'--target','probe','-j2'])

    def run_chd(self, path, *, code=0, vfs=False, fault=0, rebind=None):
        env = {**os.environ,'ASAN_OPTIONS':'detect_leaks=0'}; env.pop('P6_CHD_VFS',None); env.pop('P6_CHD_FAULT',None)
        if vfs: env['P6_CHD_VFS'] = '1'
        if fault: env['P6_CHD_FAULT'] = str(fault)
        p = subprocess.run([str(self.work/'out/probe'),str(path)]+([] if rebind is None else [str(rebind)]),env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(p.returncode,code,p.stderr+p.stdout[-300:]); return p.stdout.splitlines()

    def fixture(self, name, *, padding=0, payload=0, subchange=0, opaque=b'opaque', tail=b'', replace=None, badheader=False, variant=False):
        spec = [('MODE1','NONE',0,False),('MODE1_RAW','RW_RAW',2,True),('AUDIO','RW',2,True),('MODE2_RAW','NONE',3,False),
                ('AUDIO','NONE',1,False),('MODE1/2352','RW',1,False),('MODE1/2048','NONE',2,True),('MODE2/2352','RW_RAW',2,True)]
        if variant:
            spec[1] = ('MODE1_RAW','NONE',2,True); spec[2] = ('AUDIO','RW_RAW',2,True); spec[7] = ('MODE2/2352','RW',2,True)
        units = []; streams = []; tracks = []; records = []; cursor = 0
        for n,(kind,sub,gap,stored) in enumerate(spec):
            frames = 3 if n == len(spec)-1 else 5; mode = 3 if kind == 'AUDIO' else 0 if kind in ('MODE1','MODE1/2048') else 2 if kind.startswith('MODE2') else 1
            stride = 2048 if mode == 0 else 2352 if sub == 'NONE' else 2448; stream = bytearray()
            for k in range(frames):
                main = bytearray((n*31+k*7+i+payload) & 255 for i in range(2048 if mode == 0 else 2352)); canonical = bytes(main)
                if mode in (1,2): main[15] = 2 if mode == 2 or badheader else 1; canonical = bytes(main)
                if mode == 3:
                    values = [(n*317+k*211+i*13+payload)%65536-32768 for i in range(1176)]
                    main = struct.pack('>1176h',*values); canonical = struct.pack('<1176h',*values)
                pw = bytes((n*11+k+i*13+subchange)&255 for i in range(96))
                stream += canonical + (pw if stride == 2448 else b'')
                raw = bytes(main) + (nrg.interleave(pw) if sub == 'RW_RAW' else pw) if sub != 'NONE' else bytes(main)
                units.append(raw.ljust(2448,b'\x9d'))
            units += [bytes([(n*17+padding)&255])*2448] * ((-frames)&3); streams.append(bytes(stream))
            end = cursor+gap+frames-(gap if stored else 0)
            tracks.append([cursor,cursor+gap,end,n,stride,0,mode,1,0 if mode == 3 else 4,int(stored),0 if stored else gap,0]); cursor = end
            pg = ('V' if stored else '') + kind if gap else 'MODE1'; pgsub = sub if stored else 'NONE'
            text = f'TRACK:{n+1} TYPE:{kind} SUBTYPE:{sub} FRAMES:{frames} PREGAP:{gap} PGTYPE:{pg} PGSUB:{pgsub} POSTGAP:0'
            if replace and replace[0] == n: text = text.replace(replace[1],replace[2])
            records.append(text.encode()+b'\0')
        logical = len(units)*2448; units += [bytes(2448)] * ((-len(units))&7)
        data = reader.cd_map([(4,b''.join(units[n:n+8])) for n in range(0,len(units),8)],sector=bytes(19584))
        struct.pack_into('>Q',data,32,logical); data = reader.metadata_chain(data,records+[opaque],tag=[0x43485432]*len(records)+[0x54455354])
        path = self.work/(name+'.chd'); path.write_bytes(data+tail); return path,tracks,streams

    def canonical(self, tracks, streams, lba):
        t = next(t for t in tracks if lba < t[2]); first = t[0] if t[9] else t[1]; gap = lba < first; at = (lba-first)*t[4]
        main = bytes(2048 if t[6] == 0 else 2352) if gap else streams[t[3]][at:at+min(t[4],2352)]
        if t[6] == 0 or (gap and t[6] == 1): main = mode1(main if t[6] == 0 else bytes(2048),lba)
        if gap and t[6] == 2: main = nrg.mode2_header(lba)+bytes(2336)
        sub = streams[t[3]][at+2352:at+2448] if t[4] == 2448 and not gap else mds.subq(t[:7],lba,t[3]+1)
        return t,gap,main,sub

    def verify(self, fixture, vfs=False):
        path,tracks,streams = fixture; rows = self.run_chd(path,vfs=vfs); blob = path.read_bytes(); digest = hashlib.sha256
        ident = struct.pack('<Q',len(blob))+digest(blob).digest(); self.assertIn(f'container {len(blob)} {digest(blob).hexdigest()}',rows)
        logical,hunk = struct.unpack_from('>Q',blob,32)[0],struct.unpack_from('>I',blob,56)[0]; padded = sum((len(s)//t[4]+3)//4*4 for t,s in zip(tracks,streams))
        ident = b'P6CHD1'+ident+struct.pack('<QIQQII',logical,hunk,padded,logical//2448-padded,len(tracks),tracks[-1][2])
        for n,(t,s) in enumerate(zip(tracks,streams)):
            self.assertIn('track '+' '.join(map(str,t)),rows); self.assertIn(f'source {n} {len(s)} {digest(s).hexdigest()}',rows)
            ident += struct.pack('<IIIIIQIIIIIIQ',*t,len(s))+digest(s).digest()
        self.assertIn(f'disc {len(tracks)} {tracks[-1][2]} {digest(ident).hexdigest()}',rows)
        for row in map(str.split,rows):
            if row[0] == 'bytes': n,at,count = map(int,row[1:4]); self.assertEqual(row[4],digest(streams[n][at:at+count]).hexdigest())
            if row[0] not in ('sector','info'): continue
            lba = int(row[1]); t,gap,main,sub = self.canonical(tracks,streams,lba)
            if row[0] == 'info': self.assertEqual(list(map(int,row[2:])),[t[8],int(t[4]==2448 and not gap),int(lba<t[1]),0]); continue
            view,status = map(int,row[2:4]); error = 5 if view == 2048 and t[6] in (2,3) else 4 if view == 2048 and t[6] == 1 and main[15] != 1 else 0
            self.assertEqual(status,error)
            if not error: self.assertEqual(row[4],digest(main[16:2064] if view == 2048 else main if view == 2352 else main+sub).hexdigest(),row)
        return rows

    def test_four_codecs_and_mixed_raw_cooked_audio_subchannels_and_gaps(self):
        pattern = reader.dependency.load('chd_pattern',ROOT/'tests/fixtures/chd/generate.py')
        for codec in pattern.CODECS:
            self.verify((ROOT/f'tests/fixtures/chd/{codec}.chd',[[0,0,40,0,2352,0,3,1,0,0,0,0]],[pattern.audio()]))
        self.verify(self.fixture('mixed')); self.verify(self.fixture('all-subtypes',variant=True)); self.verify(self.fixture('header',badheader=True))

    def test_complete_container_identity_and_fresh_owner_rebind(self):
        first = self.fixture('first'); self.verify(first); expected = hashlib.sha256(b''.join(self.canonical(first[1],first[2],lba)[2]+self.canonical(first[1],first[2],lba)[3] for lba in range(first[1][-1][2]))).hexdigest()
        for n,options in enumerate(({}, {'padding':1},{'payload':1},{'subchange':1},{'opaque':b'changed'},{'tail':b'valid tail'})):
            other = self.fixture('rebind'+str(n),**options); self.verify(other)
            self.assertEqual(self.run_chd(first[0],rebind=other[0])[-1],f'rebind {2 if n else 0} {expected}')

    def test_out_of_domain_and_geometry_do_not_publish(self):
        cases = [(0,'PREGAP:0','PREGAP:1'),(1,'POSTGAP:0','POSTGAP:1'),(3,'PREGAP:3','PREGAP:151'),(1,'PGTYPE:VMODE1_RAW','PGTYPE:VMODE1'),(2,'PGSUB:RW','PGSUB:NONE'),(5,'PGSUB:NONE','PGSUB:RW'),(1,'TYPE:MODE1_RAW','TYPE:MODE1')]
        cases += [(3,'TYPE:MODE2_RAW','TYPE:'+mode) for mode in ('MODE2','MODE2_FORM1','MODE2_FORM2','MODE2_FORM_MIX','CDI/2352')]
        for n,replace in enumerate(cases): self.assertEqual(self.run_chd(self.fixture('reject'+str(n),replace=replace)[0],code=2),['error 5'])
        path = self.fixture('geometry')[0]; blob = bytearray(path.read_bytes()); struct.pack_into('>Q',blob,32,2448); path.write_bytes(blob); self.assertEqual(self.run_chd(path,code=2),['error 1'])
        path = self.fixture('crc')[0]; blob = bytearray(path.read_bytes()); blob[124] ^= 1; path.write_bytes(blob); self.assertEqual(self.run_chd(path,code=2),['error 4'])
        frames = 449852; sparse = bytearray(124+((frames+7)//8)*4); sparse[:8] = b'MComprHD'
        struct.pack_into('>II4I3QII',sparse,8,124,5,0,0,0,0,frames*2448,124,0,19584,2448)
        text = f'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:{frames} PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0'.encode()
        path = self.work/'msf.chd'; path.write_bytes(reader.metadata_chain(sparse,[text],tag=0x43485432))
        self.assertEqual(self.run_chd(path,code=2),['error 5'])
        self.assertEqual(self.run_chd(self.work/'missing.chd',code=2),['error 4'])

    def test_vfs_short_io_hash_and_cross_sector_reads_are_transactional(self):
        fixture = self.fixture('vfs'); self.assertEqual(self.verify(fixture),self.verify(fixture,True))
        for fault in (1,2,3,4): self.assertEqual(self.run_chd(fixture[0],code=2,vfs=True,fault=fault),['error 4'])
