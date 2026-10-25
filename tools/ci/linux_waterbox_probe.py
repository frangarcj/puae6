#!/usr/bin/env python3
"""Inventory, prepare, or run the pinned Linux WB01 mini-core; no global installs."""
import argparse
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import struct
import tempfile
import shutil
import stat
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import import_sources as sources

MARKER = 'P6_LINUX_WATERBOX_INVENTORY '
TARGET = 'x86_64-unknown-linux-gnu'
TOOLS = {name: (name,) for name in ('rustc', 'cargo', 'make', 'git', 'patch', 'ar', 'ranlib', 'ld', 'objcopy', 'gcc', 'python3', 'nasm')}
TOOLS.update({name: (name, name + '-18', name + '-19') for name in ('clang', 'ld.lld', 'llvm-ar', 'llvm-objcopy')})
REQUIRED = ('rustc', 'cargo', 'clang', 'make', 'git', 'patch', 'ar', 'ranlib', 'ld')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def command(argv):
    result = subprocess.run(list(map(str, argv)), cwd=ROOT, text=True, capture_output=True, timeout=30,
                            env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode or len(result.stdout) > 100000:
        raise ValueError(f'inventory command failed: {argv[0]} ({result.returncode}) {result.stderr[-300:]}')
    return result.stdout.strip()


def file_identity(path):
    path = Path(path)
    real = path.resolve(strict=True)
    if not stat.S_ISREG(real.stat().st_mode):
        raise ValueError(f'not a regular tool/input: {path}')
    return {'path': str(path), 'resolved_path': str(real), 'bytes': real.stat().st_size, 'sha256': sha(real)}


def tool_identity(path):
    result = file_identity(path)
    lines = command([path, *(['-flavor', 'gnu'] if Path(path).name == 'lld' else []), '--version']).splitlines()
    if not lines:
        raise ValueError('empty tool version')
    result['version'] = lines[0]
    return result


def rust_identity(launcher):
    sysroot = Path(command([launcher['path'], '--print', 'sysroot']))
    if not sysroot.is_absolute() or not sysroot.is_dir():
        raise ValueError('invalid Rust sysroot')
    libraries = sysroot / 'lib/rustlib' / TARGET / 'lib'
    if not libraries.is_dir() or libraries.is_symlink():
        raise ValueError('missing installed Linux x86_64 Rust standard library')
    inventory = sources.inventory(libraries)
    if not any(item['path'].startswith('libstd-') for item in inventory):
        raise ValueError('missing Linux Rust libstd')
    return {'sysroot': str(sysroot), 'target': TARGET, 'rustc': tool_identity(sysroot / 'bin/rustc'),
            'cargo': tool_identity(sysroot / 'bin/cargo'), 'stdlib_files': len(inventory),
            'stdlib_inventory_sha256': canonical(inventory)}


def source_identity(root):
    result = {}
    for sid in ('machawk', 'waterbox_musl_darwin'):
        captured = sources.verify_source(root, sid)
        spec = captured['source']
        result[sid] = {'commit': spec['commit'], 'manifest_sha256': sha(root / spec['snapshot']['manifest']),
                       'inventory_sha256': canonical(captured['files'])}
    return result


def cargo_cache(root, home=None):
    lock = root / 'references/machawk/waterbox/waterboxhost/Cargo.lock'
    data = tomllib.loads(lock.read_text())
    home = Path(home or os.environ.get('CARGO_HOME', Path.home() / '.cargo'))
    found = []; missing = []
    for package in data['package']:
        if not package.get('source', '').startswith('registry+'):
            continue
        name = f"{package['name']}-{package['version']}.crate"
        candidates = list((home / 'registry/cache').glob('*/' + name))
        valid = len(candidates) == 1 and candidates[0].is_file() and not candidates[0].is_symlink() and sha(candidates[0]) == package['checksum']
        (found if valid else missing).append(name)
    return {'lock_sha256': sha(lock), 'authenticated_archives': sorted(found), 'missing_archives': sorted(missing),
            'offline_ready': not missing}


def inventory(root=ROOT):
    host = {'os': platform.system(), 'isa': platform.machine(), 'page_bytes': os.sysconf('SC_PAGE_SIZE')}
    if host != {'os': 'Linux', 'isa': 'x86_64', 'page_bytes': 4096}:
        raise ValueError('requires Linux x86_64 with 4096-byte host pages')
    container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
    runner = {k: os.environ.get(k) for k in ('ImageOS', 'ImageVersion', 'RUNNER_OS', 'RUNNER_ARCH', 'RUNNER_ENVIRONMENT', 'GITHUB_ACTIONS')}
    tools = {}; missing = []
    for name, candidates in TOOLS.items():
        path = next((p for candidate in candidates if (p := shutil.which(candidate))), None)
        if path:
            tools[name] = tool_identity(path)
        else:
            missing.append(name)
    errors = []
    if container or runner['GITHUB_ACTIONS'] != 'true' or runner['RUNNER_ENVIRONMENT'] != 'github-hosted' or runner['RUNNER_ARCH'] != 'X64':
        errors.append('requires a GitHub-hosted native x86_64 runner; translated/container observations are not WB01')
    errors += ['missing required preinstalled tool: ' + name for name in REQUIRED if name in missing]
    rust = None
    if 'rustc' in tools:
        try:
            rust = rust_identity(tools['rustc'])
        except (ValueError, OSError) as error:
            errors.append(str(error))
    result = {'schema_version': 1, 'status': 'BLOCKED' if errors else 'INVENTORIED', 'scope': 'preinstalled inputs only; WB01 not executed',
              'host': host, 'runner': runner, 'container_detected': container, 'tools': tools, 'missing_tools': missing, 'rust': rust,
              'sources': source_identity(root), 'cargo_cache': cargo_cache(root), 'errors': errors,
              'recipe_sha256': sha(Path(__file__)), 'upstream_lock_sha256': sha(root / 'plan/upstream.lock.json')}
    if root == ROOT:
        result['source_revision'] = command(['git', 'rev-parse', 'HEAD'])
    return result


MUSL_FLAGS = '-Werror -fvisibility=hidden -mcmodel=large -mstack-protector-guard=global -fno-pic -fno-pie -fcf-protection=none'
OUTPUT = 'build/linux-waterbox-wb01'


def pinned_inputs(root=ROOT):
    authority = json.loads((root / 'plan/extensions/waterbox-platforms.json').read_text())['linux_wb01_inputs']
    expected = authority['runner']
    actual = dict(os=platform.system(), isa=platform.machine(), page_bytes=os.sysconf('SC_PAGE_SIZE'),
                  image_os=os.environ.get('ImageOS'), image_version=os.environ.get('ImageVersion'))
    if actual != expected or Path('/.dockerenv').exists() or Path('/run/.containerenv').exists() or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or os.environ.get('RUNNER_ARCH') != 'X64':
        raise ValueError('pinned native Linux runner identity differs')
    standard = authority.get('rust_stdlib_inventory_sha256')
    if not isinstance(standard, str) or not re.fullmatch('[0-9a-f]{64}', standard):
        raise ValueError('Rust standard library inventory is not pinned; inventory only')
    if set(authority['tools']) != {'clang', 'lld', 'ar', 'make', 'patch', 'rustc', 'cargo'}:
        raise ValueError('incomplete Linux tool authority')
    for name, pin in authority['tools'].items():
        if set(pin) != {'path', 'version', 'sha256'} or not Path(pin['path']).is_absolute() or not re.fullmatch('[0-9a-f]{64}', pin['sha256']):
            raise ValueError('invalid pinned Linux tool: ' + name)
        if file_identity(pin['path'])['sha256'] != pin['sha256']:
            raise ValueError('pinned tool bytes differ: ' + name)
        observed = tool_identity(pin['path'])
        if observed['sha256'] != pin['sha256'] or observed['version'] != pin['version']:
            raise ValueError('pinned tool bytes/version differ: ' + name)
    rust = rust_identity(authority['tools']['rustc'])
    if rust['stdlib_inventory_sha256'] != standard:
        raise ValueError('Rust standard library inventory differs')
    return {'authority': authority, 'sources': source_identity(root), 'recipe_sha256': sha(Path(__file__)),
            'rust': rust, 'rustc_bootstrap': '1', 'target': TARGET, 'musl_flags': MUSL_FLAGS}


def elf(path, kind):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
    if len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01' or struct.unpack_from('<H', header, 18)[0] != 62 or struct.unpack_from('<H', header, 16)[0] not in (kind if isinstance(kind, tuple) else (kind,)):
        raise ValueError('wrong Linux x86_64 ELF: ' + str(path))


def verify(output=None, root=ROOT):
    output = Path(output or root / OUTPUT).absolute()
    sources.no_links(root, output)
    if output.resolve() != output or not output.is_relative_to(root / 'build') or set(p.name for p in output.iterdir()) != {'runtime.so', 'sysroot', 'receipt.json'}:
        raise ValueError('foreign preparation output')
    for name in ('runtime.so', 'receipt.json'):
        sources.no_links(output, output / name)
        if not (output / name).is_file() or (output / name).stat().st_nlink != 1:
            raise ValueError('preparation output is not regular')
    receipt = json.loads((output / 'receipt.json').read_text())
    if receipt.get('schema_version') != 1 or receipt['inputs'] != pinned_inputs(root) or receipt['status'] != 'PREPARED' or receipt['scope'] != 'runtime_and_musl_only':
        raise ValueError('stale preparation inputs')
    elf(output / 'runtime.so', 3)
    if sha(output / 'runtime.so') != receipt['runtime_sha256'] or canonical(sources.inventory(output / 'sysroot')) != receipt['sysroot_inventory_sha256']:
        raise ValueError('prepared output bytes differ')
    return {**receipt, 'output': str(output)}


def execute(argv, cwd, env):
    completed = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=sys.stderr, stderr=sys.stderr, timeout=1800)
    if completed.returncode:
        raise ValueError(f'preparation command failed ({completed.returncode}): {argv[0]}')


def prepare(root=ROOT):
    before = pinned_inputs(root)
    if fcntl is None:
        raise ValueError('preparation requires POSIX file locking')
    sources.no_links(root, root / 'build')
    (root / 'build').mkdir(exist_ok=True)
    lock = root / 'build/.linux-waterbox-wb01.lock'
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'r+') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError('invalid preparation lock')
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _prepare(root, before)


def _prepare(root, before):
    output = root / OUTPUT
    sources.no_links(root, output)
    if output.exists():
        return verify(output, root)
    build = root / 'build'; build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linux-wb01-', dir=build) as tmp:
        stage = Path(tmp); publish = stage / 'publish'; publish.mkdir()
        tools = {name: spec['path'] for name, spec in before['authority']['tools'].items()}
        bindir = stage / 'bin'; bindir.mkdir()
        # LLD's real filename is generic 'lld'; explicitly select the GNU driver.
        path = bindir / 'ld.lld'
        path.write_text('#!/bin/sh\nexec ' + shlex.join([tools['lld'], '-flavor', 'gnu']) + ' "$@"\n'); path.chmod(0o755)
        for name in ('home', 'tmp'):
            (stage / name).mkdir()
        env = {'PATH': str(bindir) + ':/usr/bin:/bin', 'HOME': str(stage / 'home'), 'TMPDIR': str(stage / 'tmp'), 'LANG': 'C.UTF-8',
               'CARGO_HOME': str(stage / 'cargo-home'), 'CARGO_TARGET_DIR': str(stage / 'cargo'), 'RUSTC': tools['rustc'], 'RUSTC_BOOTSTRAP': '1',
               'RUSTFLAGS': '-C linker=' + tools['clang'] + ' -C link-arg=-fuse-ld=' + str(bindir / 'ld.lld'), 'CC': tools['clang'], 'AR': tools['ar'], 'RANLIB': tools['ar'] + ' s',
               'CFLAGS': MUSL_FLAGS, 'LDFLAGS': '-fuse-ld=' + str(bindir / 'ld.lld')}
        runtime = stage / 'runtime'
        shutil.copytree(root / 'references/machawk/waterbox/waterboxhost', runtime, symlinks=True)
        execute([tools['cargo'], 'fetch', '--locked'], runtime, env)
        packages = cargo_cache(root, stage / 'cargo-home')
        if not packages['offline_ready']:
            raise ValueError('Cargo archive checksums/closure differ from Cargo.lock')
        execute([tools['cargo'], 'build', '--offline', '--locked', '--release', '--target', TARGET, '-j2'], runtime, env)
        shutil.copyfile(stage / 'cargo' / TARGET / 'release/libwaterboxhost.so', publish / 'runtime.so')
        elf(publish / 'runtime.so', 3)
        musl = stage / 'musl'
        shutil.copytree(root / 'references/musl-darwin', musl, symlinks=True)
        header = musl / 'arch/waterbox/atomic_arch.h'; original = header.read_bytes()
        if original.count(b'\t*p++;') != 1 or original.count(b'\t*p--;') != 1:
            raise ValueError('unexpected original musl atomics')
        expected = original.replace(b'\t*p++;', b'\t(*p)++;').replace(b'\t*p--;', b'\t(*p)--;')
        patch = root / 'references/machawk/waterbox/musl-waterbox-x86_64-atomics.patch'
        execute([tools['patch'], '--batch', '--fuzz=0', '-p1', '-i', patch], musl, env)
        if header.read_bytes() != expected:
            raise ValueError('musl atomics patch output differs')
        prefix = output / 'sysroot'
        execute(['sh', './configure', '--target=waterbox', '--build=waterbox', '--disable-shared', '--disable-wrapper',
                 '--prefix=' + str(prefix), '--syslibdir=' + str(prefix / 'syslib')], musl, env)
        execute([tools['make'], '-s', '-j2'], musl, env)
        execute([tools['make'], '-s', 'install', 'DESTDIR=' + str(stage / 'install')], musl, env)
        installed = stage / 'install' / str(prefix).lstrip('/')
        installed.rename(publish / 'sysroot')
        for name in ('lib/libc.a', 'lib/crt1.o', 'include/pthread.h'):
            if not (publish / 'sysroot' / name).is_file():
                raise ValueError('musl output missing: ' + name)
        if (publish / 'sysroot/lib/libc.a').read_bytes()[:8] != b'!<arch>\n':
            raise ValueError('musl libc is not an archive')
        elf(publish / 'sysroot/lib/crt1.o', 1)
        if pinned_inputs(root) != before:
            raise ValueError('source/toolchain changed during preparation')
        receipt = {'schema_version': 1, 'status': 'PREPARED', 'scope': 'runtime_and_musl_only', 'inputs': before,
                   'runtime_sha256': sha(publish / 'runtime.so'), 'sysroot_inventory_sha256': canonical(sources.inventory(publish / 'sysroot')),
                   'cargo': packages, 'musl_patch': {'sha256': sha(patch), 'before': hashlib.sha256(original).hexdigest(), 'after': sha(header)}}
        (publish / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        publish.rename(output)  # Exclusive output, no stale cache repair or overwrite.
    return verify(output, root)


RESULT_MARKER = 'P6_LINUX_WATERBOX_RESULT '
COUNTS = dict(frames=15, callbacks=3, snapshots=23, mismatch_rejections=1, max_live_sandboxes=2, sandboxes_created=3, host_page_bytes=4096)


def validate_result(text):
    lines = [line[len(RESULT_MARKER):] for line in text.splitlines() if line.startswith(RESULT_MARKER)]
    if len(lines) != 1:
        raise ValueError('missing or duplicate WB01 result')
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('duplicate WB01 result key')
        return result
    result = json.loads(lines[0], object_pairs_hook=unique)
    if not isinstance(result, dict) or type(result.get('schema_version')) is not int or set(result) != {'schema_version', 'status', 'scope', 'assertions', *COUNTS} or result['schema_version'] != 1 or result['status'] != 'PASS' or result['scope'] != 'WB01_MINICORE':
        raise ValueError('wrong WB01 result schema/scope')
    if any(type(result[k]) is not int or result[k] != n for k, n in COUNTS.items()) or type(result['assertions']) is not int or result['assertions'] <= 0:
        raise ValueError('incomplete WB01 assertions/cases')
    return result


def guest_elf(path):
    elf(path, 2)
    if Path(path).stat().st_size > 64 << 20:
        raise ValueError('guest ELF exceeds bound')
    data = Path(path).read_bytes(); offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    if size != 56 or not 1 <= count <= 64 or offset < 64 or offset + size * count > len(data):
        raise ValueError('malformed guest ELF program headers')
    entry = struct.unpack_from('<Q', data, 24)[0]; executable = False
    for index in range(count):
        kind, flags, start, address, _, files, memory, _ = struct.unpack_from('<IIQQQQQQ', data, offset + index * size)
        if kind in (2, 3):
            raise ValueError('guest must be static without interpreter')
        if kind == 1:
            if files > memory or start + files > len(data) or (address - start) % 4096:
                raise ValueError('malformed guest ELF segment')
            executable |= bool(flags & 1 and address <= entry < address + memory)
    if not executable:
        raise ValueError('guest entry is not executable')


def smoke(root=ROOT):
    prepared = prepare(root); output = Path(prepared['output'])
    names = ('tests/waterbox-smoke/linux_guest.c', 'tests/waterbox-smoke/linux_probe.c')
    source_hashes = {name: sha(root / name) for name in names}
    tools = {name: pin['path'] for name, pin in prepared['inputs']['authority']['tools'].items()}
    sysroot = output / 'sysroot'; runtime = output / 'runtime.so'
    with tempfile.TemporaryDirectory(prefix='linux-wb01-smoke-', dir=root / 'build') as tmp:
        stage = Path(tmp); guest = stage / 'guest.wbx'; host = stage / 'linux_probe'
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(stage), 'TMPDIR': str(stage), 'LANG': 'C.UTF-8'}
        emulibc = root / 'references/machawk/waterbox/emulibc'
        flags = [*shlex.split(MUSL_FLAGS), '-O2', '-Wall', '-std=c11', '-DWBX_HOST_PAGE_SIZE=4096',
                 '-nostdinc', '-isystem', str(sysroot / 'include'), '-I', str(emulibc)]
        for source, target in ((root / names[0], stage / 'guest.o'), (emulibc / 'emulibc.c', stage / 'emulibc.o')):
            execute([tools['clang'], *flags, '-c', source, '-o', target], stage, env)
        execute([tools['lld'], '-flavor', 'gnu', '-static', '--no-dynamic-linker', '--eh-frame-hdr', '-O2',
                 '-T', root / 'references/machawk/waterbox/linkscript.T', sysroot / 'lib/crt1.o', sysroot / 'lib/crti.o',
                 stage / 'guest.o', stage / 'emulibc.o', '--start-group', sysroot / 'lib/libc.a', '--end-group', sysroot / 'lib/crtn.o', '-o', guest], stage, env)
        execute([tools['clang'], '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', root / names[1], '-ldl', '-o', host], stage, env)
        guest_elf(guest); elf(host, (2, 3)); elf(runtime, 3)
        binaries = {str(path): sha(path) for path in (runtime, guest, host)}
        if verify(root=root) != prepared or source_hashes != {name: sha(root / name) for name in names}:
            raise ValueError('WB01 inputs changed before execution')
        child = subprocess.run([str(host), str(runtime), str(guest)], cwd=stage, env=env, text=True, capture_output=True, timeout=180)
        if child.returncode or len(child.stdout) + len(child.stderr) > 1 << 20:
            raise ValueError(f'WB01 child failed ({child.returncode}): ' + child.stdout[-1500:] + child.stderr[-1500:])
        result = validate_result(child.stdout)
        if binaries != {str(path): sha(path) for path in (runtime, guest, host)} or verify(root=root) != prepared or source_hashes != {name: sha(root / name) for name in names}:
            raise ValueError('WB01 inputs/binaries changed during execution')
        return {**result, 'runtime_sha256': binaries[str(runtime)], 'guest_sha256': binaries[str(guest)], 'host_sha256': binaries[str(host)],
                'source_sha256': source_hashes, 'preparation_sha256': canonical(prepared), 'sysroot_inventory_sha256': prepared['sysroot_inventory_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--inventory', action='store_true')
    selection.add_argument('--prepare', action='store_true')
    selection.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    marker = MARKER if args.inventory else RESULT_MARKER if args.smoke else 'P6_LINUX_WATERBOX_PREPARATION '
    try:
        result = inventory() if args.inventory else smoke() if args.smoke else prepare()
    except (ValueError, OSError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as error:
        print(marker + json.dumps({'schema_version': 1, 'status': 'BLOCKED', 'scope': 'requested Linux WB01 phase failed; no execution credited', 'error': str(error)}))
        return 2
    print(marker + json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
