#!/usr/bin/env python3
"""Explicit offline extraction of the source-rebuilt Waterbox sysroot bundle."""
import argparse
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = 'third_party/waterbox-sysroot'
ARCHIVE = 'waterbox-sysroot-aarch64.tar.xz'
MAX_BYTES, MAX_FILES, MAX_ARCHIVE = 64 * 1024 * 1024, 1276, 4 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def no_links(root, path):
    require(path.is_relative_to(root) and '..' not in path.parts, 'path outside project')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'symlink in path')
        if part == root:
            break


def source_pins(root):
    no_links(root, root / 'plan/upstream.lock.json')
    lock = json.loads((root / 'plan/upstream.lock.json').read_text())
    pins = {}
    for source in lock['sources']:
        if source['id'] in ('machawk', 'waterbox_musl_darwin', 'compiler_rt_emutls'):
            pin = {k: source.get(k) for k in ('commit', 'repository', 'tag', 'tag_object')}
            if 'snapshot' in source:
                path = root / source['snapshot']['manifest']
                no_links(root, path)
                pin['snapshot_manifest_sha256'] = digest(path.read_bytes())
            pins[source['id']] = pin
    require(len(pins) == 3, 'missing source pins')
    return pins


def entries(directory):
    result, directories, total = [], set(), 0
    require(directory.is_dir() and not directory.is_symlink(), 'missing or linked directory')
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            path = Path(parent) / name
            require(not path.is_symlink(), 'linked directory')
            directories.add(path.relative_to(directory).as_posix())
        for name in files:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            require(stat.S_ISREG(mode) and stat.S_IMODE(mode) in (0o644, 0o755), 'invalid file type or mode')
            total += path.stat().st_size
            require(total <= MAX_BYTES and len(result) < MAX_FILES, 'existing inventory limits')
            result.append(dict(path=path.relative_to(directory).as_posix(),
                               mode='100755' if mode & 0o111 else '100644', sha256=digest(path.read_bytes())))
    wanted = {p.as_posix() for e in result for p in PurePosixPath(e['path']).parents if p.as_posix() != '.'}
    require(directories == wanted, 'extra empty directories')
    return sorted(result, key=lambda e: e['path'])


def unpack(data, stage, provenance):
    decoder = lzma.LZMADecompressor(memlimit=MAX_BYTES)
    raw = decoder.decompress(data, max_length=MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES and decoder.eof and not decoder.unused_data, 'decoded size/truncation/extra stream')
    seen, total = set(), 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            name, path = member.name, PurePosixPath(member.name)
            require(len(seen) < MAX_FILES, 'file count limit')
            require(len(path.parts) >= 2 and path.parts[0] in ('sysroot', 'licenses') and
                    path.as_posix() == name and not path.is_absolute() and
                    '..' not in path.parts and '\\' not in name and '\0' not in name, 'noncanonical archive path')
            require(name not in seen, 'duplicate archive path')
            require(member.type == tarfile.REGTYPE and not member.linkname and not member.pax_headers and
                    member.offset_data == member.offset + 512, 'unsupported archive type/header')
            require(member.mode in (0o644, 0o755), 'unsupported archive mode')
            total += member.size
            require(0 <= member.size <= MAX_BYTES and total <= MAX_BYTES, 'payload size limit')
            seen.add(name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                payload = archive.extractfile(member).read(member.size + 1)
                require(len(payload) == member.size, 'truncated member')
                stream.write(payload)
            target.chmod(member.mode)
        require(len(raw) - archive.offset >= 1024 and not any(raw[archive.offset:]), 'missing tar terminator or trailing tar data')
    require((len(seen), total) == (provenance['archive']['files'], provenance['archive']['payload_bytes']), 'archive inventory counts')


def prepare(root=ROOT, *, output=None, notices=None):
    root = Path(root).absolute()
    bundle = root / BUNDLE
    no_links(root, bundle / 'PROVENANCE.json')
    provenance = json.loads((bundle / 'PROVENANCE.json').read_text())
    no_links(root, root / 'plan/toolchains.json')
    lock = json.loads((root / 'plan/toolchains.json').read_text())
    rebuild = lock['waterbox_sysroot_rebuild']
    recipe = 'tools/build_waterbox_sysroot.py'
    no_links(root, root / recipe)
    require(provenance['schema_version'] == 1 and provenance['sources'] == source_pins(root), 'provenance source pins differ')
    require(provenance['sysroot'] == {k: lock['sysroot'][k] for k in ('inventory_sha256', 'file_count')}, 'sysroot pin differs')
    require(provenance['rebuild'] == dict(recipe=recipe, recipe_sha256=rebuild['recipe_sha256'],
            image_id=rebuild['image_id'], llvm_closure_inventory_sha256=rebuild['llvm_closure_inventory_sha256']) and
            digest((root / recipe).read_bytes()) == rebuild['recipe_sha256'], 'rebuild recipe/pins differ')
    require(provenance['limits'] == dict(decoded_bytes=MAX_BYTES, files=MAX_FILES) and
            provenance['archive']['files'] == MAX_FILES and provenance['archive']['file'] == ARCHIVE and len(provenance['notices']) == 6 and
            provenance['sysroot']['file_count'] + len(provenance['notices']) == MAX_FILES and
            canonical(provenance['notices']) == provenance['notices_inventory_sha256'], 'provenance limits/notices differ')
    archive = bundle / ARCHIVE
    no_links(root, archive)
    size = provenance['archive']['size_bytes']
    require(0 < size <= MAX_ARCHIVE and archive.stat().st_size == size, 'archive size differs')
    with archive.open('rb') as stream:
        data = stream.read(size + 1)
    require(len(data) == size and digest(data) == provenance['archive']['sha256'], 'archive SHA differs')
    outputs = [Path(output or lock['sysroot']['source_path']), Path(notices or rebuild['notices_path'])]
    outputs = [p if p.is_absolute() else root / p for p in outputs]
    build = root / 'build'
    for path in outputs:
        no_links(root, path)
        require('..' not in path.parts and path.is_relative_to(build) and path != build, 'output must be below build/')
    require(not any(a.is_relative_to(b) for a, b in (outputs, outputs[::-1])), 'overlapping outputs')
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sysroot-bundle-', dir=build) as temporary:
        stage = Path(temporary)
        unpack(data, stage, provenance)
        expected = [entries(stage / name) for name in ('sysroot', 'licenses')]
        require(len(expected[0]) == lock['sysroot']['file_count'] and canonical(expected[0]) == lock['sysroot']['inventory_sha256'], 'sysroot inventory differs')
        require(expected[1] == provenance['notices'], 'notices inventory differs')
        # Check BOTH destinations before publishing either; never repair another tree.
        for path, inventory in zip(outputs, expected):
            if path.exists():
                require(entries(path) == inventory, 'existing output differs: ' + str(path))
        for path, name in zip(outputs, ('sysroot', 'licenses')):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                (stage / name).rename(path)
    return dict(status='PASS', sysroot=str(outputs[0]), notices=str(outputs[1]), inventory_sha256=lock['sysroot']['inventory_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--notices', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(output=args.output, notices=args.notices), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, lzma.LZMAError, tarfile.TarError) as error:
        print('FAIL: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
