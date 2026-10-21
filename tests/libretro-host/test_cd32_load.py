"""Direct CD sessions through retro_*; local firmware stays outside the tree."""
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from tests.build_support import ROOT, build_targets
from tests.media import test_mds_metadata as mds_ref, test_chd_reader as chd_ref, test_cd_chd as chd_provider
from tests.media import test_cd_nrg as nrg_ref


class Cd32Session(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        main, ext = os.environ.get('P6_CD32_ROM_MAIN'), os.environ.get('P6_CD32_ROM_EXT')
        if not main or not ext:
            raise unittest.SkipTest('BLOCKED: authorized P6_CD32_ROM_MAIN and P6_CD32_ROM_EXT required')
        built = build_targets(ROOT / 'build/state-opt', ['p6_libretro_host', 'puae6_libretro', 'p6_headless'], optimized=True)
        cls.host, cls.core = built['p6_libretro_host'], built['puae6_libretro']
        cls.headless = built['p6_headless']
        cls.tmp = tempfile.TemporaryDirectory(prefix='cd32-load-', dir=ROOT / 'build')
        cls.counter = 0
        cls.directory = Path(cls.tmp.name)
        cls.system_dir = cls.directory / 'system'; cls.system_dir.mkdir()
        (cls.system_dir / 'kick40060.CD32').symlink_to(Path(main).resolve())
        (cls.system_dir / 'kick40060.CD32.ext').symlink_to(Path(ext).resolve())
        cls.iso = cls.directory / 'public.iso'; cls.iso.write_bytes(bytes(512 * 2048))
        cls.other = cls.directory / 'other.iso'; cls.other.write_bytes(b'x' * (512 * 2048))

    def run_cd(self, *flags, content=None, system=None, expect=0, timeout=180):
        result = subprocess.run([str(self.host), str(self.core), '--system', str(system or self.system_dir),
                                 '--content', str(content or self.iso), *map(str, flags)], capture_output=True, text=True, timeout=timeout)
        self.assertEqual(expect, result.returncode, result.stdout[-2000:] + result.stderr[-3000:])
        self.assertIn('vfs closed live=0', result.stdout)
        self.assertNotIn('VIOLATION', result.stdout)
        return result.stdout


class Cd32NrgMultisessionTests(Cd32Session):
    def test_dao_multisession_direct_load_restore_cold_and_identity(self):
        work=self.work()
        content,raw,_,directory,blocks=nrg_ref.nrg_multisession_fixture(work,'public')
        renamed=nrg_ref.nrg_multisession_fixture(work,'renamed')[0]
        cut,end,again,fresh=(work/name for name in ('cut','end','again','fresh'))
        # Original synthetic audio/data, not an application or a boot claim.
        original=self.run_cd('--runs',332,'--cycles',2,'--do','300:save:s','--do',f'300:savefile:{cut}',
                             '--do','316:memory','--do',f'316:savefile:{end}','--do','316:load:s',
                             '--do','332:memory','--do',f'332:savefile:{again}',content=content)
        self.assertIn('machine CD32',original); self.assertEqual(end.read_bytes(),again.read_bytes())
        view=lambda r:tuple(r[k] for k in ('unit','ticks','hash','ahash','audio'))
        chunks=re.split(r'cycle \d+ end[^\n]*\n',original)[:2]
        futures=[]
        for chunk in chunks:
            runs=self.runs(chunk); self.assertEqual(len(runs),332)
            future=[view(r) for r in runs[300:316]]; futures.append(future)
            self.assertEqual(future,[view(r) for r in runs[316:332]])
            memory=re.findall(r'^memory hash (.*)$',chunk,re.M); self.assertEqual(len(memory),2); self.assertEqual(*memory)
        self.assertEqual(futures[0],futures[1])
        self.assertEqual([view(r) for r in self.runs(chunks[0])],[view(r) for r in self.runs(chunks[1])])
        accepted=self.run_cd('--no-vfs','--set','puae6_machine=CD32','--runs',16,
                             '--do',f'0:loadfile:{cut}:pos=300','--do','0:images',
                             '--do','16:memory','--do',f'16:savefile:{fresh}',content=renamed)
        self.assertIn(f'state load {cut} pos=300 ok=1',accepted)
        self.assertIn('state images count=1 index=0 eject=0',accepted)
        self.assertEqual(futures[0],[view(r) for r in self.runs(accepted)])
        self.assertEqual(re.findall(r'^memory hash (.*)$',accepted,re.M),re.findall(r'^memory hash (.*)$',chunks[0],re.M)[:1])
        self.assertEqual(end.read_bytes(),fresh.read_bytes())
        expected_id=None
        for path in (cut,end,again,fresh):
            device,body=Cd32PlaylistTests.device(self,path,controller=True)
            if expected_id is None: expected_id=device[0]
            self.assertEqual(device[:3],(expected_id,1,1))
            # P6AK semantic LE codec, shared with the HTOA test below.
            at=8+156+64+64+1339+16
            self.assertEqual(struct.unpack_from('<8i',body,at),(1,1,3,3,0,39,3,6))
            entries=[struct.unpack_from('<8B2i',body,at+32+n*16) for n in range(6)]
            self.assertEqual([(e[3],e[4],e[-1],e[1]) for e in entries],
                             [(160,1,0,0),(1,1,0,0),(2,2,6,4),(3,3,32,4),(161,3,39,0),(162,0,39,0)])
        source=content.read_bytes()
        for n,at in enumerate((directory+blocks[1]['DAO']+12,raw[2][1]+17)):
            changed=bytearray(source); changed[at]^=1
            foreign=work/('foreign'+str(n)+'.nrg'); foreign.write_bytes(changed)
            before,after=work/('before'+str(n)),work/('after'+str(n))
            refused=self.run_cd('--runs',1,'--do',f'1:savefile:{before}','--do',f'1:loadfile:{cut}',
                                '--do',f'1:savefile:{after}',content=foreign)
            self.assertIn(f'state load {cut} exact ok=0',refused); self.assertEqual(before.read_bytes(),after.read_bytes())


class Cd32LoadTests(Cd32Session):
    def test_real_reads_replay_runahead_and_fresh_owner(self):
        content = os.environ.get('P6_CD_CUE_FILE', str(self.iso))
        out = self.run_cd('--runs', 345, '--cycles', 2, '--do', '300:save:s', '--do', '315:memory',
                          '--do', '330:load:s', '--do', '345:memory', '--ahead', '305:2', content=content)
        self.assertIn('machine CD32', out)
        self.assertGreater(int(re.search(r'vfs closed live=0 run_reads=(\d+)', out)[1]), 0)
        chunks = re.split(r'cycle \d+ end[^\n]*\n', out)[:2]
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        for chunk in chunks:
            runs = self.runs(chunk)
            self.assertEqual(345, len(runs))
            self.assertEqual([view(r) for r in runs[300:315]], [view(r) for r in runs[330:345]])
            self.assertIn('state load s exact ok=1', chunk)
            memory = re.findall(r'^memory hash (.*)$', chunk, re.M)
            self.assertEqual(2, len(memory)); self.assertEqual(memory[0], memory[1])
        self.assertEqual([view(r) for r in self.runs(chunks[0])], [view(r) for r in self.runs(chunks[1])])

    def test_same_cd_in_new_process_and_other_cd_rejected_without_mutation(self):
        state = self.directory / 'cd.state'
        original = self.run_cd('--runs', 301, '--do', f'300:savefile:{state}')
        accepted = self.run_cd('--runs', 2, '--do', f'1:loadfile:{state}:pos=300', '--no-vfs', '--set', 'puae6_machine=CD32')
        self.assertIn(f'state load {state} pos=300 ok=1', accepted)
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        self.assertEqual(view(self.runs(original)[300]), view(self.runs(accepted)[1]))
        bad = self.run_cd('--runs', 3, '--do', '1:save:before', '--do', f'1:loadfile:{state}',
                          '--do', '1:save:after', content=self.other)
        self.assertIn(f'state load {state} exact ok=0', bad)
        hashes = re.findall(r'^state save (?:before|after) ok=1 .* hash=(\w+)', bad, re.M)
        self.assertEqual(2, len(hashes)); self.assertEqual(*hashes)

    def test_headless_accepts_combined_firmware_without_claiming_cd_input(self):
        rom = (self.system_dir / 'kick40060.CD32.ext').read_bytes() + (self.system_dir / 'kick40060.CD32').read_bytes()
        result = subprocess.run([str(self.headless), '--run', '/dev/stdin', '--machine', 'cd32', '--units', '2'],
                                input=rom, capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr.decode())
        self.assertIn(b'unit 2 ticks=', result.stdout)

    def test_nvram_sram_persistence_reset_and_snapshot_use_the_same_backing(self):
        work = self.work(); seed = work / 'seed.srm'; durable = work / 'saved.srm'; restored = work / 'restored.srm'
        initial = bytes((i * 17 + 9) & 255 for i in range(1024)); seed.write_bytes(initial)
        out = self.run_cd('--runs', 7, '--do', f'0:sramload:{seed}', '--do', '1:save:s',
                          '--do', '2:srampoke:13:cc', '--do', '3:reset', '--do', f'4:sramsave:{durable}',
                          '--do', '5:load:s', '--do', f'7:sramsave:{restored}')
        changed = bytearray(initial); changed[13] = 0xcc
        self.assertEqual(bytes(changed), durable.read_bytes())
        self.assertEqual(initial, restored.read_bytes())
        pointers = re.findall(r'sram size=1024 ptr=(\S+)', out)
        self.assertEqual(4, len(pointers)); self.assertEqual(1, len(set(pointers)))
        imported = work / 'imported.srm'
        self.run_cd('--runs', 1, '--do', f'0:sramload:{durable}', '--do', f'1:sramsave:{imported}')
        self.assertEqual(durable.read_bytes(), imported.read_bytes())

    def assert_fast_memory_restore_and_stock_refusal(self, flags, content):
        work = self.work(); state = work / 'cd32fr.state'
        out = self.run_cd(*flags, '--runs', 315,
                          '--do', '300:poke:0x200100:1234abcd', '--do', '300:poke:0x9ffffc:a9876543',
                          '--do', f'300:savefile:{state}', '--do', '315:memory', content=content)
        self.assertRegex(out, r'memory-desc \d+ name=FAST start=0x200000 len=0x800000 ')
        manual = self.run_cd('--set', 'puae6_machine=CD32', '--set', 'puae6_fast_size=MB8', '--runs', 16,
                             '--do', f'1:loadfile:{state}:pos=300', '--do', '1:peek:0x200100:4',
                             '--do', '1:peek:0x9ffffc:4', '--do', '16:memory', content=content)
        self.assertIn('peek 0x200100 4 1234abcd', manual); self.assertIn('peek 0x9ffffc 4 a9876543', manual)
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        self.assertEqual([view(r) for r in self.runs(out)[300:315]], [view(r) for r in self.runs(manual)[1:16]])
        self.assertEqual(re.findall(r'^memory hash (.*)$', out, re.M), re.findall(r'^memory hash (.*)$', manual, re.M))
        refused = self.run_cd('--set', 'puae6_machine=CD32', '--runs', 1, '--do', '1:save:before', '--do', f'1:loadfile:{state}',
                              '--do', '1:save:after', content=content)
        self.assertIn(f'state load {state} exact ok=0', refused)
        hashes = re.findall(r'^state save (?:before|after) ok=1 .* hash=(\w+)', refused, re.M)
        self.assertEqual(len(hashes), 2); self.assertEqual(*hashes)

    def test_cd32fr_preset_fast_memory_restore_and_stock_refusal(self):
        self.assert_fast_memory_restore_and_stock_refusal(('--set', 'puae6_preset=cd32_fast8'),
                                                         os.environ.get('P6_CD_CUE_FILE', str(self.iso)))
        rom = (self.system_dir / 'kick40060.CD32.ext').read_bytes() + (self.system_dir / 'kick40060.CD32').read_bytes()
        cli = subprocess.run([str(self.headless), '--run', '/dev/stdin', '--machine', 'cd32', '--fast', '8192', '--units', '2'],
                             input=rom, capture_output=True, timeout=30)
        self.assertEqual(cli.returncode, 0, cli.stderr.decode())
        self.assertIn(b'unit 2 ticks=', cli.stdout)

    def test_direct_cd_filename_tags_fast_memory_and_restore(self):
        def disc(tag):
            path = self.work() / f'Disc {tag}.iso'; path.symlink_to(self.iso)
            return path
        for tag in ('(CD32FR)', 'FastRAM'):
            with self.subTest(tag=tag):
                self.assert_fast_memory_restore_and_stock_refusal((), disc(tag))
        fr = disc('(CD32FR)')
        for content, flags in ((disc('(CD32)'), ()), (disc('(CD32NF)'), ()),
                               (fr, ('--set', 'puae6_filename_tags=disabled')),
                               (fr, ('--set', 'puae6_machine=CD32')),
                               (fr, ('--set', 'puae6_fast_size=None'))):
            with self.subTest(content=content.name, flags=flags):
                out = self.run_cd(*flags, '--runs', 2, content=content)
                self.assertIn('machine CD32', out)
                self.assertNotRegex(out, r'memory-desc \d+ name=FAST ')
        preset = self.run_cd('--set', 'puae6_preset=cd32_fast8', '--runs', 2, content=disc('(CD32NF)'))
        self.assertRegex(preset, r'memory-desc \d+ name=FAST start=0x200000 len=0x800000 ')
        unsupported = self.run_cd('--set', 'puae6_fast_size=MB4', content=fr, expect=3)
        self.assertIn('either no Fast RAM or the CD32FR 8 MiB board', unsupported)
        incompatible = self.run_cd('--set', 'puae6_machine=A1200', content=fr, expect=3)
        self.assertIn('requires Automatic or CD32 machine', incompatible)

    def test_invalid_model_firmware_and_vfs_fail_and_release_handles(self):
        self.run_cd('--set', 'puae6_machine=A1200', expect=3)
        floppy = self.directory / 'not-a-cd.adf'; floppy.write_bytes(bytes(901120))
        self.assertIn('requires a direct CD', self.run_cd('--set', 'puae6_machine=CD32', content=floppy, expect=3))
        self.run_cd('--vfs-no-seek', expect=3)
        missing = self.work()
        (missing / 'kick40060.CD32').symlink_to(self.system_dir / 'kick40060.CD32')
        self.assertIn('extended ROM', self.run_cd(system=missing, expect=3))
        # A present but corrupt preferred alias must not hide a valid alternate.
        (missing / 'kick40060.CD32.ext').write_bytes(bytes(512 * 1024))
        (missing / 'amiga-os-310-cd32-ext.rom').symlink_to(self.system_dir / 'kick40060.CD32.ext')
        out = self.run_cd('--runs', 2, '--disk', '1:add', '--disk', '2:eject', system=missing)
        self.assertRegex(out, r'(?m)^disk 1 add result=0 .* count=1$')
        self.assertRegex(out, r'(?m)^disk 2 eject result=1 .* count=1$')
        (missing / 'kick40060.CD32').unlink()
        (missing / 'kick40060.CD32').write_bytes(bytes(512 * 1024))
        self.run_cd(system=missing, expect=3)


class Cd32MdsTests(Cd32Session):
    def test_mds_public_snapshot_new_process_and_media_identity(self):
        def disc(name, mutation=None):
            folder=self.directory/name; folder.mkdir()
            path=folder/(name+'.MdS'); data,_,at=mds_ref.mds(modes=((0xaa,2048,0),),name='*.mdf')
            # Synthetic 150-sector pregap, then 512 data sectors at byte17; no MDF pregap prefix.
            struct.pack_into('<i',data,92,512); data[112+160+9:112+160+12]=bytes((0,8,62))
            struct.pack_into('<Q',data,at['track']+40,17); struct.pack_into('<II',data,at['extra'],150,512)
            payload=b'offset padding...'+self.iso.read_bytes()+b'unmapped tail'
            if mutation=='mds': data[60]^=1
            if mutation=='mdf': payload=bytes((payload[0]^1,))+payload[1:]
            path.write_bytes(data); path.with_suffix('.mdf').write_bytes(payload); return path
        state=self.directory/'mds.state'; end=self.directory/'end.state'; fresh=self.directory/'fresh.state'
        content=disc('original (CD32)')
        original=self.run_cd('--runs',315,'--do',f'300:savefile:{state}','--do',f'315:savefile:{end}',content=content)
        self.assertIn('machine CD32',original)
        extensions=re.search(r'extensions=(\S+)',original)[1].split('|')
        self.assertIn('mds',extensions); self.assertIn('chd',extensions); self.assertNotIn('mdf',extensions)
        self.assertGreater(int(re.search(r'vfs closed live=0 run_reads=(\d+)',original)[1]),0)
        accepted=self.run_cd('--runs',16,'--do',f'1:loadfile:{state}:pos=300','--do',f'16:savefile:{fresh}',
                             '--no-vfs','--set','puae6_machine=CD32',content=disc('renamed'))
        self.assertIn(f'state load {state} pos=300 ok=1',accepted)
        view=lambda r:(r['unit'],r['ticks'],r['hash'],r['ahash'],r['audio'])
        self.assertEqual([view(r) for r in self.runs(original)[300:315]],[view(r) for r in self.runs(accepted)[1:16]])
        self.assertEqual(end.read_bytes(),fresh.read_bytes(),'all native/adapter state and CD identity must match')
        for mutation in ('mds','mdf'):
            before=self.directory/(mutation+'-before.state'); after=self.directory/(mutation+'-after.state')
            rejected=self.run_cd('--runs',1,'--do',f'1:savefile:{before}','--do',f'1:loadfile:{state}',
                                 '--do',f'1:savefile:{after}',content=disc(mutation,mutation))
            self.assertIn(f'state load {state} exact ok=0',rejected)
            self.assertEqual(before.read_bytes(),after.read_bytes(),'rejection must not mutate native or adapter state')


class Cd32PlaylistTests(Cd32Session):
    def playlist(self, names=('A', 'B', 'Alias'), order=(0, 1, 0)):
        work = self.work(); paths = []
        for name, index in zip(names, order):
            path = work / (name + '.iso'); path.write_bytes((self.iso, self.other)[index].read_bytes()); paths.append(path)
        playlist = work / 'set.m3u'
        playlist.write_text(''.join(f'{p.name}|Disc {i}\n' for i, p in enumerate(paths)))
        return playlist, paths

    def device(self, path, *, controller=False):
        data = path.read_bytes()
        self.assertEqual(b'P6LRSTAT', data[:8])
        version, header, native_bytes, section_bytes = struct.unpack_from('<IIQI', data, 8)
        self.assertEqual((8, 64), (version, header))
        native_at = header + section_bytes; end = native_at + native_bytes
        self.assertLessEqual(end, len(data)); self.assertGreater(native_bytes, 0)
        self.assertEqual(data[32:64], hashlib.sha256(data[header:native_at]).digest())
        self.assertFalse(any(data[end:]))
        native = data[native_at:end]
        self.assertEqual(b'P6NATIVE', native[:8])
        version, header = struct.unpack_from('<II', native, 8)
        self.assertEqual((1, 336), (version, header))
        payload_bytes, count = struct.unpack_from('<QI', native, 40)
        self.assertEqual(len(native), header + payload_bytes)
        at = header; device = None; body = None
        for _ in range(count):
            self.assertLessEqual(at + 16, len(native))
            tag, version, size = struct.unpack_from('<4sIQ', native, at); at += 16
            self.assertLessEqual(size, len(native) - at)
            if tag == b'P6AK':
                self.assertIsNone(device); self.assertEqual(1, version); self.assertGreaterEqual(size, 60)
                device = struct.unpack_from('<32s7I', native, at + size - 60)
                body = native[at:at+size]
            at += size # P6RT is legitimately empty on a CD32 without RTC.
        self.assertEqual(at, len(native)); self.assertIsNotNone(device)
        return (device,body) if controller else device

    @staticmethod
    def iso_id(path):
        data = path.read_bytes()
        return hashlib.sha256(b'P6ISO1' + struct.pack('<Q', len(data)) + hashlib.sha256(data).digest()).digest()

    def test_slots_initial_aliases_boundary_and_fixed_catalog(self):
        content, paths = self.playlist(); work = self.work()
        states = [work / f'{i}.state' for i in range(5)]
        out = self.run_cd('--initial', f'1:{paths[1]}', '--runs', 6,
                          '--do', f'1:savefile:{states[0]}', '--do', '1:images',
                          '--disk', '2:eject', '--do', f'2:savefile:{states[1]}',
                          '--disk', '3:index=2', '--disk', '3:insert', '--do', f'3:savefile:{states[2]}',
                          '--disk-during', '4:eject', '--do', f'4:savefile:{states[3]}',
                          '--do', f'5:savefile:{states[4]}', '--disk', '6:add',
                          '--disk', '6:remove=0', '--disk', f'6:replace=0:{paths[1]}', content=content)
        self.assertRegex(out, r'state images count=3 index=1 eject=0')
        self.assertEqual(self.iso_id(paths[1]), self.device(states[0])[0])
        for i, present in enumerate((1, 0, 1, 1, 0)):
            dev = self.device(states[i]); self.assertEqual((1, present, 1), dev[1:4])
            self.assertEqual(self.iso_id(paths[1 if i < 2 else 0]), dev[0])
        for operation in ('add', 'remove=0', f'replace=0:{paths[1]}'):
            self.assertIn(f'{operation} result=0', out)
        before, after = work / 'fixed-before', work / 'fixed-after'
        self.run_cd('--runs', 1, '--disk', '1:eject', '--do', f'1:savefile:{before}',
                    '--do', '1:disk:add', '--do', '1:disk:remove=0', '--do', f'1:disk:replace=0:{paths[1]}',
                    '--do', f'1:savefile:{after}', content=content)
        self.assertEqual(before.read_bytes(), after.read_bytes())
        # Legacy callbacks route to the same real unit, including same-disc close.
        closed = work / 'closed.state'
        legacy = self.run_cd('--legacy-disk-control', '--runs', 2, '--disk', '1:eject',
                             '--disk', '1:index=2', '--disk', '1:insert', '--do', f'2:savefile:{closed}', content=content)
        self.assertIn('index=2 result=1', legacy)
        self.assertEqual((self.iso_id(paths[0]), 1, 1, 1), self.device(closed)[:4])

    def test_restore_a_after_b_in_new_process_and_reject_foreign_catalog(self):
        content, paths = self.playlist(); work = self.work()
        cut, end, fresh, selected_b = [work / name for name in ('cut', 'end', 'fresh', 'b')]
        original = self.run_cd('--runs', 315, '--do', f'300:savefile:{cut}', '--do', f'315:savefile:{end}', content=content)
        renamed, newpaths = self.playlist(('renamed-a', 'renamed-b', 'renamed-alias'))
        resumed = self.run_cd('--no-vfs', '--runs', 17, '--disk', '2:eject', '--disk', '2:index=1', '--disk', '2:insert',
                              '--do', f'2:savefile:{selected_b}', '--do', f'2:loadfile:{cut}:pos=300',
                              '--do', '2:images', '--do', f'17:savefile:{fresh}', content=renamed)
        self.assertEqual(self.iso_id(newpaths[1]), self.device(selected_b)[0])
        self.assertIn(f'state load {cut} pos=300 ok=1', resumed)
        self.assertIn('state images count=3 index=0 eject=0', resumed)
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        self.assertEqual([view(r) for r in self.runs(original)[300:315]], [view(r) for r in self.runs(resumed)[2:17]])
        self.assertEqual(end.read_bytes(), fresh.read_bytes())
        for order in ((1, 0, 1), (0, 0, 1), (0, 1), (0, 1, 0)):
            bad, badpaths = self.playlist(order=order); before, after = work / 'before', work / 'after'
            if order == (0, 1, 0): badpaths[1].write_bytes(b'z' * (512 * 2048))
            out = self.run_cd('--runs', 1, '--do', f'1:savefile:{before}', '--do', f'1:loadfile:{cut}',
                              '--do', f'1:savefile:{after}', content=bad)
            self.assertIn(f'state load {cut} exact ok=0', out)
            self.assertEqual(before.read_bytes(), after.read_bytes())

    def test_ejected_and_pending_selection_restore(self):
        content, paths = self.playlist(); work = self.work()
        for pending in (False, True):
            cut, end, fresh = [work / f'{pending}-{name}' for name in ('cut', 'end', 'fresh')]
            flags = ('--disk-during', '2:eject', '--disk-during', '2:index=1', '--disk-during', '2:insert') if pending else (
                '--disk', '2:eject', '--disk', '2:index=1')
            original = self.run_cd('--runs', 5, *flags, '--do', f'2:savefile:{cut}', '--do', f'5:savefile:{end}', content=content)
            replay = self.run_cd('--runs', 4, '--disk', '1:eject', '--disk', '1:index=2',
                                 '--do', f'1:loadfile:{cut}:pos=2', '--do', f'4:savefile:{fresh}', content=content)
            self.assertIn(f'state load {cut} pos=2 ok=1', replay)
            self.assertEqual(end.read_bytes(), fresh.read_bytes())
            self.assertEqual(self.iso_id(paths[1 if pending else 0]), self.device(fresh)[0])
            self.assertEqual(int(pending), self.device(fresh)[2])

    def test_invalid_playlists_release_handles(self):
        content, paths = self.playlist(); work = self.work()
        floppy = work / 'floppy.adf'; floppy.write_bytes(bytes(901120))
        for text in (f'{paths[0]}\n{floppy}\n', f'{paths[0]}\n#SAVEDISK:save\n',
                     f'{paths[0]}\nmissing.iso\n', f'{paths[0]}\n' * 17):
            bad = work / 'bad.m3u'; bad.write_text(text)
            self.run_cd(content=bad, expect=3)
        self.run_cd('--vfs-no-seek', content=content, expect=3)


class Cd32ChdTests(Cd32Session):
    def chd(self, name='disc', tail=b''):
        # Own stored-v5 data fixture, not a bootable application. No chdman in CI.
        payload=self.iso.read_bytes(); units=b''.join(payload[n:n+2048]+bytes(400) for n in range(0,len(payload),2048))
        data=chd_ref.cd_map([(4,units[n:n+19584]) for n in range(0,len(units),19584)],sector=bytes(19584))
        text=b'TRACK:1 TYPE:MODE1 SUBTYPE:NONE FRAMES:512 PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0\0'
        path=self.work()/(name+'.ChD'); path.write_bytes(chd_ref.metadata_chain(data,[text],tag=0x43485432)+tail)
        return path

    def test_chd_direct_snapshot_new_process_vfs_and_container_identity(self):
        content=self.chd(); work=self.work(); cut,end,fresh=(work/name for name in ('cut','end','fresh'))
        original=self.run_cd('--runs',315,'--do',f'300:savefile:{cut}','--do',f'315:savefile:{end}',content=content)
        self.assertIn('machine CD32',original); self.assertIn('chd',re.search(r'extensions=(\S+)',original)[1].split('|'))
        self.assertGreater(int(re.search(r'vfs closed live=0 run_reads=(\d+)',original)[1]),0)
        accepted=self.run_cd('--no-vfs','--set','puae6_machine=CD32','--runs',16,
                             '--do',f'1:loadfile:{cut}:pos=300','--do',f'16:savefile:{fresh}',content=self.chd('renamed'))
        self.assertIn(f'state load {cut} pos=300 ok=1',accepted)
        view=lambda r:(r['unit'],r['ticks'],r['hash'],r['ahash'],r['audio'])
        self.assertEqual([view(r) for r in self.runs(original)[300:315]],[view(r) for r in self.runs(accepted)[1:16]])
        self.assertEqual(end.read_bytes(),fresh.read_bytes())
        changed=self.chd('opaque',b'valid unmapped tail'); before,after=work/'before',work/'after'
        rejected=self.run_cd('--runs',1,'--do',f'1:savefile:{before}','--do',f'1:loadfile:{cut}',
                             '--do',f'1:savefile:{after}',content=changed)
        self.assertIn(f'state load {cut} exact ok=0',rejected); self.assertEqual(before.read_bytes(),after.read_bytes())
        self.run_cd('--vfs-no-seek',content=content,expect=3)
        broken=work/'truncated.chd'; broken.write_bytes(content.read_bytes()[:100])
        self.run_cd(content=broken,expect=3)


    def test_chd_htoa_public_cold_restore_and_identity(self):
        work=self.work(); gap=200
        fixture,_=chd_provider.htoa_fixture(work,'htoa',gap); content,tracks,streams=fixture
        cut,end,fresh=(work/name for name in ('htoa-cut','htoa-end','htoa-fresh'))
        # Independent P6CHR1 identity, including complete container and normalized sources.
        raw=content.read_bytes(); logical=struct.unpack_from('>Q',raw,32)[0]; hunk=struct.unpack_from('>I',raw,56)[0]
        padded=sum((len(s)//t[4]+3)//4*4 for t,s in zip(tracks,streams))
        digest=hashlib.sha256
        identity=b'P6CHR1'+struct.pack('<Q',len(raw))+digest(raw).digest()+struct.pack('<QIQQII',logical,hunk,padded,logical//2448-padded,2,gap+12)
        for t,stream,region in zip(tracks,streams,([1,3,0],[0,0,0])):
            identity+=struct.pack('<IIIIIQIIIIIIIIIQ',*t,*region,len(stream))+digest(stream).digest()
        expected_id=digest(identity).digest()
        def verify(path):
            device,body=Cd32PlaylistTests.device(self,path,controller=True)
            self.assertEqual(device[:3],(expected_id,1,1))
            # P6AKv1 state_io/akiko.h: config/misc8 + fields156 + input/result64
            # + command/response64 + toc_bytes1339 + q16. Semantic LE, no C++ padding.
            at=8+156+64+64+1339+16
            self.assertEqual(struct.unpack_from('<8i',body,at),(1,1,2,2,0,gap+12,2,5))
            entries=[struct.unpack_from('<8B2i',body,at+32+n*16) for n in range(5)]
            self.assertEqual([(e[0],e[1],e[3],e[4],e[-1]) for e in entries[1:3]],[(1,0,1,1,gap),(1,4,2,2,gap+3)])
            self.assertEqual((entries[-1][3],entries[-1][-1]),(0xa2,gap+12))
        original=self.run_cd('--runs',315,'--do',f'300:savefile:{cut}','--do',f'315:savefile:{end}',content=content)
        self.assertIn('machine CD32',original); verify(cut); verify(end)
        renamed=work/'renamed.chd'; renamed.write_bytes(content.read_bytes())
        resumed=self.run_cd('--no-vfs','--runs',16,'--do',f'1:loadfile:{cut}:pos=300','--do','1:images',
                            '--do',f'16:savefile:{fresh}',content=renamed)
        self.assertIn(f'state load {cut} pos=300 ok=1',resumed)
        self.assertIn('state images count=1 index=0 eject=0',resumed)
        view=lambda r:(r['unit'],r['ticks'],r['hash'],r['ahash'],r['audio'])
        self.assertEqual([view(r) for r in self.runs(original)[300:315]],[view(r) for r in self.runs(resumed)[1:16]])
        self.assertEqual(end.read_bytes(),fresh.read_bytes()); verify(fresh)
        for n,options in enumerate(({'payload':1},{'gap':201})):
            other,_=chd_provider.htoa_fixture(work,'foreign'+str(n),options.get('gap',gap),payload=options.get('payload',0))
            before,after=work/('before'+str(n)),work/('after'+str(n))
            rejected=self.run_cd('--runs',1,'--do',f'1:savefile:{before}','--do',f'1:loadfile:{cut}',
                                 '--do',f'1:savefile:{after}',content=other[0])
            self.assertIn(f'state load {cut} exact ok=0',rejected); self.assertEqual(before.read_bytes(),after.read_bytes())

    def test_chd_playlist_catalog_switch_and_restore(self):
        def playlist():
            a=self.chd(); folder=a.parent; b=folder/'other.iso'; alias=folder/'alias.chd'
            b.write_bytes(self.other.read_bytes()); alias.write_bytes(a.read_bytes())
            path=folder/'mixed.m3u'; path.write_text(f'{a.name}|A\n{b.name}|B\n{alias.name}|Alias A\n')
            return path,(a,b,alias)
        content,paths=playlist(); work=self.work(); cut,end,fresh,selected,alias=(work/name for name in ('cut','end','fresh','selected','alias'))
        original=self.run_cd('--runs',315,'--do',f'300:savefile:{cut}','--do',f'315:savefile:{end}',content=content)
        renamed,newpaths=playlist()
        resumed=self.run_cd('--no-vfs','--runs',17,'--disk','2:eject','--disk','2:index=1','--disk','2:insert',
                            '--do',f'2:savefile:{selected}','--do',f'2:loadfile:{cut}:pos=300',
                            '--do','2:images','--do',f'17:savefile:{fresh}',content=renamed)
        device=lambda p:Cd32PlaylistTests.device(self,p)
        self.assertEqual(Cd32PlaylistTests.iso_id(newpaths[1]),device(selected)[0])
        self.assertIn('state images count=3 index=0 eject=0',resumed)
        self.assertIn(f'state load {cut} pos=300 ok=1',resumed)
        view=lambda r:(r['unit'],r['ticks'],r['hash'],r['ahash'],r['audio'])
        self.assertEqual([view(r) for r in self.runs(original)[300:315]],[view(r) for r in self.runs(resumed)[2:17]])
        self.assertEqual(end.read_bytes(),fresh.read_bytes())
        switched=self.run_cd('--initial',f'2:{newpaths[2]}','--runs',1,'--do',f'1:savefile:{alias}',content=renamed)
        self.assertIn('images count=3 index=2 eject=0',switched); self.assertEqual(device(cut)[0],device(alias)[0])
        before,after=work/'foreign-before',work/'foreign-after'
        renamed.write_text(f'{newpaths[1].name}\n{newpaths[0].name}\n{newpaths[2].name}\n')
        refused=self.run_cd('--runs',1,'--do',f'1:savefile:{before}','--do',f'1:loadfile:{cut}',
                            '--do',f'1:savefile:{after}',content=renamed)
        self.assertIn(f'state load {cut} exact ok=0',refused); self.assertEqual(before.read_bytes(),after.read_bytes())


class Cd32GameTests(Cd32Session):
    def brian_session(self):
        content = os.environ.get('P6_CD32_GAME_CUE')
        if not content:
            self.skipTest('BLOCKED: authorized Brian the Lion P6_CD32_GAME_CUE required')
        # English, skip logos, select NO SAVE, enter THE STEAMY JUNGLE, move right.
        phases = ((1400, 1), (1410, 0), (2100, 1), (2110, 0), (3100, 1), (3110, 0),
                  (4501, 0x20), (4502, 0), (4504, 0x20), (4506, 0), (4508, 0x20), (4510, 0),
                  (4520, 1), (4530, 0), (5501, 1), (5510, 0), (6501, 0x80), (6541, 0))
        buttons = ('--device', '0:513', *(arg for at, mask in phases for arg in ('--joy-at', f'{at}:0:{mask:x}')))
        return content, buttons

    def test_brian_gameplay_and_restore(self):
        content, buttons = self.brian_session()
        work = self.work(); cut = work / 'game.state'
        end, replayed, fresh = (work / name for name in ('end.state', 'replayed.state', 'fresh.state'))
        n, m = 6500, 80
        # Visually checked against the authorized game: language wheel and Brian
        # standing in the first jungle level with its HUD. No pixels/assets kept.
        menu, level = '5a9b63564f6b4283', '4ee0d34fdf9a835b'
        out = self.run_cd(*buttons, '--runs', n + 2 * m,
                          '--do', f'{n}:savefile:{cut}', '--do', f'{n + m}:savefile:{end}',
                          '--do', f'{n + m}:memory', '--do', f'{n + m}:loadfile:{cut}:pos={n}',
                          '--do', f'{n + 2 * m}:savefile:{replayed}', '--do', f'{n + 2 * m}:memory',
                          content=content, timeout=240)
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        runs = self.runs(out)
        self.assertEqual(len(runs), n + 2 * m)
        self.assertEqual(runs[1399]['hash'], menu, 'must reach the game language wheel')
        self.assertEqual(runs[n - 1]['hash'], level, 'must reach gameplay, beyond firmware/logos')
        future = [view(r) for r in runs[n:n + m]]
        self.assertEqual(future, [view(r) for r in runs[n + m:]])
        memory = re.findall(r'^memory hash (.*)$', out, re.M)
        self.assertEqual(len(memory), 2); self.assertEqual(*memory)
        self.assertIn(f'state load {cut} pos={n} ok=1', out)
        def silence(frames):
            value = 1469598103934665603
            for _ in range(frames * 4): value = value * 1099511628211 & ((1 << 64) - 1)
            return f'{value:016x}'
        self.assertTrue(any(r['ahash'] != silence(r['audio']) for r in runs[n:n + m]), 'game audio must be active')
        self.assertEqual(end.read_bytes(), replayed.read_bytes(), 'all native chunks and adapter state must return')
        # Both owners restore the original game's cut before their first run;
        # they do not merely produce their own independent cold-boot snapshots.
        other = self.run_cd(*buttons, '--runs', m, '--cycles', 2, '--do', f'0:loadfile:{cut}:pos={n}',
                            '--do', f'{m}:savefile:{fresh}', '--do', f'{m}:memory', content=content)
        for chunk in re.split(r'cycle \d+ end[^\n]*\n', other)[:2]:
            self.assertEqual([view(r) for r in self.runs(chunk)], future)
            self.assertIn(f'state load {cut} pos={n} ok=1', chunk)
            self.assertEqual(re.findall(r'^memory hash (.*)$', chunk, re.M), [memory[0]])
        self.assertEqual(end.read_bytes(), fresh.read_bytes())
        idle = self.run_cd('--device', '0:513', '--runs', m, '--do', f'0:loadfile:{cut}:pos={n}', content=content)
        self.assertNotEqual(self.runs(idle)[-1]['hash'], self.runs(other)[-1]['hash'], 'Right must move Brian on screen')

    def test_brian_cold_reload_matches_fresh_session(self):
        # T66r8 reproducer: a completed game session contaminates early video of
        # the next cold load. This is a separate gate from restoring a game cut.
        content, buttons = self.brian_session()
        out = self.run_cd(*buttons, '--runs', 6580, '--cycles', 2, content=content, timeout=360)
        chunks = re.split(r'cycle \d+ end[^\n]*\n', out)[:2]
        first, second = (self.runs(chunk) for chunk in chunks)
        self.assertEqual(len(first), 6580); self.assertEqual(len(second), 6580)
        view = lambda r: (r['unit'], r['ticks'], r['hash'], r['ahash'], r['audio'])
        for a, b in zip(first, second):
            self.assertEqual(view(a), view(b), f'cold reload differs at unit {a["unit"]}')


if __name__ == '__main__':
    unittest.main()
