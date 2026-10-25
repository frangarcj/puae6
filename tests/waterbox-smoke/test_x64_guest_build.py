"""Own ELF/tool fixtures for the isolated x64 build; no guest compilation/execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox_x64 as x64


def elf():
    raw = bytearray(512)
    struct.pack_into('<16sHHIQQQIHHHHHH', raw, 0, b'\x7fELF\x02\x01' + bytes(10),
                     2, 62, 1, 0, 64, 256, 0, 64, 56, 1, 64, 2, 0)
    struct.pack_into('<IIQQQQQQ', raw, 64, 1, 5, 0, 0x100000, 0x100000, 512, 4096, 4096)
    struct.pack_into('<IIQQQQIIQQ', raw, 320, 0, 2, 0, 0, 384, 24, 0, 0, 8, 24)
    return raw


class X64GuestBuildTests(unittest.TestCase):
    def test_derived_linkscript_has_page_boundaries_and_authenticates_both_versions(self):
        spec = json.loads(x64.PROFILE.read_text())
        text = x64.linkscript(spec)
        self.assertNotIn('ALIGN(CONSTANT (MAXPAGESIZE)) + (. & (CONSTANT (MAXPAGESIZE) - 1)) :', text)
        for field in ('sha256', 'derived_sha256'):
            wrong = copy.deepcopy(spec); wrong['linkscript'][field] = '0'*64
            with self.subTest(field=field), self.assertRaises(x64.Error): x64.linkscript(wrong)

    def test_aarch64_probe_branch_is_unchanged(self):
        text = (ROOT / 'adapters/waterbox/abi_probe.cpp').read_text()
        prefix, branches = text.split('#if defined(__aarch64__)\n', 1)
        original = prefix + branches.split('\n#elif defined(__x86_64__)', 1)[0]
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         'a1a80d998dbb3b0622234e4936c873d39298b21780f0d08aae87c89746722e28')

    def test_windows_mapping_rejects_the_observed_four_kib_base(self):
        raw = elf(); struct.pack_into('<Q', raw, 80, 0x101000)
        with self.assertRaisesRegex(x64.Error, 'mapping base'):
            self.check(raw)

    @unittest.skipUnless(os.environ.get('P6_TEST_X64_LAYOUT_LINK') == '1', 'explicit tiny real LLD layout check')
    def test_real_link_keeps_windows_mapping_base_and_four_kib_permissions(self):
        spec = json.loads(x64.PROFILE.read_text()); tools, _ = x64.verify_profile(spec)
        script = x64.linkscript(spec)
        marker = '  .wbx_image_base 0x36f00000000 : { BYTE(0) }\n'
        self.assertEqual(script.count(marker), 1)
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as temporary:
            stage = Path(temporary); source = stage / 'own.s'; obj = stage / 'own.o'
            source.write_text('.text\n.global _start\n_start: ret\n.rodata\n.quad 42\n.data\n.quad 99\n')
            subprocess.run([str(tools['clang']), '--target=x86_64-linux-gnu', '-c', str(source), '-o', str(obj)], check=True)
            for name, text in (('original', script.replace(marker, '')), ('fixed', script)):
                linker = stage / (name + '.T'); linker.write_text(text)
                guest = stage / (name + '.wbx')
                subprocess.run([str(tools['lld']), '-static', '-z', 'max-page-size=4096', '-T', str(linker), str(obj), '-o', str(guest)], check=True)
                if name == 'original':
                    with self.assertRaisesRegex(x64.Error, 'mapping base'):
                        x64.validate_elf(guest)
                else:
                    result = x64.validate_elf(guest)
                    self.assertEqual(result['mapping_base'], 0x36f00000000)
                    first = result['load_segments'][0]
                    self.assertEqual((first['vaddr'], first['memsz'], first['flags']), (0x36f00000000, 1, 4))
                    self.assertEqual({s['flags'] for s in result['load_segments']}, {4, 5, 6})

    @unittest.skipUnless(os.environ.get('P6_TEST_X64_PROBE_COMPILE') == '1', 'explicit tiny cross-compile check')
    def test_probe_compiles_for_both_guest_architectures(self):
        spec = json.loads(x64.PROFILE.read_text())
        tools, _ = x64.verify_profile(spec)
        arm_sysroot = Path(os.environ.get('P6_ARM_SYSROOT',
            '/Users/frangar/Fun/uae/build/waterbox-guest-sysroot/sysroot'))
        x64.OUTPUT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=x64.OUTPUT) as tmp:
            for arch, sysroot, flags in (
                    ('x86_64', Path(spec['sysroot']['path']), []),
                    ('aarch64', arm_sysroot, ['-ffixed-x18', '-ffixed-x28'])):
                obj = Path(tmp) / (arch + '.o')
                subprocess.run([str(tools['clang']), '--target=' + arch + '-linux-gnu', *flags,
                    '-std=c++17', '-O2', '-nostdlibinc', '-nostdinc++', '-isystem', str(sysroot / 'include'),
                    '-I', str(ROOT / 'references/machawk/waterbox/emulibc'), '-c',
                    str(ROOT / 'adapters/waterbox/abi_probe.cpp'), '-o', str(obj)], check=True)
                machine = int.from_bytes(obj.read_bytes()[18:20], 'little')
                self.assertEqual(machine, 62 if arch == 'x86_64' else 183)
                symbols = x64.run([tools['nm'], '--defined-only', obj])
                for name in ('p6w_probe_abi_registers', 'p6w_probe_abi', 'p6w_identity'):
                    self.assertRegex(symbols, r'(?m)\b' + name + '$')
                if arch == 'x86_64':
                    disassembly = x64.run([tools['objdump'], '-d', '--no-show-raw-insn', obj])
                    self.assertGreater(x64.check_segment_access(disassembly), 0)
                    for canary in ('0xb003', '0xb005', '0xb012', '0xb013', '0xb014', '0xb015'):
                        self.assertIn(canary, disassembly.lower())

    def check(self, raw):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'own.elf'
            p.write_bytes(raw)
            return x64.validate_elf(p)

    def test_x86_64_4k_fixture_and_wrong_isa(self):
        self.assertEqual(self.check(elf())['machine'], 62)
        raw = elf(); struct.pack_into('<H', raw, 18, 183)
        with self.assertRaisesRegex(x64.Error, 'EM_X86_64'):
            self.check(raw)

    def test_bad_load_bounds_alignment_and_writable_code(self):
        for offset, value in ((72, 1), (104, 2**32 + 1), (112, 1024)):
            raw = elf(); struct.pack_into('<Q', raw, offset, value)
            with self.assertRaisesRegex(x64.Error, 'load segment'):
                self.check(raw)
        raw = elf(); struct.pack_into('<I', raw, 68, 7)
        with self.assertRaisesRegex(x64.Error, 'load segment'):
            self.check(raw)

    def test_tls_segments_sections_symbols_and_unresolved_imports(self):
        variants = []
        raw = elf(); struct.pack_into('<I', raw, 64, 7); variants.append(raw)
        raw = elf(); struct.pack_into('<Q', raw, 328, 0x400); variants.append(raw)
        raw = elf(); raw[388] = 6; variants.append(raw)
        raw = elf(); struct.pack_into('<IB', raw, 384, 1, 0x10); variants.append(raw)
        for raw in variants:
            with self.assertRaises(x64.Error):
                self.check(raw)
        raw = elf(); struct.pack_into('<Q', raw, 344, 10000)
        with self.assertRaises(x64.Error):
            self.check(raw)

    def test_hidden_weak_reference_localized_to_zero_is_not_an_external_import(self):
        raw = elf(); struct.pack_into('<IB', raw, 384, 1, 0)
        self.assertEqual(self.check(raw)['machine'], 62)
        struct.pack_into('<Q', raw, 392, 1)
        with self.assertRaises(x64.Error): self.check(raw)

    def test_compiler_tls_disassembly_and_explicit_sandbox_context(self):
        self.assertEqual(x64.check_segment_access('movq %gs:0x18, %rax'), 1)
        for instruction in ('movq %fs:0x0,%rax', 'movq %gs:0x8,%rax', 'movq %gs:(%rax),%rax'):
            with self.assertRaisesRegex(x64.Error, 'TLS access'):
                x64.check_segment_access(instruction)

    def test_profile_pairs_and_tool_tampering_fail_before_sysroot(self):
        spec = json.loads(x64.PROFILE.read_text())
        for key, value in (('target', 'aarch64-linux-gnu'), ('machine', 183), ('host_page_size', 16384)):
            wrong = copy.deepcopy(spec); wrong[key] = value
            with self.assertRaisesRegex(x64.Error, 'profile'):
                x64.verify_profile(wrong)
        with patch.object(x64.shared, 'sha256', return_value='0' * 64):
            with self.assertRaisesRegex(x64.Error, 'tool differs'):
                x64.verify_profile(spec)
        self.assertFalse(spec['publication_ready'])
        self.assertTrue(spec['release_source_rebuild_verified'])

    def test_make_keeps_arm_defaults_and_refuses_mixed_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'references/machawk/waterbox').mkdir(parents=True)
            # Stub only common.mak, so evaluation tests the actual PUAE6 profile selector.
            (root / 'references/machawk/waterbox/common.mak').write_text(
                'probe:\n\t@echo $(WBX_TARGET_ARCH)/$(WBX_HOST_PAGE_SIZE)\n')
            command = ['make', '-s', '-f', str(ROOT / 'ports/waterbox/Makefile'), 'probe',
                       'REPO=' + str(root), 'SOURCES_MK=/dev/null']
            for args, expected in (([], 'aarch64/16384'), (['WBX_TARGET_ARCH=x86_64'], 'x86_64/4096')):
                self.assertEqual(subprocess.check_output(command + args, text=True).strip(), expected)
            for pair in ('x86_64/16384', 'aarch64/4096', 'mips/4096'):
                arch, size = pair.split('/')
                result = subprocess.run(command + ['WBX_TARGET_ARCH=' + arch, 'WBX_HOST_PAGE_SIZE=' + size],
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Unsupported PUAE6', result.stderr)

    def test_sysroot_tampering_is_refused(self):
        spec = json.loads(x64.PROFILE.read_text())
        pins = {entry['path']: entry for entry in spec['tools'].values()}
        with (patch.object(x64.shared, 'sha256', side_effect=lambda p: pins[str(p)]['sha256']),
              patch.object(x64, 'run', side_effect=lambda args: pins[str(args[0])]['version']),
              patch.object(x64.shared, 'inventory', return_value=[])):
            with self.assertRaisesRegex(x64.Error, 'sysroot inventory'):
                x64.verify_profile(spec)


if __name__ == '__main__':
    unittest.main()
