#!/usr/bin/env python3
"""Verify an offline NuGet feed; only --fetch prepares missing input archives."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = {f'{folder}/{name}' for folder, project in (
    ('integration/bizhawk/core', 'Puae6.Core'), ('integration/bizhawk/smoke', 'Smoke'),
    ('tests/ffi', 'Ffi')) for name in (project + '.csproj', 'global.json')}
MAX_PACKAGE, MAX_TOTAL = 64 << 20, 128 << 20

class InputError(ValueError):
    pass

class MissingFeed(InputError):
    pass

def safe_path(root, relative):
    if root.is_symlink() or not root.is_dir():
        raise InputError('project root must be a real directory')
    path = root
    for part in Path(relative).parts:
        if part in ('..', '.') or '/' in part or '\\' in part:
            raise InputError('invalid project-relative path')
        path /= part
        if path.is_symlink():
            raise InputError('symlink input/output path')
        if path.exists() and path.stat().st_uid != os.getuid():
            raise InputError('project input/output must belong to current user')
    return path

def regular(path, limit):
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_uid != os.getuid() or not 0 < observed.st_size <= limit:
        raise InputError('expected owned regular unlinked file within size bound')
    return observed

def manifest(root):
    path = safe_path(root, 'tools/ci/managed-inputs.json')
    regular(path, 1 << 20)
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'scope', 'sdk_version', 'target_framework', 'source_files', 'hash_definition', 'packages'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or data['sdk_version'] != '10.0.402' or data['target_framework'] != 'net8.0'
            or any(not isinstance(data[k], str) or not data[k] for k in ('scope', 'hash_definition'))
            or not isinstance(data['source_files'], dict) or set(data['source_files']) != SOURCE_FILES):
        raise InputError('unsupported managed input schema/profile/source selection')
    for name, digest in data['source_files'].items():
        source = safe_path(root, name)
        regular(source, 1 << 20)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise InputError('managed project/global.json differs from pinned inputs')
    packages = data['packages']
    if not isinstance(packages, list) or not 1 <= len(packages) <= 64:
        raise InputError('invalid package count')
    found, total = set(), 0
    for item in packages:
        if not isinstance(item, dict) or set(item) != {'id', 'version', 'bytes', 'archive_sha512_base64', 'url', 'catalog'}:
            raise InputError('invalid package fields')
        identity, version = item['id'], item['version']
        if (not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', identity) or len(identity) > 100
                or not isinstance(version, str) or len(version) > 40 or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) or identity in found):
            raise InputError('invalid or duplicate package identity/version')
        found.add(identity)
        filename = f'{identity}.{version}.nupkg'
        if (item['url'] != f'https://api.nuget.org/v3-flatcontainer/{identity}/{version}/{filename}'
                or not isinstance(item['catalog'], str) or not re.fullmatch(r'https://api\.nuget\.org/v3/catalog0/data/[0-9]{4}(?:\.[0-9]{2}){5}/' + re.escape(f'{identity}.{version}.json'), item['catalog'])):
            raise InputError('package URL/catalog must be the fixed official HTTPS resource')
        try:
            digest = base64.b64decode(item['archive_sha512_base64'], validate=True)
            if len(digest) != 64 or base64.b64encode(digest).decode() != item['archive_sha512_base64']:
                raise ValueError()
        except (ValueError, TypeError):
            raise InputError('invalid archive SHA512 Base64') from None
        if type(item['bytes']) is not int or not 0 < item['bytes'] <= MAX_PACKAGE:
            raise InputError('package size exceeds bound')
        total += item['bytes']
    if total > MAX_TOTAL:
        raise InputError('feed size exceeds bound')
    return packages

def verify_feed(feed, packages):
    expected = {f"{p['id']}.{p['version']}.nupkg": p for p in packages}
    if not feed.is_dir() or set(p.name for p in feed.iterdir()) != set(expected):
        raise InputError('existing feed has a different inventory; refusing replacement')
    for name, item in expected.items():
        path = feed / name
        stat = regular(path, item['bytes'])
        digest = hashlib.sha512()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(65536), b''):
                digest.update(block)
        if stat.st_size != item['bytes'] or base64.b64encode(digest.digest()).decode() != item['archive_sha512_base64']:
            raise InputError('existing feed archive size/SHA512 mismatch; refusing replacement')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InputError('redirects are not permitted for pinned package URLs')

def prepare(root=ROOT, *, fetch=False, opener=None):
    root = Path(root).absolute()
    packages = manifest(root)  # Validate every pin and project before any network request.
    feed = safe_path(root, 'build/hosted-managed-feed')
    if feed.exists():
        verify_feed(feed, packages)
    elif not fetch:
        raise MissingFeed('managed feed missing; explicitly run tools/ci/prepare_managed_inputs.py --fetch')
    else:
        feed.parent.mkdir(exist_ok=True)
        opener = opener or urllib.request.build_opener(NoRedirect()).open
        with tempfile.TemporaryDirectory(prefix='.managed-inputs-', dir=feed.parent) as temp:
            staged = Path(temp) / 'feed'
            staged.mkdir()
            for item in packages:
                target = staged / f"{item['id']}.{item['version']}.nupkg"
                digest, count, deadline = hashlib.sha512(), 0, time.monotonic() + 300
                with opener(item['url'], timeout=30) as response, target.open('xb') as output:
                    if response.status != 200 or response.geturl() != item['url']:
                        raise InputError('unexpected package HTTP status/URL')
                    if response.headers.get('Content-Length') is not None and int(response.headers['Content-Length']) != item['bytes']:
                        raise InputError('HTTP package size differs from pin')
                    while True:
                        block = response.read(min(65536, item['bytes'] - count + 1))
                        if not block:
                            break
                        count += len(block)
                        if count > item['bytes'] or time.monotonic() > deadline:
                            raise InputError('package stream exceeds size/time bound')
                        digest.update(block)
                        output.write(block)
                if count != item['bytes'] or base64.b64encode(digest.digest()).decode() != item['archive_sha512_base64']:
                    raise InputError('downloaded package size/SHA512 differs from pin')
            verify_feed(staged, packages)
            if safe_path(root, 'build/hosted-managed-feed').exists():
                raise InputError('feed appeared during preparation; refusing replacement')
            staged.rename(feed)
    return {'status': 'PASS', 'scope': 'managed-input-archives-only', 'feed': str(feed),
            'packages': len(packages), 'bytes': sum(p['bytes'] for p in packages)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='explicitly download the pinned missing feed')
    args = parser.parse_args()
    try:
        result, code = prepare(fetch=args.fetch), 0
    except Exception as error:
        code = 2 if isinstance(error, MissingFeed) else 1
        result = {'status': 'BLOCKED' if code == 2 else 'FAIL', 'scope': 'managed-input-archives-only', 'diagnostic': str(error)}
    print(json.dumps(result, sort_keys=True))
    return code

if __name__ == '__main__':
    raise SystemExit(main())
