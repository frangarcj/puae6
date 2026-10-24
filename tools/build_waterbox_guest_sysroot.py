#!/usr/bin/env python3
"""Source-only derivation of two libc objects; base and product inputs stay read-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
try:
    import fcntl
except ImportError:
    fcntl = None
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_waterbox as wb
import import_sources as sources

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'integration/waterbox-guest/0001-setjmp-x28.patch'
TARGETS = {'setjmp.o': ('__setjmp', '_setjmp', 'setjmp'), 'longjmp.o': ('_longjmp', 'longjmp')}
FLAGS = ['--target=aarch64-linux-gnu', '-ffixed-x18', '-ffixed-x28', '-DWBX_HOST_PAGE_SIZE=16384', '-Wa,--noexecstack']


def require(ok, message):
    if not ok: raise ValueError(message)


def digest(data): return hashlib.sha256(data).hexdigest()


def archive(data, *, with_index=False):
    require(data.startswith(b'!<arch>\n') and len(data) <= 64 << 20, 'Unsupported archive')
    at = 8; raw = []; strings = b''; index = None
    while at < len(data):
        header = data[at:at+60]; require(len(header) == 60 and header[58:] == b'`\n', 'Bad ar header')
        name = header[:16].decode('ascii').strip(); size = int(header[48:58]); end = at+60+size
        require(size >= 0 and end <= len(data) and (not size%2 or data[end:end+1] == b'\n'), 'Truncated ar member')
        payload = data[at+60:end]
        if name == '//': require(not strings, 'Duplicate longname table'); strings = payload
        elif name == '/': require(index is None, 'Duplicate index'); index = payload
        else: raw.append((name, payload, at))
        at = end+(size&1)
    require(at == len(data) and index is not None and len(index) >= 4, 'Missing archive index')
    members = []
    for name, payload, offset in raw:
        if name.startswith('/') and name[1:].isdigit():
            position = int(name[1:]); require(position < len(strings), 'Bad longname offset')
            end = strings.find(b'/\n', position); require(end >= position, 'Bad longname'); name = strings[position:end].decode('ascii')
        else: require(name.endswith('/') and not name.startswith(('/', '#')), 'Unsupported member name'); name = name[:-1]
        require(name and '/' not in name and '\\' not in name, 'Unsafe member name'); members.append((name, payload, offset))
    count = int.from_bytes(index[:4], 'big'); require(count <= 100000 and 4+4*count <= len(index), 'Bad ar index count')
    offsets = struct.unpack_from('>'+str(count)+'I', index, 4); names = index[4+4*count:].split(b'\0')
    require(len(names) > count and not any(names[count:]), 'Bad ar symbol names')
    valid = {offset for _, _, offset in members}; require(all(n in valid for n in offsets), 'Index points outside member headers')
    symbols = [(name.decode('ascii'), offset) for name, offset in zip(names[:count], offsets)]
    for target, aliases in TARGETS.items():
        selected = [m for m in members if m[0] == target]; require(len(selected) == 1, 'Missing/duplicate target member')
        require(all((alias, selected[0][2]) in symbols for alias in aliases), 'Missing/wrong archive alias')
    ordinals={offset:i for i,(_,_,offset) in enumerate(members)}
    require(all(name for name,_ in symbols),'Empty archive index symbol')
    return (members,[(name,ordinals[offset]) for name,offset in symbols]) if with_index else members


def elf(data, aliases):
    require(len(data) >= 64 and data[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<HH', data, 16) == (1,183), 'Object must be AArch64 ELF64 ET_REL')
    offset = struct.unpack_from('<Q', data, 40)[0]; width, count, names_index = struct.unpack_from('<HHH', data, 58)
    require(width == 64 and 0 < count < 128 and names_index < count and offset+count*64 <= len(data), 'Invalid ELF sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset+i*64) for i in range(count)]
    def payload(section):
        start, size = section[4:6]; require(start+size <= len(data), 'Truncated ELF payload'); return data[start:start+size]
    names = payload(sections[names_index]); result = {}; symbols = []
    for section in sections[1:]:
        end = names.find(b'\0', section[0]); require(end >= section[0], 'Bad ELF section name'); name = names[section[0]:end].decode('ascii')
        require(name not in result and not section[2]&0x400, 'Duplicate/TLS section'); result[name] = (section[1], section[2], section[8], payload(section), (section[3],section[6],section[7],section[9]))
        if section[1] == 2:
            require(section[9] == 24 and section[5]%24 == 0 and section[6] < count, 'Bad ELF symbol table')
            strings = payload(sections[section[6]])
            for at in range(0,section[5],24):
                n, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', payload(section), at)
                end = strings.find(b'\0', n); require(end >= n, 'Bad symbol name'); symbols.append((strings[n:end].decode('ascii'),info,other,shndx,value,size))
    text = result.get('.text'); require(text is not None and text[0] == 1 and len(text[3])%4 == 0, 'Missing object text')
    text_index = next(i for i,s in enumerate(sections) if payload(sections[names_index])[s[0]:].split(b'\0')[0] == b'.text')
    require(all(any(s[0] == alias and s[1] == 0x12 and s[3] == text_index and s[4] == 0 for s in symbols) for alias in aliases), 'Object aliases differ')
    return result, symbols


def metadata(before, after, name, *, base_metadata=False):
    old, symbols = elf(before, TARGETS[name]); new, new_symbols = elf(after, TARGETS[name])
    require(before[:40]==after[:40] and before[48:64]==after[48:64], 'Object ELF header attributes differ')
    require(list(old) == list(new), 'Object sections differ')
    renamed = False
    if base_metadata and symbols != new_symbols:
        text_index=next(s[3] for s in symbols if s[0]==TARGETS[name][0])
        local=('$x.0',0,0,text_index,0,0); replacement=('$x',*local[1:])
        require(symbols.count(local)==1 and [replacement if s==local else s for s in symbols]==new_symbols, 'Unexpected assembler symbol delta')
        renamed=True
    else: require(symbols == new_symbols, 'Object symbols differ')
    for section in old:
        a,b=old[section],new[section]
        require(a[:3]+a[4:] == b[:3]+b[4:], 'Object section attributes differ')
        if renamed and section=='.strtab':
            require(a[0]==3 and a[3].startswith(b'\0') and a[3].endswith(b'\0') and b[3].startswith(b'\0') and b[3].endswith(b'\0')
                and sorted(b'$x' if s==b'$x.0' else s for s in a[3].split(b'\0'))==sorted(b[3].split(b'\0')), 'Unexpected assembler string table delta')
        elif renamed and section=='.symtab': require(a[0]==2 and len(a[3])==len(b[3]), 'Unexpected symbol table delta')
        elif section != '.text': require(a == b, 'Unexpected object metadata/relocation delta')
    return old,new


def objects(before, after, name, *, base_metadata=False):
    old,new=metadata(before,after,name,base_metadata=base_metadata)
    if not base_metadata:
        changes=[i for i,(a,b) in enumerate(zip(before,after)) if a!=b]
        require(len(before)==len(after) and len(changes)==1 and before[changes[0]]^after[changes[0]]==0x0c, 'Unexpected raw object delta')
    a, b = old['.text'][3], new['.text'][3]; opcode = 0xa904701b if name == 'setjmp.o' else 0xa944701b
    require(old['.text'][:3] == new['.text'][:3] and len(a) == len(b) and len(a) >= 20
        and a[:16] == b[:16] and a[20:] == b[20:] and struct.unpack_from('<I',a,16)[0] == opcode
        and struct.unpack_from('<I',b,16)[0] == opcode+0xc00, 'Text is not exactly the reserved x28 instruction change')


def preserved(before, after):
    (a,ai),(b,bi)=archive(before,with_index=True),archive(after,with_index=True)
    require([m[0] for m in a] == [m[0] for m in b] and ai==bi, 'Archive member order/occurrences or symbol mapping changed')
    for x,y in zip(a,b):
        if x[0] not in TARGETS: require(x[1] == y[1], 'Unrelated archive member changed')
    return a,b


def identity(root):
    lock = json.loads((root/'plan/toolchains.json').read_text()); cfg = lock['waterbox_guest_sysroot']
    require(cfg['schema_version'] == 1 and type(cfg['schema_version']) is int and cfg['patch']['file'] == PATCH and wb.sha256(root/PATCH) == cfg['patch']['sha256'], 'Guest overlay pin differs')
    effective = wb.selected_toolchain(lock); compiler = effective['tools']['clang']; paths = wb.selected_tool_paths(effective)
    for path,pin in ((paths['clang'],compiler),(Path(cfg['archiver']['path']),cfg['archiver'])):
        require(path.is_absolute() and wb.sha256(path) == pin['sha256'], 'Compiler/archiver pin differs')
    base = root/lock['sysroot']['source_path']; inventory = wb.inventory(base)
    require(len(inventory) == lock['sysroot']['file_count'] and wb.canonical_hash(inventory) == lock['sysroot']['inventory_sha256'], 'Base sysroot differs')
    capture = sources.verify_source(root, lock['sysroot']['associated_source']); captured = {f['path']:f['sha256'] for f in capture['files']}
    require(all(captured.get(n) == h for n,h in capture['source']['snapshot'].get('overlay_sha256',{}).items()), 'Musl capture overlay differs')
    inputs = dict(base=wb.canonical_hash(inventory), source=wb.canonical_hash(capture), patch=cfg['patch'], compiler=compiler,
        compiler_path=str(paths['clang']), archiver=cfg['archiver'], recipe=wb.sha256(Path(__file__)), flags=FLAGS)
    return lock,cfg,base,inventory,root/capture['destination'],inputs


def verify(output=None, *, root=ROOT):
    root=Path(root); output=Path(output or root/'build/waterbox-guest-sysroot'); sources.no_links(root,output)
    for path in (output/'sysroot',output/'receipt.json'): sources.no_links(root,path)
    require({p.name for p in output.iterdir()}=={'sysroot','receipt.json'},'Foreign derived output entries')
    lock,cfg,base,original,_,inputs=identity(root); receipt=json.loads((output/'receipt.json').read_text()); derived=wb.inventory(output/'sysroot')
    require(receipt['inputs'] == inputs and wb.canonical_hash(derived) == receipt['inventory_sha256'] == cfg['result']['inventory_sha256']
        and wb.sha256(output/'sysroot/lib/libc.a') == receipt['libc_sha256'] == cfg['result']['libc_sha256'], 'Derived sysroot authority/receipt differs')
    require([e for e in original if e['path']!='lib/libc.a'] == [e for e in derived if e['path']!='lib/libc.a'] and len(original)==len(derived), 'Other sysroot paths changed')
    a,b=preserved((base/'lib/libc.a').read_bytes(),(output/'sysroot/lib/libc.a').read_bytes())
    for name in TARGETS: objects(next(m[1] for m in a if m[0]==name),next(m[1] for m in b if m[0]==name),name,base_metadata=True)
    return dict(sysroot=output/'sysroot', inventory_sha256=receipt['inventory_sha256'], libc_sha256=receipt['libc_sha256'], receipt_sha256=wb.sha256(output/'receipt.json'), inputs=inputs)


def derive(*, root=ROOT, output=None, execute=False):
    root=Path(root); output=Path(output or root/'build/waterbox-guest-sysroot'); sources.no_links(root,output)
    require(output.is_relative_to(root/'build') and output != root/'build' and not output.exists() and output.parent.is_dir(), 'Output must be new below build')
    lock,cfg,base,inventory,musl,inputs=identity(root)
    if not execute: return dict(status='NOT_RUN', output=str(output), inputs=inputs)
    require(fcntl is not None, 'Materialization requires POSIX process locking (Darwin/Linux)')
    environment={k:v for k,v in os.environ.items() if k not in ('CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','OBJC_INCLUDE_PATH','LIBRARY_PATH','COMPILER_PATH','GCC_EXEC_PREFIX','SDKROOT','CFLAGS','CPPFLAGS','LDFLAGS','ARFLAGS','MAKEFLAGS')}
    environment.update(LC_ALL='C',LANG='C'); working=root
    def command(args):
        result=subprocess.run(list(map(str,args)),cwd=working,env=environment,capture_output=True,text=True,timeout=60)
        require(result.returncode==0 and len(result.stdout)+len(result.stderr)<1<<20,'Derivation command failed: '+result.stderr[-1000:]); return result.stdout
    for path,pin in ((inputs['compiler_path'],inputs['compiler']),(cfg['archiver']['path'],cfg['archiver'])):
        require(command([path,'--version']).splitlines()[0].strip()==pin['version'],'Observed compiler/archiver version differs')
    with tempfile.TemporaryDirectory(prefix='guest-sysroot-',dir=root/'build') as temporary:
        stage=Path(temporary); working=stage; shutil.copytree(base,stage/'sysroot'); source=stage/'src/setjmp/waterbox_aarch64'; source.mkdir(parents=True)
        for name in TARGETS: shutil.copyfile(musl/'src/setjmp/waterbox_aarch64'/name.replace('.o','.s'),source/name.replace('.o','.s'))
        compiled={}
        for phase in ('original','fixed'):
            if phase=='fixed':
                result=subprocess.run(['patch','-p1','--fuzz=0','--no-backup-if-mismatch','-i',str(root/PATCH)],cwd=stage,env=environment,capture_output=True,timeout=10)
                require(result.returncode==0,'Atomic source patch rejected')
            directory=stage/'objects'/phase; directory.mkdir(parents=True)
            for name in TARGETS:
                target=directory/name; command([inputs['compiler_path'],*FLAGS,'-c',source/name.replace('.o','.s'),'-o',target]); compiled[phase,name]=target.read_bytes()
        old_members=archive((base/'lib/libc.a').read_bytes()); differences={}
        for name in TARGETS:
            objects(compiled['original',name],compiled['fixed',name],name)
            old=next(m[1] for m in old_members if m[0]==name); elf(old,TARGETS[name])
            baseline,recompiled=metadata(old,compiled['original',name],name,base_metadata=True)
            require(baseline['.text'][3]==recompiled['.text'][3],'Original source object text differs from base')
            differences[name]=dict(base_identical=old==compiled['original',name], original_sha256=digest(compiled['original',name]),fixed_sha256=digest(compiled['fixed',name]))
        archive_path=stage/'sysroot/lib/libc.a'; command([cfg['archiver']['path'],'rcsD','--format=gnu',archive_path,*[stage/'objects/fixed'/n for n in TARGETS]])
        _,updated=preserved((base/'lib/libc.a').read_bytes(),archive_path.read_bytes())
        require(all(next(m[1] for m in updated if m[0]==name)==compiled['fixed',name] for name in TARGETS),'Archiver did not replace exact objects')
        derived=wb.inventory(stage/'sysroot'); require(len(derived)==len(inventory) and [e for e in derived if e['path']!='lib/libc.a']==[e for e in inventory if e['path']!='lib/libc.a'],'Other sysroot paths changed')
        require(identity(root)[-1]==inputs,'Source/base/tool inputs changed')
        receipt=dict(schema_version=1,inputs=inputs,objects=differences,inventory_sha256=wb.canonical_hash(derived),libc_sha256=wb.sha256(archive_path))
        publish=stage/'publish'; publish.mkdir(); (stage/'sysroot').rename(publish/'sysroot')
        (publish/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
        # Serialize publication; a competing derivation cannot replace an output.
        fd=os.open(root/'build/.guest-sysroot.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd); require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'Invalid publication lock')
            fcntl.flock(fd,fcntl.LOCK_EX); require(not output.exists() and not output.is_symlink(),'Output already exists'); publish.rename(output)
        finally: os.close(fd)
    return dict(status='PASS_DERIVED_ONLY',output=str(output),inventory_sha256=receipt['inventory_sha256'],libc_sha256=receipt['libc_sha256'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--execute',action='store_true'); parser.add_argument('--output',type=Path); args=parser.parse_args()
    try:
        result=derive(output=args.output,execute=args.execute); print(json.dumps(result,sort_keys=True))
        sys.exit(3 if result['status']=='NOT_RUN' else 0)
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as error: sys.exit('BLOCKED: '+str(error))
