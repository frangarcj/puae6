#!/usr/bin/env python3
"""Diagnostic only: exact mini-guest snapshots, never an EmuHawk acceptance gate."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox as wb


def require(value, message):
    if not value: raise ValueError(message)


def decode(capture):
    require(isinstance(capture, dict) and set(capture) == {'data', 'pages', 'chunks'}, 'Capture schema differs')
    require(isinstance(capture['data'], str) and len(capture['data']) <= 12 << 20, 'Encoded snapshot bound')
    data = base64.b64decode(capture['data'], validate=True)
    pages = base64.b64decode(capture['pages'], validate=True)
    require(0 < len(data) <= 8 << 20 and 0 < len(pages) <= 1 << 20, 'Snapshot bound')
    chunks = capture['chunks']; position = 0; raw = []
    require(isinstance(chunks, list) and 0 < len(chunks) <= 32768, 'Callback count bound')
    for chunk in chunks:
        require(isinstance(chunk, list) and len(chunk) == 2, 'Callback schema differs')
        offset, size = chunk
        require(type(offset) is int and type(size) is int and offset == position and 0 < size <= len(data) - position, 'Invalid callback boundaries')
        raw.append(data[offset:offset + size]); position += size
    require(position == len(data) and len(raw) > 15, 'Truncated capture')
    regions = []; index = 0
    def take(size=None, magic=None, kind='header', guest=None, protection=None):
        nonlocal index
        require(index < len(raw), 'Truncated snapshot schema')
        part = raw[index]
        require((size is None or len(part) == size) and (magic is None or part == magic), 'Ambiguous/fragmented snapshot schema')
        start, length = chunks[index]; regions.append((start, start + length, kind, guest, protection)); index += 1
        return part
    take(magic=b'ActivatedWaterboxHost_v1'); take(magic=b'FileSystem', kind='filesystem')
    # Boundaries come from actual writes, never a byte search inside guest memory.
    ends = [i for i in range(index, len(raw)) if raw[i] == b'FileSystemEnd']
    require(len(ends) == 1, 'Ambiguous filesystem boundary')
    while index <= ends[0]: take(kind='filesystem')
    take(8); take(magic=b'ElfLoader'); take(32, kind='elf-hash')
    take(magic=b'ActivatedMemoryBlock'); take(32, kind='sealed-memory-hash')
    address = int.from_bytes(take(8), 'little'); size = int.from_bytes(take(8), 'little')
    require(address % 4096 == 0 and size == len(pages) * 4096, 'Page count/range differs')
    statuses = take(kind='page-status'); dirty = take(len(pages), kind='dirty-map')
    require(len(statuses) % len(pages) == 0 and 1 <= len(statuses) // len(pages) <= 8, 'Unknown status width')
    width = len(statuses) // len(pages); encodings = {}; inverse = {}
    for n, info in enumerate(pages):
        code = statuses[n * width:(n + 1) * width]; prot = info & 0x3f
        require(prot in (0, 0x20, 1, 3, 5, 7, 0x13) and dirty[n] in (0, 1) and dirty[n] == bool(info & 0x80), 'Incompatible page map')
        require(encodings.setdefault(code, prot) == prot and inverse.setdefault(prot, code) == code, 'Ambiguous Rust status encoding')
        if info & 0x80 and not info & 0x40:
            take(4096, kind='guest-page', guest=address + n * 4096, protection=prot)
    take(magic=b'GuestThreadSet', kind='threading')
    take(4, kind='threading'); take(4, kind='threading')
    threads = int.from_bytes(take(8, kind='threading'), 'little'); require(1 <= threads <= 64, 'Thread bound')
    for _ in range(threads):
        part = take(kind='threading'); require(24 <= len(part) <= 128, 'Unknown thread layout')
    futexes = int.from_bytes(take(8, kind='threading'), 'little'); require(futexes <= 1024, 'Futex bound')
    for _ in range(futexes):
        take(8, kind='threading'); waiters = int.from_bytes(take(8, kind='threading'), 'little'); require(waiters <= 64, 'Waiter bound')
        for _ in range(waiters): take(4, kind='threading')
    take(magic=b'GuestThreadSet', kind='threading'); take(magic='ʇsoHxoqɹǝʇɐMpǝʇɐʌᴉʇɔ∀'.encode())
    require(index == len(raw), 'Unparsed snapshot suffix')
    return data, regions


def compare(left, right, buffers):
    require(len(buffers) == 2 and all(type(n) is int and 0 < n < 2**63 for n in buffers) and buffers[0] != buffers[1], 'Invalid buffer identities')
    a, ar = decode(left); b, br = decode(right)
    require(len(a) == len(b) and ar == br, 'Snapshot structures differ; unresolved')
    ranges = []; pointers = []; position = 0
    for start, end, kind, guest, prot in ar:
        while position < end:
            if a[position] == b[position]: position += 1; continue
            first = position
            while position < end and a[position] != b[position]: position += 1
            ranges.append({'offset': first, 'bytes': position - first, 'section': kind,
                'guest': None if guest is None else guest + first - start, 'protection': prot})
        if kind == 'guest-page' and prot == 0x13:
            for at in range(start, end - 7, 8):
                x, y = struct.unpack_from('<Q', a, at)[0], struct.unpack_from('<Q', b, at)[0]
                if buffers[0] <= x < buffers[0] + 64 and y - buffers[1] == x - buffers[0]:
                    pointers.append({'guest': guest + at - start, 'offset': at, 'buffer_offset': x - buffers[0]})
    require(len(ranges) <= 4096, 'Diagnostic range bound exceeded; unresolved')
    return {'equal': a == b, 'ranges': ranges, 'identified_buffer_spills': pointers,
        'left_sha256': hashlib.sha256(a).hexdigest(), 'right_sha256': hashlib.sha256(b).hexdigest()}


def inputs():
    receipt_path = wb.runtime_receipt_path('release'); receipt = wb.read_runtime_receipt(receipt_path)
    require(receipt['runtime_profile'] == 'release', 'Release runtime required')
    paths = {'runtime': Path(receipt['runtime_dylib']['path']), 'guest': Path(receipt['guest']['path'])}
    require(paths == {'runtime': wb.BUILD / 'cargo/aarch64-apple-darwin/release/libwaterboxhost.dylib', 'guest': wb.BUILD / 'guest/release/p6-smoke.wbx'}, 'Unexpected artifact paths')
    for name, field in (('runtime', 'runtime_dylib'), ('guest', 'guest')):
        require(paths[name].resolve() == paths[name] and not paths[name].is_symlink(), 'Linked artifact')
        require(wb.sha256(paths[name]) == receipt[field]['sha256'], 'Artifact receipt differs')
    wb.validate_elf(paths['guest'], 16384)
    for tool in wb.selected_toolchain()['tools'].values():
        require(wb.sha256(Path(tool['path'])) == tool['sha256'], 'Pinned compiler/tool differs')
    header = paths['runtime'].read_bytes()[:32]
    require(len(header) == 32 and struct.unpack_from('<II', header) == (0xfeedfacf, 0x100000c), 'Runtime ISA differs')
    for name, path in {'guest_cpp': ROOT / 'tests/waterbox-smoke/guest.cpp', 'smoke_makefile': ROOT / 'tests/waterbox-smoke/Makefile',
        'common_mak': ROOT / 'references/machawk/waterbox/common.mak', 'linkscript_aarch64': ROOT / 'references/machawk/waterbox/linkscript_aarch64.T',
        'emulibc_source': ROOT / 'references/machawk/waterbox/emulibc/emulibc.c', 'build_recipe': ROOT / 'tools/build_waterbox.py', 'toolchains_json': ROOT / 'plan/toolchains.json'}.items():
        require(wb.sha256(path) == receipt['input_sha256'][name], 'Smoke recipe/source differs: ' + name)
    sources = [ROOT / p for p in ('integration/bizhawk/smoke/Native.cs', 'tests/waterbox-smoke/StateProbe.cs', 'integration/bizhawk/smoke/global.json', 'tools/waterbox_state_probe.py', 'Directory.Build.targets')]
    require(json.loads(sources[2].read_text()) == {'sdk': {'version': '10.0.402', 'rollForward': 'disable'}}, 'SDK pin differs')
    proof = {str(p): wb.sha256(p) for p in [receipt_path, *paths.values(), *sources]}
    dotnet = Path(shutil.which('dotnet') or '/missing-dotnet').resolve(); proof[str(dotnet)] = wb.sha256(dotnet)
    return paths, proof, dotnet


def run():
    paths, before, dotnet = inputs()
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'Darwin ARM64 is required')
    with tempfile.TemporaryDirectory(prefix='waterbox-state-probe-', dir=ROOT / 'build') as tmp:
        stage = Path(tmp); environment = {k: v for k, v in os.environ.items() if not k.startswith(('DOTNET_', 'COMPlus_', 'COREHOST_'))}
        environment.update(P6_EVICT='1', DOTNET_CLI_HOME=str(stage), HOME=str(stage), TMPDIR=str(stage), TMP=str(stage), TEMP=str(stage),
            NUGET_PACKAGES=str(stage / 'packages'), DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='true',
            DOTNET_ROOT=str(dotnet.parent), DOTNET_MULTILEVEL_LOOKUP='0')
        project = stage / 'Probe.csproj'; (stage / 'empty-feed').mkdir()
        shutil.copyfile(ROOT / 'integration/bizhawk/smoke/global.json', stage / 'global.json')
        project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><AllowUnsafeBlocks>true</AllowUnsafeBlocks><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><UseAppHost>false</UseAppHost><EnableDefaultCompileItems>false</EnableDefaultCompileItems><NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>' + ''.join('<Compile Include="' + str(ROOT / p) + '" />' for p in ('integration/bizhawk/smoke/Native.cs', 'tests/waterbox-smoke/StateProbe.cs')) + '</ItemGroup></Project>')
        def execute(args, limit):
            result = subprocess.run([str(dotnet), *args], cwd=stage, env=environment, capture_output=True, text=True, timeout=120)
            require(result.returncode == 0 and len(result.stdout) + len(result.stderr) <= limit, 'Diagnostic child failed: ' + result.stderr[-1500:])
            return result.stdout
        execute(['restore', str(project), '--source', str(stage / 'empty-feed')], 1 << 20)
        execute(['build', str(project), '--no-restore', '--nologo', '-o', str(stage / 'out')], 1 << 20)
        (stage / 'out/Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'net10.0', 'rollForward': 'Disable', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '10.0.13'}}}))
        output = execute([str(stage / 'out/Probe.dll'), str(paths['runtime']), str(paths['guest'])], 32 << 20)
        lines = [line[len('P6_STATE_PROBE '):] for line in output.splitlines() if line.startswith('P6_STATE_PROBE ')]
        require(len(lines) == 1 and 'VIOLATION' not in output, 'Missing/duplicate diagnostic transport')
        record = json.loads(lines[0]); require(set(record) == {'eviction', 'runtime', 'frames', 'buffers', 'captures'} and record['eviction'] is True
            and record['runtime'] == '10.0.13' and type(record['frames']) is int and record['frames'] == 3, 'Diagnostic context differs')
        require(isinstance(record['buffers'], list) and len(record['buffers']) == 2 and all(type(n) is int and 0 < n < 2**63 for n in record['buffers'])
            and record['buffers'][0] != record['buffers'][1], 'Invalid real buffer identities')
        captures = record['captures']; require(set(captures) == {n + str(i) for n in ('initialA', 'initialB', 'afterA', 'afterB', 'interventionA') for i in (0, 1)}, 'Capture coverage differs')
        pairs = [('initialA0', 'initialA1'), ('initialB0', 'initialB1'), ('initialA0', 'initialB0'), ('afterA0', 'afterA1'), ('afterB0', 'afterB1'), ('afterA0', 'afterB0'), ('interventionA0', 'interventionA1'), ('afterA0', 'interventionA0')]
        observations = {a + ':' + b: compare(captures[a], captures[b], record['buffers']) for a, b in pairs}
        require(inputs()[1] == before, 'Inputs changed during diagnostic')
        spills = observations['afterA0:interventionA0']['identified_buffer_spills']
        return {'scope': 'mini-guest-only', 'eviction': True, 'inputs': before, 'observations': observations,
            'conclusion': 'host-buffer-spill-proven' if spills else 'unresolved' if any(not o['equal'] for o in observations.values()) else 'no-divergence-in-this-path'}


if __name__ == '__main__':
    try: print(json.dumps(run(), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error: sys.exit('BLOCKED: ' + str(error))
