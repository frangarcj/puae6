"""Owned ELF/ar byte fixtures. No compiler, archive executable or guest is run."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('guest_sysroot',ROOT/'tools/build_waterbox_guest_sysroot.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


def obj(name, fixed=False, extra=False, *, legacy=False, extra_symbol=False, global_name=None):
    aliases=p.TARGETS[name]; strings=b'\0'; offsets={}
    local='$x.0' if legacy else '$x'; words=('.strtab','.text','.note.GNU-stack','.symtab',local,*aliases)
    if global_name: words=(*words,global_name)
    for word in reversed(words) if legacy else words: offsets[word]=len(strings); strings+=word.encode()+b'\0'
    text=bytearray(24); opcode=0xa904701b if name=='setjmp.o' else 0xa944701b
    struct.pack_into('<I',text,16,opcode+(0xc00 if fixed else 0)); text[0]=int(extra)
    symbols=bytes(24)+struct.pack('<IBBHQQ',offsets[local],0,0,2,0,0)
    symbols+=b''.join(struct.pack('<IBBHQQ',offsets[global_name if global_name and a==aliases[0] else a],0x12,0,2,0,0) for a in aliases)
    if extra_symbol: symbols+=struct.pack('<IBBHQQ',offsets[local],0,0,2,4,0)
    data=bytearray(64); sections=[(0,0,0,0,0,0,0,0,0,0)]
    for word,kind,flags,payload,link,align,entry in (('.strtab',3,0,strings,0,1,0),('.text',1,6,text,0,4,0),('.note.GNU-stack',1,0,b'',0,1,0),('.symtab',2,0,symbols,1,8,24)):
        while len(data)%align: data.append(0)
        sections.append((offsets[word],kind,flags,0,len(data),len(payload),link,1,align,entry)); data+=payload
    while len(data)%8: data.append(0)
    shoff=len(data); data+=b''.join(struct.pack('<IIQQQQIIQQ',*s) for s in sections)
    struct.pack_into('<16sHHIQQQIHHHHHH',data,0,b'\x7fELF\x02\x01\x01'+bytes(9),1,183,1,0,0,shoff,0,64,0,0,64,len(sections),1)
    return bytes(data)


def ar(members, *, other_symbol=True):
    def member(name,data):
        h=name.encode().ljust(16)+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+b'644'.ljust(8)+str(len(data)).encode().ljust(10)+b'`\n'
        return h+data+(b'\n' if len(data)%2 else b'')
    symbols=[(alias,name) for name in p.TARGETS for alias in p.TARGETS[name]]
    if other_symbol: symbols.append(('own_symbol','dup.o'))
    strings=b''.join(alias.encode()+b'\0' for alias,_ in symbols); index_size=4+4*len(symbols)+len(strings)
    offset=8+60+index_size+(index_size&1); positions={}
    for name,data in members:
        positions.setdefault(name,offset); offset+=60+len(data)+(len(data)&1)
    index=struct.pack('>I',len(symbols))+b''.join(struct.pack('>I',positions[name]) for _,name in symbols)+strings
    return b'!<arch>\n'+member('/',index)+b''.join(member(name+'/',data) for name,data in members)


class GuestSysrootTests(unittest.TestCase):
    def members(self,fixed=False): return [('dup.o',b'own-one'),('setjmp.o',obj('setjmp.o',fixed)),('dup.o',b'own-two'),('longjmp.o',obj('longjmp.o',fixed))]

    def test_base_assembler_local_mapping_name_and_offsets_only(self):
        for name in p.TARGETS:
            base,current,fixed=obj(name,legacy=True),obj(name),obj(name,True)
            a,b=p.metadata(base,current,name,base_metadata=True); self.assertEqual(a['.text'],b['.text'])
            p.objects(base,fixed,name,base_metadata=True); p.objects(current,fixed,name)
            with self.assertRaises(ValueError): p.objects(base,fixed,name)
            for bad in (obj(name,global_name='changed'),obj(name,extra_symbol=True),obj(name,True,True)):
                with self.assertRaises(ValueError): p.objects(base,bad,name,base_metadata=True)
            flags=bytearray(fixed); struct.pack_into('<I',flags,48,1)
            with self.assertRaises(ValueError): p.objects(base,flags,name,base_metadata=True)

    def test_duplicate_member_occurrences_index_and_exact_text_delta(self):
        before,after=ar(self.members()),ar(self.members(True)); p.preserved(before,after)
        self.assertEqual([m[0] for m in p.archive(after)],['dup.o','setjmp.o','dup.o','longjmp.o'])
        for name in p.TARGETS:
            p.objects(obj(name),obj(name,True),name)
            with self.assertRaises(ValueError): p.objects(obj(name),obj(name,True,True),name)
            wrong=bytearray(obj(name)); struct.pack_into('<H',wrong,18,62)
            with self.assertRaises(ValueError): p.elf(wrong,p.TARGETS[name])
        changed=self.members(True); changed[2]=('dup.o',b'changed')
        with self.assertRaises(ValueError): p.preserved(before,ar(changed))
        broken=bytearray(after); struct.pack_into('>I',broken,8+60+4,1)
        with self.assertRaises(ValueError): p.archive(broken)
        with self.assertRaises(ValueError): p.archive(after[:-1])
        redirected=bytearray(after); struct.pack_into('>I',redirected,8+60+4+5*4,p.archive(after)[2][2])
        with self.assertRaises(ValueError): p.preserved(before,redirected)
        with self.assertRaises(ValueError): p.preserved(before,ar(self.members(True),other_symbol=False))

    def test_preflight_exclusive_outputs_execute_failure_cleanup_and_readonly_verify(self):
        (ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='guest-sysroot-fixture-',dir=ROOT/'build') as temporary:
            root=Path(temporary); base=root/'build/base'; (base/'lib').mkdir(parents=True); (base/'lib/libc.a').write_bytes(ar(self.members()))
            (base/'own.h').write_bytes(b'own header'); original=p.wb.inventory(base); musl=root/'musl'
            source=musl/'src/setjmp/waterbox_aarch64'; source.mkdir(parents=True)
            for name in p.TARGETS: shutil.copyfile(ROOT/'references/musl-darwin/src/setjmp/waterbox_aarch64'/name.replace('.o','.s'),source/name.replace('.o','.s'))
            patch=root/p.PATCH; patch.parent.mkdir(parents=True); shutil.copyfile(ROOT/p.PATCH,patch)
            cfg={'archiver':{'path':'/own/ar','version':'own ar'}}; inputs={'compiler_path':'/own/clang','compiler':{'version':'own clang'},'own':'fixture'}
            identity=({},cfg,base,original,musl,inputs); real_run=subprocess.run; mode='ok'
            def command(args,**kwargs):
                args=list(map(str,args))
                if args[0]=='patch': return real_run(args,**kwargs)
                if args[-1]=='--version': return subprocess.CompletedProcess(args,0,'own ar\n' if args[0]=='/own/ar' else 'own clang\n','')
                if args[0]=='/own/clang':
                    output=Path(args[-1]); output.write_bytes(obj(output.name,output.parent.name=='fixed'))
                else:
                    archive=Path(args[3]); members=self.members(True)
                    if mode=='bad': members[0]=('dup.o',b'corrupted')
                    archive.write_bytes(ar(members))
                return subprocess.CompletedProcess(args,0,'','')
            with mock.patch.object(p,'identity',return_value=identity),mock.patch.object(p.subprocess,'run',side_effect=command) as child:
                self.assertEqual(p.derive(root=root)['status'],'NOT_RUN'); child.assert_not_called()
                with mock.patch.object(p,'fcntl',None):
                    self.assertEqual(p.derive(root=root)['status'],'NOT_RUN')
                    with self.assertRaisesRegex(ValueError,'POSIX process locking'): p.derive(root=root,execute=True)
                    child.assert_not_called()
                mode='bad'
                with self.assertRaises(ValueError): p.derive(root=root,execute=True)
                self.assertFalse((root/'build/waterbox-guest-sysroot').exists())
                self.assertFalse(list((root/'build').glob('guest-sysroot-*'))); mode='ok'
                result=p.derive(root=root,execute=True); cfg['result']={k:result[k] for k in ('inventory_sha256','libc_sha256')}
                verified=p.verify(root=root); self.assertEqual({n.name for n in (root/'build/waterbox-guest-sysroot').iterdir()},{'sysroot','receipt.json'}); self.assertEqual(verified['libc_sha256'],result['libc_sha256'])
                with self.assertRaises(ValueError): p.derive(root=root,execute=True)
                (verified['sysroot']/'own.h').write_bytes(b'changed')
                with self.assertRaises(ValueError): p.verify(root=root)
            self.assertEqual(p.wb.inventory(base),original)
