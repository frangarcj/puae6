"""Own oracle fixtures; native execution is explicit and leaves products intact."""
import copy
import os
import unittest
from tools import waterbox_entry_gpr as p


class EntryGprTests(unittest.TestCase):
    def test_original_control_and_changed_full_future_are_required(self):
        records = [{'mode':'original','contaminated':True},
                   {'mode':'fixed','baseline':'a'*64,'future':'b'*64,'callbacks':2},
                   {'mode':'restore','baseline':'a'*64,'future':'b'*64}]
        self.assertEqual(p.validate(records)['scope'], 'entry-gpr-oracle-only')
        for mode in ('negative', 'callbacks', 'equal', 'restore', 'coverage'):
            wrong=copy.deepcopy(records)
            if mode=='negative': wrong[0]['contaminated']=False
            if mode=='callbacks': wrong[1]['callbacks']=True
            if mode=='equal': wrong[1]['future']=wrong[2]['future']='a'*64
            if mode=='restore': wrong[2]['future']='c'*64
            if mode=='coverage': wrong.pop()
            with self.subTest(mode=mode), self.assertRaises(ValueError): p.validate(wrong)

    def test_entry_overlay_has_no_ambiguous_insertion(self):
        with self.assertRaises(ValueError): p.canonical_entry('unrelated ASM')
        text='host_saved:\n    mov     x28, x10\n\n    blr     x11\nhost_restored:\n'
        changed=p.canonical_entry(text)
        self.assertEqual(changed.count('    mov     x19, xzr'),1)
        self.assertIn('    mov     x29, xzr',changed)
        self.assertEqual(text.count('    mov     x28, x10'),changed.count('    mov     x28, x10'))


@unittest.skipUnless(os.environ.get('P6_TEST_WATERBOX_ENTRY_GPR')=='1', 'explicit Darwin ARM64 entry oracle required')
class RealEntryGprTests(unittest.TestCase):
    def test_native_entry_and_full_restore(self):
        result=p.run(); self.assertTrue(result['fresh_process']); print(result)
