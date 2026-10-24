#!/usr/bin/env python3
"""Compose and verify standalone Waterbox source/notice policy, without building."""
import argparse
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
from urllib.parse import quote
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
STD_LOCK = '53d9ccff0c906de434b65af90d5c30063894b940fc839261f1fb06ef2e69e3d3'
STD_BANDS = {'compiler-builtins': ['LICENSE.txt', 'libm/LICENSE.txt'], 'backtrace': ['LICENSE-APACHE', 'LICENSE-MIT'],
             'portable-simd': ['LICENSE-APACHE', 'LICENSE-MIT', 'crates/core_simd/LICENSE-APACHE', 'crates/core_simd/LICENSE-MIT'],
             'stdarch': ['LICENSE-APACHE', 'LICENSE-MIT', 'crates/core_arch/LICENSE-APACHE', 'crates/core_arch/LICENSE-MIT',
                         'crates/intrinsic-test/LICENSE-APACHE', 'crates/intrinsic-test/LICENSE-MIT']}


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
    std_complete = verify_std_external(p, blob)
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
    result['rust_std_external_complete'] = std_complete
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


def source_body(record, url=None):
    data = record['text'].encode()
    require(digest(data) == record['sha256'] and (url is None or record['url'] == url), 'Std primary source pin')
    return data


def verify_std_external(p, blob):
    e = p.get('rust_std_external')
    if e is None:
        return False
    commit = p['rust_std']['commit']; raw = f'https://raw.githubusercontent.com/rust-lang/rust/{commit}/'
    lock = source_body(e['lock'], raw + 'library/Cargo.lock')
    require(digest(lock) == STD_LOCK, 'Std Cargo.lock pin')
    packages = tomllib.loads(lock.decode())['package']
    registry = {(c['name'], c['version']): c for c in packages if c.get('checksum')}
    local = {(c['name'], c['version']) for c in packages if not c.get('checksum')}
    require(len(packages) == 49 and len(registry) == 30 and len(local) == 19, 'Std lock coverage')
    tree = json.loads(source_body(e['root_tree'], 'https://api.github.com/repos/rust-lang/rust/git/trees/' + commit))
    library = next(n for n in tree['tree'] if n['path'] == 'library')
    subtree = json.loads(source_body(e['library_tree'], 'https://api.github.com/repos/rust-lang/rust/git/trees/' + library['sha']))
    require(library['type'] == 'tree' and subtree['sha'] == library['sha']
            and not tree.get('truncated') and not subtree.get('truncated'), 'Std library tree pin')
    entries = {n['path']: n for n in subtree['tree']}
    modules = configparser.ConfigParser(); modules.read_string(source_body(e['gitmodules'], raw + '.gitmodules').decode())
    repos = {modules[s]['path']: modules[s]['url'] for s in modules.sections()}
    require({n['path'] for n in subtree['tree'] if n['type'] == 'commit'} == {'backtrace'}, 'Std gitlink coverage')
    require(len(e['bands']) == 4 and {b['path'] for b in e['bands']} == set(STD_BANDS), 'Std source band coverage')
    for b in e['bands']:
        name = b['path']; entry = entries[name]; linked = name == 'backtrace'
        repo = repos['library/' + name] if linked else 'https://github.com/rust-lang/rust.git'
        revision = entry['sha'] if linked else commit
        slug = repo.removeprefix('https://github.com/').removesuffix('.git')
        require(entry['type'] == ('commit' if linked else 'tree') and b['kind'] == entry['type']
                and b['tree_sha'] == entry['sha'] and b['repository'] == repo and b['commit'] == revision, 'Std band origin pin')
        require(b['required_notices'] == STD_BANDS[name]
                and sorted(n['member'] for n in b['notices']) == sorted(STD_BANDS[name]), 'Std band notice coverage')
        for n in b['notices']:
            path = n['member'] if linked else 'library/' + name + '/' + n['member']
            require(n['url'] == f"https://raw.githubusercontent.com/{slug}/{revision}/{path}", 'Std band notice source')
            notice_bytes(blob, n)
    actual = [(c['name'], c['version']) for c in e['registry']]
    require(len(actual) == len(set(actual)) and set(actual) == set(registry), 'Std registry coverage')
    for c in e['registry']:
        key = (c['name'], c['version']); pin = registry[key]
        require(c['checksum'] == pin['checksum'] and c['source_url'] ==
                f"https://static.crates.io/crates/{key[0]}/{key[0]}-{quote(key[1], safe='')}.crate", 'Std crate pin')
        meta = tomllib.loads(source_body(c['manifest']).decode())['package']
        require((meta['name'], meta['version']) == key and c['license'] == meta.get('license')
                and c['license_file'] == meta.get('license-file'), 'Std crate manifest')
        names = [n['member'] for n in c['notices']]
        require(names and len(names) == len(set(names)) and sorted(names) == sorted(c['required_notices'])
                and (not c['license_file'] or c['license_file'] in names), 'Std crate notice coverage')
        require(not any('url' in n for n in c['notices']) or bool(c.get('vcs')), 'Std upstream notice lacks VCS pin')
        if c.get('vcs'):
            vcs = json.loads(source_body(c['vcs']))
            repo = meta['repository'].rstrip('/').removesuffix('.git')
            for n in c['notices']:
                if 'url' in n:
                    path = n['origin_path']
                    require(path and not path.startswith('/') and '..' not in Path(path).parts
                            and n['url'] == f"https://raw.githubusercontent.com/{repo[19:]}/{vcs['git']['sha1']}/{path}",
                            'Std crate upstream notice pin')
        if c['name'] in ('r-efi', 'r-efi-alloc'):
            require(c['selected_license'] == 'MIT' and 'MIT' in c['license'].split() and 'AUTHORS' in names,
                    'Std selected license coverage')
        for n in c['notices']:
            notice_bytes(blob, n)
    actual = [(c['name'], c['version']) for c in e['local']]
    require(len(actual) == len(set(actual)) and set(actual) == local, 'Std local package coverage')
    for c in e['local']:
        path = {'compiler_builtins': 'compiler-builtins/compiler-builtins', 'windows-link': 'windows_link'}.get(c['name'], c['name'])
        require(c['path'] == path + '/Cargo.toml', 'Std local source path')
        meta = tomllib.loads(source_body(c['manifest'], raw + 'library/' + c['path']).decode())['package']
        require((meta['name'], meta['version']) == (c['name'], c['version']) and c['license'] == meta.get('license')
                and c['notice_band'] == ('compiler-builtins' if c['name'] == 'compiler_builtins' else 'rust-own'), 'Std local attribution')
    return True


def verify_std_archives(p, cache):
    e = p['rust_std_external']; cache = Path(cache); blob = (ROOT / p['notices']['path']).read_bytes()
    for c in e['registry']:
        path = cache / 'crates' / f"{c['name']}-{c['version']}.crate"; data = path.read_bytes()
        require(len(data) == c['archive_bytes'] and digest(data) == c['checksum'], 'Std crate archive pin')
        with tarfile.open(path) as archive:
            prefix = f"{c['name']}-{c['version']}/"
            require(archive.extractfile(prefix + 'Cargo.toml').read() == source_body(c['manifest']), 'Std archive manifest mismatch')
            if c.get('vcs'):
                require(archive.extractfile(prefix + '.cargo_vcs_info.json').read() == source_body(c['vcs']), 'Std archive VCS mismatch')
            for n in c['notices']:
                data = ((cache / 'sources' / n['sha256']).read_bytes() if 'url' in n
                        else archive.extractfile(prefix + n['member']).read())
                if 'range' in n:
                    start, end = n['range']
                    require(type(start) is int and type(end) is int and 0 <= start < end <= len(data), 'Std notice slice')
                    data = data[start:end]
                require(data == notice_bytes(blob, n), 'Std archive notice mismatch')
            found = {m.name.removeprefix(prefix) for m in archive.getmembers() if m.isfile()
                     and (any(word in m.name.upper() for word in ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT', 'AUTHORS'))
                          or m.name == prefix + (c.get('license_file') or ''))}
            require(found == {n['member'] for n in c['notices'] if 'url' not in n and 'range' not in n},
                    'Std archive notice inventory')
    records = [e[k] for k in ('lock', 'root_tree', 'library_tree', 'gitmodules')] + [c['manifest'] for c in e['local']]
    for record in records:
        require((cache / 'sources' / record['sha256']).read_bytes() == source_body(record), 'Std cached primary source mismatch')
    for b in e['bands']:
        for n in b['notices']:
            require((cache / 'sources' / n['sha256']).read_bytes() == notice_bytes(blob, n), 'Std cached band notice mismatch')


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
    for name in ('crate-cache', 'nuget-feed', 'cargo', 'cargo-home', 'std-source-cache'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(); p = json.loads((ROOT / POLICY).read_bytes())
    composed = compose(p)
    require(bool(args.crate_cache) == bool(args.nuget_feed), 'Supply both archive caches')
    if args.crate_cache:
        verify_archives(p, args.crate_cache, args.nuget_feed)
    if args.std_source_cache:
        verify_std_archives(p, args.std_source_cache)
    require(bool(args.cargo) == bool(args.cargo_home), 'Supply Cargo executable and home')
    if args.cargo:
        pin = json.loads((ROOT / 'plan/toolchains.json').read_bytes())['tools']['cargo']
        require(digest(args.cargo.read_bytes()) == pin['sha256'], 'Cargo executable pin')
        output = subprocess.check_output([args.cargo, 'metadata', '--offline', '--locked', '--format-version', '1',
                                          '--filter-platform', p['target'], '--manifest-path', ROOT / p['cargo_manifest']],
                                         env={**os.environ, 'CARGO_HOME': str(args.cargo_home)}, timeout=120)
        actual = {(c['name'], c['version']): c['phase'] for c in p['components'] if c['kind'] == 'crate'}
        require(metadata_selection(json.loads(output)) == actual, 'Offline Cargo selection changed')
    if p['rust_std_external_pending'] or not composed['rust_std_external_complete']:
        print('BLOCKED: Rust std external-crate notices remain uncovered; partial policy inputs verified, no package claim')
        return 2
    print('Waterbox source/notice policy verified; no package or execution claim')
    return 0


if __name__ == '__main__':
    sys.exit(main())
