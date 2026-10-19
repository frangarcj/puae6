#!/usr/bin/env python3
"""Build a local native preview with corresponding sources and an isolated smoke."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'manifests/release/native-preview.json'
PRODUCTS = ('puae6_libretro.dylib', 'p6_headless', 'p6_libretro_host')


def run(argv, **kwargs):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=1200, **kwargs)
    if p.returncode:
        raise RuntimeError(p.stdout[-1500:] + p.stderr[-2500:])
    return p.stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_revision():
    if run(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=ROOT).strip():
        raise ValueError('Commit the public sources before packaging')
    return run(['git', 'rev-parse', 'HEAD'], cwd=ROOT).strip()


def source_paths():
    paths = json.loads(POLICY.read_text())['source_paths']
    files = run(['git', 'ls-files', '--', *paths], cwd=ROOT).splitlines()
    if not files or any(Path(p).is_absolute() or '..' in Path(p).parts for p in files):
        raise ValueError('Invalid source selection')
    if any(p.lower().endswith(('.rom', '.hdf', '.adf', '.ipf', '.lha', '.wbx')) for p in files):
        raise ValueError('Disk/firmware images do not belong in the source package')
    return paths


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    expected = set(manifest['files']) | {'manifest.json'}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual != expected or any(p.is_symlink() for p in directory.rglob('*')):
        raise ValueError('Unexpected, missing or linked package files')
    for name, digest in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(directory / name) != digest:
            raise ValueError('Package file changed: ' + name)
    for name in PRODUCTS:
        binary = directory / 'bin' / name
        if 'arm64' not in run(['file', '-b', binary]):
            raise ValueError('Wrong package architecture')
        for line in run(['otool', '-L', binary]).splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if not dependency.startswith('/usr/lib/') and dependency != '@rpath/puae6_libretro.dylib':
                raise ValueError('Non-system dependency: ' + dependency)
    with tarfile.open(directory / 'source.tar.gz') as archive:
        members = {m.name for m in archive.getmembers() if m.isfile()}
    if 'source/tools/package.py' not in members or any('/whdload/' in p and p.endswith('.hdf') for p in members):
        raise ValueError('Incomplete or private source payload')
    return manifest


def smoke(directory):
    """Exercise only extracted binaries and the public micro-ROM sources shipped with them."""
    directory = Path(directory).resolve()
    verify(directory)
    with tempfile.TemporaryDirectory(prefix='p6-installed-') as temporary:
        work = Path(temporary)
        with tarfile.open(directory / 'source.tar.gz') as archive:
            for name in ('build_rom.py', 'boot.s'):
                member = archive.extractfile('source/tests/fixtures/microrom/' + name)
                (work / name).write_bytes(member.read())
        spec = importlib.util.spec_from_file_location('package_microrom', work / 'build_rom.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        system = work / 'system'
        system.mkdir()
        rom = system / 'kick34005.A500'
        rom.write_bytes(module.build((work / 'boot.s').read_text()))
        host, core = (directory / 'bin' / n for n in ('p6_libretro_host', 'puae6_libretro.dylib'))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_LIBRARY_PATH'))}
        output = run([host, core, '--system', system, '--runs', '12', '--do', '6:save:s', '--do', '9:load:s'], cwd=work, env=env)
        rows = [line.split(' ', 2)[2] for line in output.splitlines() if line.startswith('run ')]
        if len(rows) != 12 or rows[6:9] != rows[9:12] or 'state load s exact ok=1' not in output or 'VIOLATION' in output:
            raise RuntimeError('Installed libretro advance/restore failed:\n' + output[-2000:])
        empty = work / 'empty-system'
        empty.mkdir()
        missing = subprocess.run([str(host), str(core), '--system', str(empty)], cwd=work, env=env,
                                 capture_output=True, text=True, timeout=60)
        if missing.returncode != 3 or 'no Kickstart' not in missing.stdout + missing.stderr:
            raise RuntimeError('Installed core did not reject missing firmware')
        headless = run([directory / 'bin/p6_headless', '--run', rom, '--units', '3'], cwd=work, env=env)
        if len([line for line in headless.splitlines() if line.startswith('unit ')]) != 3:
            raise RuntimeError('Installed headless did not advance')
    return {'libretro_units': 12, 'restored_units_compared': 3, 'headless_units': 3, 'missing_firmware_rejected': True}


def unpack(archive, directory):
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path = Path(info.filename)
            if path.is_absolute() or '..' in path.parts or info.is_dir():
                raise ValueError('Invalid preview archive member')
            target = Path(directory) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            target.chmod((info.external_attr >> 16) & 0o777)


def build(output, tree):
    if (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
        raise ValueError('This preview requires the native macOS ARM64 host')
    revision = clean_revision()
    paths = source_paths()
    tree = Path(tree).resolve()
    tree.relative_to(ROOT / 'build')
    run(['cmake', '-S', ROOT, '-B', tree, '-G', 'Ninja', '-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/macos-appleclang.cmake',
         '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_FLAGS_RELEASE=-O2', '-DCMAKE_CXX_FLAGS_RELEASE=-O2',
         '-DCMAKE_INSTALL_NAME_DIR=@rpath', '-DCMAKE_BUILD_WITH_INSTALL_NAME_DIR=ON',
         '-DP6_NATIVE_IPO=OFF', '-DP6_PGO_MODE=OFF', '-DP6_SANITIZERS=OFF'], cwd=ROOT)
    run(['cmake', '--build', tree, '--parallel', '2', '--target', 'puae6_libretro', 'p6_headless', 'p6_libretro_host'], cwd=ROOT)
    policy = json.loads(POLICY.read_text())
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('Output already exists; choose a new filename')
    epoch = int(run(['git', 'show', '-s', '--format=%ct', revision], cwd=ROOT).strip())
    with tempfile.TemporaryDirectory(prefix='p6-package-', dir=ROOT / 'build') as temporary:
        stage = Path(temporary)
        (stage / 'bin').mkdir()
        for name in PRODUCTS:
            shutil.copy2(tree / name, stage / 'bin' / name)
        (stage / 'licenses').mkdir()
        for name, original in policy['licenses'].items():
            shutil.copyfile(ROOT / original, stage / 'licenses' / name)
        shutil.copyfile(ROOT / 'docs/user/native-preview.md', stage / 'LEEME.md')
        with (stage / 'source.tar.gz').open('wb') as stream:
            subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=source/', revision, '--', *paths],
                           cwd=ROOT, stdout=stream, check=True, timeout=120)
        manifest = {'schema_version': 1, 'kind': 'local-preview', 'platform': policy['platform'], 'revision': revision,
                    'compiler': run(['/usr/bin/clang', '--version']).splitlines()[0],
                    'headless_identity': run([tree / 'p6_headless', '--identity']),
                    'binary_reproducibility_verified': False,
                    'files': {p.relative_to(stage).as_posix(): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        if clean_revision() != revision:
            raise RuntimeError('Sources changed during packaging')
        files = [path for path in sorted(stage.rglob('*')) if path.is_file()]
        candidate = stage / 'preview.zip'
        with zipfile.ZipFile(candidate, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), time.gmtime(max(epoch, 315532800))[:6])
                info.external_attr = (0o755 if path.parent.name == 'bin' else 0o644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        with tempfile.TemporaryDirectory(prefix='p6-preview-extracted-') as temporary:
            unpack(candidate, temporary)
            result = smoke(temporary)
        candidate.replace(output)
    return {'archive': str(output), 'revision': revision, 'sha256': sha(output), 'smoke': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'check'))
    parser.add_argument('--archive', type=Path, default=ROOT / 'build/product/puae6-preview-macos-arm64.zip')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/native-release')
    args = parser.parse_args()
    if args.command == 'build':
        result = build(args.archive, args.build_dir)
    else:
        with tempfile.TemporaryDirectory(prefix='p6-preview-check-') as temporary:
            unpack(args.archive, temporary)
            result = smoke(temporary)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
