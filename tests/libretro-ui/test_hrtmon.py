"""Public HRTMon through libretro options and real keyboard input; no private Freeze calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

from tests.build_support import ROOT, build_targets
from tools import generate_options as gen
from tests.state.harness import parse_chunks

sys.path.insert(0, str(ROOT/'tests/libretro-host'))
import test_libretro as lr
from test_input import RETROK


class HrtMonOptionTests(unittest.TestCase):
    def test_flag_parts_compose_and_reject_overlaps_or_unrelated_duplicate_fields(self):
        schema = gen.load_schema()
        flags = json.loads(gen.generate_table(schema))['p6_config']['flags']
        self.assertEqual((flags['default'],flags['allowed']), (0,[0,1,2,3]))
        self.assertEqual([p['option'] for p in flags['parts']], ['floppy_writes','cartridge'])
        for writes in (0,1):
            for hrtmon in (0,2):
                normalized = gen.normalize_config(schema, {'floppy_writes': 'enabled' if writes else 'disabled',
                                                         'cartridge': 'hrtmon' if hrtmon else 'disabled'})
                self.assertEqual(gen.to_p6_config(schema,normalized)['flags'], writes|hrtmon)
        for field,abi in (('p6_config.flags',1),('p6_config.flags',3),('p6_config.audio_rate_hz',2)):
            bad = copy.deepcopy(schema)
            cartridge = next(o for o in bad['options'] if o['id']=='cartridge')
            cartridge['maps_to']=field; cartridge['values'][1]['abi']=abi
            with self.assertRaises(gen.SchemaError): gen.validate_schema(bad,gen.p6_config_fields())


class HrtMonLibretroTests(lr.LibretroHarness):
    @classmethod
    def setUpClass(cls):
        firmware = os.environ.get('P6_KICKSTART_A1200_ROM')
        if not firmware: raise unittest.SkipTest('BLOCKED: authorized P6_KICKSTART_A1200_ROM required')
        built = build_targets(ROOT/'build/state-opt',['p6_libretro_host','puae6_libretro'],optimized=True)
        cls.host, cls.core = built['p6_libretro_host'], built['puae6_libretro']
        cls.tmp = tempfile.TemporaryDirectory(prefix='hrtmon-libretro-',dir=ROOT/'build')
        cls.counter=0; cls.roms={'real':Path(firmware)}
        cls.system_dir=Path(cls.tmp.name)/'system'; cls.system_dir.mkdir()
        (cls.system_dir/lr.KICKSTART_NAMES_A1200[0]).symlink_to(Path(firmware).resolve())

    def chunks(self,path):
        data=path.read_bytes(); self.assertEqual(data[:8],b'P6LRSTAT')
        version,header,length,section=struct.unpack_from('<IIQI',data,8)
        self.assertEqual((version,header),(8,64)); at=header+section
        self.assertLessEqual(at+length,len(data)); self.assertFalse(any(data[at+length:]))
        self.assertEqual(hashlib.sha256(data[header:at]).digest(),data[32:64])
        native=data[at:at+length]; self.assertEqual(native[:8],b'P6NATIVE')
        self.assertEqual(struct.unpack_from('<II',native,8),(1,336))
        size,count=struct.unpack_from('<QI',native,40); self.assertEqual(len(native),336+size)
        chunks=parse_chunks(native); self.assertEqual(len(chunks),count)
        self.assertEqual(sum(16+len(body) for _,_,body in chunks),size)
        return {tag:body for tag,_,body in chunks}

    def monitor(self,path):
        chunks=self.chunks(path); ram=chunks[b'P6RM']; chip,slow=struct.unpack_from('<II',ram)
        self.assertEqual((chip,slow),(0x200000,0)); at=8+chip+slow
        size,flag,triggered,mapped=struct.unpack_from('<IIII',ram,at)
        self.assertEqual((size,triggered,mapped),(0x100000,1,1))
        self.assertEqual(len(ram),at+16+544+size)
        self.assertEqual(chunks[b'P6IN'][-1],1)
        return flag,ram[8+0x140000:8+0x140004]

    @staticmethod
    def typed(text,position):
        script=[]
        for code in [*(ord(c) for c in text),RETROK['RETURN']]:
            script += ['--key-at',f'{position}:{code}:1','--key-at',f'{position+3}:{code}:0']; position+=6
        return script,position+9

    def test_real_hotkey_command_return_and_held_state_in_fresh_process(self):
        work=self.work(); cut,filled,final,replayed=[work/name for name in ('cut.state','filled.state','final.state','replay.state')]
        options=[*lr.A1200,'--set','puae6_cartridge=hrtmon','--set','puae6_hotkey_freeze=f11']
        script=['--key-at',f'301:{RETROK["F11"]}:1']
        keys,after_fill=self.typed('o 140000 140004 5a',381); script+=keys
        keys,after_exit=self.typed('x',after_fill+1); script+=keys; end=after_exit+8
        actions=['--do',f'{after_fill}:peek:0x140000:4']
        first=self.frontend('real','--runs',end,*options,*script,*actions,
                            '--do',f'380:poke:0x140000:00000000','--do',f'380:savefile:{cut}',
                            '--do',f'{after_fill}:savefile:{filled}','--do',f'{end}:savefile:{final}',system=self.system_dir).stdout
        self.assertEqual(self.monitor(cut),(3,b'\0'*4)); self.assertEqual(self.monitor(filled),(3,b'\x5a'*4))
        self.assertEqual(self.monitor(final),(1,b'\x5a'*4)); self.assertIn('peek 0x140000 4 5a5a5a5a',first)
        second=self.frontend('real','--runs',end-380,*options,*script,
                             '--do',f'0:loadfile:{cut}:pos=380','--do',f'{end-380}:savefile:{replayed}',system=self.system_dir).stdout
        self.assertRegex(second,r'state load .* ok=1 ')
        self.assertEqual(final.read_bytes(),replayed.read_bytes())
        expected=[{k:v for k,v in run.items() if k!='n'} for run in self.runs(first)[380:]]
        self.assertEqual(expected,[{k:v for k,v in run.items() if k!='n'} for run in self.runs(second)])
        before,after=work/'off-before.state',work/'off-after.state'
        off=self.frontend('real','--runs',2,*lr.A1200,'--do',f'1:savefile:{before}',
                          '--do',f'1:loadfile:{cut}','--do',f'1:savefile:{after}',system=self.system_dir).stdout
        self.assertRegex(off,r'state load .* ok=0 '); self.assertEqual(before.read_bytes(),after.read_bytes())
