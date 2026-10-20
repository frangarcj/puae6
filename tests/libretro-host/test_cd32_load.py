"""Direct CD sessions through retro_*; local firmware stays outside the tree."""
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
from tests.media import test_mds_metadata as mds_ref


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
        self.assertRegex(out, r'(?m)^disk 1 add result=0 .* count=0$')
        self.assertRegex(out, r'(?m)^disk 2 eject result=0 .* count=0$')
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
        self.assertIn('mds',extensions); self.assertNotIn('chd',extensions); self.assertNotIn('mdf',extensions)
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
