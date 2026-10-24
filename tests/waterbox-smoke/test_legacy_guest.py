"""Owned fixtures and read-only verification; never compile or run UAE here."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('legacy_builder', ROOT / 'tools/build_legacy_guest.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


def adapters(text):
    found = re.findall(r'static void fp_(?:mod|rem)\([^)]*\)\n\{[^}]*\}', text)
    if len(found) != 2: raise ValueError('Missing real softfloat adapters')
    return '\n'.join(found)


class LegacyGuestTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='legacy-fixture-', dir=ROOT / 'build'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root / 'build').mkdir()
        self.paths = {sid: self.root / 'references' / sid for sid in p.IDS}
        for path in self.paths.values(): path.mkdir(parents=True)
        waterbox = self.paths['machawk'] / 'waterbox'
        for name in ('common.mak', 'linkscript_aarch64.T', 'uae/Makefile', 'emulibc/Makefile', 'emulibc/emulibc.c'):
            path = waterbox / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('owned source fixture\n')
        for sid in ('bizhawk_uae_reference', 'machawk_capsimg'): (self.paths[sid] / 'fixture.c').write_text('own source\n')
        target = self.paths['bizhawk_uae_reference'] / p.SOFTFLOAT; target.parent.mkdir(parents=True)
        target.write_bytes((ROOT / 'references/machawk-uae' / p.SOFTFLOAT).read_bytes())
        patch = self.root / p.PATCH; patch.parent.mkdir(parents=True); shutil.copyfile(ROOT / p.PATCH, patch)
        musl = self.paths['waterbox_musl_darwin']; (musl / 'tools').mkdir()
        for name in ('musl-clang.in', 'ld.musl-clang.in'): shutil.copyfile(ROOT / 'references/musl-darwin/tools' / name, musl / 'tools' / name)
        self.tools = {n: self.root / n for n in ('clang', 'lld', 'cargo', 'rustc', 'make')}
        self.versions = {n: 'fixed version' for n in self.tools}
        self.proof = {'tools': {n: {'sha256': 'a'*64, 'version': v} for n, v in self.versions.items()}, 'source_fixture': 'owned'}
        self.sysroot = self.root / 'build/derived/sysroot'; self.sysroot.mkdir(parents=True)
        self.output = self.root / 'build/legacy'
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(p, 'ROOT', self.root).start()

    def mocks(self):
        real_run = p.subprocess.run
        mock.patch.object(p, 'inputs', return_value=(self.paths, self.tools, self.sysroot, self.proof)).start()
        mock.patch.object(p.wb, 'selected_toolchain', return_value={}).start()
        mock.patch.object(p.wb, 'locked_tools', return_value=(self.tools, self.versions)).start()
        mock.patch.object(p.wb, 'hosted_environment', return_value={'shell_exports': '', 'tool_env': {
            k: 'untrusted' for k in ('MAKEFLAGS', 'CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH', 'LIBRARY_PATH', 'CCFLAGS',
                                     'CXXFLAGS', 'LDFLAGS', 'SOURCES_C', 'EMULIBC_DOBJS', 'SDKROOT')}}).start()
        mock.patch.object(p.wb, 'validate_elf', return_value={}).start()
        mock.patch.object(p.wb, 'check_no_compiler_tls', return_value={}).start()
        mock.patch.object(p, 'symbols', return_value={'own': 'fixture'}).start()
        def compile_fixture(command, **kwargs):
            if command[0] == 'patch': return real_run(command, **kwargs)
            self.assertEqual(command[0], str(self.tools['make'])); self.assertIn('-j2', command)
            self.assertNotIn('install', command); self.assertNotIn('clean', command)
            stage = Path(command[2]).parents[1]
            self.assertEqual(kwargs['cwd'], stage)
            self.assertEqual(set(kwargs['env']), {'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LC_ALL'})
            if 'debug' in command:
                guest = stage / 'objects/uae/debug/uae.wbx'; guest.parent.mkdir(parents=True); guest.write_bytes(b'own fixture, not executable')
            return SimpleNamespace(returncode=0)
        child = mock.patch.object(p.subprocess, 'run', side_effect=compile_fixture).start()
        child.fixture_effect = compile_fixture
        return child

    def test_debug_recipe_wrappers_atomic_publication_and_readonly_verification(self):
        child = self.mocks(); result = p.build(self.output, execute=True)
        self.assertEqual(result['status'], p.STATUS); self.assertFalse(result['historical_binary_reproduction'])
        self.assertEqual(p.verify(self.output), result)
        self.assertEqual(set(x.name for x in self.output.iterdir()), {'uae.wbx', 'receipt.json'})
        self.assertFalse(list((self.root / 'build').glob('.legacy-source-*')))
        first, second = [call.args[0] for call in child.call_args_list if call.args[0][0] != 'patch']
        self.assertTrue(first[3].endswith('/emulibc/debug/emulibc.c.o')); self.assertEqual(second[3], 'debug')
        for command in (first, second):
            for flag in ('WBX_TARGET_ARCH=aarch64', 'WBX_HOST_PAGE_SIZE=16384', 'CCFLAGS_DEBUG=-O0 -g', 'CXXFLAGS_DEBUG=-O0 -g'): self.assertIn(flag, command)
        stage = self.root / 'stage'; stage.mkdir()
        hashes = p.wrappers(stage / 'tools', self.paths['waterbox_musl_darwin'], self.sysroot, self.tools, 'export OWN=1\n')
        self.assertEqual(set(hashes), {'musl-clang', 'ld.musl-clang'})
        linker = (stage / 'tools/ld.musl-clang').read_text(); self.assertIn('exec "'+str(self.tools['lld'])+'"', linker)
        self.assertNotIn('-print-prog-name', linker); self.assertIn('export OWN=1', linker)
        (self.output / 'uae.wbx').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'guest differs'): p.verify(self.output)
        with self.assertRaisesRegex(ValueError, 'already exists'): p.build(self.output, execute=True)

    def test_default_is_not_run_and_bad_paths_or_missing_derivation_never_spawn(self):
        mock.patch.object(p, 'inputs', return_value=(self.paths, self.tools, self.sysroot, self.proof)).start()
        with mock.patch.object(p.subprocess, 'run') as child:
            self.assertEqual(p.build(self.output)['status'], 'NOT_RUN'); self.assertFalse(self.output.exists()); child.assert_not_called()
            for path in (self.root, self.root / 'build', self.root / 'build/../outside'):
                with self.assertRaises(ValueError): p.build(path)
            (self.root / 'build/link').symlink_to(self.sysroot, target_is_directory=True)
            with self.assertRaises(ValueError): p.build(self.root / 'build/link/output')
            p.inputs.side_effect = ValueError('derived receipt missing')
            with self.assertRaisesRegex(ValueError, 'derived'): p.build(self.output, execute=True)
            child.assert_not_called()

    def test_compile_failure_input_drift_and_bad_isa_never_publish(self):
        child = self.mocks(); child.side_effect = lambda command, **kw: child.fixture_effect(command, **kw) if command[0] == 'patch' else SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(ValueError, 'compilation failed'): p.build(self.output, execute=True)
        self.assertFalse(self.output.exists()); self.assertFalse(list((self.root / 'build').glob('.legacy-source-*')))
        mock.patch.stopall(); mock.patch.object(p, 'ROOT', self.root).start(); self.mocks()
        p.inputs.side_effect = [(self.paths, self.tools, self.sysroot, self.proof), (self.paths, self.tools, self.sysroot, {**self.proof, 'drift': True})]
        with self.assertRaisesRegex(ValueError, 'Inputs changed'): p.build(self.output, execute=True)
        self.assertFalse(self.output.exists()); self.assertFalse(list((self.root / 'build').glob('.legacy-source-*')))
        mock.patch.stopall(); mock.patch.object(p, 'ROOT', self.root).start(); self.mocks()
        p.wb.validate_elf.side_effect = ValueError('Wrong ISA')
        with self.assertRaisesRegex(ValueError, 'ISA'): p.build(self.output, execute=True)
        self.assertFalse(self.output.exists())

    def test_preflight_requires_all_captures_eight_overlays_tool_pins_and_derived_receipt(self):
        overlays = {str(n): 'a'*64 for n in range(8)}
        def capture(sid, mode):
            self.assertEqual(mode, 'snapshot')
            return {'snapshot': {'destination': 'references/'+sid, 'overlay_sha256': overlays}}, {'commit': 'pinned'}
        with mock.patch.object(p.wb, 'source_spec', side_effect=capture) as captures, mock.patch.object(p.wb, 'selected_toolchain', return_value={'profile': 'fixture', 'toolchain_profile': 'local', 'tools': {n: {'sha256': 'a'*64, 'version': v} for n, v in self.versions.items()}}), mock.patch.object(p.wb, 'selected_tool_paths', return_value=self.tools), mock.patch.object(p.wb, 'sha256', return_value='a'*64), mock.patch.object(p, 'derived', return_value={'sysroot': self.sysroot, 'receipt_sha256': 'b'*64}):
            self.assertEqual(p.inputs()[3]['captures']['bizhawk_uae_reference']['overlays'], overlays)
            self.assertEqual([c.args[0] for c in captures.call_args_list], list(p.IDS))
            overlays.pop('7')
            with self.assertRaisesRegex(ValueError, 'eight'): p.inputs()
            overlays['7'] = 'a'*64; p.wb.sha256.return_value = 'c'*64
            with self.assertRaisesRegex(ValueError, 'tools differ'): p.inputs()
            p.wb.sha256.return_value = 'a'*64; p.derived.side_effect = ValueError('missing derivation')
            with self.assertRaisesRegex(ValueError, 'derivation'): p.inputs()

    def test_identity_ignores_unrelated_lock_keys_but_tracks_effective_tools_profiles_and_derivation(self):
        plan = self.root / 'plan'; plan.mkdir(); lockfile = plan / 'toolchains.json'
        (plan / 'machawk.reference.json').write_text('{"own_fingerprint":true}')
        for name, path in self.tools.items(): path.write_text('own '+name+' fixture')
        lock = {'profile': 'fixture-profile', 'tools': {n: {'path': str(path), 'sha256': p.wb.sha256(path), 'version': 'v1'} for n, path in self.tools.items()}}
        def write(): lockfile.write_text(json.dumps(lock))
        write(); select = p.wb.selected_toolchain
        def capture(sid, mode):
            return {'snapshot': {'destination': 'references/'+sid, 'overlay_sha256': {str(n): 'a'*64 for n in range(8)}}}, {'commit': sid}
        with mock.patch.dict(p.os.environ, {}, clear=True), mock.patch.object(p.wb, 'source_spec', side_effect=capture), mock.patch.object(p.wb, 'selected_toolchain', side_effect=lambda: select(json.loads(lockfile.read_text()))), mock.patch.object(p, 'derived', return_value={'sysroot': self.sysroot, 'receipt_sha256': 'b'*64, 'inputs': {'authority': 'derived pin'}}):
            before = p.inputs()[3]
            self.assertEqual(before['tools']['clang']['path'], str(self.tools['clang']))
            lock.update(documentation='changed prose', default_product_guest={'new_unrelated_pin': True}); write()
            self.assertEqual(p.inputs()[3], before)
            lock['profile'] = 'other'; write(); self.assertNotEqual(p.inputs()[3], before)
            lock['profile'] = 'fixture-profile'; lock['tools']['clang']['version'] = 'v2'; write(); self.assertNotEqual(p.inputs()[3], before)
            lock['tools']['clang']['version'] = 'v1'; lock['tools']['clang']['sha256'] = 'f'*64; write()
            with self.assertRaisesRegex(ValueError, 'tools differ'): p.inputs()
            lock['tools']['clang']['sha256'] = p.wb.sha256(self.tools['clang']); write()
            alternative = self.root / 'other-clang'; shutil.copyfile(self.tools['clang'], alternative)
            lock['tools']['clang']['path'] = str(alternative); write(); self.assertNotEqual(p.inputs()[3], before)
            lock['tools']['clang']['path'] = str(self.tools['clang']); write()
            p.derived.return_value = {**p.derived.return_value, 'receipt_sha256': 'c'*64}
            self.assertNotEqual(p.inputs()[3], before)
            p.derived.side_effect = ValueError('derived authority mismatch')
            with self.assertRaisesRegex(ValueError, 'authority'): p.inputs()

    def test_import_export_parser_rejects_incomplete_or_strong_undefined_symbols(self):
        path = self.root / 'symbols.data'
        def fixture(extra=None):
            names = b'\0Init\0FrameAdvance\0GetMemoryAreas\0Unexpected\0'; table = bytes(24)
            for name, section in [('Init', 1), ('FrameAdvance', 1), ('GetMemoryAreas', 1), *([extra] if extra else [])]:
                table += struct.pack('<IBBHQQ', names.index(name.encode()), 16, 0, section, 0, 0)
            header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01'+bytes(9), 2, 183, 1, 0, 0, 64, 0, 64, 56, 0, 64, 3, 0)
            sections = bytes(64) + struct.pack('<IIQQQQIIQQ', 0, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
            sections += struct.pack('<IIQQQQIIQQ', 0, 2, 0, 0, 256+len(names), len(table), 1, 0, 8, 24)
            path.write_bytes(header + sections + names + table)
        fixture(); self.assertEqual(p.symbols(path)['strong_undefined'], [])
        fixture(('Unexpected', 0))
        with self.assertRaisesRegex(ValueError, 'imports/exports'): p.symbols(path)
        fixture(); path.write_bytes(path.read_bytes()[:100])
        with self.assertRaisesRegex(ValueError, 'sections'): p.symbols(path)

    def test_overlay_is_exact_reversible_and_neighbor_changes_are_rejected(self):
        stage = self.root / 'stage'; stage.mkdir(); p.prepare(stage, self.paths)
        original = self.paths['bizhawk_uae_reference'] / p.SOFTFLOAT
        copied = stage / 'waterbox/uae/libretro-uae' / p.SOFTFLOAT
        self.assertEqual(copied.read_bytes(), p.adapted(original.read_bytes()))
        with self.assertRaisesRegex(ValueError, 'overlay rejected'): p.source_overlay(copied.parents[2])
        (stage / 'waterbox/uae/libretro-uae/fixture.c').write_text('unexpected neighbor change')
        with self.assertRaisesRegex(ValueError, 'outside overlay'): p.audit_copy(stage, self.paths)
        with self.assertRaisesRegex(ValueError, 'adapter'): p.adapted(b'not the captured functions')


class LegacySoftfloatAdapterTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('P6_TEST_LEGACY_SOFTFLOAT_COMPILER') == '1', 'requires the pinned native compiler for a tiny adapter fixture')
    def test_real_lp64_pointer_diagnostic_and_patched_adapter_values(self):
        p.wb.source_spec('bizhawk_uae_reference', 'snapshot')
        lock = p.wb.selected_toolchain(); tools = p.wb.selected_tool_paths(lock); compiler = tools['clang']
        self.assertEqual(p.wb.sha256(compiler), lock['tools']['clang']['sha256'])
        env = p.wb.hosted_environment(tools)['direct_env']
        self.assertEqual(subprocess.check_output([str(compiler), '--version'], env=env, text=True).splitlines()[0], lock['tools']['clang']['version'])
        original = (ROOT / 'references/machawk-uae' / p.SOFTFLOAT).read_bytes()
        # Guest musl LP64 uses unsigned long; Darwin stdint could hide this error.
        prefix = '''typedef unsigned long uint64_t;
typedef unsigned long long uae_u64;
typedef unsigned char uae_u8;
_Static_assert(sizeof(uint64_t)==8 && sizeof(uae_u64)==8, "LP64 guest types");
typedef struct { uint64_t fpx; } fpdata;
static int fs, failure, writes; static uint64_t expected; static uae_u8 sign;
static uint64_t stub(uint64_t a,uint64_t b,uint64_t*q,uae_u8*s,int*status) {
 if(a!=11 || b!=3 || *q!=0 || status!=&fs) failure=1;
 if(writes) *q=expected; *s=sign; return 42;
}
static uint64_t floatx80_mod(uint64_t a,uint64_t b,uint64_t*q,uae_u8*s,int*status) { return stub(a,b,q,s,status); }
static uint64_t floatx80_rem(uint64_t a,uint64_t b,uint64_t*q,uae_u8*s,int*status) { return stub(a,b,q,s,status); }
'''
        suffix = '''int main(void) {
 uint64_t values[]={0UL,1UL,0x8000000000000000UL,0xfedcba9876543210UL,0xffffffffffffffffUL};
 for(unsigned op=0;op<2;op++) for(unsigned mode=0;mode<2;mode++)
 for(unsigned si=0;si<2;si++) for(unsigned n=0;n<5;n++) {
  fpdata a={11},b={3}; uae_u64 q=123; uae_u8 s=99;
  writes=mode; expected=values[n]; sign=si;
  if(op) fp_rem(&a,&b,&q,&s); else fp_mod(&a,&b,&q,&s);
  if(failure || q!=(mode?expected:0) || s!=sign || a.fpx!=42 || b.fpx!=3) return 1;
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='softfloat-adapter-', dir=ROOT / 'build') as temporary:
            stage = Path(temporary); source = stage / 'fixture.c'; binary = stage / 'fixture'
            env = {**env, 'TMPDIR': str(stage), 'TMP': str(stage), 'TEMP': str(stage)}
            args = [str(compiler), '-std=c11', '-O2', '-fstrict-aliasing', '-Werror=incompatible-pointer-types', str(source), '-o', str(binary)]
            source.write_text(prefix+adapters(original.decode())+'\n'+suffix)
            bad = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(bad.returncode, 0); self.assertIn('incompatible pointer types', bad.stderr)
            copied = stage / p.SOFTFLOAT; copied.parent.mkdir(parents=True); copied.write_bytes(original)
            p.source_overlay(stage)
            source.write_text(prefix+adapters(copied.read_text())+'\n'+suffix)
            good = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(subprocess.run([str(binary)], env=env, timeout=10).returncode, 0)
        self.assertEqual((ROOT / 'references/machawk-uae' / p.SOFTFLOAT).read_bytes(), original)
        self.assertEqual(p.wb.sha256(compiler), lock['tools']['clang']['sha256'])

class LegacyArtifactTests(unittest.TestCase):
    def test_current_source_built_guest_matches_all_effective_inputs(self):
        result = p.verify()
        self.assertEqual(result["status"], p.STATUS)
        self.assertFalse(result["historical_binary_reproduction"])
