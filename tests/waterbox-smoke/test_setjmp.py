"""Data/metadata fixtures only; real C/ASM/CLR execution is explicitly opt-in."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('waterbox_setjmp', ROOT / 'tools/waterbox_setjmp.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


class SetjmpTests(unittest.TestCase):
    def test_atomic_arch_only_patch_matches_two_exact_instructions_and_reverse(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        source = ROOT / 'references/musl-darwin/src/setjmp/waterbox_aarch64'
        before = {name: (source / name).read_bytes() for name in p.ASM}
        with tempfile.TemporaryDirectory(prefix='setjmp-patch-fixture-', dir=ROOT / 'build') as tmp:
            stage = Path(tmp); original = p.overlay(source, stage / 'original', False); fixed = p.overlay(source, stage / 'fixed', True)
            for name, op in (('setjmp.s', b'stp'), ('longjmp.s', b'ldp')):
                expected = before[name].replace(op+b' x27, x28, [x0,#64]', op+b' x27, xzr, [x0,#64]')
                self.assertNotEqual(before[name], expected); self.assertEqual((fixed/name).read_bytes(), expected)
                self.assertEqual((original/name).read_bytes(), before[name]); self.assertEqual((source/name).read_bytes(), before[name])
            own = stage / 'bad'; own.mkdir()
            for name in p.ASM: (own/name).write_bytes(before[name].replace(b'x27', b'x26'))
            with self.assertRaises(ValueError): p.overlay(own, stage / 'rejected', True)
        patch = (ROOT / p.PATCH).read_text(); self.assertEqual(patch.count('--- a/src/setjmp/waterbox_aarch64/'), 2)
        self.assertNotIn('aarch64/', patch.replace('waterbox_aarch64/', ''))

    def test_missing_process_wrong_return_count_or_snapshot_cannot_pass(self):
        records = [{'status': 'EXPECTED_ORIGINAL_REJECTION', 'jumps': 0},
            {'status': 'FIXED', 'jumps': 10, 'baseline': 'a'*64, 'future': 'b'*64},
            {'status': 'RESTORED', 'jumps': 2, 'baseline': 'a'*64, 'future': 'b'*64}]
        self.assertEqual(p.validate(records)['valid_jumps'], 12)
        for bad in (records[:2], records+[records[2]], [{**records[0], 'jumps': False}, *records[1:]],
                    [records[0], {**records[1], 'jumps': 7}, records[2]],
                    [records[0], {**records[1], 'future': 'a'*64}, {**records[2], 'future': 'a'*64}],
                    [*records[:2], {**records[2], 'future': 'c'*64}], [*records[:2], {**records[2], 'baseline': 'bad'}]):
            with self.subTest(record=bad), self.assertRaises(ValueError): p.validate(bad)

    def test_authenticated_base_isa_and_sources_are_required_without_execution(self):
        with tempfile.TemporaryDirectory(prefix='setjmp-input-fixture-', dir=ROOT / 'build') as tmp:
            root = Path(tmp)
            sources = [p.PATCH, 'tools/waterbox_setjmp.py', 'tests/waterbox-smoke/setjmp_probe.c', 'tests/waterbox-smoke/SetjmpProbe.cs',
                'references/musl-darwin/arch/waterbox_aarch64/bits/setjmp.h', *('references/musl-darwin/src/setjmp/waterbox_aarch64/'+n for n in p.ASM)]
            for name in sources: path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'own source data, never executed')
            with mock.patch.object(p, 'ROOT', root), mock.patch.object(p.base, 'inputs', return_value=({'runtime': root/'own-data'}, {'base-proof': 'pinned'}, root/'own-dotnet')) as base, mock.patch.object(p.base.wb, 'source_spec') as capture, mock.patch.object(p.subprocess, 'run') as child:
                before = p.inputs()[1]; self.assertEqual(before['base-proof'], 'pinned'); capture.assert_called_once_with('waterbox_musl_darwin', 'snapshot')
                (root/sources[-1]).write_bytes(b'changed own source'); self.assertNotEqual(p.inputs()[1], before)
                base.side_effect = ValueError('ISA mismatch')
                with self.assertRaisesRegex(ValueError, 'ISA mismatch'): p.inputs()
                child.assert_not_called()


@unittest.skipUnless(os.environ.get('P6_TEST_WATERBOX_SETJMP') == '1', 'requires authenticated offline Darwin ARM64 runtime, sysroot, compiler and SDK')
class RealSetjmpTests(unittest.TestCase):
    def test_real_original_rejection_fixed_live_jumps_and_full_cross_process_restore(self):
        result = p.run(); self.assertEqual(result['valid_jumps'], 12); print(json.dumps(result, sort_keys=True))
