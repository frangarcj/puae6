"""Own pins and mocked derivation only; no compiler, download or emulator."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.ci import prepare_guest_sysroot as p


class HostedGuestSysrootTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        (p.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='own-hosted-guest-',dir=p.ROOT/'build') as tmp:
            root=Path(tmp); (root/'build').mkdir(); cache=root/'build/hosted-tools'
            ar=dict(path='/local/ar',sha256='a'*64,version='Homebrew LLVM version 23.1.4',hosted_path=p.guest.HOSTED_AR)
            lock=dict(toolchain_profile='hosted-macos26',waterbox_guest_sysroot={'archiver':ar})
            manifest={'normalized_macho':[dict(path=p.guest.HOSTED_AR.removeprefix('build/hosted-tools/'),sha256='a'*64,bytes=123)]}
            identity=dict(base='b'*64,archiver={**{k:ar[k] for k in ('path','sha256','version')},'path':str(root/p.guest.HOSTED_AR)})
            proof=dict(inputs=identity,inventory_sha256='c'*64,libc_sha256='d'*64,receipt_sha256='e'*64)
            with mock.patch.object(p,'ROOT',root),mock.patch.object(p.guest.wb,'selected_toolchain',return_value=lock), \
                 mock.patch.object(p.guest.wb,'source_audit_mode',return_value='snapshot'), \
                 mock.patch.dict(p.guest.os.environ,{'P6_WATERBOX_HOSTED_TOOLS':'build/hosted-tools'}), \
                 mock.patch.object(p.hosted,'prepare',return_value=cache) as cache_prepare, \
                 mock.patch.object(p.hosted,'inputs',return_value=(manifest,{})), \
                 mock.patch.object(p.guest,'identity',return_value=({}, {},None,None,None,identity)) as observe, \
                 mock.patch.object(p.guest,'derive',return_value=dict(status='PASS_DERIVED_ONLY')) as derive, \
                 mock.patch.object(p.guest,'verify',return_value=proof) as verify:
                yield root,lock,manifest,cache_prepare,observe,derive,verify

    def test_derives_only_after_authenticated_preflight_then_verifies_and_reuses_exact_output(self):
        with self.fixture() as (root,_,_,cache,observe,derive,verify):
            self.assertEqual(p.prepare()['status'],'PASS_DERIVED_ONLY')
            cache.assert_called_once_with(fetch=False); derive.assert_called_once_with(root=root,execute=True)
            verify.assert_called_once_with(root=root); self.assertEqual(observe.call_count,2)
            (root/'build/waterbox-guest-sysroot').mkdir(); derive.reset_mock()
            p.prepare(); derive.assert_not_called()
            verify.side_effect=ValueError('own foreign existing output')
            with self.assertRaisesRegex(ValueError,'foreign existing'): p.prepare()
            derive.assert_not_called()

    def test_missing_cache_profile_audit_or_archiver_pin_blocks_before_derivation(self):
        for fault in ('cache','profile','audit','loader','path','missing-row','duplicate-row','sha','version'):
            with self.subTest(fault=fault),self.fixture() as (root,lock,manifest,cache,_,derive,_):
                if fault=='cache': cache.side_effect=FileNotFoundError('own missing cache')
                if fault=='profile': lock['toolchain_profile']='local'
                if fault=='audit': p.guest.wb.source_audit_mode.return_value='checkout'
                if fault=='loader': p.guest.os.environ['P6_WATERBOX_HOSTED_TOOLS']='foreign'
                if fault=='path': lock['waterbox_guest_sysroot']['archiver']['hosted_path']='../ar'
                if fault=='missing-row': manifest['normalized_macho']=[]
                if fault=='duplicate-row': manifest['normalized_macho']*=2
                if fault=='sha': manifest['normalized_macho'][0]['sha256']='f'*64
                if fault=='version': lock['waterbox_guest_sysroot']['archiver']['version']='LLVM other'
                with self.assertRaises((ValueError,OSError)): p.prepare()
                derive.assert_not_called()

    def test_derivation_failure_not_run_and_input_drift_never_produce_prepared_result(self):
        for fault in ('compile','not-run','verify','drift'):
            with self.subTest(fault=fault),self.fixture() as (_,_,_,_,observe,derive,verify):
                if fault=='compile': derive.side_effect=ValueError('own compiler failure')
                if fault=='not-run': derive.return_value={'status':'NOT_RUN'}
                if fault=='verify': verify.side_effect=ValueError('own wrong inventory')
                if fault=='drift': observe.side_effect=[observe.return_value,({}, {},None,None,None,{'changed':'source'})]
                with self.assertRaises(ValueError): p.prepare()

    def test_action_prepares_derived_authority_before_building_the_product_guest(self):
        action=(Path(p.__file__).resolve().parents[2]/'.github/actions/prepare-performance-waterbox/action.yml').read_text()
        self.assertIn('python3 -B -m tools.ci.prepare_guest_sysroot',action)
        self.assertLess(action.index('tools.ci.prepare_guest_sysroot'),action.index('--source-audit snapshot --puae6'))

    def test_action_module_imports_in_a_fresh_process_and_blocks_before_any_preparation(self):
        root=Path(p.__file__).resolve().parents[2]
        result=subprocess.run([sys.executable,'-B','-m','tools.ci.prepare_guest_sysroot'],cwd=root,
            env={**os.environ,'P6_WATERBOX_TOOLCHAIN_PROFILE':'local','P6_WATERBOX_SOURCE_AUDIT':'checkout'},
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(status='BLOCKED',diagnostic='requires explicit hosted-macos26 snapshot inputs'))
        self.assertEqual(result.stderr,'')
