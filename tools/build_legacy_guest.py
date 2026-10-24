#!/usr/bin/env python3
"""Build a source-derived debug UAE reference; never install or execute a guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox as wb
import import_sources as sources

IDS = ('machawk', 'bizhawk_uae_reference', 'machawk_capsimg', 'waterbox_musl_darwin')
PATCH = 'integration/waterbox-guest/0002-legacy-softfloat-quotient.patch'
SOFTFLOAT = 'sources/src/fpp_softfloat.c'
STATUS = 'BUILD01_GUEST_NOT_EXECUTED'
FLAGS = ['--target=aarch64-linux-gnu', '-ffixed-x18', '-ffixed-x28', '-DWBX_HOST_PAGE_SIZE=16384',
         '-fvisibility=hidden', '-mcmodel=large', '-mstack-protector-guard=global', '-fno-pic', '-fno-pie',
         '-fcf-protection=none', '-O0', '-g']


def require(ok, message):
    if not ok: raise ValueError(message)


def derived():
    from tools.build_waterbox_guest_sysroot import verify
    return verify(root=ROOT)


def adapted(data):
    for name in ('mod', 'rem'):
        old = ('\ta->fpx = floatx80_'+name+'(a->fpx, b->fpx, q, s, &fs);').encode()
        new = ('\tuint64_t quotient = 0;\n\ta->fpx = floatx80_'+name+'(a->fpx, b->fpx, &quotient, s, &fs);\n\t*q = quotient;').encode()
        require(data.count(old) == 1, 'Unexpected legacy softfloat adapter')
        data = data.replace(old, new)
    return data


def source_overlay(tree):
    before = (tree / SOFTFLOAT).read_bytes()
    for flags in (['--forward'], ['--reverse', '--dry-run']):
        result = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '--no-backup-if-mismatch', *flags, '-i', str(ROOT / PATCH)],
                                cwd=tree, capture_output=True, timeout=10)
        require(result.returncode == 0, 'Legacy source overlay rejected: ' + result.stderr.decode()[-1000:])
    require((tree / SOFTFLOAT).read_bytes() == adapted(before), 'Unexpected source overlay delta')


def inputs():
    captures = {}; paths = {}
    for sid in IDS:
        item, identity = wb.source_spec(sid, 'snapshot')
        paths[sid] = ROOT / item['snapshot']['destination']
        captures[sid] = identity
        if sid == 'bizhawk_uae_reference':
            overlays = item['snapshot'].get('overlay_sha256', {})
            require(len(overlays) == 8, 'The eight authorized UAE overlays are required')
            captures[sid] = {**identity, 'overlays': overlays}
    lock = wb.selected_toolchain(); tools = wb.selected_tool_paths(lock)
    hashes = {name: wb.sha256(path) for name, path in tools.items()}
    require(all(hashes[n] == pin['sha256'] for n, pin in lock['tools'].items()), 'Pinned tools differ')
    sysroot = derived()
    require(isinstance(sysroot['sysroot'], Path) and sysroot['sysroot'].resolve() == sysroot['sysroot'], 'Canonical derived sysroot required')
    proof = {'captures': captures, 'tools': {n: {'path': str(tools[n]), 'sha256': hashes[n], 'version': lock['tools'][n]['version']} for n in tools},
             'sysroot': {k: str(v) if isinstance(v, Path) else v for k, v in sysroot.items()},
             'recipe_sha256': wb.sha256(Path(__file__)), 'profile': lock['profile'], 'toolchain_profile': lock['toolchain_profile'],
             'reference_fingerprint_sha256': wb.sha256(ROOT / 'plan/machawk.reference.json')}
    original = paths['bizhawk_uae_reference'] / SOFTFLOAT
    proof['source_overlay'] = {'file': PATCH, 'sha256': wb.sha256(ROOT / PATCH), 'target': SOFTFLOAT,
                              'original_sha256': wb.sha256(original), 'patched_sha256': hashlib.sha256(adapted(original.read_bytes())).hexdigest()}
    return paths, tools, sysroot['sysroot'], proof


def output_path(value=None):
    output = Path(value) if value is not None else ROOT / 'build/legacy-guest'
    if not output.is_absolute(): output = ROOT / output
    sources.no_links(ROOT, output)
    require(output.resolve() == output and output.is_relative_to(ROOT / 'build') and output != ROOT / 'build', 'Output must be canonical below build/')
    return output


def wrappers(directory, musl, sysroot, tools, exports):
    directory.mkdir()
    require(not any(c in str(tools['lld']) for c in '\\"$`\n'), 'Unsafe linker path')
    values = {'@CC@': str(tools['clang']), '@PREFIX@': str(sysroot), '@INCDIR@': str(sysroot / 'include'),
              '@LIBDIR@': str(sysroot / 'lib'), '@LDSO@': '/lib/libc.so'}
    result = {}
    for template, name in (('musl-clang.in', 'musl-clang'), ('ld.musl-clang.in', 'ld.musl-clang')):
        text = (musl / 'tools' / template).read_text()
        for key, value in values.items():
            require(not any(c in value for c in '\\"$`\n') and (key != '@CC@' or ' ' not in value), 'Unsafe wrapper substitution')
            text = text.replace(key, value)
        require(not re.search(r'@[A-Z]+@', text), 'Unresolved wrapper token')
        first, newline, body = text.partition('\n'); require(first.startswith('#!') and newline, 'Missing wrapper shebang')
        # Fix the linker directly: no ambient clang sibling or PATH fallback.
        if name == 'ld.musl-clang':
            require('exec $($cc -print-prog-name=ld.lld)' in body, 'Unexpected linker template')
            body = body.replace('exec $($cc -print-prog-name=ld.lld)', 'exec "' + str(tools['lld']) + '"')
        path = directory / name; path.write_text(first + newline + exports + body); path.chmod(0o755)
        result[name] = wb.sha256(path)
    return result


def prepare(stage, paths):
    waterbox = stage / 'waterbox'; waterbox.mkdir()
    captured = paths['machawk'] / 'waterbox'
    for name in ('common.mak', 'linkscript_aarch64.T'): shutil.copy2(captured / name, waterbox / name)
    for name in ('emulibc', 'uae'): shutil.copytree(captured / name, waterbox / name, symlinks=True)
    for sid, name in (('bizhawk_uae_reference', 'libretro-uae'), ('machawk_capsimg', 'capsimg')):
        shutil.copytree(paths[sid], waterbox / 'uae' / name, symlinks=True)
    source_overlay(waterbox / 'uae/libretro-uae')
    audit_copy(stage, paths)
    return waterbox


def audit_copy(stage, paths):
    waterbox = stage / 'waterbox'; captured = paths['machawk'] / 'waterbox'
    for name in ('common.mak', 'linkscript_aarch64.T'):
        require(wb.sha256(captured / name) == wb.sha256(waterbox / name), 'Copied recipe differs')
    # Every copied source byte is checked; generated CPU files stay untouched.
    for name in ('emulibc', 'uae'):
        expected = wb.inventory(captured / name)
        actual = [e for e in wb.inventory(waterbox / name) if name != 'uae' or not e['path'].startswith(('libretro-uae/', 'capsimg/'))]
        require(actual == expected, 'Copied MacHawk source differs')
    for sid, name in (('bizhawk_uae_reference', 'libretro-uae'), ('machawk_capsimg', 'capsimg')):
        expected = wb.inventory(paths[sid])
        if sid == 'bizhawk_uae_reference':
            expected = [{**e, 'sha256': hashlib.sha256(adapted((paths[sid] / SOFTFLOAT).read_bytes())).hexdigest()} if e['path'] == SOFTFLOAT else e for e in expected]
        require(wb.inventory(waterbox / 'uae' / name) == expected, 'Copied legacy source differs outside overlay')


def commands(stage, tools, sysroot):
    tree = stage / 'waterbox'; wrapper = stage / 'toolchain/musl-clang'
    common = ['-j2', 'WBX_TARGET_ARCH=aarch64', 'WBX_HOST_PAGE_SIZE=16384', 'SYSROOT=' + str(sysroot),
              'CC=' + str(wrapper), 'CXX=' + str(wrapper), 'CCFLAGS_DEBUG=-O0 -g', 'CXXFLAGS_DEBUG=-O0 -g']
    obj = stage / 'objects/emulibc/debug/emulibc.c.o'
    return [[str(tools['make']), '-C', str(tree / 'emulibc'), str(obj), 'OUT_DIR=' + str(stage / 'objects/emulibc'), *common],
            [str(tools['make']), '-C', str(tree / 'uae'), 'debug', 'OUT_DIR=' + str(stage / 'objects/uae'), 'EMULIBC_DOBJS=' + str(obj), *common]]


def symbols(path):
    raw = path.read_bytes(); header = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    off, width, count = header[6], header[11], header[12]
    require(width >= 64 and count > 0 and off + width * count <= len(raw), 'Missing ELF symbol sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, off + n * width) for n in range(count)]
    defined = set(); strong = set(); weak = set()
    for s in sections:
        if s[1] not in (2, 11): continue
        require(s[9] == 24 and s[5] % 24 == 0 and s[4] + s[5] <= len(raw) and s[6] < count, 'Invalid symbol table')
        strings = sections[s[6]]; require(strings[4] + strings[5] <= len(raw), 'Invalid string table')
        names = raw[strings[4]:strings[4] + strings[5]]
        for at in range(s[4], s[4] + s[5], 24):
            name, info, visibility, section, _, _ = struct.unpack_from('<IBBHQQ', raw, at)
            end = names.find(b'\0', name); require(name < len(names) and end >= 0, 'Invalid symbol name')
            label = names[name:end].decode('ascii')
            if label and info >> 4 in (1, 2):
                if section == 0: (strong if info >> 4 == 1 else weak).add(label)
                elif visibility & 3 == 0: defined.add(label)
    require(not strong and {'Init', 'FrameAdvance', 'GetMemoryAreas'} <= defined, 'Legacy imports/exports differ')
    return {'strong_undefined': [], 'weak_undefined': sorted(weak), 'required_exports': ['FrameAdvance', 'GetMemoryAreas', 'Init']}


def verify(output=None):
    output = output_path(output); sources.no_links(ROOT, output / 'receipt.json'); sources.no_links(ROOT, output / 'uae.wbx')
    require(set(p.name for p in output.iterdir()) == {'receipt.json', 'uae.wbx'}, 'Foreign legacy output inventory')
    data = json.loads((output / 'receipt.json').read_text()); proof = inputs()[3]
    require(data['schema_version'] == 1 and data['status'] == STATUS and data['inputs'] == proof
            and data['flags'] == FLAGS and data['configuration'] == 'debug' and data['historical_binary_reproduction'] is False
            and data['observed_versions'] == {n: p['version'] for n, p in proof['tools'].items()}, 'Legacy receipt inputs differ')
    guest = output / 'uae.wbx'
    require(data['guest'] == {'bytes': guest.stat().st_size, 'sha256': wb.sha256(guest)}, 'Legacy guest differs')
    wb.validate_elf(guest, 16384); wb.check_no_compiler_tls(guest)
    require(data['symbols'] == symbols(guest), 'Legacy symbol inventory differs')
    return data


def build(output=None, *, execute=False):
    output = output_path(output); require(not output.exists(), 'Output already exists; never overwrite a guest')
    paths, tools, sysroot, proof = inputs()
    if not execute: return {'status': 'NOT_RUN', 'output': str(output), 'inputs': proof, 'configuration': 'debug', 'flags': FLAGS}
    observed, versions = wb.locked_tools(wb.selected_toolchain()); require(observed == tools, 'Tool selection changed')
    loader = wb.hosted_environment(tools); output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.legacy-source-', dir=output.parent) as temporary:
        stage = Path(temporary); prepare(stage, paths)
        wrapper_hashes = wrappers(stage / 'toolchain', paths['waterbox_musl_darwin'], sysroot, tools, loader['shell_exports'])
        # Makefile += assignments can inherit arbitrary variables such as SOURCES_C.
        # Retain only tool lookup/verified loader state; flags, SDK and make knobs
        # come exclusively from the captured recipe and our explicit arguments.
        env = {k: v for k, v in loader['tool_env'].items() if k in ('PATH', 'DYLD_LIBRARY_PATH')}
        env.update(HOME=str(stage), TMPDIR=str(stage), TMP=str(stage), TEMP=str(stage), LC_ALL='C')
        for command in commands(stage, tools, sysroot):
            with tempfile.TemporaryFile(mode='w+', dir=stage) as log:
                result = subprocess.run(command, cwd=stage, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
                log.seek(0, os.SEEK_END); size = log.tell(); log.seek(max(0, size - 3000))
                require(result.returncode == 0, 'Legacy source compilation failed: ' + log.read())
        guest = stage / 'objects/uae/debug/uae.wbx'
        wb.validate_elf(guest, 16384); wb.check_no_compiler_tls(guest); imported = symbols(guest)
        audit_copy(stage, paths)
        require(inputs()[3] == proof and wb.locked_tools(wb.selected_toolchain()) == (tools, versions), 'Inputs changed during compilation')
        require(all(wb.sha256(stage / 'toolchain' / n) == h for n, h in wrapper_hashes.items()), 'Own wrappers changed')
        publish = stage / 'publish'; publish.mkdir(); shutil.copy2(guest, publish / 'uae.wbx')
        data = {'schema_version': 1, 'status': STATUS, 'configuration': 'debug', 'flags': FLAGS, 'inputs': proof,
                'observed_versions': versions, 'wrappers': wrapper_hashes, 'symbols': imported,
                'guest': {'bytes': guest.stat().st_size, 'sha256': wb.sha256(guest)}, 'historical_binary_reproduction': False}
        (publish / 'receipt.json').write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
        require(not output.exists() and not output.is_symlink(), 'Output appeared during compilation'); publish.rename(output)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path); parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        result = build(args.output, execute=args.execute); print(json.dumps(result, sort_keys=True)); return 3 if result['status'] == 'NOT_RUN' else 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, wb.BuildError) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr); return 2


if __name__ == '__main__': sys.exit(main())
