"""Private CHD provider domain: exact numeric sectors, IDs, rebind and native/VFS I/O."""
import binascii
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


@functools.lru_cache(maxsize=2048)
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


def form2_body(payload, subheader=b'\0\0\x20\0\0\0\x20\0'):
    # XA duplicates four bytes: submode is at2 and6, not at5 as in the old synth.
    # Bitwise polynomial oracle independent of the WinUAE EDC table/helper.
    assert len(payload) == 2324 and len(subheader) == 8
    body = bytearray(subheader+payload+bytes(4)); edc = 0
    for value in body[:-4]:
        edc ^= value
        for _ in range(8): edc = (edc >> 1) ^ (0xd8018001 if edc&1 else 0)
    struct.pack_into('<I',body,2332,edc); return bytes(body)


def htoa_fixture(work, name, gap, *, sub='NONE', payload=0, stored=True):
    # MAME cdrom_open(CHD), libMirage add_redbook_pregap and DiscSystem SaveChd:
    # physical CHD frame0 is visible LBA0; the separate -150..0 never consumes it.
    frames=gap+3; stride=2352 if sub=='NONE' else 2448
    tracks=[[0,gap,frames,0,stride,0,3,1,0,int(stored),0 if stored else gap,0],
            [frames,frames,frames+9,1,2048,0,0,1,4,0,0,0]]
    units=[]; audio=bytearray(); wire=bytearray()
    for k in range(frames):
        samples=[(k*211+i*13+payload)%65536-32768 for i in range(1176)]
        raw=struct.pack('>1176h',*samples); body=struct.pack('<1176h',*samples)
        # Usable CRC with deliberately contradictory INDEX01, never a TOC oracle.
        q=mds.subq([0,0,frames,0,stride,0,3],k,1)
        audio+=body+(q if sub!='NONE' else b'')
        wire+=body+(q if sub!='NONE' else mds.subq(tracks[0][:7],k,1))
        units.append((raw+(nrg.interleave(q) if sub=='RW_RAW' else q) if sub!='NONE' else raw).ljust(2448,b'\x9d'))
    units += [bytes([0xa7])*2448]*((-frames)&3)
    data=b''.join(bytes((n*31+i+payload)&255 for i in range(2048)) for n in range(9))
    for k in range(9):
        units.append(data[k*2048:(k+1)*2048]+bytes(400))
        wire+=mode1(data[k*2048:(k+1)*2048],frames+k)+mds.subq(tracks[1][:7],frames+k,2)
    units += [bytes([0xb8])*2448]*3; logical=len(units)*2448
    raw_units=b''.join(units); units += [bytes(2448)]*((-len(units))&7)
    image=reader.cd_map([(4,b''.join(units[n:n+8])) for n in range(0,len(units),8)],sector=bytes(19584))
    struct.pack_into('>Q',image,32,logical)
    records=[f'TRACK:1 TYPE:AUDIO SUBTYPE:{sub} FRAMES:{frames} PREGAP:{gap} PGTYPE:{"V" if stored else ""}AUDIO PGSUB:{sub} POSTGAP:0'.encode(),
             b'TRACK:2 TYPE:MODE1 SUBTYPE:NONE FRAMES:9 PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0']
    path=work/(name+'.chd'); path.write_bytes(reader.metadata_chain(image,records,tag=0x43485432))
    spec=[[7,2 if sub=='NONE' else 1,frames,gap,7,2 if sub=='NONE' else 1,int(stored),0],[0,2,9,0,0,2,0,0]]
    path.with_suffix('.json').write_text(json.dumps({'tracks':spec,'xaGap':False}))
    path.with_suffix('.units').write_bytes(raw_units)
    return (path,tracks,[bytes(audio),data]),bytes(wire)


class CdChdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'build').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='cd-chd-',dir=ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup); cls.work = Path(cls.temp.name)
        entries = json.loads((ROOT/'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync','0356-cue-metadata-only','0357-ccd-metadata-only','0361-nrg-metadata-only','0388-mds-metadata-only','0389-nrg-dao-mode3-2336','0391-nrg-dao-mode2-form1','0396-chd-mode2-form2-edc'):
            entry = next(p for p in entries if p['id'] == identifier); patch = (ROOT/entry['file']).read_bytes()
            assert hashlib.sha256(patch).hexdigest() == entry['sha256']
            for record in entry['files']:
                target = cls.work/record['path']; target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists(): target.write_bytes((ROOT/'upstream/winuae'/record['path']).read_bytes())
                assert hashlib.sha256(target.read_bytes()).hexdigest() == record['before_sha256']
            mds.mat._apply(patch,cls.work,True); mds.mat._apply(patch,cls.work,False)
            for record in entry['files']: assert hashlib.sha256((cls.work/record['path']).read_bytes()).hexdigest() == record['after_sha256']
        target = cls.work/'cdrom.cpp'
        paths = [ROOT/'tests/media/cd_chd_probe.cpp',ROOT/'adapters/libretro/cd_chd.cpp',target]
        paths += [ROOT/'ports/portable/media'/name for name in ('sha256.c','cd_source.cpp','cd_wave.cpp','cd_disc.cpp','cd_read.cpp','cd_subchannel.cpp','cd_utilities.cpp','cd_akiko.cpp')]
        (cls.work/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.19)\nproject(provider C CXX)\n'+f'include("{ROOT}/cmake/Chd.cmake")\n'+
            'add_executable(probe '+ ' '.join('"'+str(p)+'"' for p in paths)+')\n'+
            'target_link_libraries(probe PRIVATE p6_chd_reader)\ntarget_compile_options(probe PRIVATE -Wall -Wextra -Werror "$<$<COMPILE_LANGUAGE:CXX>:-include;cstring>")\n'+
            'target_compile_definitions(probe PRIVATE UAE_SYSDEPS_H)\ntarget_include_directories(probe PRIVATE "'+str(cls.work/'include')+'" '+ ' '.join('"'+str(ROOT/p)+'"' for p in ('ports/portable','references/libretro-api/include','upstream/winuae/include'))+')\n')
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

    def cooked_fixture(self, name, *, prefix=66, shift=0, replace=None, form2=False, payload=0, subchange=0):
        kinds = ('MODE2_FORM2','MODE2/2324') if form2 else ('MODE1','MODE1/2048','MODE2','MODE2/2336','MODE2_FORM_MIX','MODE2_FORM1','MODE2/2048')
        specs = [('AUDIO','NONE',0,False,prefix)]
        specs += [(kind,sub,2 if sub == 'RW' else 3,sub == 'RW',5) for kind in kinds for sub in ('NONE','RW','RW_RAW')]
        units = []; streams = []; tracks = []; records = []; cursor = 0
        for n,(kind,sub,gap,stored,frames) in enumerate(specs):
            if n == 1: gap += shift
            cooked1 = kind in ('MODE1','MODE1/2048'); body2 = kind in ('MODE2','MODE2/2336','MODE2_FORM_MIX')
            mode = 3 if kind == 'AUDIO' else 0 if cooked1 and sub == 'NONE' else 1 if cooked1 else 2
            stride = 2048 if mode == 0 else 2352 if sub == 'NONE' else 2448
            start = cursor if stored else cursor+gap; stream = bytearray()
            for k in range(frames):
                if not n: raw = canonical = bytes(2352)
                else:
                    size = 2324 if form2 else 2336 if body2 else 2048
                    raw = bytes((n*31+k*7+i+payload)&255 for i in range(size))
                    # Include both Form1 and Form2 submode bits in stored2336 bodies.
                    if body2: raw = raw[:2]+bytes([32 if k&1 else 0])+raw[3:6]+bytes([32 if k&1 else 0])+raw[7:]
                    canonical = raw if mode == 0 else mode1(raw,start+k) if cooked1 else nrg.mode2_header(start+k)+(raw if body2 else form2_body(raw) if form2 else nrg.form1_body(raw))
                pw = bytes((n*11+k+i*13+subchange)&255 for i in range(96))
                stream += canonical + (pw if sub != 'NONE' else b'')
                unit = raw + (nrg.interleave(pw) if sub == 'RW_RAW' else pw) if sub != 'NONE' else raw
                units.append(unit.ljust(2448,b'\x9d'))
            units += [bytes([n&255])*2448]*((-frames)&3); streams.append(bytes(stream))
            end = cursor+gap+frames-(gap if stored else 0)
            tracks.append([cursor,cursor+gap,end,n,stride,0,mode,1,0 if mode == 3 else 4,int(stored),0 if stored else gap,0]); cursor = end
            pgtype = ('V' if stored else '')+kind if gap else 'MODE1'
            text = f'TRACK:{n+1} TYPE:{kind} SUBTYPE:{sub} FRAMES:{frames} PREGAP:{gap} PGTYPE:{pgtype} PGSUB:{sub if stored else "NONE"} POSTGAP:0'
            if replace and replace[0] == n:
                self.assertIn(replace[1],text); text = text.replace(replace[1],replace[2])
            records.append(text.encode()+b'\0')
        logical = len(units)*2448; units += [bytes(2448)]*((-len(units))&7)
        data = reader.cd_map([(4,b''.join(units[n:n+8])) for n in range(0,len(units),8)],sector=bytes(19584))
        struct.pack_into('>Q',data,32,logical); data = reader.metadata_chain(data,records,tag=0x43485432)
        path = self.work/(name+'.chd'); path.write_bytes(data); return path,tracks,streams

    def regional_fixture(self, name, *, prefix=66, payload=0, subchange=0, gapchange=0, postchange=0, variant=False):
        pairs = [('AUDIO','MODE1'),('MODE1','AUDIO'),('MODE2_FORM1','MODE2_RAW'),('MODE2_FORM2','MODE1_RAW'),
                 ('MODE1_RAW','MODE2/2336'),('MODE2_FORM_MIX','MODE2_FORM2'),('MODE2_RAW','MODE1/2048'),('MODE1/2048','MODE2_FORM1')]
        specs = [('AUDIO','AUDIO','NONE','NONE',False,0,0,prefix)]
        for pg,kind in pairs:
            for n,(pgsub,sub) in enumerate((('NONE','RW'),('RW','NONE'),('RW_RAW','RW'),('RW','RW_RAW'))):
                specs.append((pg,kind,pgsub,sub,n<3,2 if n<3 else 151+(len(specs)%2)*49,1+len(specs)%2,5))
        units=[]; streams=[]; tracks=[]; regions=[]; records=[]; cursor=0
        def packing(kind):
            return 3 if kind=='AUDIO' else 0 if kind in ('MODE1','MODE1/2048') else 1 if kind in ('MODE1_RAW','MODE1/2352') else 2
        for n,(pg,kind,pgsub,sub,stored,gap,post,frames) in enumerate(specs):
            if n==1: gap+=gapchange; post+=postchange
            if variant and n==1: pg='MODE2_FORM1'; pgsub='RW_RAW'
            regional=bool(n); stride=2448 if sub!='NONE' or (stored and pgsub!='NONE') else 2352
            mainmode=packing(kind); mainmode=1 if mainmode==0 else mainmode
            pgmode=packing(pg); pgmode=1 if pgmode==0 else pgmode
            flags=(1|(2 if sub!='NONE' else 0)|(4 if stored and pgsub!='NONE' else 0)|(32 if post else 0)) if regional else 0
            policy=[flags,pgmode,0 if pgmode==3 else 4] if regional else [0,0,0]
            end=cursor+gap+frames-(gap if stored else 0)+post
            tracks.append([cursor,cursor+gap,end,n,stride,0,mainmode,1,0 if mainmode==3 else 4,int(stored),0 if stored else gap,post]); regions.append(policy)
            start=cursor if stored else cursor+gap; stream=bytearray()
            for k in range(frames):
                selected,st=(pg,pgsub) if stored and k<gap else (kind,sub); mode=packing(selected)
                size=2352 if mode in (1,3) or selected in ('MODE2_RAW','MODE2/2352') else 2324 if selected in ('MODE2_FORM2','MODE2/2324') else 2336 if selected in ('MODE2','MODE2/2336','MODE2_FORM_MIX') else 2048
                raw=bytes((n*31+k*7+i+payload)&255 for i in range(size)); lba=start+k
                if mode==3:
                    samples=[(n*317+k*211+i*13+payload)%65536-32768 for i in range(1176)]
                    raw=struct.pack('>1176h',*samples); body=struct.pack('<1176h',*samples)
                elif mode==0: body=mode1(raw,lba)
                elif size==2352: raw=raw[:15]+bytes([1 if mode==1 else 2])+raw[16:]; body=raw
                else: body=nrg.mode2_header(lba)+(raw if size==2336 else form2_body(raw) if size==2324 else nrg.form1_body(raw))
                pw=bytes((n*11+k+i*13+subchange)&255 for i in range(96))
                stream+=body+((pw if st!='NONE' else bytes(96)) if stride==2448 else b'')
                unit=raw+(nrg.interleave(pw) if st=='RW_RAW' else pw) if st!='NONE' else raw
                units.append(unit.ljust(2448,b'\x9d'))
            units += [bytes([n&255])*2448]*((-frames)&3); streams.append(bytes(stream)); cursor=end
            pgtype=('V' if stored else '')+pg if gap else 'MODE1'
            records.append(f'TRACK:{n+1} TYPE:{kind} SUBTYPE:{sub} FRAMES:{frames} PREGAP:{gap} PGTYPE:{pgtype} PGSUB:{pgsub} POSTGAP:{post}'.encode())
        logical=len(units)*2448; units += [bytes(2448)]*((-len(units))&7)
        data=reader.cd_map([(4,b''.join(units[n:n+8])) for n in range(0,len(units),8)],sector=bytes(19584))
        struct.pack_into('>Q',data,32,logical); path=self.work/(name+'.chd'); path.write_bytes(reader.metadata_chain(data,records,tag=0x43485432))
        return path,tracks,streams,regions

    def regional_sector(self, fixture, lba):
        _,tracks,streams,regions=fixture; n=next(n for n,t in enumerate(tracks) if lba<t[2]); t=tracks[n]; f,pgmode,pgcontrol=regions[n]
        pre,post=lba<t[1],lba>=t[2]-t[11]; first=t[0] if t[9] else t[1]; synthetic=lba<first or post
        mode,control=(pgmode,pgcontrol) if f and pre else (t[6],t[8]); at=(lba-first)*t[4]
        stored=not synthetic and (bool(f&(4 if pre else 2)) if f else t[4]==2448)
        body=streams[n][at:at+2352] if not synthetic else bytes(2352) if mode==3 else mode1(bytes(2048),lba) if mode==1 else nrg.mode2_header(lba)+bytes(2336)
        if mode==0: body=mode1(streams[n][at:at+2048] if not synthetic else bytes(2048),lba)
        if stored: sub=streams[n][at+2352:at+2448]
        else:
            def bcd(n): return n//10*16+n%10
            def clock(n): return bytes(map(bcd,(n//4500,n//75%60,n%75)))
            q=bytes((control*16+1,bcd(n+1),0 if pre else 2 if post and f&32 else 1))+clock(abs(lba-t[1]))+b'\0'+clock(lba+150)
            sub=(b'\xff' if pre or post else b'\0')*12+q+(binascii.crc_hqx(q,0)^0xffff).to_bytes(2,'big')+bytes(72)
        return body,sub,mode,[control,int(stored),int(pre or post),int(synthetic and mode==3)]

    def verify_regional(self, fixture, vfs=False):
        path,tracks,streams,regions=fixture; rows=self.run_chd(path,vfs=vfs); blob=path.read_bytes(); digest=hashlib.sha256
        logical,hunk=struct.unpack_from('>Q',blob,32)[0],struct.unpack_from('>I',blob,56)[0]; padded=sum((len(s)//t[4]+3)//4*4 for t,s in zip(tracks,streams))
        encoded=b'P6CHR1'+struct.pack('<Q',len(blob))+digest(blob).digest()+struct.pack('<QIQQII',logical,hunk,padded,logical//2448-padded,len(tracks),tracks[-1][2])
        for n,(t,stream,region) in enumerate(zip(tracks,streams,regions)):
            self.assertIn('track '+' '.join(map(str,t)),rows); self.assertIn('regions '+' '.join(map(str,[n]+region)),rows)
            self.assertIn(f'source {n} {len(stream)} {digest(stream).hexdigest()}',rows)
            encoded+=struct.pack('<IIIIIQIIIIIIIIIQ',*t,*region,len(stream))+digest(stream).digest()
        self.assertIn(f'container {len(blob)} {digest(blob).hexdigest()}',rows)
        self.assertIn(f'disc {len(tracks)} {tracks[-1][2]} {digest(encoded).hexdigest()}',rows)
        for row in map(str.split,rows):
            if row[0]=='bytes': n,at,count=map(int,row[1:4]); self.assertEqual(row[4],digest(streams[n][at:at+count]).hexdigest())
            if row[0] not in ('sector','info'): continue
            lba=int(row[1]); body,sub,mode,info=self.regional_sector(fixture,lba)
            if row[0]=='info': self.assertEqual(list(map(int,row[2:])),info); continue
            view,status=map(int,row[2:4]); error=5 if view==2048 and mode in (2,3) else 0
            self.assertEqual(status,error)
            if not error: self.assertEqual(row[4],digest(body[16:2064] if view==2048 else body if view==2352 else body+sub).hexdigest())
        return rows

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
        cases = [(0,'PREGAP:0','PREGAP:1'),(1,'PGTYPE:VMODE1_RAW','PGTYPE:VCDI/2352'),(2,'PGSUB:RW','PGSUB:BAD')]
        cases += [(3,'TYPE:MODE2_RAW','TYPE:'+mode) for mode in ('CDI/2352',)]
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

    def test_cooked_modes_sub_offsets_headers_and_view_frontiers(self):
        fixture = self.cooked_fixture('cooked'); rows = self.verify(fixture)
        self.assertEqual(rows,self.verify(fixture,True))
        # Mode1+SUB stored INDEX00 crosses frame74/75 in the default fixture.
        self.assertEqual(fixture[1][2][0],74)
        # Each new packing itself crosses absolute4499/4500, not just a synthetic gap.
        for prefix,number in ((4340,2),(4303,7),(4240,16)):
            minute = self.cooked_fixture('minute'+str(number),prefix=prefix)
            self.assertEqual(minute[1][number][0] if minute[1][number][9] else minute[1][number][1],4348)
            self.verify(minute)
        for n,old,new in ((7,'PGTYPE:MODE2','PGTYPE:MODE2/2336'),(14,'PGTYPE:VMODE2_FORM_MIX','PGTYPE:VMODE2')):
            self.verify(self.cooked_fixture('alias'+str(n),replace=(n,old,new)))

    def test_cooked_identity_rebind_and_transactional_reads(self):
        first = self.cooked_fixture('cooked-first'); self.verify(first)
        expected = hashlib.sha256(b''.join(self.canonical(first[1],first[2],lba)[2]+self.canonical(first[1],first[2],lba)[3] for lba in range(first[1][-1][2]))).hexdigest()
        for shift in (0,1):
            other = self.cooked_fixture('cooked-shift'+str(shift),shift=shift); self.verify(other)
            self.assertEqual(self.run_chd(first[0],rebind=other[0])[-1],f'rebind {2 if shift else 0} {expected}')
            if shift:
                for n in (2,7,16): self.assertNotEqual(first[2][n],other[2][n])
        for fault in (1,2,3,4): self.assertEqual(self.run_chd(first[0],code=2,vfs=True,fault=fault),['error 4'])
        for n,old,new in ((2,'PGTYPE:VMODE1','PGTYPE:VCDI/2352'),(7,'PGTYPE:MODE2','PGTYPE:CDI/2352'),(16,'PGTYPE:MODE2_FORM1','PGTYPE:CDI/2352'),(2,'PGSUB:RW','PGSUB:BAD')):
            self.assertEqual(self.run_chd(self.cooked_fixture('cooked-reject'+str(n)+new[-1],replace=(n,old,new))[0],code=2),['error 5'])

    def test_form2_2324_edc_subchannels_and_rebind(self):
        first = self.cooked_fixture('form2',form2=True); rows = self.verify(first)
        self.assertEqual(rows,self.verify(first,True))
        self.verify(self.cooked_fixture('form2-minute',form2=True,prefix=4340))
        for stream,t in zip(first[2][1:],first[1][1:]):
            sector = stream[:2352]; self.assertEqual(sector[16:20],sector[20:24])
            self.assertEqual(sector[16:24],b'\0\0\x20\0\0\0\x20\0')
            self.assertNotEqual(sector[2076:2348],bytes(272)) # Form2 payload occupies the would-be Form1 P/Q area.
        expected = hashlib.sha256(b''.join(self.canonical(first[1],first[2],lba)[2]+self.canonical(first[1],first[2],lba)[3] for lba in range(first[1][-1][2]))).hexdigest()
        for n,options in enumerate(({}, {'shift':1},{'payload':1},{'subchange':1})):
            other = self.cooked_fixture('form2-rebind'+str(n),form2=True,**options); self.verify(other)
            self.assertEqual(self.run_chd(first[0],rebind=other[0])[-1],f'rebind {2 if n else 0} {expected}')
        for fault in (1,2,3,4): self.assertEqual(self.run_chd(first[0],code=2,vfs=True,fault=fault),['error 4'])
        # Direct helper must preserve arbitrary subheaders and all non-EDC bytes.
        arbitrary = bytes((n*37+11)&255 for n in range(2352))
        expected = arbitrary[:16]+form2_body(arbitrary[24:2348],arbitrary[16:24])
        self.assertEqual(self.run_chd('--form2-edc'),['form2-edc '+hashlib.sha256(expected).hexdigest()])
        for n,old,new in ((2,'PGTYPE:VMODE2_FORM2','PGTYPE:VCDI/2352'),(5,'PGSUB:RW','PGSUB:BAD')):
            self.assertEqual(self.run_chd(self.cooked_fixture('form2-reject'+str(n),form2=True,replace=(n,old,new))[0],code=2),['error 5'])

    def test_regional_pregap_packings_subchannels_postgap_and_identity(self):
        fixture=self.regional_fixture('regional'); rows=self.verify_regional(fixture)
        self.assertEqual(rows,self.verify_regional(fixture,True))
        self.verify_regional(self.regional_fixture('regional-minute',prefix=4340))

    def test_regional_reader_failures_and_fresh_owner_rebind(self):
        first=self.regional_fixture('regional-first'); self.verify_regional(first)
        expected=hashlib.sha256(b''.join(b''.join(self.regional_sector(first,lba)[:2]) for lba in range(first[1][-1][2]))).hexdigest()
        for n,options in enumerate(({}, {'payload':1},{'subchange':1},{'gapchange':1},{'postchange':1},{'variant':True})):
            other=self.regional_fixture('regional-rebind'+str(n),**options); self.verify_regional(other)
            self.assertEqual(self.run_chd(first[0],rebind=other[0])[-1],f'rebind {2 if n else 0} {expected}')
        for fault in (1,2,3,4): self.assertEqual(self.run_chd(first[0],code=2,vfs=True,fault=fault),['error 4'])

    def test_first_stored_pregap_htoa_addresses_toc_subq_and_rebind(self):
        for gap in (1,149,150,151,200):
            for sub in ('NONE','RW_RAW'):
                fixture,wire=htoa_fixture(self.work,f'htoa-{gap}-{sub}',gap,sub=sub)
                if gap>150:
                    regions=[[1|(6 if sub!='NONE' else 0),3,0],[0,0,0]]
                    rows=self.verify_regional((*fixture,regions))
                    self.assertEqual(rows,self.verify_regional((*fixture,regions),True))
                else:
                    rows=self.verify(fixture); self.assertEqual(rows,self.verify(fixture,True))
                self.assertIn(f'toc 1 1 0 1 {gap}',rows)
                self.assertIn(f'toc 2 1 4 2 {gap+3}',rows)
                self.assertIn(f'toc 162 1 0 0 {gap+12}',rows)
                for lba in (0,gap-1,gap,gap+2,gap+3):
                    expected=hashlib.sha256(wire[lba*2448:(lba+1)*2448]).hexdigest()
                    self.assertIn(f'sector {lba} 2448 0 {expected}',rows)
                other,_=htoa_fixture(self.work,f'htoa-rebind-{gap}-{sub}',gap,sub=sub,payload=1)
                digest=hashlib.sha256(wire).hexdigest()
                self.assertEqual(self.run_chd(fixture[0],rebind=fixture[0])[-1],f'rebind 0 {digest}')
                self.assertEqual(self.run_chd(fixture[0],rebind=other[0])[-1],f'rebind 2 {digest}')
        for fault in (1,2,3,4):
            self.assertEqual(self.run_chd(fixture[0],vfs=True,fault=fault,code=2),['error 4'])
        synthetic,_=htoa_fixture(self.work,'htoa-not-stored',1,stored=False)
        self.assertEqual(self.run_chd(synthetic[0],code=2),['error 5'])
