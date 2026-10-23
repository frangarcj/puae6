"""Stock CDTV through the public native/libretro API; original CDFS/HUNK, no injected state."""
from pathlib import Path
import re
import hashlib
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from tests.build_support import ROOT, build_targets
from tests.media import cdtv_boot_image, test_cdtv_boot


class CdtvLoadTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        main,ext,tm=test_cdtv_boot.boot_inputs()
        built=build_targets(ROOT/'build/state-opt',['p6_libretro_host','puae6_libretro','p6_headless'],optimized=True)
        cls.host,cls.core,cls.headless=(built[k] for k in ('p6_libretro_host','puae6_libretro','p6_headless'))
        cls.tmp=tempfile.TemporaryDirectory(prefix='cdtv-load-',dir=ROOT/'build'); cls.counter=0
        cls.directory=Path(cls.tmp.name); cls.system_dir=cls.directory/'system'; cls.system_dir.mkdir()
        (cls.system_dir/'kick34005.A500').symlink_to(Path(main).resolve())
        (cls.system_dir/'CDTV-Extended-ROMv2.7.rom').symlink_to(Path(ext).resolve())
        cls.iso=cls.directory/'Original CDTV.iso'; cls.iso.write_bytes(cdtv_boot_image.image(tm))

    def play(self,*flags,content='fixture',system=None,expect=0):
        path=self.iso if content=='fixture' else content
        result=subprocess.run([str(self.host),str(self.core),'--system',str(system or self.system_dir),
                               *(['--content',str(path)] if path else []),*map(str,flags)],capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,expect,result.stdout[-1200:]+result.stderr[-3000:])
        self.assertIn('vfs closed live=0',result.stdout); self.assertNotIn('VIOLATION',result.stdout)
        return result.stdout

    def markers(self,text):
        result=[]
        for match in re.finditer(r'^peek 0x0 1048576 ([0-9a-f]+)$',text,re.M):
            ram=bytes.fromhex(match[1]); at=0; found=[]
            while (at:=ram.find(cdtv_boot_image.MAGIC+cdtv_boot_image.NONCE,at))>=0:
                count=int.from_bytes(ram[at+32:at+36],'big'); at+=2
                if count: found.append(count)
            self.assertEqual(len(found),1,'unique executing HUNK marker, not a cached file'); result.append(found[0])
        return result

    def same_frames(self,left,right,phase):
        self.assertEqual(len(left),len(right),phase)
        for index,(a,b) in enumerate(zip(left,right),1):
            self.assertEqual(a,b,f'{phase}: first differing frame {index}: {a} != {b}')

    def test_new_owner_after_long_session_matches_cold_power_on(self):
        # 170x2 does not expose it: the preceding owner must reach the display
        # programmed by the real firmware. No state load participates here.
        out=self.play('--runs',1032,'--cycles',2,'--do','1032:memory')
        chunks=re.split(r'cycle \d+ end[^\n]*\n',out)[:2]
        observations=lambda chunk:[tuple(r[k] for k in ('unit','ticks','hash','ahash','audio')) for r in self.runs(chunk)]
        self.assertEqual([len(self.runs(c)) for c in chunks],[1032,1032])
        self.same_frames(observations(chunks[0]),observations(chunks[1]),'cold power-on after long owner')
        self.assertEqual(re.findall(r'^memory hash (.*)$',chunks[0],re.M),re.findall(r'^memory hash (.*)$',chunks[1],re.M))

    def test_original_cd_boot_serialize_and_cold_reload(self):
        work=self.work(); cut,end,again,cold=(work/n for n in ('cut','end','again','cold'))
        out=self.play('--runs',1032,'--cycles',2,'--do','1000:save:s','--do',f'1000:savefile:{cut}',
                      '--do','1000:peek:0:1048576','--do','1016:memory','--do','1016:peek:0:1048576',
                      '--do',f'1016:savefile:{end}','--do','1016:load:s','--do','1032:memory',
                      '--do','1032:peek:0:1048576','--do',f'1032:savefile:{again}')
        self.assertIn('machine CDTV',out); self.assertEqual(end.read_bytes(),again.read_bytes())
        view=lambda r:tuple(r[k] for k in ('unit','ticks','hash','ahash','audio'))
        chunks=re.split(r'cycle \d+ end[^\n]*\n',out)[:2]
        for chunk in chunks:
            runs=self.runs(chunk); self.assertEqual(len(runs),1032)
            self.assertEqual([view(r) for r in runs[1000:1016]],[view(r) for r in runs[1016:1032]])
            counters=self.markers(chunk); self.assertEqual(len(counters),3)
            self.assertGreater(counters[0],0); self.assertGreater(counters[1],counters[0]); self.assertEqual(counters[1],counters[2])
            memory=re.findall(r'^memory hash (.*)$',chunk,re.M); self.assertEqual(len(memory),2); self.assertEqual(*memory)
        self.same_frames([view(r) for r in self.runs(chunks[0])],[view(r) for r in self.runs(chunks[1])],'independent owners with local restore')
        fresh=self.play('--runs',16,'--no-vfs','--set','puae6_machine=CDTV','--do',f'0:loadfile:{cut}:pos=1000',
                        '--do','0:peek:0:1048576','--do','16:memory','--do','16:peek:0:1048576','--do',f'16:savefile:{cold}')
        self.assertIn(f'state load {cut} pos=1000 ok=1',fresh)
        self.assertEqual([view(r) for r in self.runs(chunks[0])[1000:1016]],[view(r) for r in self.runs(fresh)])
        self.assertEqual(self.markers(fresh),self.markers(chunks[0])[:2])
        self.assertEqual(re.findall(r'^memory hash (.*)$',fresh,re.M),re.findall(r'^memory hash (.*)$',chunks[0],re.M)[:1])
        self.assertEqual(end.read_bytes(),cold.read_bytes())

    def cd_state(self,path):
        data=path.read_bytes(); self.assertEqual(data[:8],b'P6LRSTAT')
        version,header,native_size,section=struct.unpack_from('<IIQI',data,8)
        self.assertEqual((version,header),(8,64)); start=header+section
        self.assertEqual(data[32:64],hashlib.sha256(data[header:start]).digest())
        native=data[start:start+native_size]; self.assertEqual(native[:8],b'P6NATIVE')
        self.assertFalse(any(data[start+native_size:]))
        version,header=struct.unpack_from('<II',native,8); self.assertEqual((version,header),(1,336))
        payload,count=struct.unpack_from('<QI',native,40); self.assertEqual(len(native),header+payload)
        at=header; found=[]
        for _ in range(count):
            tag,version,size=struct.unpack_from('<4sIQ',native,at); at+=16
            self.assertLessEqual(at+size,len(native))
            if tag==b'P6CT':
                self.assertEqual(version,1); body=native[at:at+size]
                # P6CTv1 semantic LE body: cd_media at72, TOC at37112;
                # device follows its 32-byte header and 103 16-byte entries.
                self.assertGreaterEqual(len(body),38852)
                found.append((struct.unpack_from('<I',body,72)[0],
                              struct.unpack_from('<I',body,37132)[0],
                              struct.unpack_from('<32s7I',body,38792)))
            at+=size
        self.assertEqual(at,len(native)); self.assertEqual(len(found),1)
        return found[0]

    def test_catalog_change_waits_for_observed_absence_and_restores_pending(self):
        work=self.work(); other=work/'second.iso'
        other.write_bytes(self.iso.read_bytes()+bytes(17*2048))
        content=work/'catalog (CDTV).m3u'
        content.write_text(f'{self.iso}|A\n{other}|B\n{self.iso}|Alias A\n')
        cut,end,again,cold=(work/n for n in ('pending','end','again','cold'))
        # The first disc must already be recognized before testing its removal.
        out=self.play('--runs',540,'--do',f'299:savefile:{work/"initial"}','--disk','300:eject','--disk','300:index=1','--disk','300:insert',
                      '--do',f'300:savefile:{cut}','--do',f'420:savefile:{end}',
                      '--do','420:memory','--do',f'420:loadfile:{cut}:pos=300',
                      '--do',f'540:savefile:{again}','--do','540:memory',content=content)
        for operation in ('eject','index=1','insert'):
            self.assertIn(operation+' result=1',out)
        initial=self.cd_state(work/'initial')
        self.assertEqual((initial[0],initial[1],initial[2][1:4]),(1,self.iso.stat().st_size//2048,(1,1,1)))
        media,toc,device=self.cd_state(cut)
        self.assertEqual(media,1); self.assertEqual(toc,self.iso.stat().st_size//2048)
        self.assertEqual(device[1:4],(1,0,1)) # bound, absent, same open unit
        media,toc,device=self.cd_state(end)
        self.assertEqual((media,toc,device[1:4]),(1,other.stat().st_size//2048,(1,1,1)))
        raw=other.read_bytes()
        self.assertEqual(device[0],hashlib.sha256(b'P6ISO1'+struct.pack('<Q',len(raw))+hashlib.sha256(raw).digest()).digest())
        view=lambda r:tuple(r[k] for k in ('unit','ticks','hash','ahash','audio'))
        rows=self.runs(out); self.assertEqual(len(rows),540)
        self.same_frames([view(r) for r in rows[300:420]],[view(r) for r in rows[420:540]],'pending change local')
        self.assertEqual(end.read_bytes(),again.read_bytes())
        memory=re.findall(r'^memory hash (.*)$',out,re.M); self.assertEqual(len(memory),2); self.assertEqual(*memory)
        fresh=self.play('--no-vfs','--set','puae6_machine=CDTV','--runs',120,
                        '--do',f'0:loadfile:{cut}:pos=300','--do',f'120:savefile:{cold}',
                        '--do','120:memory',content=content)
        self.assertIn(f'state load {cut} pos=300 ok=1',fresh)
        self.same_frames([view(r) for r in rows[300:420]],[view(r) for r in self.runs(fresh)],'pending change cold')
        self.assertEqual(end.read_bytes(),cold.read_bytes())
        self.assertEqual(re.findall(r'^memory hash (.*)$',fresh,re.M),memory[:1])
        # A new frontend request can cancel an insertion still waiting on the poll.
        cancelled=work/'cancelled'
        cancelled_out=self.play('--initial',f'2:{self.iso}','--runs',420,'--do','1:images',
            '--disk','300:eject','--disk','300:index=1','--disk','300:insert','--disk','301:eject',
            '--do',f'420:savefile:{cancelled}',content=content)
        self.assertIn('state images count=3 index=2 eject=0',cancelled_out)
        absent,_,dev=self.cd_state(cancelled); self.assertEqual((absent,dev[1:4]),(0,(1,0,1)))
        self.assertEqual(dev[0],self.cd_state(cut)[2][0])
        # A different ordered catalog must fail before either owner is mutated.
        foreign=work/'foreign.m3u'; foreign.write_text(f'{other}\n{self.iso}\n{other}\n')
        before,after=work/'before',work/'after'
        refused=self.play('--set','puae6_machine=CDTV','--runs',1,'--do',f'1:savefile:{before}',
            '--do',f'1:loadfile:{cut}','--do',f'1:savefile:{after}',content=foreign)
        self.assertIn(f'state load {cut} exact ok=0',refused); self.assertEqual(before.read_bytes(),after.read_bytes())

    def test_model_firmware_vfs_and_fixed_media_refusals(self):
        fixed=('--set','puae6_machine=CDTV','--runs',2)
        for flags,content in (((),self.iso),(fixed,self.iso),(fixed,None)):
            self.assertIn('machine CDTV',self.play(*flags,'--runs',2,content=content))
        # No tags means the normal direct-CD fallback CD32; explicit incompatible models fail.
        no_tags=self.play('--set','puae6_filename_tags=disabled',expect=3)
        self.assertIn('machine CD32',no_tags)
        self.play('--set','puae6_machine=A1200',expect=3)
        self.play(*fixed,'--set','puae6_kickstart=kick34005.A500',expect=3)
        self.play(*fixed,'--vfs-no-seek',expect=3)
        for extension,size in (('adf',901120),('hdf',512*32)):
            path=self.work()/('unsupported.'+extension); path.write_bytes(bytes(size))
            self.assertIn('CD catalog or no content',self.play(*fixed,content=path,expect=3))
        playlist=self.work()/'two.m3u'; playlist.write_text(str(self.iso)+'\n'+str(self.iso)+'\n')
        self.assertIn('machine CDTV',self.play(*fixed,content=playlist))
        missing=self.work(); (missing/'kick34005.A500').symlink_to(self.system_dir/'kick34005.A500')
        self.assertIn('extended ROM',self.play(*fixed,system=missing,expect=3))
        (missing/'kick34005.CDTV').write_bytes(bytes(262144))
        self.play(*fixed,system=missing,expect=3)
        (missing/'CDTV-Extended-ROMv2.7.rom').symlink_to(self.system_dir/'CDTV-Extended-ROMv2.7.rom')
        self.assertIn('machine CDTV',self.play(*fixed,'--no-vfs',system=missing))
        (missing/'kick34005.A500').unlink(); (missing/'kick34005.A500').write_bytes(bytes(262144))
        self.play(*fixed,system=missing,expect=3)
        out=self.play(*fixed,'--do','1:save:before',*[part for op in ('add','remove=0',f'replace=0:{self.iso}')
                      for part in ('--do','1:disk:'+op)],'--do','1:images','--do','1:save:after')
        rows=re.findall(r'^disk 1 .* result=(\d) eject=(\d) index=(\d+) count=(\d+)$',out,re.M)
        self.assertEqual(rows,[('0','0','0','1')]*3)
        hashes=re.findall(r'^state save (?:before|after) ok=1 .* hash=(\w+)',out,re.M)
        self.assertEqual(len(hashes),2); self.assertEqual(*hashes)
        rom=(self.system_dir/'CDTV-Extended-ROMv2.7.rom').read_bytes()+(self.system_dir/'kick34005.A500').read_bytes()
        cli=subprocess.run([str(self.headless),'--run','/dev/stdin','--machine','cdtv','--units','2'],input=rom,capture_output=True,timeout=30)
        self.assertEqual(cli.returncode,0,cli.stderr.decode()); self.assertIn(b'unit 2 ticks=',cli.stdout)
