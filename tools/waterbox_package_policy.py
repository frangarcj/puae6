#!/usr/bin/env python3
"""Compose and verify standalone Waterbox source/notice policy, without building."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import zipfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import package

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'manifests/release/waterbox-preview.json'
BASE = 'manifests/release/native-preview.json'
CARGO = 'references/machawk/waterbox/waterboxhost/'
EXTRA = [p for n in ('machawk', 'musl-darwin', 'compiler-rt-emutls')
         for p in ('references/' + n, 'references/' + n + '.manifest.json')]
INPUTS = {BASE, 'plan/toolchains.json', 'tools/ci/managed-inputs.json', CARGO + 'Cargo.toml', CARGO + 'Cargo.lock',
          'third_party/waterbox-sysroot/PROVENANCE.json', 'integration/bizhawk/core/Puae6.Core.csproj',
          'tests/ffi/Ffi.csproj', 'tools/build_waterbox_sysroot.py', *EXTRA[1::2]}
# Audited with Cargo metadata --offline --locked --filter-platform aarch64-apple-darwin.
RUNTIME = set('anyhow bitflags block-buffer cfg-if cpufeatures digest either generic-array goblin itertools '
              'lazy_static libc log opaque-debug page_size plain scroll sha2 typenum'.split())
BUILD = set('proc-macro2 quote scroll_derive syn unicode-ident version_check'.split())
WINDOWS = {'winapi': '0.3.9', 'winapi-i686-pc-windows-gnu': '0.4.0', 'winapi-x86_64-pc-windows-gnu': '0.4.0'}
NUGET = {'communitytoolkit.highperformance': ['License.md', 'ThirdPartyNotices.txt'], 'newtonsoft.json': ['LICENSE.md']}
RUST = {'COPYRIGHT', 'LICENSE-MIT', 'LICENSE-APACHE', 'license-metadata.json',
        'LICENSES/Unicode-3.0.txt', 'LICENSES/BSD-2-Clause.txt'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def notice_bytes(blob, notice):
    start, size = notice['offset'], notice['bytes']
    require(type(start) is int and type(size) is int and start >= 0 and size > 0
            and start + size <= len(blob), 'Invalid notice bounds')
    data = blob[start:start + size]
    require(digest(data) == notice['sha256'], 'Changed original notice: ' + notice['member'])
    return data


def crate_notices(name):
    pair = ['LICENSE-APACHE', 'LICENSE-MIT']
    return ({'generic-array': ['LICENSE'], 'goblin': ['LICENSE'], 'scroll': ['LICENSE'], 'scroll_derive': ['LICENSE'],
             'typenum': ['LICENSE', *pair], 'unicode-ident': [*pair, 'LICENSE-UNICODE']}).get(name, pair)


def compose(policy=None, read=None):
    read = read or (lambda p: (ROOT / p).read_bytes())
    p = json.loads(read(POLICY)) if policy is None else policy
    require(p['schema_version'] == 1 and p['base_policy'] == BASE and p['target'] == 'aarch64-apple-darwin', 'Policy profile')
    require(set(p['inputs']) == INPUTS and p['source_additions'] == EXTRA, 'Unexpected source selection')
    require('rust_std_external_pending' in p, 'Missing std external coverage declaration')
    for path, expected in p['inputs'].items():
        require(digest(read(path)) == expected, 'Changed policy input: ' + path)
    require(p['cargo_manifest'] == CARGO + 'Cargo.toml' and p['cargo_lock'] == CARGO + 'Cargo.lock'
            and p['excluded_cargo'] == WINDOWS, 'Cargo source/target selection')
    lock = tomllib.loads(read(p['cargo_lock']).decode())['package']
    crates = {c['name']: c for c in lock if c.get('checksum') and c['name'] not in WINDOWS}
    require(set(crates) == RUNTIME | BUILD and {c['name']: c['version'] for c in lock if c['name'] in WINDOWS} == WINDOWS,
            'Cargo closure changed')
    managed = json.loads(read('tools/ci/managed-inputs.json'))
    nuget = {c['id']: c for c in managed['packages'] if c['id'] in NUGET}
    sysroot = p['sysroot']
    require(sysroot['archive'] == 'third_party/waterbox-sysroot/waterbox-sysroot-aarch64.tar.xz'
            and sysroot['provenance'] == 'third_party/waterbox-sysroot/PROVENANCE.json'
            and sysroot['source_recipe'] == 'tools/build_waterbox_sysroot.py', 'Sysroot source selection')
    provenance = json.loads(read(sysroot['provenance']))
    require(digest(read(sysroot['archive'])) == provenance['archive']['sha256']
            and sysroot['complete_llvm_source_url'] == provenance['llvm_complete_source_archive'], 'Sysroot source pin')
    expected = {('crate', n): crate_notices(n) for n in crates}
    expected.update({('nuget', n): names for n, names in NUGET.items()})
    expected.update({('sysroot', n['path']): ['licenses/' + n['path']] for n in provenance['notices']})
    expected[('rust-std', 'rust-std')] = RUST
    actual = [(c['kind'], c['name']) for c in p['components']]
    require(len(actual) == len(set(actual)) and set(actual) == set(expected), 'Missing/extra policy component')
    require(p['notices']['path'] == 'manifests/release/waterbox-notices.txt', 'Notice source selection')
    blob = read(p['notices']['path'])
    require(len(blob) == p['notices']['bytes'] and digest(blob) == p['notices']['sha256'], 'Changed notice collection')
    for c in p['components']:
        names = [n['member'] for n in c['notices']]
        require(len(names) == len(set(names)) and set(names) == set(expected[c['kind'], c['name']]), 'Missing/extra original notice')
        for n in c['notices']:
            notice_bytes(blob, n)
        if c['kind'] == 'crate':
            pin = crates[c['name']]
            require(c['version'] == pin['version'] and c['checksum'] == pin['checksum']
                    and c['source_url'] == f"https://static.crates.io/crates/{c['name']}/{c['name']}-{c['version']}.crate"
                    and c['phase'] == ('runtime' if c['name'] in RUNTIME else 'build'), 'Cargo component pin/phase')
        else:
            require(c['phase'] == 'runtime', 'Unexpected component phase')
        if c['kind'] == 'nuget':
            pin = nuget[c['name']]
            require(all(c[k] == pin[k] for k in ('version', 'bytes', 'archive_sha512_base64'))
                    and c['source_url'] == pin['url'], 'NuGet component pin')
    verify_std(p, blob, read)
    # Prove all six imported sysroot notices against the committed, authenticated archive.
    import io
    with tarfile.open(fileobj=io.BytesIO(read(sysroot['archive']))) as archive:
        for c in p['components']:
            if c['kind'] == 'sysroot':
                for n in c['notices']:
                    require(archive.extractfile(n['member']).read() == notice_bytes(blob, n), 'Sysroot notice mismatch')
    result = json.loads(read(BASE))
    result['platform'] = 'waterbox-darwin-arm64'
    result['source_paths'] += EXTRA
    result['licenses']['WATERBOX-NOTICES.txt'] = p['notices']['path']
    result['rust_std_external_pending'] = p['rust_std_external_pending']
    package.notice_files(result, read)
    return result


def verify_std(p, blob, read):
    std = p['rust_std']; commit = std['commit']
    pin = json.loads(read('plan/toolchains.json'))['tools']['rustc']['version']
    require(len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
            and ('rustc ' + std['version'] + ' (' + commit[:9]) in pin
            and std['source_url'] == f'https://github.com/rust-lang/rust/archive/{commit}.tar.gz', 'Rust std source pin')
    c = next(c for c in p['components'] if c['kind'] == 'rust-std')
    require(c['version'] == std['version'], 'Rust std version')
    for n in c['notices']:
        require(n['url'] == f"https://raw.githubusercontent.com/rust-lang/rust/{commit}/{n['member']}", 'Rust notice source')
    metadata = json.loads(notice_bytes(blob, next(n for n in c['notices'] if n['member'] == 'license-metadata.json')))
    root = metadata['files']['children'][0]
    entries = [root['license']]
    def visit(node):
        if 'license' in node:
            entries.append(node['license'])
        for child in node.get('children', []):
            visit(child)
    for child in root['children']:
        if child.get('name', '').startswith('library/'):
            visit(child)
    licenses = set()
    for entry in entries:
        require(entry['copyright'], 'Missing std attribution')
        licenses.update(entry['spdx'].replace('(', '').replace(')', '').split())
    licenses -= {'OR', 'AND'}
    require(licenses == set(std['licenses']) == {'MIT', 'Apache-2.0', 'BSD-2-Clause', 'Unicode-3.0'}, 'Std license coverage')


def verify_archives(p, crate_cache, nuget_feed):
    blob = (ROOT / p['notices']['path']).read_bytes()
    for c in p['components']:
        if c['kind'] == 'crate':
            path = Path(crate_cache) / f"{c['name']}-{c['version']}.crate"
            require(digest(path.read_bytes()) == c['checksum'], 'Crate archive pin')
            with tarfile.open(path) as archive:
                for n in c['notices']:
                    require(archive.extractfile(f"{c['name']}-{c['version']}/{n['member']}").read() == notice_bytes(blob, n),
                            'Crate notice mismatch')
        if c['kind'] == 'nuget':
            path = Path(nuget_feed) / f"{c['name']}.{c['version']}.nupkg"; data = path.read_bytes()
            require(len(data) == c['bytes'] and base64.b64encode(hashlib.sha512(data).digest()).decode() == c['archive_sha512_base64'],
                    'NuGet archive pin')
            with zipfile.ZipFile(path) as archive:
                for n in c['notices']:
                    require(archive.read(n['member']) == notice_bytes(blob, n), 'NuGet notice mismatch')


def metadata_selection(metadata):
    packages = {p['id']: p for p in metadata['packages']}
    nodes = {n['id']: n for n in metadata['resolve']['nodes']}
    def closure(normal):
        pending = [metadata['resolve']['root']]; seen = set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            for d in nodes[key]['deps']:
                proc = any('proc-macro' in t['kind'] for t in packages[d['pkg']]['targets'])
                if not normal or (not proc and any(k['kind'] is None for k in d['dep_kinds'])):
                    pending.append(d['pkg'])
        return {(packages[k]['name'], packages[k]['version']) for k in seen if packages[k]['source'] is not None}
    runtime = closure(True)
    return {key: ('runtime' if key in runtime else 'build') for key in closure(False)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('crate-cache', 'nuget-feed', 'cargo', 'cargo-home'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(); p = json.loads((ROOT / POLICY).read_bytes())
    compose(p)
    require(bool(args.crate_cache) == bool(args.nuget_feed), 'Supply both archive caches')
    if args.crate_cache:
        verify_archives(p, args.crate_cache, args.nuget_feed)
    require(bool(args.cargo) == bool(args.cargo_home), 'Supply Cargo executable and home')
    if args.cargo:
        pin = json.loads((ROOT / 'plan/toolchains.json').read_bytes())['tools']['cargo']
        require(digest(args.cargo.read_bytes()) == pin['sha256'], 'Cargo executable pin')
        output = subprocess.check_output([args.cargo, 'metadata', '--offline', '--locked', '--format-version', '1',
                                          '--filter-platform', p['target'], '--manifest-path', ROOT / p['cargo_manifest']],
                                         env={**os.environ, 'CARGO_HOME': str(args.cargo_home)}, timeout=120)
        actual = {(c['name'], c['version']): c['phase'] for c in p['components'] if c['kind'] == 'crate'}
        require(metadata_selection(json.loads(output)) == actual, 'Offline Cargo selection changed')
    if p['rust_std_external_pending']:
        print('BLOCKED: Rust std external-crate notices remain uncovered; partial policy inputs verified, no package claim')
        return 2
    print('Waterbox source/notice policy verified; no package or execution claim')
    return 0


if __name__ == '__main__':
    sys.exit(main())
