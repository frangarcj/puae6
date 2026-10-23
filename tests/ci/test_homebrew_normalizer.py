"""Synthetic Mach-O data; no compiler, installation or target execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def command(kind, text, dylib=True):
    raw = text.encode() + b'\0'
    size = (24 if dylib else 12) + len(raw)
    size = (size + 7) & ~7
    head = struct.pack('<6I', kind, size, 24, 0, 0, 0) if dylib else struct.pack('<3I', kind, size, 12)
    return (head + raw).ljust(size, b'\0')


def fixture(dylib=False, dylib_id="@@HOMEBREW_PREFIX@@/opt/test/lib/libtest.dylib"):
    def segment(name, offset, size):
        return struct.pack('<II16sQQQQiiII', 0x19, 72, name.encode(), offset, 0x1000, offset, size, 7, 5, 0, 0)
    commands = [segment('__TEXT', 0, 4096), segment('__LINKEDIT', 4096, 16),
                struct.pack('<II16s', 0x1b, 24, bytes(range(16))),
                struct.pack('<6I', 0x32, 24, 1, 0x1a0000, 0x1a0500, 0),
                command(0xc, '@@HOMEBREW_PREFIX@@/opt/llvm/lib/libLLVM.dylib'),
                command(0xc, '/usr/lib/libSystem.B.dylib'),
                command(0x8000001c, '@@HOMEBREW_CELLAR@@/llvm/23.1.4/lib', False),
                command(0x8000001c, '@loader_path/../lib', False)]
    if dylib:
        commands.append(command(0xd, dylib_id))
    body = b''.join(commands)
    return (struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6 if dylib else 2, len(commands), len(body), 0, 0) + body).ljust(4112, b'\0')


def load_commands(data):
    offset = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, offset)
        yield kind, data[offset:offset + size]
        offset += size


class HomebrewNormalizerTests(unittest.TestCase):
    def setUp(self):
        ruby = os.environ.get('P6_TEST_RUBY') or shutil.which('ruby')
        if not ruby:
            self.fail('Ruby >= 3.3 is required; set P6_TEST_RUBY to an existing interpreter')
        version = subprocess.run([ruby, '-e', 'puts RUBY_VERSION'], capture_output=True, text=True, timeout=10)
        self.assertEqual(version.returncode, 0, 'Cannot inspect the existing Ruby interpreter')
        self.assertGreaterEqual(tuple(map(int, version.stdout.strip().split('.')[:2])), (3, 3),
                                'Ruby >= 3.3 required; set P6_TEST_RUBY to an existing interpreter')
        self.ruby = ruby
        (ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='normalizer-', dir=ROOT / 'build')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ('tools/ci', 'plan', 'build', 'references'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in ('tools/ci/normalize_homebrew.rb', 'plan/upstream.lock.json', 'references/ruby-macho.manifest.json'):
            shutil.copy2(ROOT / name, self.root / name)
        shutil.copytree(ROOT / 'references/ruby-macho', self.root / 'references/ruby-macho')
        self.target = self.root / 'build/fixture'
        self.target.write_bytes(fixture())
        self.target.chmod(0o755)

    def run_helper(self, target=None, prefix=(), options=()):
        args = [self.ruby, *prefix, str(self.root / 'tools/ci/normalize_homebrew.rb'), *options, str(target or self.target)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return result, json.loads(result.stdout)

    def test_relocation_signature_and_idempotence(self):
        for dylib in (False, True):
            with self.subTest(dylib=dylib):
                self.target.write_bytes(fixture(dylib))
                result, facts = self.run_helper()
                self.assertEqual(result.returncode, 0, facts)
                self.assertEqual(facts['replacements'], 3 if dylib else 2)
                self.assertEqual(facts['scope'], 'host-tool-data-normalization-only')
                data = self.target.read_bytes()
                self.assertEqual(facts['after_sha256'], hashlib.sha256(data).hexdigest())
                self.assertNotIn(b'@@HOMEBREW_', data)
                self.assertIn(b'/opt/homebrew/opt/llvm/lib/libLLVM.dylib\0', data)
                self.assertIn(b'/opt/homebrew/Cellar/llvm/23.1.4/lib\0', data)
                self.assertIn(b'@loader_path/../lib\0', data)
                signature = next(raw for kind, raw in load_commands(data) if kind == 0x1d)
                offset, size = struct.unpack_from('<II', signature, 8)
                magic, length, count = struct.unpack_from('>III', data, offset)
                self.assertEqual(magic, 0xfade0cc0)
                slots = dict(struct.unpack_from('>II', data, offset + 12 + i * 8) for i in range(count))
                cd = offset + slots[0]
                fields = struct.unpack_from('>9I4B', data, cd)
                self.assertEqual((fields[3], fields[9], fields[10], fields[12]), (2, 32, 2, 12))
                for index in range(fields[7]):
                    expected = hashlib.sha256(data[index * 4096:min((index + 1) * 4096, fields[8])]).digest()
                    self.assertEqual(data[cd + fields[4] + index * 32:cd + fields[4] + (index + 1) * 32], expected)
                identifier = data[cd + fields[5]:].split(b'\0', 1)[0]
                self.assertEqual(identifier, b'fixture-' + (b'UUID' + bytes(range(16))).hex().encode())
                before = self.target.stat()
                again, facts = self.run_helper()
                self.assertEqual(again.returncode, 0, facts)
                self.assertFalse(facts['modified'])
                self.assertEqual((data, before.st_mtime_ns, before.st_ino), (self.target.read_bytes(), self.target.stat().st_mtime_ns, self.target.stat().st_ino))
                self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)

    def test_explicit_rpath_id_uses_original_id_basename_signs_and_is_noop_when_equal(self):
        self.target.write_bytes(fixture(True, '@rpath/liboriginal.1.dylib'))
        self.assertEqual(0, self.run_helper()[0].returncode)
        before = list(load_commands(self.target.read_bytes()))
        identity = '/opt/homebrew/opt/test/lib/liboriginal.1.dylib'
        result, facts = self.run_helper(options=('--dylib-id', identity))
        self.assertEqual(0, result.returncode, facts); self.assertEqual(1, facts['replacements'])
        data = self.target.read_bytes(); commands = list(load_commands(data))
        id_command = next(raw for kind, raw in commands if kind == 0xd)
        self.assertEqual(identity.encode(), id_command[struct.unpack_from('<I', id_command, 8)[0]:].split(b'\0')[0])
        self.assertEqual([(k, v) for k, v in before if k not in (0xd, 0x1d)], [(k, v) for k, v in commands if k not in (0xd, 0x1d)])
        signature = next(raw for kind, raw in commands if kind == 0x1d)
        offset, size = struct.unpack_from('<II', signature, 8)
        magic, length, count = struct.unpack_from('>III', data, offset)
        self.assertEqual(0xfade0cc0, magic); self.assertLessEqual(length, size)
        slots = dict(struct.unpack_from('>II', data, offset + 12 + i * 8) for i in range(count))
        cd = offset + slots[0]; fields = struct.unpack_from('>9I4B', data, cd)
        self.assertEqual((2, 32, 2, 12), (fields[3], fields[9], fields[10], fields[12]))
        for index in range(fields[7]):
            expected = hashlib.sha256(data[index * 4096:min((index + 1) * 4096, fields[8])]).digest()
            self.assertEqual(expected, data[cd + fields[4] + index * 32:cd + fields[4] + (index + 1) * 32])
        stat = self.target.stat()
        result, facts = self.run_helper(options=('--dylib-id', identity))
        self.assertEqual(0, result.returncode, facts); self.assertFalse(facts['modified'])
        self.assertEqual((data, stat.st_ino, stat.st_mtime_ns), (self.target.read_bytes(), self.target.stat().st_ino, self.target.stat().st_mtime_ns))

    def test_invalid_explicit_id_and_executable_are_unchanged(self):
        valid = '/opt/homebrew/opt/test/lib/liboriginal.1.dylib'
        for identity in ('', '@rpath/liboriginal.1.dylib', '/foreign/liboriginal.1.dylib',
                         valid.replace('/lib/', '/../lib/'), valid.replace('/lib/', '//lib/'),
                         valid.replace('liboriginal.1', 'different'), valid + '\n', valid + '/extra'):
            self.target.write_bytes(fixture(True, '@rpath/liboriginal.1.dylib'))
            before = self.target.read_bytes()
            result, facts = self.run_helper(options=('--dylib-id', identity))
            self.assertNotEqual(0, result.returncode, facts); self.assertEqual(before, self.target.read_bytes())
        self.target.write_bytes(fixture(False)); before = self.target.read_bytes()
        result, facts = self.run_helper(options=('--dylib-id', valid))
        self.assertNotEqual(0, result.returncode, facts); self.assertEqual(before, self.target.read_bytes())

    def test_invalid_inputs_preserve_target(self):
        for case in ('bytes', 'type', 'signature_error', 'symlink', 'hardlink', 'outside', 'parent_link', 'extra_argument', 'old_ruby'):
            with self.subTest(case=case):
                self.target.write_bytes(fixture())
                target, prefix = self.target, ()
                if case == 'bytes': self.target.write_bytes(b'invalid')
                if case == 'type': self.target.write_bytes(fixture().replace(struct.pack('<I', 0x100000c), struct.pack('<I', 0x1000007), 1))
                if case == 'signature_error': self.target.write_bytes(fixture().replace(b'__LINKEDIT', b'__OTHER___'))
                if case == 'symlink':
                    target = self.root / 'build/link'; target.symlink_to(self.target)
                if case == 'hardlink': os.link(self.target, self.root / 'build/hardlink')
                if case == 'outside': target = self.root / 'outside'; target.write_bytes(fixture())
                if case == 'parent_link':
                    link = self.root / 'build/parent'; link.symlink_to(self.root / 'build', target_is_directory=True); target = link / 'fixture'
                if case == 'extra_argument': prefix = ('-e', 'ARGV << "extra"; load ARGV.shift')
                if case == 'old_ruby': prefix = ('-e', 'Object.send(:remove_const, :RUBY_VERSION); RUBY_VERSION="3.2.0"; load ARGV.shift')
                before = self.target.read_bytes()
                result, facts = self.run_helper(target, prefix)
                self.assertNotEqual(result.returncode, 0, facts)
                self.assertEqual(facts['status'], 'FAIL')
                self.assertEqual(self.target.read_bytes(), before)
                if case == 'hardlink': (self.root / 'build/hardlink').unlink()

    def test_capture_is_audited_before_require(self):
        library = self.root / 'references/ruby-macho/lib/macho.rb'
        original = library.read_bytes()
        for case in ('bytes', 'mode', 'special_mode', 'extra', 'missing', 'symlink', 'pin', 'manifest_path', 'manifest_sha'):
            with self.subTest(case=case):
                library.write_bytes(original); library.chmod(0o644)
                lock = self.root / 'plan/upstream.lock.json'
                lock.write_bytes((ROOT / 'plan/upstream.lock.json').read_bytes())
                manifest = self.root / 'references/ruby-macho.manifest.json'
                manifest.write_bytes((ROOT / 'references/ruby-macho.manifest.json').read_bytes())
                if case == 'bytes': library.write_text('abort "UNVERIFIED CODE EXECUTED"')
                if case == 'mode': library.chmod(0o755)
                if case == 'special_mode': library.chmod(0o4644)
                extra = self.root / 'references/ruby-macho/extra'
                if case == 'extra': extra.write_text('extra')
                if case == 'missing': library.unlink()
                if case == 'symlink': library.unlink(); library.symlink_to(ROOT / 'references/ruby-macho/lib/macho.rb')
                if case == 'pin':
                    data = json.loads(lock.read_text()); next(s for s in data['sources'] if s['id'] == 'ruby_macho')['commit'] = '0' * 40; lock.write_text(json.dumps(data))
                if case == 'manifest_path':
                    data = json.loads(manifest.read_text()); data['files'][0]['path'] = '../escape'; manifest.write_text(json.dumps(data))
                if case == 'manifest_sha':
                    data = json.loads(manifest.read_text()); data['files'][0]['sha256'] = '0' * 64; manifest.write_text(json.dumps(data))
                before = self.target.read_bytes()
                result, facts = self.run_helper()
                self.assertNotEqual(result.returncode, 0, facts)
                self.assertNotIn('UNVERIFIED CODE EXECUTED', result.stderr)
                self.assertEqual(self.target.read_bytes(), before)
                if library.is_symlink(): library.unlink()
                if extra.exists(): extra.unlink()
