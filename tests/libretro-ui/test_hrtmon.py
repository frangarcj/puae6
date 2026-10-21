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


class LibretroCartridgeTests(lr.LibretroHarness):
    chunks = HrtMonLibretroTests.chunks
    models = ('ar1','ar2','ar3','ar1200')
    aliases = ('Action_Replay_I_v1.00.rom','Action_Replay_II_v2.14.rom','Action_Replay_III_v3.17.rom','Action_Replay_1200.rom')

    @classmethod
    def setUpClass(cls):
        cls.kicks = [Path(os.environ.get(key, str(Path.home()/'Downloads'/name))) for key,name in (
            ('P6_KICKSTART_A500_ROM','Kickstart v1.3 r34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom'),
            ('P6_KICKSTART_A1200_ROM','Kickstart v3.1 r40.68 (1993)(Commodore)(A1200)[!].rom'))]
        cls.carts = [Path(os.environ.get('P6_ACTION_REPLAY'+model[2:]+'_ROM',str(ROOT/'build/assets/action-replay'/alias)))
                     for model,alias in zip(cls.models,cls.aliases)]
        if not all(path.is_file() for path in cls.kicks+cls.carts):
            raise unittest.SkipTest('BLOCKED: authorized Kickstart A500/A1200 and P6_ACTION_REPLAY1/2/3/1200_ROM required')
        expected=('1ead9dda2dad29146441f5ef7218375022e01248','255d6df63a4eab0a838eb1a16a267b0959dff634',
                  '5d4987c2e3ffea8b1b02e31430ef190f2db76542','0f6ab8342810094ac0642f62ba42f78bc0b07e6a')
        for path,sha in zip(cls.carts,expected):
            if hashlib.sha1(path.read_bytes()).hexdigest()!=sha: raise AssertionError(f'wrong authorized cartridge: {path}')
        built=build_targets(ROOT/'build/state-opt',['p6_libretro_host','puae6_libretro'],optimized=True)
        cls.host,cls.core=built['p6_libretro_host'],built['puae6_libretro']
        cls.tmp=tempfile.TemporaryDirectory(prefix='lr-cartridges-',dir=ROOT/'build'); cls.counter=0

    def cart_system(self,index,alias=False):
        system=self.work()
        for rom,name in zip(self.kicks,(lr.KICKSTART_NAMES[0],lr.KICKSTART_NAMES_A1200[0])):
            (system/name).symlink_to(rom.resolve())
        name=self.aliases[index] if alias else self.models[index]+'.rom'
        (system/name).symlink_to(self.carts[index].resolve())
        return system

    def cart_options(self,index):
        return ['--set','puae6_machine='+('A1200' if index==3 else 'A500'),
                '--set','puae6_cartridge='+self.models[index],'--set','puae6_hotkey_freeze=f11']

    def active_cart(self,path,index):
        chunks=self.chunks(path); ram=chunks[b'P6RM']; chip,slow=struct.unpack_from('<II',ram); at=8+chip+slow
        self.assertEqual(chunks[b'P6IN'][-1],1)
        if index==3:
            self.assertEqual(struct.unpack_from('<4I',ram,at),(0x100000,3,1,1))
            self.assertEqual(len(ram),at+560+0x80000)
            # The pinned ROM copies the bus-error Freeze cause at $800210,
            # masks bit2 at $80021a, then writes status8 at $80023c.
            self.assertEqual(struct.unpack_from('>H',ram,at+560+0x2ce)[0],4)
            self.assertEqual(struct.unpack_from('>H',ram,at+560+0x40000)[0],8)
        else:
            size,flag,mapped,mode,_,_,hide,hidden,banks=struct.unpack_from('<9I',ram,at)
            self.assertEqual((size,flag,mapped,mode,hide,hidden,banks),(65536,3,1,0,0,0,1))
            self.assertEqual(len(ram),at+584+(8 if index==0 else 0)+65536)
            if index==0:
                pop,pc=struct.unpack_from('<II',ram,at+584); self.assertEqual(pop,0); self.assertNotEqual(pc,0)
                self.assertEqual(ram[8+0x7c:8+0x80],bytes.fromhex('c5f00006'))  # BYTE/WAIT_PC/reentry handoff completed.
        self.assertEqual(ram[8+0x11000:8+0x11004],bytes.fromhex('01020304'))

    def test_system_roms_freeze_and_cold_state(self):
        for index,model in enumerate(self.models):
            with self.subTest(model=model):
                self.check_system_rom_freeze_and_cold_state(index)

    def check_system_rom_freeze_and_cold_state(self,index):
        work=self.work(); system=self.cart_system(index); cut=950; opts=self.cart_options(index)
        script=['--key-at',f'331:{RETROK["F11"]}:1']
        if index in (1,2): script+=['--key-at',f'301:{RETROK["F1"]}:1','--key-at',f'304:{RETROK["F1"]}:0']
        before=work/'cut.state'
        captures=lambda prefix,offset: [part for unit in range(1,16) for part in
            ('--do',f'{unit+offset}:savefile:{work/(prefix+str(unit))}')]
        first=self.frontend('boot','--runs',cut+15,*opts,*script,
            '--do',f'{cut}:poke:0x11000:01020304','--do',f'{cut}:savefile:{before}',*captures('a',cut),system=system).stdout
        self.active_cart(before,index)
        second=self.frontend('boot','--runs',15,*opts,*script,'--do',f'0:loadfile:{before}:pos={cut}',
                             *captures('b',0),system=system).stdout
        self.assertRegex(second,r'state load .* ok=1 ')
        self.assertEqual(len(self.runs(first)),cut+15); self.assertEqual(len(self.runs(second)),15)
        self.assertEqual([{k:v for k,v in r.items() if k!='n'} for r in self.runs(first)[cut:]],
                         [{k:v for k,v in r.items() if k!='n'} for r in self.runs(second)])
        for unit in range(1,16):
            a,b=work/('a'+str(unit)),work/('b'+str(unit))
            self.active_cart(a,index); self.assertEqual(a.read_bytes(),b.read_bytes()); a.unlink(); b.unlink()
        for out in (first,second):
            self.assertEqual(out.count('vfs open '+str(system/(model+'.rom'))+'\n'),1)
            self.assertIn('vfs closed live=0 run_reads=0',out)

    def test_missing_invalid_wrong_profile_and_reload_are_explicit(self):
        opts=self.cart_options(0); primary='ar1.rom'; alias=self.aliases[0]
        def refused(system,*extra,content=None,needle='cartridge'):
            out=self.frontend('boot','--runs',2,*opts,*extra,system=system,content=content,expect=3).stdout
            self.assertIn(needle,out); self.assertIn('vfs closed live=0',out); return out
        for index,model in enumerate(self.models):
            system=self.cart_system(index); (system/(model+'.rom')).unlink()
            out=self.frontend('boot',*self.cart_options(index),system=system,expect=3).stdout
            self.assertIn('missing cartridge ROM '+model+'.rom',out)
        for kind in ('short','content','directory'):
            system=self.cart_system(0,alias=True)
            if kind=='directory': (system/primary).mkdir()
            else: (system/primary).write_bytes(b'bad' if kind=='short' else bytes(65536))
            out=refused(system); self.assertIn(primary,out); self.assertNotIn('vfs open '+str(system/alias),out)
        alias_system=self.cart_system(0,alias=True)
        out=self.frontend('boot','--runs',2,*opts,system=alias_system).stdout; self.assertIn('cartridge '+alias+' copied through VFS',out)
        refused(alias_system,'--vfs-version',1,needle='VFS cannot prove absence')
        good=self.cart_system(0)
        out=self.frontend('boot','--runs',2,*opts,'--vfs-version',1,system=good).stdout; self.assertIn('cartridge ar1.rom copied through VFS',out)
        refused(good,'--vfs-claim',65537,needle='too large')
        refused(good,'--set','puae6_machine=A1200',needle='stock compatible A500')
        refused(good,'--set','puae6_floppy_writes=enabled',needle='stock compatible A500')
        refused(self.cart_system(3),'--set','puae6_cartridge=ar1200',needle='stock A1200')
        cd=self.work()/'disc.iso'; cd.write_bytes(bytes(40960))
        refused(good,'--set','puae6_machine=auto',content=cd,needle='without a CD catalog')
        # A failed first Kick must not discard the bound cart OR a nonempty manifest.
        disk=self.work()/'disk.adf'; disk.write_bytes(lr.adf('cartridge-retry')); digest=hashlib.sha256(disk.read_bytes()).digest()
        work=self.work(); baseline=work/'base.state'; retried=work/'retry.state'
        baseline_out=self.frontend('boot','--runs',2,*opts,'--do',f'2:savefile:{baseline}',content=disk,system=good).stdout
        self.assertIn('images count=1',baseline_out)
        retry=self.cart_system(0); (retry/lr.KICKSTART_NAMES[0]).unlink()
        bad=bytearray(self.kicks[0].read_bytes()); bad[0x100]^=1; (retry/lr.KICKSTART_NAMES[0]).write_bytes(bad)
        (retry/lr.KICKSTART_NAMES[1]).symlink_to(self.kicks[0].resolve())
        out=self.frontend('boot','--runs',2,*opts,'--do',f'2:savefile:{retried}',content=disk,system=retry).stdout
        self.assertIn('p6_load failed',out); self.assertIn('Kickstart amiga-os-130.rom',out)
        self.assertEqual(out.count('vfs open '+str(retry/primary)+'\n'),1)
        self.assertIn('images count=1',out); self.assertEqual(struct.unpack_from('<i',self.chunks(retried)[b'P6BR'],20)[0],0)
        self.assertEqual(baseline.read_bytes(),retried.read_bytes()); self.assertEqual(hashlib.sha256(disk.read_bytes()).digest(),digest)
        # Reboot selection is latched. A new load applies it; AR bytes never become flags.
        deferred=work/'deferred.state'
        out=self.frontend('boot','--runs',2,*opts,'--set-at','1:puae6_cartridge=disabled',
            '--do',f'2:savefile:{deferred}',content=disk,system=good).stdout
        self.assertIn('reload the content to apply it',out); self.assertEqual(baseline.read_bytes(),deferred.read_bytes())
        out=self.frontend('boot','--runs',2,*opts,'--set-at','1:puae6_cartridge=disabled','--cycles',2,
            '--do',f'2:savefile:{deferred}',content=disk,system=good).stdout
        ram=self.chunks(deferred)[b'P6RM']; self.assertEqual(len(ram),8+sum(struct.unpack_from('<II',ram)))
        for foreign in ('disabled','other-ROM','ar2'):
            system=self.cart_system(1 if foreign=='ar2' else 0); extra=[]
            if foreign=='disabled': extra=['--set','puae6_cartridge=disabled']
            elif foreign=='ar2': extra=['--set','puae6_cartridge=ar2']
            else:
                (system/primary).unlink(); other=bytearray(self.carts[0].read_bytes()); other[0xfff8]^=1; (system/primary).write_bytes(other)
            before,after=work/'before.state',work/'after.state'
            out=self.frontend('boot','--runs',1,*opts,*extra,'--do',f'1:savefile:{before}',
                '--do',f'1:loadfile:{baseline}','--do',f'1:savefile:{after}',content=disk,system=system).stdout
            self.assertRegex(out,r'state load .* ok=0 '); self.assertEqual(before.read_bytes(),after.read_bytes())
        hrt=self.frontend('boot','--runs',2,*lr.A1200,'--set','puae6_cartridge=hrtmon',system=good).stdout
        self.assertNotIn('vfs open '+str(good/primary),hrt)  # Builtin selection never also binds an external AR.
