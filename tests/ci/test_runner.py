"""CI infrastructure tests; synthetic verdicts do not certify emulator behaviour."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.ci import run as ci
from tools.ci import nightly

GOOD = dict(tests=1, failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0)


def result(counts=GOOD, code=0):
    return subprocess.CompletedProcess([], code, "fixture noise\n" + json.dumps(counts), "")


class ProtocolTests(unittest.TestCase):
    def test_only_complete_nonempty_counts_and_successful_exit_can_pass(self):
        self.assertEqual(ci.verdict(result())[0], "PASS")
        for counts in ({}, dict(GOOD, tests=0), dict(GOOD, tests=True), dict(GOOD, tests=-1), dict(GOOD, extra=0)):
            with self.subTest(counts=counts):
                self.assertEqual(ci.verdict(result(counts))[0], "FAIL")
        for key in ("failures", "errors", "unexpected_successes"):
            self.assertEqual(ci.verdict(result(dict(GOOD, **{key: 1})))[0], "FAIL")
        self.assertEqual(ci.verdict(result(code=1))[0], "FAIL")
        self.assertEqual(ci.verdict(result(code=None))[0], "FAIL")
        self.assertEqual(ci.verdict(subprocess.CompletedProcess([], 0, "truncated {", ""))[0], "FAIL")

    def test_skipped_and_expected_failures_block_even_with_zero_exit(self):
        for key in ("skipped", "expected_failures"):
            self.assertEqual(ci.verdict(result(dict(GOOD, **{key: 1})))[0], "BLOCKED")

    def test_missing_gate_does_not_shrink_the_recipe_and_shared_suites_run_once(self):
        steps = ci.recipe()
        self.assertEqual(set(ci.GATES), {gate for gates in steps.values() for gate in gates})
        self.assertTrue(any({"WB13", "WB14", "WB15"} <= set(gates) for gates in steps.values()))
        reduced = dict(ci.check.TASK_SUITES)
        del reduced[("WB15", "T39a")]
        with mock.patch.object(ci.check, "TASK_SUITES", reduced), self.assertRaisesRegex(ValueError, "WB15"):
            ci.recipe()

    def test_missing_asset_blocks_without_printing_its_path(self):
        with mock.patch.object(ci.platform, "system", return_value="Darwin"), mock.patch.object(ci.platform, "machine", return_value="arm64"), \
             mock.patch.object(ci.os, "sysconf", return_value=16384), mock.patch.object(ci.shutil, "which", return_value="tool"), \
             mock.patch.dict(os.environ, {"P6_REQUIRED_ROM": "/absent/private.rom"}):
            with self.assertRaisesRegex(ValueError, "missing required asset: P6_REQUIRED_ROM") as failure:
                ci.preflight(("P6_REQUIRED_ROM",))
            self.assertNotIn("/absent", str(failure.exception))

    def test_other_host_cannot_claim_the_waterbox_runner(self):
        with mock.patch.object(ci.platform, "system", return_value="Linux"), self.assertRaisesRegex(ValueError, "declared macOS"):
            ci.preflight()

    def test_private_paths_are_redacted_and_environment_is_never_dumped(self):
        with mock.patch.dict(os.environ, {"P6_KICKSTART_ROM": "/some/private/firmware.rom"}):
            text = ci.redact(f"{ci.ROOT}/bridge/state.cpp {Path.home()}/private /some/private/firmware.rom {tempfile.gettempdir()}/fixture")
        self.assertNotIn(str(Path.home()), text)
        self.assertNotIn("/some/private", text)
        self.assertIn("@repo/bridge/state.cpp", text)

    def test_real_worker_import_error_and_empty_suite_are_failures(self):
        for module in ("tests.test_plan.DoesNotExist", "types"):
            process = ci.command([sys.executable, "-c", ci.check.DRIVER, module], timeout=30)
            self.assertEqual(ci.verdict(process)[0], "FAIL", process.stdout + process.stderr)

    def test_timeout_terminates_the_child_as_well_as_the_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory) / "child.pid"
            script = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            process = ci.command([sys.executable, "-c", script, str(pid)], timeout=0.5)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("entire worker process group", process.stderr)
            state = subprocess.run(["ps", "-o", "stat=", "-p", pid.read_text()], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_identity_tracks_dirty_sources_but_never_untracked_firmware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            git("init", "-q")
            source = root / "source.cpp"
            source.write_text("first")
            git("add", "source.cpp")
            git("-c", "user.name=CI fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
            with mock.patch.object(ci, "ROOT", root):
                first = ci.identity()
                source.write_text("second")
                changed = ci.identity()
                (root / "private.rom").write_bytes(b"synthetic untracked asset")
                private = ci.identity()
            self.assertNotEqual(first["source_tree_sha256"], changed["source_tree_sha256"])
            self.assertEqual(changed["source_tree_sha256"], private["source_tree_sha256"])
            self.assertNotEqual(first["dirty"], changed["dirty"])


class SourceAuditSelectionTests(unittest.TestCase):
    """Only CI plumbing and tiny subprocesses; no compiler/guest execution."""

    def test_resolver_default_precedence_and_invalid_values(self):
        key = ci.wb.SOURCE_AUDIT_ENV
        with mock.patch.dict(os.environ):
            os.environ.pop(key, None)
            self.assertEqual('checkout', ci.wb.source_audit_mode())
            os.environ[key] = 'snapshot'
            self.assertEqual('snapshot', ci.wb.source_audit_mode())
            self.assertEqual('checkout', ci.wb.source_audit_mode('checkout'))
            for invalid in ('', 'auto', ' snapshot ', 'invalid'):
                os.environ[key] = invalid
                with self.assertRaises(ci.wb.BuildError): ci.wb.source_audit_mode()
                self.assertEqual('snapshot', ci.wb.source_audit_mode('snapshot'))
                with mock.patch.object(ci.wb, 'locked_tools') as tools:
                    with self.assertRaises(ci.wb.BuildError): ci.wb.toolchain()
                    tools.assert_not_called()
                with mock.patch.object(ci.wb, 'runtime_receipt_path') as receipt:
                    with self.assertRaises(ci.wb.BuildError): ci.wb.ensure_runtime_lane('release')
                    receipt.assert_not_called()

    def test_ci_cli_pins_checkout_and_requires_flag_for_snapshot_even_with_inherited_env(self):
        for runner, args, prefix in ((ci, [], ()), (nightly, ['--lane', 'sanitizers'], ('sanitizers',))):
            for inherited in ('snapshot', 'invalid'):
                for flag, expected in (([], 'checkout'), (['--source-audit', 'snapshot'], 'snapshot')):
                    with (mock.patch.dict(os.environ, {ci.wb.SOURCE_AUDIT_ENV: inherited}),
                          mock.patch.object(sys, 'argv', ['CI', *args, *flag]), mock.patch('builtins.print'),
                          mock.patch.object(runner, 'run', return_value={'status': 'BLOCKED'}) as run):
                        self.assertEqual(2, runner.main())
                        run.assert_called_once_with(*prefix, expected)

    def test_invalid_api_environment_blocks_before_preparation_and_explicit_override_wins(self):
        for runner, args in ((ci, ()), (nightly, ('sanitizers',))):
            with (mock.patch.dict(os.environ, {ci.wb.SOURCE_AUDIT_ENV: 'invalid'}),
                  mock.patch.object(runner, '_run', return_value={'status': 'PASS'}) as worker):
                self.assertEqual('BLOCKED', runner.run(*args)['status'])
                worker.assert_not_called()
                self.assertEqual('PASS', runner.run(*args, source_audit='checkout')['status'])
                worker.assert_called_once_with(*args)
                self.assertEqual('invalid', os.environ[ci.wb.SOURCE_AUDIT_ENV])

    def test_worker_children_and_identity_receive_the_same_selected_mode(self):
        (ci.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ci.ROOT / 'build', prefix='ci-audit-') as directory:
            root = Path(directory); (root / 'source.cpp').write_text('own fixture')
            for args in (('init', '-q'), ('add', 'source.cpp'),
                         ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')):
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            for runner, args in ((ci, ()), (nightly, ('sanitizers',))):
                original = dict(runner.ENV)
                def worker(*unused):
                    facts = runner.identity()
                    script = ('import os,subprocess,sys,json; key=sys.argv[1]; '
                              'child=subprocess.check_output([sys.executable,"-c","import os,sys;print(os.environ[sys.argv[1]])",key],text=True); '
                              'print(json.dumps([os.environ[key],child.strip()]))')
                    result = ci.command([sys.executable, '-c', script, ci.wb.SOURCE_AUDIT_ENV], runner.ENV, timeout=30)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(['snapshot', 'snapshot'], json.loads(result.stdout))
                    self.assertEqual('snapshot', facts['test_environment'][ci.wb.SOURCE_AUDIT_ENV])
                    self.assertEqual(original['P6_EVICT'], facts['test_environment']['P6_EVICT'])
                    return {'status': 'PASS', 'identity': facts}
                with (mock.patch.object(ci, 'ROOT', root),
                      mock.patch.dict(os.environ, {ci.wb.SOURCE_AUDIT_ENV: 'checkout'}),
                      mock.patch.object(runner, '_run', side_effect=worker)):
                    before = runner.identity()
                    result = runner.run(*args, source_audit='snapshot')
                    self.assertEqual('checkout', os.environ[ci.wb.SOURCE_AUDIT_ENV])
                    self.assertEqual(before, runner.identity())
                    self.assertEqual(before['source_tree_sha256'], result['identity']['source_tree_sha256'])
                    self.assertNotEqual(before['test_environment'], result['identity']['test_environment'])
                self.assertEqual(original, runner.ENV)

    def test_scope_restores_absence_or_value_even_when_a_worker_raises(self):
        for runner, args in ((ci, ()), (nightly, ('sanitizers',))):
            for inherited in (None, 'checkout'):
                with mock.patch.dict(os.environ), mock.patch.object(runner, '_run', side_effect=RuntimeError('fixture')):
                    key = ci.wb.SOURCE_AUDIT_ENV
                    if inherited is None: os.environ.pop(key, None)
                    else: os.environ[key] = inherited
                    with self.assertRaisesRegex(RuntimeError, 'fixture'):
                        runner.run(*args, source_audit='snapshot')
                    self.assertEqual(inherited, os.environ.get(key))


if __name__ == "__main__":
    unittest.main()
