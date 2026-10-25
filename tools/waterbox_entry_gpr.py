#!/usr/bin/env python3
"""Isolated entry-register oracle. Never replace the product runtime or receipts."""
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import waterbox_state_probe as base
from tools.materialize_upstream import _apply

ROOT = base.ROOT
PATCH = ROOT / 'integration/waterbox-runtime/proposed/0003-darwin-entry-gpr.patch'
ASM = 'src/context/interop_aarch64.s'
BIN = 'src/context/interop_aarch64.bin'


def execute(command, *, cwd, env=None, expected=0):
    result = subprocess.run([str(x) for x in command], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    base.require(result.returncode == expected and len(result.stdout) + len(result.stderr) <= 1 << 20,
                 'Entry GPR command failed: ' + result.stdout[-1500:] + result.stderr[-2000:])
    return result.stdout


def canonical_entry(text):
    anchor = '    mov     x28, x10\n\n    blr     x11'
    base.require(text.count(anchor) == 1, 'Unexpected entry ASM')
    moves = ''.join('    mov     x%d, xzr\n' % n for n in [*range(19, 28), 29])
    return text.replace(anchor, '    mov     x28, x10\n\n'
        '    // Host non-argumental values must not enter guest setjmp/state.\n' + moves + '\n    blr     x11')


def assemble(source, destination, tools, stage):
    obj = stage / 'interop.o'
    execute([tools['clang'], '--target=aarch64-linux-gnu', '-c', source, '-o', obj], cwd=stage)
    execute([tools['lld'], '-Ttext=0', '--image-base=0', '--oformat=binary', '--entry=call_guest_simple', obj, '-o', destination], cwd=stage)
    base.require(destination.stat().st_size == 0x800, 'Interop offsets/size changed')


def prepare():
    base.inputs(); tools = base.wb.selected_tool_paths(base.wb.selected_toolchain())
    source = base.wb.BUILD / 'runtime-src'
    with tempfile.TemporaryDirectory(prefix='entry-gpr-prepare-', dir=ROOT / 'build') as tmp:
        stage = Path(tmp)
        before = stage / 'before'; after = stage / 'after'
        for folder in (before, after): (folder / 'src/context').mkdir(parents=True)
        for name in (ASM, BIN): shutil.copy2(source / name, before / name)
        rebuilt = stage / 'baseline.bin'; assemble(before / ASM, rebuilt, tools, stage)
        base.require(rebuilt.read_bytes() == (source / BIN).read_bytes(), 'ASM recipe does not reproduce baseline interop')
        text = (before / ASM).read_text(); (after / ASM).write_text(canonical_entry(text))
        assemble(after / ASM, after / BIN, tools, stage); shutil.copymode(before / BIN, after / BIN)
        delta = subprocess.run(['git', 'diff', '--no-index', '--binary', '--', 'before/'+BIN, 'after/'+BIN],
                               cwd=stage, capture_output=True, text=True, timeout=10)
        base.require(delta.returncode == 1 and 'GIT binary patch' in delta.stdout, 'Missing generated binary patch')
        binary = delta.stdout.replace('a/before/', 'a/').replace('b/after/', 'b/')
        patch = ''.join(difflib.unified_diff(text.splitlines(True), (after / ASM).read_text().splitlines(True),
                                           fromfile='a/'+ASM, tofile='b/'+ASM)) + binary
        base.require(not PATCH.exists(), 'Do not overwrite an existing proposal')
        PATCH.write_text(patch)
    return {'proposal': str(PATCH.relative_to(ROOT)), 'sha256': base.wb.sha256(PATCH)}


def validate(records):
    base.require(len(records) == 3 and records[0] == {'mode': 'original', 'contaminated': True}, 'Missing original negative control')
    fixed, restored = records[1:]
    base.require(set(fixed) == {'mode', 'baseline', 'future', 'callbacks'} and fixed['mode'] == 'fixed'
                 and type(fixed['callbacks']) is int and fixed['callbacks'] == 2
                 and set(restored) == {'mode', 'baseline', 'future'} and restored['mode'] == 'restore', 'Incomplete fixed coverage')
    for key in ('baseline', 'future'):
        base.require(isinstance(fixed[key], str) and re.fullmatch('[0-9a-f]{64}', fixed[key])
                     and fixed[key] == restored[key], 'Fresh-process state differs')
    base.require(fixed['baseline'] != fixed['future'], 'Restore must change observable guest state')
    return {'scope': 'entry-gpr-oracle-only', 'host_seeds': 2, 'guest_gpr_canonical': True,
            'host_gpr_preserved': True, 'callbacks_reentrant': 2, 'fresh_process': True,
            'baseline': fixed['baseline'], 'future': fixed['future']}


def run():
    paths, proof, dotnet = base.inputs(); tools = base.wb.selected_tool_paths(base.wb.selected_toolchain())
    owned = [PATCH, Path(__file__), *(ROOT / ('tests/waterbox-smoke/'+n) for n in ('entry_gpr.S','EntryGprProbe.cs'))]
    before = {str(p): base.wb.sha256(p) for p in owned}
    tc = base.wb.toolchain()
    with tempfile.TemporaryDirectory(prefix='entry-gpr-oracle-', dir=ROOT / 'build') as tmp:
        stage = Path(tmp); candidate = stage / 'runtime'
        shutil.copytree(tc['runtime_copy'], candidate, symlinks=True)
        _apply(PATCH.read_bytes(), candidate, check=True); _apply(PATCH.read_bytes(), candidate, check=False)
        env = {**tc['env'], 'CARGO_TARGET_DIR': str(stage / 'cargo')}
        execute([tools['cargo'], 'build', '--offline', '--locked', '--release', '-j2', '--target',
                 tc['lock']['runtime']['target'], '--manifest-path', candidate / 'Cargo.toml'], cwd=stage, env=env)
        runtime = stage / 'cargo/aarch64-apple-darwin/release/libwaterboxhost.dylib'
        guest = stage / 'guest'; guest.mkdir()
        for name in ('guest.cpp', 'entry_gpr.S'): shutil.copyfile(ROOT / 'tests/waterbox-smoke' / name, guest / name)
        (guest / 'Makefile').write_text('TARGET := entry-gpr.wbx\nSRCS := guest.cpp entry_gpr.S\n'
            'WBX_TARGET_ARCH := aarch64\nWBX_HOST_PAGE_SIZE := 16384\nOUT_DIR := '+str(guest/'out')+'\n'
            'override EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o\ninclude '+str(ROOT/'references/machawk/waterbox/common.mak')+'\n'
            '$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c\n\t@mkdir -p $(@D)\n\t$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)\n')
        execute([tools['make'], '-C', guest, 'release', 'SYSROOT='+str(tc['sysroot_copy']),
                 'CC='+str(tc['cc_wrapper'])], cwd=stage, env=env)
        image = guest / 'out/release/entry-gpr.wbx'; base.wb.validate_elf(image,16384); base.wb.check_no_compiler_tls(image)
        shim = stage / 'libentry_gpr_host.dylib'
        execute([tools['clang'], '-dynamiclib', '-DHOST_SHIM', ROOT/'tests/waterbox-smoke/entry_gpr.S',
                 '-o', shim], cwd=stage, env=env)
        shutil.copyfile(ROOT/'integration/bizhawk/smoke/global.json', stage/'global.json'); (stage/'empty-feed').mkdir()
        (stage/'Probe.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
            '<TargetFramework>net10.0</TargetFramework><AllowUnsafeBlocks>true</AllowUnsafeBlocks><ImplicitUsings>enable</ImplicitUsings>'
            '<Nullable>enable</Nullable><UseAppHost>false</UseAppHost><EnableDefaultCompileItems>false</EnableDefaultCompileItems>'
            '<NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>'+''.join('<Compile Include="'+str(ROOT/n)+'" />'
            for n in ('integration/bizhawk/smoke/Native.cs','tests/waterbox-smoke/EntryGprProbe.cs'))+'</ItemGroup></Project>')
        env.update(P6_EVICT='1', DOTNET_ROOT=str(dotnet.parent), DOTNET_MULTILEVEL_LOOKUP='0', DOTNET_CLI_HOME=str(stage),
                   NUGET_PACKAGES=str(stage/'packages'), DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='true', HOME=str(stage), TMPDIR=str(stage))
        execute([dotnet,'restore',stage/'Probe.csproj','--source',stage/'empty-feed'],cwd=stage,env=env)
        execute([dotnet,'build',stage/'Probe.csproj','--no-restore','--nologo','-o',stage/'out'],cwd=stage,env=env)
        state = stage/'state.bin'; records=[]
        for mode, lib in [('original', paths['runtime']),('fixed', runtime),('restore', runtime)]:
            stdout=execute([dotnet,stage/'out/Probe.dll',mode,lib,image,shim,state],cwd=stage,env=env)
            rows=[line.removeprefix('P6_ENTRY_GPR ') for line in stdout.splitlines() if line.startswith('P6_ENTRY_GPR ')]
            base.require(len(rows)==1,'Missing/duplicate oracle result'); records.append(json.loads(rows[0]))
        result=validate(records)
        base.require(base.inputs()[1]==proof and all(base.wb.sha256(Path(p))==digest for p,digest in before.items()),'Oracle input changed')
        return {**result,'candidate_runtime_sha256':base.wb.sha256(runtime),'guest_sha256':base.wb.sha256(image)}


if __name__ == '__main__':
    try: print(json.dumps(prepare() if sys.argv[1:] == ['prepare'] else run(), sort_keys=True))
    except (OSError, ValueError, base.wb.BuildError, subprocess.TimeoutExpired) as error: sys.exit('BLOCKED: '+str(error))
