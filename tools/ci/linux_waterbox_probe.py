#!/usr/bin/env python3
"""Inventory preinstalled Linux WB01 inputs; no installs, downloads or builds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
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
    result['version'] = command([path, '--version']).splitlines()[0]
    if not result['version']:
        raise ValueError('empty tool version')
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


def cargo_cache(root):
    lock = root / 'references/machawk/waterbox/waterboxhost/Cargo.lock'
    data = tomllib.loads(lock.read_text())
    home = Path(os.environ.get('CARGO_HOME', Path.home() / '.cargo'))
    found = []; missing = []
    for package in data['package']:
        if not package.get('source', '').startswith('registry+'):
            continue
        name = f"{package['name']}-{package['version']}.crate"
        candidates = list((home / 'registry/cache').glob('*/' + name))
        valid = [p for p in candidates if p.is_file() and not p.is_symlink() and sha(p) == package['checksum']]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', action='store_true', required=True)
    parser.parse_args()
    try:
        result = inventory()
    except (ValueError, OSError, StopIteration, subprocess.SubprocessError) as error:
        print(MARKER + json.dumps({'schema_version': 1, 'status': 'BLOCKED', 'scope': 'inventory only; no WB01 execution', 'error': str(error)}))
        return 2
    print(MARKER + json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
