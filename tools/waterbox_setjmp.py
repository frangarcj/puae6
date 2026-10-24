#!/usr/bin/env python3
"""An isolated source-overlay ABI oracle; never replace a product/sysroot/runtime."""
import hashlib
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

ROOT = base.ROOT
PATCH = 'integration/waterbox-guest/0001-setjmp-x28.patch'
ASM = ('setjmp.s', 'longjmp.s')


def overlay(source, destination, patched):
    directory = destination / 'src/setjmp/waterbox_aarch64'; directory.mkdir(parents=True)
    for name in ASM: shutil.copyfile(source / name, directory / name)
    if patched:
        for arguments in ([], ['--reverse', '--dry-run']):
            result = subprocess.run(['patch', '-p1', '--fuzz=0', '--no-backup-if-mismatch', *arguments, '-i', str(ROOT / PATCH)], cwd=destination, capture_output=True, timeout=10)
            base.require(result.returncode == 0, 'Atomic setjmp overlay rejected: ' + result.stderr.decode()[-1000:])
    return directory


def validate(records):
    base.require(isinstance(records, list) and len(records) == 3, 'Missing setjmp process coverage')
    original, fixed, restored = records
    base.require(original == {'status': 'EXPECTED_ORIGINAL_REJECTION', 'jumps': 0} and type(original['jumps']) is int, 'Original did not reject before longjmp')
    for record, status, jumps in ((fixed, 'FIXED', 10), (restored, 'RESTORED', 2)):
        base.require(set(record) == {'status', 'jumps', 'baseline', 'future'} and record['status'] == status and type(record['jumps']) is int and record['jumps'] == jumps
            and all(isinstance(record[k], str) and re.fullmatch('[0-9a-f]{64}', record[k]) for k in ('baseline', 'future')), 'Incomplete fixed setjmp/restore coverage')
    base.require(fixed['baseline'] == restored['baseline'] and fixed['future'] == restored['future'], 'Fresh-process full snapshot differs')
    base.require(fixed['baseline'] != fixed['future'], 'Jump sequence must change captured state')
    return {'scope': 'waterbox-setjmp-oracle-only', 'original_rejected_before_longjmp': True, 'values': [0, 7], 'valid_jumps': 12,
        'two_sandboxes': True, 'fresh_process': True, 'baseline_sha256': fixed['baseline'], 'future_sha256': fixed['future']}


def inputs():
    paths, proof, dotnet = base.inputs()
    base.wb.source_spec('waterbox_musl_darwin', 'snapshot')
    sources = [PATCH, 'tools/waterbox_setjmp.py', 'tests/waterbox-smoke/setjmp_probe.c', 'tests/waterbox-smoke/SetjmpProbe.cs',
        'references/musl-darwin/arch/waterbox_aarch64/bits/setjmp.h', *('references/musl-darwin/src/setjmp/waterbox_aarch64/' + name for name in ASM)]
    return paths, {**proof, **{str(ROOT / name): base.wb.sha256(ROOT / name) for name in sources}}, dotnet


def run():
    paths, proof, dotnet = inputs(); base.require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'Darwin ARM64 required')
    lock = base.wb.selected_toolchain(); tools = base.wb.selected_tool_paths(lock)
    environment = dict(base.wb.hosted_environment(tools)['tool_env'])
    with tempfile.TemporaryDirectory(prefix='waterbox-setjmp-', dir=ROOT / 'build') as temporary:
        stage = Path(temporary)
        environment = {k: v for k, v in environment.items() if not k.startswith(('DOTNET_', 'COMPlus_', 'COREHOST_'))}
        environment.update(P6_EVICT='1', HOME=str(stage), TMPDIR=str(stage), TMP=str(stage), TEMP=str(stage), DOTNET_ROOT=str(dotnet.parent),
            DOTNET_MULTILEVEL_LOOKUP='0', DOTNET_CLI_HOME=str(stage), NUGET_PACKAGES=str(stage / 'packages'), DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='true')
        def execute(command, expected=0):
            result = subprocess.run([str(x) for x in command], cwd=stage, env=environment, capture_output=True, text=True, timeout=120)
            base.require(result.returncode == expected and len(result.stdout)+len(result.stderr) <= 1 << 20 and 'VIOLATION' not in result.stdout+result.stderr,
                'Setjmp child rejected: ' + result.stdout[-1000:] + result.stderr[-1000:])
            return result.stdout
        guests = []
        for variant in ('original', 'fixed'):
            tree = stage / variant
            overlay(ROOT / 'references/musl-darwin/src/setjmp/waterbox_aarch64', tree, variant == 'fixed')
            shutil.copyfile(ROOT / 'tests/waterbox-smoke/guest.cpp', tree / 'guest.cpp'); shutil.copyfile(ROOT / 'tests/waterbox-smoke/setjmp_probe.c', tree / 'setjmp_probe.c')
            makefile = tree / 'Makefile'
            makefile.write_text('TARGET := p6-setjmp.wbx\nSRCS := guest.cpp setjmp_probe.c src/setjmp/waterbox_aarch64/setjmp.s src/setjmp/waterbox_aarch64/longjmp.s\nWBX_TARGET_ARCH := aarch64\nWBX_HOST_PAGE_SIZE := 16384\nOUT_DIR := '+str(tree / 'out')+'\noverride EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o\ninclude '+str(ROOT / 'references/machawk/waterbox/common.mak')+'\n$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c\n\t@mkdir -p $(@D)\n\t$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)\n')
            execute([tools['make'], '-C', tree, 'release', 'SYSROOT='+str(ROOT / 'build/waterbox/sysroot'), 'CC='+str(ROOT / 'build/waterbox/toolchain/bin/musl-clang')])
            guest = tree / 'out/release/p6-setjmp.wbx'; base.wb.validate_elf(guest, 16384); base.wb.check_no_compiler_tls(guest); guests.append(guest)
        shutil.copyfile(ROOT / 'integration/bizhawk/smoke/global.json', stage / 'global.json'); (stage / 'empty-feed').mkdir()
        project = stage / 'Probe.csproj'
        project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><AllowUnsafeBlocks>true</AllowUnsafeBlocks><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><UseAppHost>false</UseAppHost><EnableDefaultCompileItems>false</EnableDefaultCompileItems><NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>'+''.join('<Compile Include="'+str(ROOT / name)+'" />' for name in ('integration/bizhawk/smoke/Native.cs', 'tests/waterbox-smoke/SetjmpProbe.cs'))+'</ItemGroup></Project>')
        execute([dotnet, 'restore', project, '--source', stage / 'empty-feed']); execute([dotnet, 'build', project, '--no-restore', '--nologo', '-o', stage / 'out'])
        (stage / 'out/Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'net10.0', 'rollForward': 'Disable', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '10.0.13'}}}))
        own = {str(path): base.wb.sha256(path) for path in [*guests, *sorted((stage / 'out').glob('Probe.*'))] if path.is_file()}
        records = []; snapshot = stage / 'state.bin'
        for mode, guest, code in (('original', guests[0], 28), ('fixed', guests[1], 0), ('restore', guests[1], 0)):
            stdout = execute([dotnet, stage / 'out/Probe.dll', mode, paths['runtime'], guest, snapshot], code)
            lines = [line[len('P6_SETJMP '):] for line in stdout.splitlines() if line.startswith('P6_SETJMP ')]
            base.require(len(lines) == 1, 'Missing/duplicate setjmp result'); records.append(json.loads(lines[0]))
        result = validate(records); base.require(inputs()[1] == proof and all(base.wb.sha256(Path(path)) == digest for path, digest in own.items()), 'Setjmp inputs changed')
        return {**result, 'inputs': proof, 'guests': {name: base.wb.sha256(guest) for name, guest in zip(('original', 'fixed'), guests)}}


if __name__ == '__main__':
    try: print(json.dumps(run(), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error: sys.exit('BLOCKED: ' + str(error))
