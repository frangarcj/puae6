"""Native CD image factory and target-local metadata compilation inventory."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from tests.media import test_cd_ccd, test_cd_nrg, test_mds_metadata as mds_ref
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which('clang++'), 'clang++ unavailable')
class CdImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'build').mkdir(exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(prefix='cd-image-',dir=ROOT/'build'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); entries=json.loads((ROOT/'patches/winuae/series.json').read_text())['patches']
        for identifier in ('0355-cdrom-mode1-sync','0356-cue-metadata-only','0357-ccd-metadata-only','0361-nrg-metadata-only','0388-mds-metadata-only','0389-nrg-dao-mode3-2336'):
            e=next(x for x in entries if x['id']==identifier); r=e['files'][0]; target=cls.work/r['path']
            if not target.exists(): target.write_bytes((ROOT/'upstream/winuae'/r['path']).read_bytes())
            patch=(ROOT/e['file']).read_bytes()
            assert hashlib.sha256(target.read_bytes()).hexdigest()==r['before_sha256']
            assert hashlib.sha256(patch).hexdigest()==e['sha256']
            mat._apply(patch,cls.work,True); mat._apply(patch,cls.work,False)
            assert hashlib.sha256(target.read_bytes()).hexdigest()==r['after_sha256']
        common=['clang++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fPIC','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-I'+str(ROOT/'ports/portable/media')]
        objects=[]
        for kind in ('cue','ccd','nrg','mds'):
            obj=cls.work/(kind+'.o'); objects.append(str(obj))
            out=subprocess.run(common+['-DP6_'+kind.upper()+'_METADATA','-c',str(cls.work/'blkdev_cdimage.cpp'),'-o',str(obj)],capture_output=True,text=True,timeout=60)
            if out.returncode: raise AssertionError(out.stderr)
        sources=['tests/media/cd_image_probe.cpp']+['adapters/libretro/'+name+'.cpp' for name in ('cd_image','cd_cue','cd_ccd','cd_nrg','cd_mds')]
        sources+=['ports/portable/media/'+name for name in ('cd_source.cpp','cd_wave.cpp','cd_disc.cpp','cd_read.cpp','cd_subchannel.cpp','cd_utilities.cpp','sha256.c')]
        cls.exe=cls.work/'probe'
        out=subprocess.run(common+['-DUAE_SYSDEPS_H','-include','cstring','-I'+str(ROOT/'ports/portable'),'-I'+str(ROOT/'references/libretro-api/include'),'-I'+str(ROOT/'upstream/winuae/include'),'-x','c++']+
                           [str(ROOT/p) for p in sources]+[str(cls.work/'cdrom.cpp'),'-x','none']+objects+['-o',str(cls.exe)],capture_output=True,text=True,timeout=60)
        if out.returncode: raise AssertionError(out.stderr)

    def run_image(self,path,*,code=0,vfs=False,rebind=None):
        env={k:v for k,v in os.environ.items() if k!='P6_CD_IMAGE_VFS'}
        if vfs: env['P6_CD_IMAGE_VFS']='1'
        out=subprocess.run([str(self.exe),str(path)]+([] if rebind is None else [str(rebind)]),capture_output=True,text=True,timeout=60,env=env)
        self.assertEqual(code,out.returncode,out.stderr+out.stdout[-300:]); return out.stdout.splitlines()

    def iso(self,name='disc.ISO',data=None):
        path=self.work/name; payload=bytes(range(256))*32 if data is None else data; path.write_bytes(payload); return path,payload

    def test_iso_identity_complete_selection_case_and_vfs_lifetime(self):
        path,payload=self.iso(); lines=self.run_image(path)
        identity=hashlib.sha256(b'P6ISO1'+struct.pack('<Q',len(payload))+hashlib.sha256(payload).digest()).hexdigest()
        self.assertEqual(lines[0],f'disc 1 4 1 {identity}')
        self.assertEqual(lines[1],f'source {len(payload)} 2048 {hashlib.sha256(payload).hexdigest()}')
        self.assertEqual(lines,self.run_image(path,vfs=True)); self.assertEqual(lines[-1],'closed')
        same,_=self.iso('renamed.iSo'); changed,_=self.iso('changed.iso',payload[:-1]+bytes((payload[-1]^1,)))
        self.assertTrue(self.run_image(path,rebind=same,vfs=True)[-2].startswith('rebind 0 '))
        self.assertTrue(self.run_image(path,rebind=changed,vfs=True)[-2].startswith('rebind 2 '))

    def test_delegates_all_formats_with_parsers_together_and_no_fallback(self):
        path,payload=self.iso(); cue=self.work/'disc.CuE'; cue.write_text('FILE disc.ISO BINARY\nTRACK 1 MODE1/2048\nINDEX 1 00:00:00\n')
        iso_sectors=[r for r in self.run_image(path) if r.startswith('sector ')]
        cue_lines=self.run_image(cue,vfs=True); self.assertEqual(iso_sectors,[r for r in cue_lines if r.startswith('sector ')])
        ccd=test_cd_ccd.CdCcdTests.fixture(self,'ccd')[0]; renamed=ccd.with_suffix('.CcD'); ccd.rename(renamed)
        nrg=test_cd_nrg.CdNrgTests.fixture(self,'dao')[0]; nrg_case=nrg.with_suffix('.NrG'); nrg.rename(nrg_case)
        mode3=test_cd_nrg.CdNrgTests.fixture(self,'mode3',modes=(3,7))[0]
        for image,tracks in ((renamed,3),(nrg_case,7),(mode3,2)):
            lines=self.run_image(image); self.assertEqual(tracks,int(lines[0].split()[1])); self.assertEqual(lines,self.run_image(image,vfs=True))
            self.assertTrue(self.run_image(image,rebind=image,vfs=True)[-2].startswith('rebind 0 '))
        for suffix in ('bin','mdf','chd','iso.trailing'):
            unknown,_=self.iso('unsupported.'+suffix); self.assertEqual(self.run_image(unknown,code=2,vfs=True),['error 5 opens 0'])
        bad,_=self.iso('bad.cue'); self.assertTrue(self.run_image(bad,code=2,vfs=True)[0].startswith('error '))
        bad,_=self.iso('bad.mds'); self.assertTrue(self.run_image(bad,code=2,vfs=True)[0].startswith('error '))
        bad,_=self.iso('bad.nrg'); self.assertTrue(self.run_image(bad,code=2,vfs=True)[0].startswith('error '))
        for name,data in (('empty.iso',b''),('short.iso',b'X')):
            bad,_=self.iso(name,data); self.assertEqual(self.run_image(bad,code=2,vfs=True),['error 3 opens 1'])

    def test_mds_delegates_with_four_parsers_identity_vfs_and_fresh_owner(self):
        path,payload=self.iso(); data,_,at=mds_ref.mds(modes=((0xaa,2048,0),),name='disc.ISO')
        struct.pack_into('<i',data,92,4); data[112+160+9:112+160+12]=bytes((0,2,4))
        struct.pack_into('<Q',data,at['track']+40,0); struct.pack_into('<II',data,at['extra'],150,4)
        descriptor=self.work/'disc.MdS'; descriptor.write_bytes(data)
        lines=self.run_image(descriptor,vfs=True); self.assertEqual(lines,self.run_image(descriptor))
        self.assertEqual([r for r in self.run_image(path) if r.startswith('sector ')],[r for r in lines if r.startswith('sector ')])
        renamed=self.work/'renamed.mds'; renamed.write_bytes(data)
        self.assertTrue(self.run_image(descriptor,rebind=renamed,vfs=True)[-2].startswith('rebind 0 '))
        data[60]^=1; renamed.write_bytes(data)
        self.assertTrue(self.run_image(descriptor,rebind=renamed,vfs=True)[-2].startswith('rebind 2 '))

    def test_cmake_has_four_target_local_pic_compilations_without_building(self):
        if not shutil.which('cmake') or not shutil.which('ninja'): self.fail('cmake and ninja required to inspect the real native graph')
        build=self.work/'configure'
        out=subprocess.run(['cmake','-S',str(ROOT),'-B',str(build),'-G','Ninja'],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,out.returncode,out.stderr+out.stdout[-300:])
        entries=json.loads((build/'compile_commands.json').read_text())
        parsers=[e['command'] for e in entries if e['file'].endswith('/blkdev_cdimage.cpp')]
        self.assertEqual(4,len(parsers))
        for kind in ('CUE','CCD','NRG','MDS'):
            self.assertEqual(1,sum('-DP6_'+kind+'_METADATA' in command for command in parsers))
        for command in parsers:
            self.assertEqual(1,sum('-DP6_'+kind+'_METADATA' in command for kind in ('CUE','CCD','NRG','MDS'))); self.assertIn('-fPIC',command)
        self.assertTrue(all('-fPIC' in e['command'] for e in entries if e['file'].endswith('/cd_image.cpp')))
