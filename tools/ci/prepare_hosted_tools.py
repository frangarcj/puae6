#!/usr/bin/env python3
"""Prepare pinned bottles as data; never load or execute the supplied tools."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[2]
MAX_FILES, MAX_EXPANDED, MAX_FILE = 200000, 4 << 30, 512 << 20
ASAN_PATH = 'llvm/23.1.4/lib/clang/23/lib/darwin/libclang_rt.asan_osx_dynamic.dylib'
ASAN_ID = '@rpath/libclang_rt.asan_osx_dynamic.dylib'
ASAN_SOURCE_SHA = '7c8af9f0737137dcb777c0d964753a1161f00a8314450dc4fc5eff6a1728af87'
ASAN_SOURCE_BYTES = 3966096


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), f"symlink component: {part}")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, f"regular unlinked file required: {path}")
    return info


def digest(path):
    regular(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(value):
    require(isinstance(value, str) and all(re.fullmatch(r'[A-Za-z0-9@+_. -]+', p) and p not in ('.', '..')
            for p in value.split('/')) and not value.startswith('/'), 'noncanonical relative path')
    return value


def inputs():
    path = ROOT / 'tools/ci/hosted-tools.json'
    require(regular(path).st_size < 1 << 20, 'manifest too large')
    manifest = json.loads(path.read_text())
    require(manifest['schema_version'] == 1 and manifest['bottle_arch'] == 'arm64_tahoe', 'unsupported manifest')
    archives, targets = manifest['archives'], manifest['normalized_macho']
    require(1 <= len(archives) <= 16 and 1 <= len(targets) <= 128, 'manifest count limit')
    prefixes = set()
    for row in archives:
        prefix = relative(row['prefix'])
        require(prefix == relative(row['owner']) + '/' + relative(row['version']) and prefix not in prefixes
                and '/' not in row['owner'] + row['version'], 'invalid bottle prefix')
        prefixes.add(prefix)
        require(row['slug'] == row['owner'].replace('@', '/'), 'invalid bottle slug')
        require(re.fullmatch('[0-9a-f]{64}', row['sha256']) and type(row['bytes']) is int and 0 < row['bytes'] <= 1 << 30, 'invalid archive pin')
        require(row['url'] == f"https://ghcr.io/v2/homebrew/core/{row['slug']}/blobs/sha256:{row['sha256']}", 'untrusted archive URL')
        require(row['notices'] and all(relative(p).startswith(prefix + '/') for p in row['notices']), 'invalid notices')
    paths = set()
    def owned(value):
        return any(relative(value).startswith(p + '/') for p in prefixes)
    excluded = manifest['excluded_symlinks']
    require(isinstance(excluded, dict) and all(owned(name) and isinstance(target, str) and 0 < len(target) <= 4096
            and '\0' not in target for name, target in excluded.items()), 'invalid excluded symlink mapping')
    materialized = manifest['materialized_symlinks']
    require(isinstance(materialized, dict) and all(owned(name) and name not in excluded and isinstance(item, dict)
            and set(item) == {'target', 'source'} and owned(item['source']) and item['source'] not in excluded
            and item['source'] != name and isinstance(item['target'], str) and 0 < len(item['target']) <= 4096
            and '\0' not in item['target'] for name, item in materialized.items()), 'invalid materialized symlink mapping')
    for row in targets:
        require(owned(row['path']) and row['path'] not in paths, 'invalid target path')
        paths.add(row['path'])
        asan = row.get('signature_policy') == 'asan-arm64-rpath'
        if asan:
            require(row['path'] == ASAN_PATH and row.get('dylib_id') == ASAN_ID
                    and row.get('source_sha256') == ASAN_SOURCE_SHA and row.get('source_bytes') == ASAN_SOURCE_BYTES,
                    'invalid ASan source/path/ID policy')
        else:
            require('source_sha256' not in row and 'source_bytes' not in row, 'source pins require explicit ASan policy')
        if 'dylib_id' in row:
            parts = PurePosixPath(row['path']).parts
            parent = '/opt/homebrew/opt/' + parts[0] + '/' + '/'.join(parts[2:-1]) + '/'
            value = row['dylib_id']
            require(asan or (parts[2] == 'lib' and row['path'].endswith('.dylib') and isinstance(value, str)
                    and value.startswith(parent) and re.fullmatch(r'[A-Za-z0-9_+.-]+\.dylib', value[len(parent):])), 'invalid explicit dylib ID')
        if 'signature_policy' in row:
            require(asan or (row['path'] == 'zstd/1.5.7_1/lib/libzstd.1.5.7.dylib' and 'dylib_id' in row
                    and row['signature_policy'] == 'preserve-adhoc-layout'), 'invalid signature policy')
        require(re.fullmatch('[0-9a-f]{64}', row['sha256']) and type(row['bytes']) is int and 0 < row['bytes'] <= MAX_FILE, 'invalid target pin')
    require(set(manifest['entrypoints']) == {'clang', 'lld', 'cargo', 'rustc'}, 'invalid entrypoints')
    aliases = manifest['aliases']
    require(len(aliases) == 1 and all(owned(a) and b in paths and a.endswith('/bin/ld.lld') and b.endswith('/bin/lld') for a, b in aliases.items()), 'invalid aliases')
    require(all(p in paths or p in aliases for p in manifest['entrypoints'].values()), 'unpinned entrypoint')
    dirs = manifest['library_directories']
    require(len(dirs) == len(prefixes) and set(dirs) == {p + '/lib' for p in prefixes}, 'invalid library directories')
    normalizer = manifest['normalizer']
    require(normalizer['path'] == 'tools/ci/normalize_homebrew.rb' and digest(ROOT / normalizer['path']) == normalizer['sha256'], 'normalizer pin mismatch')
    source = ROOT / 'references/ruby-macho.manifest.json'
    require(digest(source) == normalizer['ruby_source_manifest_sha256'], 'Ruby manifest pin mismatch')
    capture = json.loads(source.read_text())
    lock = ROOT / 'plan/upstream.lock.json'
    regular(lock)
    pin, = [p for p in json.loads(lock.read_text())['sources'] if p['id'] == 'ruby_macho']
    require(pin['version'] == '7.0.0' and capture['schema_version'] == 1 and capture['source'] == {k: pin[k] for k in ('id', 'repository', 'commit', 'snapshot')}
            and capture['destination'] == 'references/ruby-macho', 'Ruby capture source mismatch')
    expected = {relative(e['path']): ['file', int(e['mode'], 8) & 0o7777, digest(ROOT / capture['destination'] / e['path'])] for e in capture['files']}
    require(len(expected) == len(capture['files']) and all(e['mode'] == '100644' and expected[e['path']][2] == e['sha256'] for e in capture['files']), 'Ruby capture mismatch')
    actual = inventory(ROOT / capture['destination'])
    allowed_dirs = {str(p) for name in expected for p in PurePosixPath(name).parents if str(p) != '.'}
    require({p: [v[0], v[1], v[3]] for p, v in actual.items() if v[0] == 'file'} == expected
            and all(v[0] == 'file' or (v[0] == 'dir' and p in allowed_dirs) for p, v in actual.items()), 'Ruby capture inventory mismatch')
    return manifest, {'manifest': digest(path), 'lock': digest(lock), 'normalizer': normalizer['sha256'], 'ruby_manifest': digest(source)}


def inventory(root, cache=False):
    require(root.is_dir() and not root.is_symlink(), 'invalid inventory root')
    result, total = {}, 0
    for path in sorted(root.rglob('*')):
        rel, info = str(path.relative_to(root)), path.lstat()
        if cache and rel == 'cache.json':
            continue
        require(len(result) < MAX_FILES, 'inventory count limit')
        mode = stat.S_IMODE(info.st_mode)
        require(not mode & 0o7000, 'special file mode')
        if path.is_symlink():
            target = os.readlink(path)
            parts = PurePosixPath(rel).parts
            require(len(parts) > 2 and not os.path.isabs(target) and '\\' not in target
                    and path.resolve().is_relative_to(root.joinpath(*parts[:2]).resolve()) and path.resolve().exists(), 'escaping or dangling symlink')
            result[rel] = ['link', mode, target]
        elif path.is_dir():
            result[rel] = ['dir', mode]
        else:
            require(info.st_size <= MAX_FILE, 'file size limit')
            total += info.st_size
            require(total <= MAX_EXPANDED, 'inventory size limit')
            result[rel] = ['file', mode, info.st_size, digest(path)]
    return result


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        require(urllib.parse.urlsplit(url).scheme == 'https', 'insecure redirect')
        redirected = super().redirect_request(request, fp, code, msg, headers, url)
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(request.full_url).netloc:
            redirected.remove_header('Authorization')
        return redirected


def download(row, destination):
    opener = urllib.request.build_opener(Redirect())
    scope = urllib.parse.quote('repository:homebrew/core/' + row['slug'] + ':pull', safe='')
    with opener.open('https://ghcr.io/token?service=ghcr.io&scope=' + scope, timeout=30) as response:
        token_bytes = response.read(65537)
    require(len(token_bytes) <= 65536, 'token response limit')
    token = json.loads(token_bytes)['token']
    require(isinstance(token, str) and 0 < len(token) < 16384 and not any(c in token for c in '\r\n'), 'invalid pull token')
    request = urllib.request.Request(row['url'], headers={'Authorization': 'Bearer ' + token})
    size, checksum = 0, hashlib.sha256()
    with opener.open(request, timeout=60) as response, destination.open('xb') as output:
        while chunk := response.read(1 << 20):
            size += len(chunk)
            require(size <= row['bytes'], 'archive size limit')
            checksum.update(chunk)
            output.write(chunk)
    require(size == row['bytes'] and checksum.hexdigest() == row['sha256'], 'archive SHA/size mismatch')


class Limited:
    def __init__(self, stream):
        self.stream, self.size = stream, 0

    def read(self, size):
        data = self.stream.read(size)
        self.size += len(data)
        require(self.size <= MAX_EXPANDED, 'decoded tar size limit')
        return data


def extract(archive, row, destination, budget=None, excluded=None, materialized=None):
    require(regular(archive).st_size == row['bytes'] and digest(archive) == row['sha256'], 'archive SHA/size mismatch')
    seen, budget = set(), ([0, 0] if budget is None else budget)
    required = {name: ('excluded', target) for name, target in (excluded or {}).items() if name.startswith(row['prefix'] + '/')}
    required.update({name: ('materialized', item['target']) for name, item in (materialized or {}).items() if name.startswith(row['prefix'] + '/')})
    with gzip.open(archive, 'rb') as decoded, tarfile.open(fileobj=Limited(decoded), mode='r|') as stream:
        for member in stream:
            name = relative(member.name.removesuffix('/'))
            require(name not in seen and budget[0] < MAX_FILES, 'duplicate member or count limit')
            seen.add(name)
            budget[0] += 1
            require(name == row['prefix'] or name.startswith(row['prefix'] + '/'), 'wrong archive prefix')
            if name in required:
                kind, expected = required[name]
                require(member.issym() and member.linkname == expected, f'{kind} symlink differs: {name}')
                del required[name]
                continue  # Never create this link or inspect its external target.
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(), 'unsupported archive type')
            require(not member.mode & 0o7000 and 0 <= member.size <= MAX_FILE, 'archive mode/size limit')
            budget[1] += member.size
            require(budget[1] <= MAX_EXPANDED, 'expanded archive limit')
            if member.issym() or member.islnk():
                link = member.linkname
                target = (destination / name).parent / link if member.issym() else destination / link
                require(not os.path.isabs(link) and '\\' not in link and target.resolve().is_relative_to(destination / row['prefix']), f'escaping archive link: {name} -> {link}')
            stream.extract(member, destination, filter='data')
        while tail := stream.fileobj.read(1 << 20):
            require(not tail.strip(b'\0'), 'nonzero tar trailer')
    require(not required, 'missing ' + ', '.join(f'{kind} symlink: {name}' for name, (kind, _) in sorted(required.items())))


def verify(base, manifest):
    for name in manifest['excluded_symlinks']:
        require(not os.path.lexists(base / name), f'excluded symlink present: {name}')
    for name, item in manifest['materialized_symlinks'].items():
        require(digest(base / name) == digest(base / item['source'])
                and stat.S_IMODE(regular(base / name).st_mode) == stat.S_IMODE(regular(base / item['source']).st_mode), f'materialized symlink mismatch: {name}')
    mismatches = []
    for row in manifest['normalized_macho']:
        path = base / row['path']
        try:
            size, checksum = regular(path).st_size, digest(path)
            if size != row['bytes'] or checksum != row['sha256']:
                mismatches.append(f'normalized target mismatch: {row["path"]}; bytes={size}, sha256={checksum}; expected bytes={row["bytes"]}, sha256={row["sha256"]}')
        except (OSError, ValueError) as error:
            mismatches.append(f'normalized target mismatch: {row["path"]}; {error}')
    require(not mismatches, '\n'.join(mismatches))
    for archive in manifest['archives']:
        for name in archive['notices']:
            require(regular(base / name).st_size > 0, 'missing notice')
    for alias, source in manifest['aliases'].items():
        require(digest(base / alias) == digest(base / source), 'alias mismatch')
    for name in manifest['entrypoints'].values():
        require(regular(base / name).st_mode & 0o111, 'nonexecutable entrypoint')
    for name in manifest['library_directories']:
        require((base / name).is_dir() and not (base / name).is_symlink(), 'missing library directory')


def prepare(fetch=False, ruby='ruby'):
    manifest, identity = inputs()  # All pins and source inputs precede network access.
    build, output = ROOT / 'build', ROOT / 'build/hosted-tools'
    require(not build.is_symlink() and not output.is_symlink(), 'symlink output')
    expected_paths = {'schema_version': 1, 'paths': {**{k: str(output / v) for k, v in manifest['entrypoints'].items()}, 'make': '/usr/bin/make'}}
    directories = [str(output / p) for p in manifest['library_directories']]
    if output.exists():
        require(regular(output / 'cache.json').st_size <= 64 << 20, 'cache receipt size limit')
        require(regular(output / 'paths.json').st_size <= 65536, 'paths size limit')
        receipt = json.loads((output / 'cache.json').read_text())
        require(set(receipt) == {'schema_version', 'scope', 'inputs', 'library_directories', 'inventory'} and receipt['scope'] == 'authenticated-bottle-cache-only-not-execution', 'invalid cache receipt')
        require(receipt['schema_version'] == 1 and receipt['inputs'] == identity and receipt['library_directories'] == directories, 'cache input mismatch')
        require(json.loads((output / 'paths.json').read_text()) == expected_paths and inventory(output, True) == receipt['inventory'], 'cache inventory mismatch')
        verify(output, manifest)
        return output
    if not fetch:
        raise FileNotFoundError('BLOCKED: hosted-tools cache missing; use explicit --fetch')
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.hosted-tools-', dir=build) as temporary:
        stage = Path(temporary) / 'payload'
        stage.mkdir()
        budget = [0, 0]
        for row in manifest['archives']:
            archive = Path(temporary) / 'bottle.tar.gz'
            download(row, archive)
            extract(archive, row, stage, budget, manifest['excluded_symlinks'], manifest['materialized_symlinks'])
            archive.unlink()
        for row in manifest['normalized_macho']:
            regular(stage / row['path'])
            if row.get('signature_policy') == 'asan-arm64-rpath':
                require(regular(stage / row['path']).st_size == row['source_bytes'] and digest(stage / row['path']) == row['source_sha256'], 'ASan source SHA/size mismatch')
            options = ['--dylib-id', row['dylib_id']] if 'dylib_id' in row else []
            if 'signature_policy' in row:
                options += ['--signature-policy', row['signature_policy']]
            result = subprocess.run([ruby, str(ROOT / manifest['normalizer']['path']), *options, str(stage / row['path'])], capture_output=True, text=True, timeout=120)
            diagnostic = result.stdout.replace(str(stage) + '/', '').replace(str(ROOT) + '/', '')[:240]
            require(result.returncode == 0 and json.loads(result.stdout)['status'] == 'PASS', f'data normalization failed: {row["path"]}: {diagnostic}')
        for name, item in manifest['materialized_symlinks'].items():
            regular(stage / item['source'])
            require(not os.path.lexists(stage / name), f'materialized symlink path already exists: {name}')
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / item['source'], stage / name)
        for alias, source in manifest['aliases'].items():
            target = stage / alias
            if target.is_symlink() or target.is_file():
                target.unlink()
            shutil.copy2(stage / source, target)
        verify(stage, manifest)
        (stage / 'paths.json').write_text(json.dumps(expected_paths, indent=2) + '\n')
        receipt = {'schema_version': 1, 'scope': 'authenticated-bottle-cache-only-not-execution', 'inputs': identity,
                   'library_directories': directories, 'inventory': inventory(stage, True)}
        (stage / 'cache.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        require(not output.exists() and not output.is_symlink(), 'output appeared during preparation')
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--ruby', default='ruby')
    args = parser.parse_args()
    try:
        print(prepare(args.fetch, args.ruby))
        return 0
    except (OSError, EOFError, ValueError, KeyError, TypeError, RuntimeError, zlib.error, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f'hosted-tools: {error}')
        return 2 if isinstance(error, FileNotFoundError) else 1


if __name__ == '__main__':
    raise SystemExit(main())
