"""Offline recipe tests: Docker/compiler execution is mocked; Git/capture checks are real."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox_sysroot as recipe
import import_sources as sources


class SysrootRecipeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='sysroot-recipe-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.llvm = self.root / 'llvm'
        self.llvm.mkdir()
        for name in recipe.LLVM_DIRS:
            p = self.llvm / name / ('LICENSE.TXT' if name in ('compiler-rt', 'libcxx', 'libcxxabi', 'libunwind') else 'input')
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text('own fixture\n')
        self.git('init', '-q'); self.git('add', '.'); self.git('commit', '-qm', 'fixture')
        self.git('tag', '-a', 'llvmorg-18.1.8', '-m', 'fixture')
        items = [{'id': 'compiler_rt_emutls', 'repository': 'https://example.invalid/fixture',
                  'local_path': 'unused', 'commit': self.git('rev-parse', 'HEAD').strip(),
                  'tag': 'llvmorg-18.1.8', 'tag_object': self.git('rev-parse', 'refs/tags/llvmorg-18.1.8').strip(),
                  'snapshot': {'destination': 'references/llvm', 'manifest': 'references/llvm.json'}}]
        for sid, files in (('musl', {'COPYRIGHT': 'own fixture', 'configure': 'own fixture', 'source.c': 'overlay'}),
                           ('machawk', {'LICENSE': 'own fixture', **{f'waterbox/libcxx/configure-for-waterbox-phase-{p}': 'own fixture'
                                                                    for p in ('-', '0', '1', '2')}})):
            item = {'id': sid, 'repository': None, 'local_path': 'missing-private-checkout', 'commit': 'a' * 40,
                    'snapshot': {'destination': 'references/' + sid, 'manifest': 'references/' + sid + '.json'}}
            d = self.root / item['snapshot']['destination']; d.mkdir(parents=True)
            for name, content in files.items():
                p = d / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
            if sid == 'musl': item['snapshot']['overlay_sha256'] = {'source.c': recipe.wb.sha256(d / 'source.c')}
            manifest = {**sources.metadata(item), 'files': sources.inventory(d)}
            (self.root / item['snapshot']['manifest']).write_text(json.dumps(manifest))
            items.append(item)
        (self.root / 'plan').mkdir()
        self.upstream = self.root / 'plan/upstream.lock.json'
        self.upstream.write_text(json.dumps({'sources': items}))
        self.image = 'sha256:' + 'c' * 64
        entries = [{'path': n, 'mode': '100644', 'sha256': hashlib.sha256(b'' if n in recipe.MARKERS else b'header').hexdigest()}
                   for n in sorted((*recipe.MARKERS, 'include/header.h'))]
        self.lock = {'waterbox_sysroot_rebuild': {'image_id': self.image},
                     'sysroot': {'associated_source': 'musl', 'file_count': len(entries),
                                 'inventory_sha256': recipe.wb.canonical_hash(entries)}}
        self.lockpath = self.root / 'plan/toolchains.json'; self.lockpath.write_text(json.dumps(self.lock))
        self.output = self.root / 'build/candidate'
        self.calls = []
        original = recipe.command
        self.raw_command = original
        def compiler_mock(args, *, build=False):
            self.calls.append(args)
            if args[0] == 'git': return original(args, build=build)
            if args[1] == 'image':
                return json.dumps({'Id': args[3], 'Os': 'linux', 'Architecture': 'arm64'})
            if not build: return json.dumps({'os': 'Linux', 'architecture': 'aarch64', 'tools': {'clang': 'mocked'}})
            p = self.output / 'musl-sysroot-aarch64-darwin/include/header.h'
            p.parent.mkdir(parents=True); p.write_bytes(b'header')
        self.compiler_mock = compiler_mock
        self.command_mock = self.enterContext(mock.patch.object(recipe, 'command', side_effect=compiler_mock))
        self.enterContext(mock.patch.object(recipe, 'ROOT', self.root))

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.llvm), '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@example.invalid', *args],
                              capture_output=True, text=True, check=True).stdout

    def test_dry_run_is_not_run_and_mounts_only_new_staging_writable(self):
        result = recipe.rebuild(self.llvm, self.output)
        self.assertEqual('NOT_RUN', result['status']); self.assertFalse(self.output.exists())
        args = result['command']
        self.assertIn('--pull=never', args); self.assertIn('--network=none', args)
        self.assertEqual('linux/arm64', args[args.index('--platform') + 1])
        mounts = [args[i + 1] for i, v in enumerate(args) if v == '--mount']
        self.assertEqual(5, len(mounts))
        self.assertEqual([f'type=bind,source={self.output},target=/work'], [m for m in mounts if not m.endswith(',readonly')])
        self.assertIn('-DWBX_HOST_PAGE_SIZE=16384', recipe.SCRIPT)
        self.assertIn('-ffixed-x18 -ffixed-x28', recipe.SCRIPT)
        self.assertIn('for phase in - 0 1 2', recipe.SCRIPT)
        self.assertIn('-j2 --target install', recipe.SCRIPT)
        self.assertIn('/work/notices', recipe.SCRIPT)
        self.assertNotIn('wbox_configure_aarch64.sh', recipe.SCRIPT)

    def test_mocked_build_passes_only_exact_inventory_and_keeps_input_identity(self):
        override = 'sha256:' + 'd' * 64
        result = recipe.rebuild(self.llvm, self.output, override, execute=True)
        self.assertEqual('PASS', result['status']); self.assertEqual(override, result['image_id'])
        self.assertEqual(self.lock['sysroot']['inventory_sha256'], result['inventory_sha256'])
        self.assertIn('no historical execution', result['marker_semantics'])
        with self.assertRaises(recipe.Blocked): recipe.rebuild(self.llvm, self.output, execute=True)

    def test_changed_capture_and_overlay_are_rejected_before_output(self):
        target = self.root / 'references/musl/source.c'; target.write_text('changed')
        with self.assertRaises(ValueError): recipe.rebuild(self.llvm, self.output)
        target.write_text('overlay')
        data = json.loads(self.upstream.read_text())
        data['sources'][1]['snapshot']['overlay_sha256']['source.c'] = 'f' * 64
        self.upstream.write_text(json.dumps(data))
        p = self.root / 'references/musl.json'; manifest = json.loads(p.read_text())
        manifest['source'] = data['sources'][1]; p.write_text(json.dumps(manifest))
        with self.assertRaises(recipe.Blocked): recipe.rebuild(self.llvm, self.output)
        self.assertFalse(self.output.exists())

    def test_llvm_dirty_missing_and_wrong_pins_are_rejected(self):
        p = self.llvm / 'libcxx/LICENSE.TXT'; p.write_text('dirty')
        with self.assertRaises(ValueError): recipe.rebuild(self.llvm, self.output)
        p.write_text('own fixture\n')
        self.git('commit', '--allow-empty', '-qm', 'wrong HEAD')
        with self.assertRaises(ValueError): recipe.rebuild(self.llvm, self.output)
        self.assertFalse(self.output.exists())
        with self.assertRaises(OSError): recipe.rebuild(self.root / 'missing-llvm', self.output)

    def test_missing_prerequisite_compile_error_and_mismatch_never_pass(self):
        def fail_tools(args, **kwargs):
            if recipe.PREFLIGHT in args: raise recipe.Blocked('missing ld.lld')
            return self.compiler_mock(args, **kwargs)
        self.command_mock.side_effect = fail_tools
        with self.assertRaises(recipe.Blocked): recipe.rebuild(self.llvm, self.output, execute=True)
        self.assertFalse(self.output.exists())
        def fail_compile(args, **kwargs):
            if kwargs.get('build'): raise recipe.Failed('mock compile failed')
            return self.compiler_mock(args, **kwargs)
        self.command_mock.side_effect = fail_compile
        with self.assertRaises(recipe.Failed): recipe.rebuild(self.llvm, self.output, execute=True)
        self.assertFalse((self.output / 'musl-sysroot-aarch64-darwin').exists())

    def test_bad_inventory_image_or_existing_output_are_not_adopted(self):
        self.lock['sysroot']['inventory_sha256'] = '0' * 64; self.lockpath.write_text(json.dumps(self.lock))
        with self.assertRaisesRegex(recipe.Failed, 'inventory mismatch'):
            recipe.rebuild(self.llvm, self.output, execute=True)
        for image in ('latest', 'puae6-portability:arm64'):
            with self.assertRaises(recipe.Blocked): recipe.rebuild(self.llvm, self.root / 'build/other', image)
        with self.assertRaises(recipe.Blocked): recipe.rebuild(self.llvm, self.root / 'outside')

    def test_cli_not_run_is_nonzero_and_does_not_claim_success(self):
        with (mock.patch.object(sys, 'argv', ['recipe', '--llvm-source', str(self.llvm), '--output', str(self.output)]),
              mock.patch('builtins.print')):
            self.assertEqual(3, recipe.main())
        self.assertFalse(self.output.exists())

    def test_image_isa_and_ignored_llvm_extras_are_rejected_before_output(self):
        def wrong_isa(args, **kwargs):
            if args[:3] == ['docker', 'image', 'inspect']:
                return json.dumps({'Id': self.image, 'Os': 'linux', 'Architecture': 'amd64'})
            return self.compiler_mock(args, **kwargs)
        self.command_mock.side_effect = wrong_isa
        with self.assertRaisesRegex(recipe.Blocked, 'identity/OS/ISA'): recipe.rebuild(self.llvm, self.output)
        self.command_mock.side_effect = self.compiler_mock
        (self.llvm / '.git/info/exclude').write_text('libcxx/ignored\n')
        (self.llvm / 'libcxx/ignored').write_text('not a pinned source')
        with self.assertRaisesRegex(recipe.Blocked, 'closure differs'): recipe.rebuild(self.llvm, self.output)
        self.assertFalse(self.output.exists())

    def test_git_audit_disables_lazy_fetch_and_restores_process_environment(self):
        original = sources.git
        def offline(*args, **kwargs):
            self.assertEqual('1', recipe.os.environ['GIT_NO_LAZY_FETCH'])
            return original(*args, **kwargs)
        with mock.patch.dict(recipe.os.environ, {'GIT_NO_LAZY_FETCH': '0'}):
            with mock.patch.object(sources, 'git', side_effect=offline): recipe.llvm_capture(self.llvm)
            self.assertEqual('0', recipe.os.environ['GIT_NO_LAZY_FETCH'])

    def test_command_timeouts_are_blocked_for_preflight_and_failed_for_build(self):
        for build, seconds, error in ((False, 180, recipe.Blocked), (True, 28800, recipe.Failed)):
            with self.subTest(build=build), mock.patch.object(recipe.subprocess, 'run',
                    side_effect=subprocess.TimeoutExpired('compiler-mock', seconds)) as run:
                with self.assertRaisesRegex(error, f'timeout after {seconds}s'):
                    self.raw_command(['compiler-mock'], build=build)
                self.assertEqual(seconds, run.call_args.kwargs['timeout'])
