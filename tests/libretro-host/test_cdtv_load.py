"""Stock CDTV through the public native/libretro API; original CDFS/HUNK, no injected state."""
from pathlib import Path
import re
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
        self.assertEqual([view(r) for r in self.runs(chunks[0])],[view(r) for r in self.runs(chunks[1])])
        fresh=self.play('--runs',16,'--no-vfs','--set','puae6_machine=CDTV','--do',f'0:loadfile:{cut}:pos=1000',
                        '--do','0:peek:0:1048576','--do','16:memory','--do','16:peek:0:1048576','--do',f'16:savefile:{cold}')
        self.assertIn(f'state load {cut} pos=1000 ok=1',fresh)
        self.assertEqual([view(r) for r in self.runs(chunks[0])[1000:1016]],[view(r) for r in self.runs(fresh)])
        self.assertEqual(self.markers(fresh),self.markers(chunks[0])[:2])
        self.assertEqual(re.findall(r'^memory hash (.*)$',fresh,re.M),re.findall(r'^memory hash (.*)$',chunks[0],re.M)[:1])
        self.assertEqual(end.read_bytes(),cold.read_bytes())

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
            self.assertIn('fixed CD or no content',self.play(*fixed,content=path,expect=3))
        playlist=self.work()/'two.m3u'; playlist.write_text(str(self.iso)+'\n'+str(self.iso)+'\n')
        self.assertIn('one fixed initial CD',self.play(*fixed,content=playlist,expect=3))
        missing=self.work(); (missing/'kick34005.A500').symlink_to(self.system_dir/'kick34005.A500')
        self.assertIn('extended ROM',self.play(*fixed,system=missing,expect=3))
        (missing/'kick34005.CDTV').write_bytes(bytes(262144))
        self.play(*fixed,system=missing,expect=3)
        (missing/'CDTV-Extended-ROMv2.7.rom').symlink_to(self.system_dir/'CDTV-Extended-ROMv2.7.rom')
        self.assertIn('machine CDTV',self.play(*fixed,'--no-vfs',system=missing))
        (missing/'kick34005.A500').unlink(); (missing/'kick34005.A500').write_bytes(bytes(262144))
        self.play(*fixed,system=missing,expect=3)
        out=self.play(*fixed,'--do','1:save:before',*[part for op in ('eject','index=0','add','remove=0',f'replace=0:{self.iso}','insert')
                      for part in ('--do','1:disk:'+op)],'--do','1:images','--do','1:save:after')
        rows=re.findall(r'^disk 1 .* result=(\d) eject=(\d) index=(\d+) count=(\d+)$',out,re.M)
        self.assertEqual(rows,[('0','0','0','1')]*6)
        hashes=re.findall(r'^state save (?:before|after) ok=1 .* hash=(\w+)',out,re.M)
        self.assertEqual(len(hashes),2); self.assertEqual(*hashes)
        rom=(self.system_dir/'CDTV-Extended-ROMv2.7.rom').read_bytes()+(self.system_dir/'kick34005.A500').read_bytes()
        cli=subprocess.run([str(self.headless),'--run','/dev/stdin','--machine','cdtv','--units','2'],input=rom,capture_output=True,timeout=30)
        self.assertEqual(cli.returncode,0,cli.stderr.decode()); self.assertIn(b'unit 2 ticks=',cli.stdout)
