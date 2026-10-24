"""Own ELF headers and mocked build/inspection commands; no compiler or emulator."""
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import package


def elf(machine, kind=3):
    header = bytearray(64)
    header[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HHI', header, 16, kind, machine, 1)
    struct.pack_into('<H', header, 52, 64)
    return bytes(header)


class LinuxPreviewTests(unittest.TestCase):
    def setUp(self):
        (package.ROOT / 'build').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='package-linux-', dir=package.ROOT / 'build')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def binaries(self, target, machine):
        (self.work / 'bin').mkdir(exist_ok=True)
        for name in package.products(target):
            (self.work / 'bin' / name).write_bytes(elf(machine))

    def test_declared_hosts_products_and_clang_commands_keep_macos(self):
        for system, machine, target in (('Darwin','arm64','macos-arm64'), ('Linux','aarch64','linux-arm64'),
                                         ('Linux','arm64','linux-arm64'), ('Linux','x86_64','linux-x86_64')):
            with self.subTest(target=target), mock.patch.object(package.platform,'system',return_value=system), \
                 mock.patch.object(package.platform,'machine',return_value=machine):
                self.assertEqual(package.native_platform(),target)
                command = package.configure_command(target, self.work)
                toolchain = 'macos-appleclang' if system=='Darwin' else 'linux-clang'
                self.assertIn('-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/'+toolchain+'.cmake',command)
                self.assertEqual(package.products(target)[0], 'puae6_libretro'+('.dylib' if system=='Darwin' else '.so'))
                self.assertIn('-DCMAKE_BUILD_TYPE=Release',command)
                self.assertIn('-DP6_SANITIZERS=OFF',command)
                self.assertEqual(system=='Darwin','-DCMAKE_INSTALL_NAME_DIR=@rpath' in command)
        with mock.patch.object(package.platform,'system',return_value='Windows'), self.assertRaises(ValueError):
            package.native_platform()
        for target in (None,[], 'linux-riscv64'):
            with self.assertRaises(ValueError): package.products(target)

    def test_elf_isa_type_and_system_dependencies_for_both_linux_platforms(self):
        for target, machine in (('linux-arm64',183),('linux-x86_64',62)):
            self.binaries(target,machine)
            (self.work/'bin/p6_headless').write_bytes(elf(machine,2))
            dynamic = '\n'.join(' 0 (NEEDED) Shared library: ['+name+']'
                                for name in ('libstdc++.so.6','libgcc_s.so.1','libm.so.6','libc.so.6'))
            with mock.patch.object(package,'run',return_value=dynamic) as run:
                package.verify_binaries(self.work,target)
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0][:3]==['readelf','--wide','--dynamic'] for c in run.call_args_list))
            self.assertTrue(all(c.kwargs['env']['LC_ALL']=='C' for c in run.call_args_list))

    def test_wrong_format_isa_and_core_executable_are_rejected_before_inspection(self):
        self.binaries('linux-arm64',183)
        core = self.work/'bin/puae6_libretro.so'
        for data in (b'Mach-O arm64',elf(62),elf(183,2),elf(183,1),elf(183)[:20],
                     elf(183).replace(b'\x7fELF\x02\x01',b'\x7fELF\x01\x01'),
                     elf(183).replace(b'\x7fELF\x02\x01',b'\x7fELF\x02\x02')):
            core.write_bytes(data)
            with mock.patch.object(package,'run') as run, self.assertRaisesRegex(ValueError,'ELF'):
                package.verify_binaries(self.work,'linux-arm64')
            run.assert_not_called()

    def test_hidden_dependencies_and_search_paths_are_rejected(self):
        self.binaries('linux-x86_64',62)
        for dynamic in ('0 (NEEDED) Shared library: [libcapsimage.so]',
                        '0 (NEEDED) Shared library: [/work/private/libc.so.6]',
                        '0 (NEEDED) malformed', '0 (RUNPATH) Library runpath: [/work/build]',
                        '0 (RPATH) Library rpath: [$ORIGIN/../private]'):
            with mock.patch.object(package,'run',return_value=dynamic), self.assertRaises(ValueError):
                package.verify_binaries(self.work,'linux-x86_64')
        with mock.patch.object(package,'run',side_effect=RuntimeError('readelf failed')), self.assertRaises(RuntimeError):
            package.verify_binaries(self.work,'linux-x86_64')

    def test_elf_interpreter_is_system_owned_for_its_isa_and_header_bounds_are_checked(self):
        for target, machine, interpreter in (('linux-arm64',183,'/lib/ld-linux-aarch64.so.1'),
                                              ('linux-x86_64',62,'/lib64/ld-linux-x86-64.so.2')):
            self.binaries(target,machine)
            def image(path):
                data=bytearray(elf(machine)); struct.pack_into('<Q',data,32,64); struct.pack_into('<HH',data,54,56,1)
                program=bytearray(56); struct.pack_into('<I',program,0,3)
                struct.pack_into('<Q',program,8,120); struct.pack_into('<Q',program,32,len(path)+1)
                return data+program+path.encode()+b'\0'
            binary=self.work/'bin/p6_headless'; binary.write_bytes(image(interpreter))
            with mock.patch.object(package,'run',return_value=''): package.verify_binaries(self.work,target)
            for data in (image('/work/private/ld-linux'), image(interpreter)[:100]):
                binary.write_bytes(data)
                with mock.patch.object(package,'run',return_value=''), self.assertRaisesRegex(ValueError,'interpreter|headers'):
                    package.verify_binaries(self.work,target)

    def test_t40c_real_installed_suite_is_shared_by_all_three_checks(self):
        from tools import scenario_schema
        with mock.patch.dict(sys.modules,{'scenario_schema':scenario_schema}):
            from tools import check
        installed='tests.packages.test_preview.InstalledPreviewTests'
        for gate in ('PKG01','BUILD01','LR01'):
            self.assertEqual(list(check.TASK_SUITES[(gate,'T40c')].values()).count(installed),1)
        self.assertEqual(check.TASK_SUITES[('BUILD01','T40c')],check.TASK_SUITES[('LR01','T40c')])

    def test_macos_binary_inspection_keeps_its_system_dependency_policy(self):
        def inspect(command):
            return 'Mach-O 64-bit arm64' if command[0]=='file' else 'own:\n /usr/lib/libSystem.B.dylib (compatibility version 1)'
        with mock.patch.object(package,'run',side_effect=inspect) as run:
            package.verify_binaries(self.work,'macos-arm64')
        self.assertEqual(run.call_count,6)
        with mock.patch.object(package,'run',side_effect=lambda c:inspect(c) if c[0]=='file' else 'own:\n /opt/local/libhidden.dylib (version 1)'), \
             self.assertRaisesRegex(ValueError,'Non-system'):
            package.verify_binaries(self.work,'macos-arm64')

    def test_mock_build_stages_each_platform_and_preserves_manifest_sources_and_notices(self):
        def archive(command, **kwargs):
            self.assertEqual(command[:2],['git','archive'])
            with tarfile.open(fileobj=kwargs['stdout'],mode='w:gz') as stream:
                data=b'own source fixture'; info=tarfile.TarInfo('source/own.py'); info.size=len(data)
                stream.addfile(info,io.BytesIO(data))
            return subprocess.CompletedProcess(command,0)
        for target in package.PLATFORMS:
            tree=self.work/target; tree.mkdir()
            for name in package.products(target): (tree/name).write_bytes(b'own binary fixture')
            output=self.work/(target+'.zip')
            def command(args, **kwargs):
                if args[0]=='cmake': return ''
                if args[:2]==['git','show']: return '1600000000'
                if str(args[0])=='/usr/bin/clang': return 'own mocked Clang version'
                self.assertEqual(args,[tree/'p6_headless','--identity']); return 'own mocked identity'
            with mock.patch.object(package,'native_platform',return_value=target), \
                 mock.patch.object(package,'clean_revision',return_value='a'*40), \
                 mock.patch.object(package,'source_paths',return_value=['own.py']), \
                 mock.patch.object(package,'run',side_effect=command) as run, \
                 mock.patch.object(package.subprocess,'run',side_effect=archive), \
                 mock.patch.object(package,'smoke',return_value={'own_infrastructure_mock':True}):
                result=package.build(output,tree)
            self.assertEqual(result['sha256'],package.sha(output))
            self.assertEqual(run.call_args_list[1].args[0][3:5],['--parallel','2'])
            package.unpack(output,self.work/('extract-'+target))
            manifest=json.loads((self.work/('extract-'+target)/'manifest.json').read_text())
            self.assertEqual(manifest['platform'],target)
            self.assertEqual({p for p in manifest['files'] if p.startswith('bin/')},{'bin/'+p for p in package.products(target)})
            self.assertIn('source.tar.gz',manifest['files']); self.assertIn('LEEME.md',manifest['files'])
            self.assertIn('licenses/COPYING',manifest['files'])
            self.assertFalse(manifest['binary_reproducibility_verified'])

    def test_installed_linux_smoke_uses_so_and_retains_restore_and_missing_firmware_checks(self):
        with tarfile.open(self.work/'source.tar.gz','w:gz') as archive:
            for name in ('build_rom.py','boot.s'):
                archive.add(package.ROOT/'tests/fixtures/microrom'/name,arcname='source/tests/fixtures/microrom/'+name)
        rows = ['a','b','c','d','e','f','g','h','i','g','h','i']
        output = '\n'.join(f'run {n} {row}' for n,row in enumerate(rows))+'\nstate load s exact ok=1'
        with mock.patch.object(package,'verify',return_value={'platform':'linux-arm64'}), \
             mock.patch.object(package,'native_platform',return_value='linux-arm64'), \
             mock.patch.object(package,'run',side_effect=[output,'unit 0\nunit 1\nunit 2\n']) as run, \
             mock.patch.object(package.subprocess,'run',return_value=subprocess.CompletedProcess([],3,'no Kickstart','')), \
             mock.patch.dict(package.os.environ,{'LD_PRELOAD':'/own/untrusted','DYLD_LIBRARY_PATH':'/own/untrusted'}):
            self.assertEqual(package.smoke(self.work),{'libretro_units':12,'restored_units_compared':3,
                             'headless_units':3,'missing_firmware_rejected':True})
        self.assertEqual(run.call_args_list[0].args[0][1],self.work/'bin/puae6_libretro.so')
        self.assertFalse(any(k.startswith(('LD_','DYLD_')) for k in run.call_args_list[0].kwargs['env']))
        with mock.patch.object(package,'verify',return_value={'platform':'linux-arm64'}), \
             mock.patch.object(package,'native_platform',return_value='macos-arm64'), \
             mock.patch.object(package,'run') as run, self.assertRaisesRegex(ValueError,'matching native'):
            package.smoke(self.work)
        run.assert_not_called()
