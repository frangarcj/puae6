"""Offline tests for the Waterbox ELF build gate."""

import struct
import json
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build_waterbox.py"
sys.path.insert(0, str(TOOL.parent))
import build_waterbox  # noqa: E402
import import_sources  # noqa: E402


def elf64(loads, machine=183, elf_class=2, elf_type=2):
    """Make a minimal ELF64 little-endian executable with PT_LOAD headers."""
    ident = b"\x7fELF" + bytes((elf_class, 1, 1, 0)) + bytes(8)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, elf_type, machine, 1,
                          0, 64, 0, 0, 64, 56, len(loads), 0, 0, 0)
    phdrs = b"".join(struct.pack("<IIQQQQQQ", 1, flags, off, va, va,
                                    filesz, memsz, 0x4000)
                      for off, va, filesz, memsz, flags in loads)
    size = max([64 + len(phdrs)] + [off + filesz for off, _, filesz, _, _ in loads])
    data = bytearray(size)
    data[:64] = header
    data[64:64 + len(phdrs)] = phdrs
    return data


class GuestToolchainCacheTests(unittest.TestCase):
    def test_changed_inputs_or_partial_failure_force_a_complete_rebuild(self):
        import prepare_hrtmon
        with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='wb-toolchain-') as directory:
            base = Path(directory)
            cc, ld = base / 'cc', base / 'ld'
            cc.write_text('compiler wrapper'); ld.write_text('linker wrapper')
            tc = {'lock': {'tools': {'clang': {'sha256': 'a' * 64}}}, 'versions': {'clang': 'test compiler'},
                  'paths': {'make': Path('/usr/bin/make')}, 'sysroot_copy': base,
                  'sysroot_inventory': [{'sha256': 'b' * 64}], 'cc_wrapper': cc, 'ld_wrapper': ld, 'env': {},
                  'source_audit': {'mode': 'checkout', 'sources': {}}}
            receipt = base / 'puae6/puae6-receipt.json'
            commands = []
            def make(command, **kwargs):
                self.assertFalse(receipt.exists(), 'invalidate before any object is overwritten')
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)
            with (mock.patch.object(build_waterbox, 'BUILD', base),
                  mock.patch.object(build_waterbox, 'ensure_current_trees'),
                  mock.patch.object(prepare_hrtmon, 'prepare'),
                  mock.patch.object(build_waterbox, 'puae6_sources', return_value=['adapters/waterbox/cd_source.cpp']),
                  mock.patch.object(build_waterbox, 'validate_elf', return_value={'sha256': 'c' * 64, 'size': 1}),
                  mock.patch.object(build_waterbox, 'check_no_compiler_tls', return_value={'wb16': 'PASS'}),
                  mock.patch.object(build_waterbox, 'audit_imports', return_value={'unexpected': []}),
                  mock.patch('builtins.print'),
                  mock.patch.object(build_waterbox.subprocess, 'run', side_effect=make) as run):
                def build(forced):
                    result = build_waterbox.build_puae6(tc, 2)
                    self.assertEqual(forced, '-B' in commands[-1])
                    self.assertEqual(tc['lock']['tools']['clang']['sha256'], result['tool_hashes']['clang'])
                    self.assertEqual(tc['source_audit'], result['source_audit'])
                build(True)   # No receipt, including existing objects from an older build.
                build(False)  # Identical inputs keep the normal incremental build.
                command = commands[-1]
                tc['source_audit'] = {'mode': 'snapshot', 'sources': {}}
                build(False)  # Audit bookkeeping changes neither inputs nor make.
                self.assertEqual(command, commands[-1])
                for change in ('compiler', 'version', 'sysroot', 'wrapper', 'flags', 'recipe', 'sources'):
                    if change == 'compiler': tc['lock']['tools']['clang']['sha256'] = 'd' * 64
                    elif change == 'version': tc['versions']['clang'] = 'another compiler'
                    elif change == 'sysroot': tc['sysroot_inventory'][0]['sha256'] = 'e' * 64
                    elif change == 'wrapper': cc.write_text('new wrapper')
                    else:
                        previous = json.loads(receipt.read_text())
                        previous['build_inputs'][change] = {'obsolete': True}
                        receipt.write_text(json.dumps(previous))
                    build(True)
                run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 1)
                with self.assertRaises(build_waterbox.BuildError): build_waterbox.build_puae6(tc, 2)
                self.assertFalse(receipt.exists())
                run.side_effect = make
                build(True)   # A failed partial build must not reuse the prior identity.


class SourceAuditTests(unittest.TestCase):
    """Real Git/capture/wrapper checks; all compiler execution is mocked."""

    def setUp(self):
        self.enterContext(mock.patch.dict(build_waterbox.os.environ, {build_waterbox.SOURCE_AUDIT_ENV: 'checkout'}))
        (ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='wb-audit-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / 'build/waterbox'
        (self.root / 'plan').mkdir()
        self.items = []
        for sid, content in (
                ('machawk', {'waterbox/waterboxhost/src/core.rs': 'original\n',
                             'waterbox/waterboxhost/Cargo.lock': 'lock',
                             'waterbox/common.mak': 'make', 'waterbox/linkscript_aarch64.T': 'link',
                             'waterbox/emulibc/emulibc.c': 'emulibc'}),
                ('waterbox_musl_darwin', {f'tools/{n}': (ROOT / 'references/musl-darwin/tools' / n).read_text()
                                        for n in ('musl-clang.in', 'ld.musl-clang.in')})):
            checkout = self.root / ('origin-' + sid)
            checkout.mkdir()
            for name, text in content.items():
                p = checkout / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
            self.git(checkout, 'init', '-q')
            self.git(checkout, 'add', '.')
            self.git(checkout, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '-qm', 'fixture')
            item = {'id': sid, 'repository': None, 'local_path': checkout.name,
                    'commit': self.git(checkout, 'rev-parse', 'HEAD').strip(),
                    'snapshot': {'destination': 'references/' + sid, 'manifest': 'references/' + sid + '.json'}}
            if sid == 'machawk':
                rel = 'waterbox/waterboxhost/src/core.rs'
                (checkout / rel).write_text('overlay\n')
                item['snapshot']['overlay_sha256'] = {rel: build_waterbox.sha256(checkout / rel)}
            self.items.append(item)
            dest = self.root / item['snapshot']['destination']; dest.mkdir(parents=True)
            for name, mode, data in import_sources.blobs(checkout, item):
                p = dest / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
                p.chmod(0o755 if mode == '100755' else 0o644)
            manifest = {**import_sources.metadata(item), 'files': import_sources.inventory(dest)}
            (self.root / item['snapshot']['manifest']).write_text(json.dumps(manifest))
        self.upstream = self.root / 'plan/upstream.lock.json'
        self.upstream.write_text(json.dumps({'sources': self.items}))
        sysroot = self.root / 'sysroot'; sysroot.mkdir(); (sysroot / 'header.h').write_text('header')
        self.paths = {n: Path('/usr/bin/true') for n in ('clang', 'lld', 'cargo', 'rustc', 'make')}
        self.versions = {n: 'mock compiler' for n in self.paths}
        patch = self.root / 'integration/waterbox-runtime/fixture.patch'
        patch.parent.mkdir(parents=True)
        patch.write_text('diff --git a/src/core.rs b/src/core.rs\n--- a/src/core.rs\n+++ b/src/core.rs\n'
                         '@@ -1 +1 @@\n-overlay\n+patched\n')
        lock = {'schema_version': 1, 'profile': 'fixture',
                'tools': {n: {'sha256': 'a' * 64, 'version': self.versions[n]} for n in self.paths},
                'sysroot': {'source_path': 'sysroot', 'file_count': 1,
                            'inventory_sha256': build_waterbox.canonical_hash(import_sources.inventory(sysroot)),
                            'associated_source': 'waterbox_musl_darwin'},
                'runtime': {'target': 'aarch64-apple-darwin', 'profile': 'release', 'rustc_bootstrap': '1',
                            'patches': [{'file': str(patch.relative_to(self.root)), 'sha256': build_waterbox.sha256(patch),
                                         'files': [{'path': 'src/core.rs',
                                                    'before_sha256': self.items[0]['snapshot']['overlay_sha256']['waterbox/waterboxhost/src/core.rs'],
                                                    'after_sha256': build_waterbox.hashlib.sha256(b'patched\n').hexdigest()}]}]}}
        self.lock = self.root / 'plan/toolchains.json'; self.lock.write_text(json.dumps(lock))
        for name, value in (('ROOT', self.root), ('BUILD', self.out), ('LOCK', self.lock)):
            self.enterContext(mock.patch.object(build_waterbox, name, value))
        self.enterContext(mock.patch.object(build_waterbox.platform, 'system', return_value='Darwin'))
        self.enterContext(mock.patch.object(build_waterbox.platform, 'machine', return_value='arm64'))
        self.enterContext(mock.patch.object(build_waterbox, 'locked_tools', return_value=(self.paths, self.versions)))

    @staticmethod
    def git(checkout, *args):
        return subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True, text=True).stdout

    def test_snapshot_needs_no_origin_checkout_and_preserves_all_materialized_bytes(self):
        checkout = build_waterbox.toolchain()
        before = import_sources.inventory(checkout['runtime_copy'])
        checkout['runtime_copy'].rename(self.out / 'checkout-runtime')
        for item in self.items:  # Valid captures, with deliberately missing origins.
            (self.root / item['local_path']).rename(self.root / ('unused-' + item['id']))
        with mock.patch.object(import_sources, 'git', side_effect=AssertionError('no origin Git inspection')):
            snapshot = build_waterbox.toolchain('snapshot')
        self.assertEqual(before, import_sources.inventory(snapshot['runtime_copy']))
        self.assertEqual('patched\n', (snapshot['runtime_copy'] / 'src/core.rs').read_text())
        self.assertEqual({k: v for k, v in checkout.items() if k != 'source_audit'},
                         {k: v for k, v in snapshot.items() if k != 'source_audit'})
        for sid, record in snapshot['source_audit']['sources'].items():
            original = checkout['source_audit']['sources'][sid]
            self.assertTrue(original['checkout_head_blobs_overlays_verified'])
            self.assertFalse(record['checkout_head_blobs_overlays_verified'])
            self.assertEqual({k: v for k, v in original.items() if k != 'checkout_head_blobs_overlays_verified'},
                             {k: v for k, v in record.items() if k != 'checkout_head_blobs_overlays_verified'})
        with self.assertRaises(build_waterbox.BuildError): build_waterbox.toolchain()

    def test_checkout_rejects_changed_head_blobs_and_overlays_without_fallback(self):
        origin = self.root / self.items[0]['local_path']
        for rel in ('waterbox/common.mak', 'waterbox/waterboxhost/src/core.rs'):
            p = origin / rel; before = p.read_bytes(); p.write_bytes(b'changed')
            with self.subTest(rel=rel), self.assertRaises(build_waterbox.BuildError):
                build_waterbox.source_spec('machawk')
            p.write_bytes(before)
        self.git(origin, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--allow-empty', '-qm', 'wrong HEAD')
        with self.assertRaises(build_waterbox.BuildError): build_waterbox.source_spec('machawk')
        build_waterbox.source_spec('machawk', 'snapshot')

    def test_snapshot_rejects_complete_capture_and_provenance_drift(self):
        target = self.root / 'references/machawk/waterbox/common.mak'
        manifest = self.root / self.items[0]['snapshot']['manifest']
        original_manifest, original_lock = manifest.read_bytes(), self.upstream.read_bytes()
        for change in ('content', 'mode', 'missing', 'extra', 'symlink', 'manifest', 'pin', 'overlay', 'overlay-bytes'):
            before = target.read_bytes()
            extra = target.parent / 'extra'
            try:
                if change == 'content': target.write_text('changed')
                elif change == 'mode': target.chmod(0o755)
                elif change == 'missing': target.unlink()
                elif change == 'extra': extra.write_text('extra')
                elif change == 'symlink': target.unlink(); target.symlink_to('/etc/passwd')
                elif change == 'manifest': manifest.write_text('{}')
                else:
                    d = json.loads(original_lock)
                    if change == 'pin': d['sources'][0]['commit'] = 'b' * 40
                    elif change == 'overlay': d['sources'][0]['snapshot']['overlay_sha256'] = {}
                    else:
                        rel = 'waterbox/waterboxhost/src/core.rs'
                        d['sources'][0]['snapshot']['overlay_sha256'][rel] = 'b' * 64
                        captured = json.loads(original_manifest); captured['source'] = d['sources'][0]
                        manifest.write_text(json.dumps(captured))
                    self.upstream.write_text(json.dumps(d))
                with self.subTest(change=change), self.assertRaises(build_waterbox.BuildError):
                    build_waterbox.source_spec('machawk', 'snapshot')
            finally:
                target.unlink(missing_ok=True); target.write_bytes(before); target.chmod(0o644)
                extra.unlink(missing_ok=True); manifest.write_bytes(original_manifest); self.upstream.write_bytes(original_lock)
        with self.assertRaises(build_waterbox.BuildError): build_waterbox.source_spec('machawk', 'auto')

    def mock_runtime_build(self, mode):
        """Exercise receipt construction only: no compiler/runtime tests execute."""
        for name in ('Makefile', 'guest.cpp', 'tls-negative/Makefile', 'tls-negative/guest.cpp'):
            p = self.root / 'tests/waterbox-smoke' / name
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
        dylib = self.root / 'mock.dylib'
        raw = bytearray(32); struct.pack_into('<II', raw, 0, 0xfeedfacf, 0x100000c); struct.pack_into('<I', raw, 12, 6)
        dylib.write_bytes(raw)
        for name in ('guest/release/p6-smoke.wbx', 'tls-negative/release/p6-tls-negative.wbx'):
            p = self.out / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(elf64([(0x4000, 0x4000, 0x100, 0x4000, 5)]))
        def tls(path):
            if 'negative' in str(path): raise build_waterbox.BuildError('mock compiler TLS rejected')
            return {'wb16': 'mocked'}
        with (mock.patch.object(build_waterbox, 'cargo_build_runtime', return_value=dylib),
              mock.patch.object(build_waterbox, 'run_logged', return_value='mocked compiler/test output') as run,
              mock.patch.object(build_waterbox, 'check_no_compiler_tls', side_effect=tls), mock.patch('builtins.print')):
            result = build_waterbox.build(build_waterbox.argparse.Namespace(fetch=False, source_audit=mode))
        return result, run.call_args_list, dylib

    def test_runtime_commands_and_inputs_are_identical_and_receipts_record_the_audit(self):
        checkout, commands, _ = self.mock_runtime_build('checkout')
        snapshot, next_commands, _ = self.mock_runtime_build('snapshot')
        self.assertEqual(commands, next_commands)
        for name in ('input_sha256', 'guest', 'runtime_dylib', 'runtime_source_inventory_sha256'):
            self.assertEqual(checkout[name], snapshot[name])
        self.assertEqual('snapshot', snapshot['source_audit']['mode'])
        self.assertEqual(snapshot['source_audit'], snapshot['guest_source_audit'])

    def test_derived_lane_keeps_guest_audit_and_never_backfills_legacy_audit(self):
        base, _, dylib = self.mock_runtime_build('checkout')
        path = build_waterbox.runtime_receipt_path('release')
        for legacy in (False, True):
            receipt = dict(base)
            if legacy:
                for key in ('source_audit', 'guest_source_audit'): receipt.pop(key)
            path.write_text(json.dumps(receipt))
            self.assertEqual(receipt, build_waterbox.read_runtime_receipt(path))
            with (mock.patch.dict(build_waterbox.os.environ, {build_waterbox.SOURCE_AUDIT_ENV: 'snapshot'}),
                  mock.patch.object(build_waterbox, 'cargo_build_runtime', return_value=dylib)):
                derived = json.loads(build_waterbox.ensure_runtime_lane('debug').read_text())
            self.assertEqual('snapshot', derived['source_audit']['mode'])
            if legacy: self.assertNotIn('guest_source_audit', derived)
            else: self.assertEqual(base['source_audit'], derived['guest_source_audit'])

    def test_cli_requires_explicit_known_mode_and_defaults_to_checkout(self):
        for inherited, options, expected in ((None, [], 'checkout'), ('snapshot', [], 'snapshot'),
                ('invalid', ['--source-audit', 'checkout'], 'checkout'), ('checkout', ['--source-audit', 'snapshot'], 'snapshot')):
            with (mock.patch.dict(build_waterbox.os.environ),
                  mock.patch.object(sys, 'argv', [str(TOOL), '--puae6', *options]),
                  mock.patch.object(build_waterbox, 'toolchain', return_value={}) as tc,
                  mock.patch.object(build_waterbox, 'build_puae6')):
                if inherited is None: build_waterbox.os.environ.pop(build_waterbox.SOURCE_AUDIT_ENV, None)
                else: build_waterbox.os.environ[build_waterbox.SOURCE_AUDIT_ENV] = inherited
                self.assertEqual(0, build_waterbox.main())
                tc.assert_called_once_with(expected)
        with mock.patch.object(sys, 'argv', [str(TOOL), '--source-audit', 'auto']), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
            build_waterbox.main()

    def test_inherited_snapshot_never_changes_the_explicit_checkout_or_source_spec_default(self):
        with mock.patch.dict(build_waterbox.os.environ, {build_waterbox.SOURCE_AUDIT_ENV: 'snapshot'}):
            self.assertEqual('snapshot', build_waterbox.toolchain()['source_audit']['mode'])
            self.assertEqual('checkout', build_waterbox.toolchain('checkout')['source_audit']['mode'])
            for item in self.items:
                (self.root / item['local_path']).rename(self.root / ('unused-' + item['id']))
            self.assertEqual('snapshot', build_waterbox.toolchain()['source_audit']['mode'])
            with self.assertRaises(build_waterbox.BuildError): build_waterbox.toolchain('checkout')
            with self.assertRaises(build_waterbox.BuildError): build_waterbox.source_spec('machawk')


@unittest.skipUnless(sys.platform == 'darwin' and platform.machine() == 'arm64', 'BLOCKED: Darwin ARM64 runtime required')
class RuntimeSmokeTests(unittest.TestCase):
    def test_current_toolchain_builds_and_executes_the_complete_runtime_smoke(self):
        result = subprocess.run([sys.executable, str(TOOL), '--smoke'], cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SMOKE_RUNTIME PASS: WB01 WB13 WB14 WB15 (WB16 en el build)', result.stdout)
        build_waterbox.read_runtime_receipt(build_waterbox.runtime_receipt_path())
        (build_waterbox.BUILD / 'smoke-result.json').unlink(missing_ok=True)


class BuildElfTests(unittest.TestCase):
    def check_bad(self, data):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(data)
            artifact.flush()
            with self.assertRaises(build_waterbox.BuildError):
                build_waterbox.validate_elf(artifact.name)

    def test_accepts_arm64_load_segments(self):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(elf64([(0x4000, 0x4000, 0x3000, 0x4000, 5),
                                  (0x8000, 0x8000, 0x2000, 0x4000, 6)]))
            artifact.flush()
            result = build_waterbox.validate_elf(artifact.name)
            self.assertIsInstance(result, dict)

    def test_rejects_non_arm64_and_non_elf64(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], machine=62))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], elf_class=1))

    def test_rejects_missing_load_and_misaligned_load(self):
        self.check_bad(elf64([]))
        self.check_bad(elf64([(0x4001, 0x4000, 0x4000, 0x4000, 5)]))
        self.check_bad(elf64([(0x4000, 0x4001, 0x4000, 0x4000, 5)]))

    def test_rejects_invalid_or_truncated_load_range(self):
        bad_size = elf64([(0x4000, 0x4000, 0x4001, 0x4000, 5)])
        self.check_bad(bad_size)
        complete = elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])
        self.check_bad(complete[:24])
        truncated = complete[:-1]
        self.check_bad(truncated)

    def test_rejects_rwx_and_mixed_permissions_in_host_page(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 7)]))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5),
                              (0x8000, 0x4000, 0x4000, 0x4000, 6)]))

    def test_cli_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.wbx"
            result = subprocess.run([sys.executable, str(TOOL), "--check-artifact",
                                     str(missing)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", (result.stdout + result.stderr).lower())


class SmokeCoverageTests(unittest.TestCase):
    """--smoke must require the whole T09 coverage, never accept a partial harness."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.damaged = Path(self.directory.name)
        good = elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])
        for kind, data in {
            "wrong-machine": elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], machine=62),
            "bad-magic": b"\0" + bytes(good[1:]),
            "truncated": bytes(good[:100]),
            "mixed-rw-rx-group": elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5),
                                        (0x8000, 0x4000, 0x4000, 0x4000, 6)]),
        }.items():
            (self.damaged / f"{kind}.wbx").write_bytes(bytes(data))
        self.receipt = {"guest": {"sha256": "a" * 64}, "runtime_dylib": {"sha256": "b" * 64}}
        self.result = {
            "status": "PASS", "evidence": "SMOKE_RUNTIME", "amiga_emulation": False,
            "guest_sha256": "a" * 64, "runtime_sha256": "b" * 64,
            "runner": {"os": "Darwin 25.6.0", "architecture": "Arm64"},
            "checks": {check: list(names) for check, names in build_waterbox.REQUIRED_ASSERTIONS.items()},
            "load_probes": [{"kind": kind, "outcome": "rejected"} for kind in build_waterbox.LAYOUT_PROBES],
        }

    def verify(self):
        return build_waterbox.verify_smoke(self.result, self.receipt, self.damaged)

    def test_accepts_complete_coverage_and_reports_build_gate_reasons(self):
        self.result["runner"] = {"os": "macOS 26.7.0", "architecture": "Arm64"}
        summary = self.verify()
        self.assertEqual(["WB01", "WB13", "WB14", "WB15"], summary["checks"])
        self.assertEqual(set(build_waterbox.DAMAGED_IMAGES), set(summary["build_gate_rejections"]))

    def test_rejects_missing_assertions_and_wrong_artifacts(self):
        self.result["checks"]["WB13"].remove("abi_callback_reentrant")
        with self.assertRaisesRegex(build_waterbox.BuildError, "WB13.*abi_callback_reentrant"):
            self.verify()
        self.setUp()
        del self.result["checks"]["WB14"]
        with self.assertRaisesRegex(build_waterbox.BuildError, "WB14"):
            self.verify()
        self.setUp()
        self.result["guest_sha256"] = "c" * 64
        with self.assertRaisesRegex(build_waterbox.BuildError, "distinto del recibo"):
            self.verify()
        self.setUp()
        self.result["runner"] = {"os": "Linux 6.1", "architecture": "X64"}
        with self.assertRaisesRegex(build_waterbox.BuildError, "runner Darwin ARM64"):
            self.verify()
        self.setUp()
        self.result["status"] = "FAIL"
        with self.assertRaises(build_waterbox.BuildError):
            self.verify()

    def test_never_accepts_a_mixed_group_and_needs_the_build_gate_to_reject_damage(self):
        for probe in self.result["load_probes"]:
            if probe["kind"] == "mixed-rw-rx-group":
                probe["outcome"] = "accepted"
        with self.assertRaisesRegex(build_waterbox.BuildError, "RW dentro de RX"):
            self.verify()
        self.setUp()
        (self.damaged / "wrong-machine.wbx").write_bytes(bytes(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])))
        with self.assertRaisesRegex(build_waterbox.BuildError, "acepta la imagen dañada wrong-machine"):
            self.verify()

    def test_runtime_aborting_on_a_mixed_group_is_fail_closed_and_recorded(self):
        for probe in self.result["load_probes"]:
            if probe["kind"] == "mixed-rw-rx-group":
                probe["outcome"] = "aborted"
        self.assertEqual("aborted", self.verify()["runtime_outcomes"]["mixed-rw-rx-group"])


class Puae6RecipeTests(unittest.TestCase):
    """T19a: what goes into puae6.wbx and how it is built."""

    def test_the_source_list_is_the_one_of_the_native_build(self):
        sources = build_waterbox.puae6_sources()
        import json
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        self.assertTrue(set(f"build/upstream/{u}" for u in selection["upstream_units"]) <= set(sources))
        self.assertTrue(set(f"build/generated/{u}" for u in selection["generated_units"]) <= set(sources))
        self.assertTrue(set(build_waterbox.cmake_sources("p6_portable")) <= set(sources))
        self.assertTrue(set(build_waterbox.cmake_sources("p6_bridge")) <= set(sources))
        self.assertEqual(["adapters/waterbox/abi_probe.cpp", "adapters/waterbox/puae6_guest.cpp", "adapters/waterbox/cd_source.cpp"], sources[-3:])
        self.assertFalse([source for source in sources if "libretro" in source], "the libretro adapter is never linked into the guest")
        self.assertEqual(len(sources), len(set(sources)))

    def test_the_makefile_uses_the_pinned_macHawk_recipe_and_adds_no_linker_script_or_target_flags_of_its_own(self):
        text = "\n".join(line for line in (ROOT / "ports/waterbox/Makefile").read_text().splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("include $(REPO)/references/machawk/waterbox/common.mak", text)
        self.assertRegex(text, r"(?m)^WBX_TARGET_ARCH := aarch64$")
        self.assertRegex(text, r"(?m)^WBX_HOST_PAGE_SIZE := 16384$")
        for forbidden in ("LINKSCRIPT", "TARGET_FLAGS", "-ffixed", "-T "):
            self.assertNotIn(forbidden, text, "the register reservation, the page size and the linker script come from common.mak")
        self.assertIn("-ffp-contract=off", text)       # as the native build: no fused multiply-add reaches Paula's filter

    def test_the_import_inventory_authorizes_no_host_facing_name(self):
        allowed = build_waterbox.allowed_imports()
        self.assertTrue({"malloc", "memcpy", "snprintf", "sinf", "abort", "_Znwm", "__cxa_throw"} <= allowed)
        for name in ("clock_gettime", "time", "fopen", "open", "pthread_create", "socket", "dlopen", "usleep", "getenv", "rand"):
            self.assertNotIn(name, allowed)

    def test_classify_imports_reports_what_is_not_authorized(self):
        allowed = {"malloc", "memcpy"}
        report = build_waterbox.classify_imports({"malloc", "clock_gettime", "p6_load"}, {"p6_load"}, allowed)
        self.assertEqual(["clock_gettime", "malloc"], report["imports"])       # p6_load is defined by our own objects
        self.assertEqual(["clock_gettime"], report["unexpected"])
        self.assertEqual(["memcpy"], report["unused_allowances"])

    @unittest.skipUnless((ROOT / "build/waterbox/puae6/puae6-receipt.json").is_file(), "puae6.wbx has not been built (tools/build_waterbox.py --puae6)")
    def test_the_built_guest_passes_the_contract_checks_and_imports_only_what_is_authorized(self):
        import json
        receipt = json.loads((ROOT / "build/waterbox/puae6/puae6-receipt.json").read_text())
        guest = ROOT / "build/waterbox/puae6/release/puae6.wbx"
        self.assertEqual(receipt["guest"]["sha256"], build_waterbox.sha256(guest))
        self.assertEqual("PASS", build_waterbox.check_no_compiler_tls(guest)["wb16"])
        self.assertEqual("EM_AARCH64", build_waterbox.validate_elf(guest)["machine"])
        self.assertEqual([], receipt["imports"]["unexpected"])
        self.assertEqual(set(build_waterbox.puae6_sources()), set(receipt["sources"]))


def lint_elf(code=(0xD503201F,), phdr_types=(), tls_section=False, tls_symbol=False):
    """ELF64 with one R+X PT_LOAD holding `code`, optional extra program headers and sections."""
    text = b"".join(struct.pack("<I", word) for word in code)
    phdrs = [(1, 5, 0x4000, 0x4000, len(text), len(text), 0x4000)]
    phdrs += [(kind, 4, 0, 0, 0, 0, 8) for kind in phdr_types]
    strtab = b"\0.shstrtab\0.tbss\0.symtab\0"
    symbol_type = 6 if tls_symbol else 1  # STT_TLS or STT_OBJECT
    symtab = bytes(24) + struct.pack("<IBBHQQ", 0, (1 << 4) | symbol_type, 0, 0, 0, 0)
    str_off = 0x4000 + len(text)
    sym_off = (str_off + len(strtab) + 7) // 8 * 8
    sh_off = (sym_off + len(symtab) + 7) // 8 * 8

    def section(name, kind, flags, offset, size, entsize=0):
        return struct.pack("<IIQQQQIIQQ", name, kind, flags, 0, offset, size, 0, 0, 1, entsize)

    sections = [bytes(64), section(1, 3, 0, str_off, len(strtab))]
    if tls_section:
        sections.append(section(11, 8, 0x403, sym_off, 0))  # .tbss, SHF_TLS
    sections.append(section(17, 2, 0, sym_off, len(symtab), 24))
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    data = bytearray(sh_off + 64 * len(sections))
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 183, 1, 0, 64, sh_off, 0,
                             64, 56, len(phdrs), 64, len(sections), 1)
    for index, (kind, flags, off, va, filesz, memsz, align) in enumerate(phdrs):
        struct.pack_into("<IIQQQQQQ", data, 64 + 56 * index, kind, flags, off, va, va, filesz, memsz, align)
    data[0x4000:0x4000 + len(text)] = text
    data[str_off:str_off + len(strtab)] = strtab
    data[sym_off:sym_off + len(symtab)] = symtab
    data[sh_off:] = b"".join(sections)
    return bytes(data)


class NoCompilerTlsTests(unittest.TestCase):
    """WB16: the final guest ELF must not contain compiler-generated TLS."""

    def check(self, data):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(data)
            artifact.flush()
            return build_waterbox.check_no_compiler_tls(artifact.name)

    def check_rejected(self, data, reason):
        with self.assertRaisesRegex(build_waterbox.BuildError, reason):
            self.check(data)

    def test_accepts_code_that_only_uses_x28_and_other_system_registers(self):
        # mov x28,x0; mrs x0,nzcv; msr nzcv,x0; nop: x28 is the sandbox context.
        result = self.check(lint_elf(code=(0xAA0003FC, 0xD53B4200, 0xD51B4200, 0xD503201F)))
        self.assertEqual("PASS", result["wb16"])
        self.assertEqual(4, result["executable_words_scanned"])

    def test_rejects_thread_register_access_in_executable_code(self):
        self.check_rejected(lint_elf(code=(0xD503201F, 0xD53BD04A)), "mrs TPIDR_EL0 en 0x4004")
        self.check_rejected(lint_elf(code=(0xD51BD049,)), "msr TPIDR_EL0")
        self.check_rejected(lint_elf(code=(0xD53BD060,)), "mrs TPIDRRO_EL0")

    def test_rejects_tls_program_header_section_and_symbol(self):
        self.check_rejected(lint_elf(phdr_types=(7,)), "PT_TLS")
        self.check_rejected(lint_elf(tls_section=True), "seccion TLS .tbss")
        self.check_rejected(lint_elf(tls_symbol=True), "simbolo STT_TLS")

    def test_data_segments_are_not_scanned_as_code(self):
        data = bytearray(lint_elf())
        struct.pack_into("<I", data, 64 + 4, 4)  # make the PT_LOAD read-only data
        struct.pack_into("<I", data, 0x4000, 0xD53BD04A)
        self.assertEqual("PASS", self.check(bytes(data))["wb16"])

    def test_rejects_truncated_segments_and_reports_extra_findings(self):
        self.check_rejected(lint_elf()[:0x4000 + 1], "excede el archivo|inválida")
        self.check_rejected(lint_elf(code=(0xD53BD04A,) * 8, phdr_types=(7,)), "y \\d+ mas")

    def test_cli_check_artifact_applies_wb16(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "tls.wbx"
            artifact.write_bytes(lint_elf(code=(0xD53BD04A,)))
            result = subprocess.run([sys.executable, str(TOOL), "--check-artifact",
                                     str(artifact)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WB16", result.stderr)


if __name__ == "__main__":
    unittest.main()
