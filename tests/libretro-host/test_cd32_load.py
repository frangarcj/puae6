"""Direct CD sessions through retro_*; local firmware stays outside the tree."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_libretro as lr
from tests.build_support import ROOT, build_targets


class Cd32LoadTests(lr.LibretroHarness):
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

    def run_cd(self, *flags, content=None, system=None, expect=0):
        result = subprocess.run([str(self.host), str(self.core), '--system', str(system or self.system_dir),
                                 '--content', str(content or self.iso), *map(str, flags)], capture_output=True, text=True, timeout=180)
        self.assertEqual(expect, result.returncode, result.stdout[-2000:] + result.stderr[-3000:])
        self.assertIn('vfs closed live=0', result.stdout)
        self.assertNotIn('VIOLATION', result.stdout)
        return result.stdout

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


if __name__ == '__main__':
    unittest.main()
