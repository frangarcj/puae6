#!/usr/bin/env python3
"""Isolated, offline x86_64 guest cross-build; never builds or certifies a runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys

import build_waterbox as shared

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'plan/extensions/waterbox-x64.json'
OUTPUT = ROOT / 'build/waterbox-x64'
Error = shared.BuildError


def run(args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, **kwargs).strip()


def linkscript(spec):
    pin = spec['linkscript']; source = ROOT / pin['source']
    if shared.sha256(source) != pin['sha256']:
        raise Error('x64 linkscript source differs')
    text = source.read_text()
    old = 'ALIGN(CONSTANT (MAXPAGESIZE)) + (. & (CONSTANT (MAXPAGESIZE) - 1)) :'
    if text.count(old) != 2:
        raise Error('unexpected large-data section alignment')
    derived = text.replace(old, 'ALIGN(CONSTANT (MAXPAGESIZE)) :')
    anchor = 'SECTIONS\n{\n'
    if derived.count(anchor) != 1:
        raise Error('unexpected linkscript section entry')
    # Windows maps the entire sandbox at its lowest nonzero PHDR address.
    # LLD otherwise omits the empty header segment and starts at base+4KiB.
    derived = derived.replace(anchor, anchor + '  .wbx_image_base 0x36f00000000 : { BYTE(0) }\n')
    if hashlib.sha256(derived.encode()).hexdigest() != pin['derived_sha256']:
        raise Error('derived x64 linkscript differs')
    return derived


def verify_profile(spec):
    if (spec.get('profile'), spec.get('target'), spec.get('machine'), spec.get('host_page_size')) != (
            'waterbox-x86_64-local-cross', 'x86_64-linux-gnu', 62, 4096):
        raise Error('invalid x86_64 profile/target/machine/page pair')
    paths = {}
    for name, pin in spec['tools'].items():
        path = Path(pin['path'])
        if shared.sha256(path) != pin['sha256'] or run([path, '--version']).splitlines()[0] != pin['version']:
            raise Error('tool differs from x64 pin: ' + name)
        paths[name] = path
    if set(paths) != {'clang', 'lld', 'make', 'nm', 'objdump'}:
        raise Error('incomplete x64 tool inventory')
    source = Path(spec['sysroot']['path'])
    entries = shared.inventory(source)
    if len(entries) != spec['sysroot']['file_count'] or shared.canonical_hash(entries) != spec['sysroot']['inventory_sha256']:
        raise Error('x64 sysroot inventory differs')
    crt = (source / 'lib/crt1.o').read_bytes()
    if crt[:6] != b'\x7fELF\x02\x01' or int.from_bytes(crt[18:20], 'little') != 62:
        raise Error('sysroot CRT is not ELF64LE x86_64')
    for name in ('libc.a', 'libc++.a', 'libc++abi.a', 'libunwind.a', 'linux/libclang_rt.builtins-x86_64.a'):
        if (source / 'lib' / name).read_bytes()[:8] != b'!<arch>\n':
            raise Error('missing static x64 library: ' + name)
    musl = Path(spec['musl']['path'])
    if run(['git', '-C', musl, 'rev-parse', 'HEAD']) != spec['musl']['commit']:
        raise Error('musl revision differs')
    diff = subprocess.check_output(['git', '-C', str(musl), 'diff', '--binary', 'HEAD'])
    if hashlib.sha256(diff).hexdigest() != spec['musl']['diff_sha256']:
        raise Error('musl local patchset differs')
    for name, digest in spec['musl']['templates'].items():
        if shared.sha256(musl / 'tools' / name) != digest:
            raise Error('musl wrapper template differs: ' + name)
    linkscript(spec)
    return paths, entries


def validate_elf(path):
    """Reject wrong ISA, TLS, dynamic/undefined imports, bad bounds and W+X before loading."""
    raw = Path(path).read_bytes()
    if len(raw) < 64 or raw[:6] != b'\x7fELF\x02\x01':
        raise Error('not ELF64 little endian')
    h = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    if h[1:3] != (2, 62):
        raise Error('requires ET_EXEC EM_X86_64')
    phoff, shoff, phsize, phnum, shsize, shnum = h[5], h[6], h[9], h[10], h[11], h[12]
    if phsize != 56 or not phnum or phoff < 64 or phoff + phnum * phsize > len(raw):
        raise Error('invalid program headers')
    loads, pages, mapping_starts = [], {}, []
    for i in range(phnum):
        kind, flags, offset, va, _, filesz, memsz, align = struct.unpack_from('<IIQQQQQQ', raw, phoff + i * phsize)
        if va:
            mapping_starts.append(va) # Same minimum as ElfLoader::elf_addr.
        if kind in (2, 3, 7):
            raise Error('dynamic/interpreter/compiler TLS segment')
        if kind != 1:
            continue
        if (filesz > memsz or offset + filesz > len(raw) or offset % 4096 or va % 4096
                or align < 4096 or align & (align - 1) or offset % align != va % align
                or memsz > 2**32 or va + memsz > 2**64 or flags & 3 == 3):
            raise Error('invalid x64 load segment bounds/alignment/protection')
        for page in range(va // 4096, (va + memsz + 4095) // 4096):
            if pages.setdefault(page, flags) != flags:
                raise Error('mixed page protections')
        loads.append(dict(offset=offset, vaddr=va, memsz=memsz, flags=flags))
    if not mapping_starts or min(mapping_starts) % 65536:
        raise Error('Windows sandbox mapping base must align to 64KiB')
    if not loads or not shnum or shsize != 64 or shoff < 64 or shoff + shnum * shsize > len(raw):
        raise Error('missing loads or invalid section headers (unstripped audit required)')
    symbols = 0
    for i in range(shnum):
        _, kind, flags, _, offset, size, _, _, _, entsize = struct.unpack_from('<IIQQQQIIQQ', raw, shoff + i * shsize)
        if flags & 0x400 or (kind != 8 and offset + size > len(raw)):
            raise Error('TLS section or out-of-bounds section')
        if kind in (2, 11):
            if entsize != 24 or size % 24:
                raise Error('invalid symbol table')
            symbols += 1
            for at in range(offset, offset + size, 24):
                name, info, _, section, value, _ = struct.unpack_from('<IBBHQQ', raw, at)
                # Hidden weak references (e.g. static musl's _DYNAMIC) become
                # local undefined zero symbols at final link: they do not ask
                # the loader for an external binding (ELF gABI 5.4).
                unresolved = name and section == 0
                if (info & 15 == 6 or (unresolved and info >> 4 not in (0, 2))
                        or (unresolved and value != 0)):
                    raise Error('TLS symbol or unresolved non-weak import')
    if not symbols:
        raise Error('missing symbol table for TLS/import audit')
    return dict(machine=62, host_page_size=4096, mapping_base=min(mapping_starts),
                sha256=shared.sha256(Path(path)), load_segments=loads)


def check_segment_access(disassembly):
    # musl Waterbox's explicit __pthread_self reads its sandbox context at GS:0x18.
    # Compiler TLS uses FS (or other GS offsets) and must not enter this guest.
    accesses = re.findall(r'%(fs|gs):([^\s,]*)', disassembly)
    if any(segment != 'gs' or offset not in ('0x18', '24') for segment, offset in accesses):
        raise Error('compiler/unknown segment TLS access')
    return len(accesses)


def build(spec, jobs):
    import import_sources
    import_sources.no_links(ROOT, OUTPUT)
    paths, entries = verify_profile(spec)
    # Do not materialize or generate into other lanes implicitly.
    for name in ('build/upstream', 'build/generated/codegen-receipt.json', 'build/assets/hrtmon_builtin.cpp'):
        if not (ROOT / name).exists():
            raise Error('prepare current engine/codegen/HRTMon first: ' + name)
    trees = shared.ensure_current_trees(ROOT)
    sources, manual = shared.puae6_sources(), shared.puae6_manual_inputs(ROOT)
    inputs = dict(profile=spec, trees=trees, manual=manual, sources={s: shared.sha256(ROOT / s) for s in sources},
                  recipe={s: shared.sha256(ROOT / s) for s in ('tools/build_waterbox_x64.py', 'tools/build_waterbox.py',
                          'ports/waterbox/Makefile', 'references/machawk/waterbox/common.mak', 'references/machawk/waterbox/linkscript.T')})
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = OUTPUT / 'receipt.json'
    previous = json.loads(receipt.read_text()) if receipt.is_file() else {}
    receipt.unlink(missing_ok=True)
    sysroot, bindir = OUTPUT / 'sysroot', OUTPUT / 'toolchain/bin'
    shared.copy_verified(Path(spec['sysroot']['path']), sysroot, entries)
    bindir.mkdir(parents=True, exist_ok=True)
    launcher = bindir / 'clang-x64'
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(paths['clang'])) + ' --target=x86_64-linux-gnu "$@"\n')
    launcher.chmod(0o755)
    cc, ld = shared.install_wrappers(sysroot, launcher, paths['lld'], Path(spec['musl']['path']), wrapper_dir=bindir)
    text = ld.read_text()
    tail = ' -dynamic-linker "$ldso"'
    entry = 'exec $($cc -print-prog-name=ld.lld)'
    if text.count(tail) != 1 or text.count(entry) != 1:
        raise Error('unexpected x64 linker wrapper')
    # A static Waterbox guest has no external interpreter. Pin the actual linker.
    ld.write_text(text.replace(tail, '').replace(entry, 'exec ' + shlex.quote(str(paths['lld']))))
    script = OUTPUT / 'linkscript.T'; script.write_text(linkscript(spec))
    env = {**os.environ, 'PATH': str(paths['lld'].parent) + os.pathsep + os.environ.get('PATH', '')}
    resolved = Path(run([launcher, '-print-prog-name=ld.lld'], env=env))
    if not resolved.is_absolute() or resolved.resolve() != paths['lld'].resolve():
        raise Error('cross compiler resolves another LLD')
    mk = OUTPUT / 'sources.mk'
    mk.write_text('SRCS := ' + ' '.join(sources) + '\n')
    command = [paths['make'], '-C', ROOT, '-f', 'ports/waterbox/Makefile', f'-j{jobs}', 'release',
               f'REPO={ROOT}', f'ROOT_DIR={ROOT}', f'OUT_DIR={OUTPUT}', f'SOURCES_MK={mk}', f'SYSROOT={sysroot}',
               f'CC={cc}', f'CXX={cc}', 'WBX_TARGET_ARCH=x86_64', 'WBX_HOST_PAGE_SIZE=4096',
               'CCFLAGS_RELEASE=-O2', 'CXXFLAGS_RELEASE=-O2', f'LINKSCRIPT={script}']
    if previous.get('build_inputs') != inputs:
        command.insert(1, '-B')
    subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True)
    if shared.puae6_manual_inputs(ROOT) != manual or any(shared.sha256(ROOT / s) != digest for s, digest in inputs['sources'].items()):
        raise Error('guest inputs changed during make')
    guest = OUTPUT / 'release/puae6.wbx'
    metadata = validate_elf(guest)
    metadata['sandbox_gs_context_reads'] = check_segment_access(run([paths['objdump'], '-d', '--no-show-raw-insn', guest]))
    imports = shared.audit_imports({'paths': paths, 'env': env}, [OUTPUT / 'release' / (s + '.o') for s in sources])
    result = dict(status='BUILD_ONLY_GUEST_NOT_EXECUTED', publication_ready=False, runtime_verified=False,
                  guest={**metadata, 'path': str(guest)}, imports=imports, build_inputs=inputs)
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true', help='explicitly compile guest; default verifies local inputs only')
    parser.add_argument('--jobs', type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    spec = json.loads(PROFILE.read_text())
    if args.build:
        result = build(spec, args.jobs)
    else:
        verify_profile(spec)
        result = dict(status='LOCAL_INPUTS_VERIFIED_GUEST_NOT_BUILT', publication_ready=False, runtime_verified=False)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Error, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
