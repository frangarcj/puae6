#!/usr/bin/env python3
"""Package existing Release Waterbox artifacts; never build, restore or fetch."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from tools import package, waterbox_package_policy as policy, build_waterbox as wb
from tools import import_sources, materialize_upstream as materialize, generate_cpu

BINS = ('Ffi.dll', 'Ffi.deps.json', 'Ffi.runtimeconfig.json', 'Puae6.Core.dll',
        'CommunityToolkit.HighPerformance.dll', 'Newtonsoft.Json.dll', 'libwaterboxhost.dylib', 'puae6.wbx')
PATHS = {'ffi': 'bin/Ffi.dll', 'guest': 'bin/puae6.wbx', 'runtime': 'bin/libwaterboxhost.dylib', 'memory': 'config/waterbox-memory.json'}
sha, run, require = package.sha, package.run, policy.require
class RunnerMissing(FileNotFoundError): pass


def environment():
    return {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_', 'P6_', 'NUGET_', 'DOTNET_ADDITIONAL', 'DOTNET_SHARED', 'DOTNET_STARTUP'))}


def ffi(directory, *args, cwd=None):
    dotnet = shutil.which('dotnet')
    if not dotnet or not re.search(r'Microsoft.NETCore.App (?:[89]|[1-9][0-9]+)\.', run([dotnet, '--list-runtimes'], env=environment())):
        raise RunnerMissing('An installed compatible ARM64 .NET runtime is required')
    return run([dotnet, Path(directory) / PATHS['ffi'], *args], cwd=cwd or directory, env=environment())


def managed(directory, guest_sha):
    directory = Path(directory)
    deps = json.loads((directory / 'Ffi.deps.json').read_bytes())
    versions = {'Ffi': '1.0.0', 'Puae6.Core': '1.0.0', 'CommunityToolkit.HighPerformance': '8.4.0', 'Newtonsoft.Json': '13.0.3'}
    expected = {n + '/' + v for n, v in versions.items()}
    require(set(deps['targets']) == {'.NETCoreApp,Version=v8.0'} and deps['runtimeTarget']['name'] == '.NETCoreApp,Version=v8.0'
            and set(deps['libraries']) == expected and not deps['compilationOptions'], 'Unexpected managed closure')
    target = deps['targets'][deps['runtimeTarget']['name']]
    require(set(target) == expected, 'Missing managed dependency')
    for key, entry in target.items():
        name = key.split('/')[0]
        require(set(entry) <= {'dependencies', 'runtime'} and len(entry['runtime']) == 1, 'Unsupported managed dependency kind')
        path = next(iter(entry['runtime']))
        require(not Path(path).is_absolute() and '..' not in Path(path).parts and '\\' not in path
                and Path(path).name == name + '.dll' and (directory / (name + '.dll')).is_file(), 'Managed dependency path')
        graph = {'Ffi': {'Puae6.Core': '1.0.0'}, 'Puae6.Core': {n: versions[n] for n in ('CommunityToolkit.HighPerformance', 'Newtonsoft.Json')}}
        require(entry.get('dependencies', {}) == graph.get(name, {})
                and deps['libraries'][key]['type'] == ('project' if name in graph else 'package'), 'Managed dependency graph')
    config = json.loads((directory / 'Ffi.runtimeconfig.json').read_bytes())['runtimeOptions']
    require(set(config) <= {'tfm', 'rollForward', 'framework', 'configProperties'} and config['tfm'] == 'net8.0'
            and config['rollForward'] == 'Major' and config['framework'] == {'name': 'Microsoft.NETCore.App', 'version': '8.0.0'}, 'Hidden managed probing/framework')
    require(ffi(directory.parent, 'pin').strip() == 'pin ' + guest_sha, 'Managed guest pin differs; rebuild Release explicitly')


def binaries(directory, manifest):
    directory = Path(directory); guest = directory / PATHS['guest']; runtime = directory / PATHS['runtime']
    require(sha(guest) == manifest['guest_sha256'] and sha(runtime) == manifest['runtime_sha256'], 'Artifact identity differs')
    wb.validate_elf(guest); wb.check_no_compiler_tls(guest)
    require(runtime.read_bytes()[:16] == struct.pack('<IIII', 0xFEEDFACF, 0x100000C, 0, 6), 'Runtime must be Mach-O ARM64 dylib')
    for line in run(['otool', '-L', runtime]).splitlines()[1:]:
        dep = line.strip().split(' (')[0]
        require(dep == '@rpath/libwaterboxhost.dylib' or (dep.startswith('/usr/lib/') and '..' not in Path(dep).parts), 'Non-system runtime dependency')
    managed(directory / 'bin', manifest['guest_sha256'])


def preflight(managed_dir, managed_receipt):
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Waterbox preview requires macOS ARM64')
    require('Release' in Path(managed_dir).parts and 'Debug' not in Path(managed_dir).parts, 'Explicit Release managed directory required')
    for path in [Path(managed_receipt), *(Path(managed_dir) / n for n in BINS[:6])]: import_sources.no_links(ROOT, path)
    lock = wb.selected_toolchain(); receipt = json.loads((wb.BUILD / 'puae6/puae6-receipt.json').read_bytes())
    wb.check_puae6_guest_abi(receipt, root=ROOT)
    guest_abi = receipt['guest_abi']
    from tools.ci.managed_release import verify_receipt
    managed_build = verify_receipt(managed_receipt, managed_dir, receipt['guest']['sha256'], root=ROOT)
    runtime = wb.read_runtime_receipt(wb.runtime_receipt_path('release'))
    require(runtime['runtime_profile'] == 'release', 'Release runtime required')
    reference = json.loads((ROOT / 'references/machawk.manifest.json').read_bytes())
    runtime_sources = {n['path'][len('waterbox/waterboxhost/'):]: dict(n, path=n['path'][len('waterbox/waterboxhost/'):])
                       for n in reference['files'] if n['path'].startswith('waterbox/waterboxhost/')}
    for patch in lock['runtime'].get('patches', []):
        for entry in patch['files']: runtime_sources[entry['path']]['sha256'] = entry['after_sha256']
    require(wb.inventory(wb.BUILD / 'runtime-src') == sorted(runtime_sources.values(), key=lambda n: n['path']), 'Runtime source capture changed')
    pristine = import_sources.verify_source(ROOT); series, series_sha = materialize._series(ROOT, pristine['source']['commit'])
    expected = materialize._expected_receipt(ROOT, pristine, series, series_sha)
    require(import_sources.inventory(ROOT / 'build/upstream') == expected['files'], 'Stale patched engine')
    recipe = json.loads((ROOT / 'manifests/codegen.json').read_bytes())
    generated = generate_cpu._cached_output(ROOT / 'build/generated', [n for g in recipe['generators'] for n in g['outputs']])
    if generated is None: raise FileNotFoundError('Missing verified build/generated')
    require(all(generated['identity'][k] == v for k, v in {'upstream_commit': expected['upstream_commit'], 'series': series_sha,
            'recipe': sha(ROOT / 'manifests/codegen.json'), 'source_manifest': sha(ROOT / 'manifests/source-manifest.json')}.items()), 'Stale generated engine')
    sources = wb.puae6_sources(); manual = wb.puae6_manual_inputs(root=ROOT)
    require(receipt['manual_inputs'] == manual and receipt['build_inputs']['manual_inputs'] == manual, 'Guest manual headers/sources changed')
    require(set(receipt['sources']) == set(sources) and all(sha(ROOT / n) == receipt['sources'][n] for n in sources), 'Guest sources changed')
    recipes = {'makefile': 'ports/waterbox/Makefile', 'common_mak': 'references/machawk/waterbox/common.mak',
               'linkscript_aarch64': 'references/machawk/waterbox/linkscript_aarch64.T', 'build_recipe': 'tools/build_waterbox.py'}
    inputs = {'profile': lock['profile'], 'toolchain_profile': lock['toolchain_profile'],
              'recipe': {k: sha(ROOT / n) for k, n in recipes.items()}, 'sources': sources,
              'tools': {k: v['sha256'] for k, v in lock['tools'].items()}, 'versions': {k: v['version'] for k, v in lock['tools'].items()},
              'flags': {'optimization': '-O2', 'target': 'aarch64', 'host_page_size': 16384}, 'sysroot': guest_abi['inventory_sha256'],
              'guest_abi': guest_abi,
              'cc_wrapper': sha(wb.BUILD / 'guest-toolchain/bin/musl-clang'), 'ld_wrapper': sha(wb.BUILD / 'guest-toolchain/bin/ld.musl-clang')}
    require(all(receipt['build_inputs'].get(k) == v for k, v in inputs.items()), 'Guest build recipe/toolchain changed')
    guest = wb.BUILD / 'puae6/release/puae6.wbx'; runtime_path = Path(runtime['runtime_dylib']['path'])
    for path in (guest, runtime_path): import_sources.no_links(ROOT, path)
    require(sha(guest) == receipt['guest']['sha256'] and sha(runtime_path) == runtime['runtime_dylib']['sha256'], 'Receipt artifact mismatch')
    return guest, runtime_path, {'guest_sha256': sha(guest), 'runtime_sha256': sha(runtime_path), 'guest_profile': inputs['flags'], 'guest_manual_inputs': manual,
                                 'managed_inputs': managed_build['inputs'], 'managed_outputs': managed_build['outputs'], 'managed_sdk_version': managed_build['sdk_version']}


def source_links(members, read):
    expected = {'source/references/machawk/' + n['path']: n['sha256'] for n in json.loads(read('references/machawk.manifest.json'))['files'] if n['mode'] == '120000'}
    links = {n.name: n for n in members if n.issym()}
    require(set(links) == set(expected), 'Missing/extra captured source link')
    for name, link in links.items():
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link.linkname))
        require(not link.linkname.startswith('/') and '\\' not in link.linkname and target.startswith('source/references/machawk/')
                and policy.digest(link.linkname.encode()) == expected[name], 'Changed/escaping captured source link')


def verify(directory):
    directory = Path(directory); require((directory / 'manifest.json').is_file(), 'Missing preview manifest')
    m = json.loads((directory / 'manifest.json').read_bytes())
    require(set(m) == {'schema_version', 'kind', 'paths', 'platform', 'revision', 'managed_configuration', 'binary_reproducibility_verified',
                       'guest_sha256', 'runtime_sha256', 'guest_profile', 'guest_manual_inputs', 'managed_inputs', 'managed_outputs', 'managed_sdk_version', 'files'}
            and m['guest_profile'] == {'optimization': '-O2', 'target': 'aarch64', 'host_page_size': 16384}
            and m['schema_version'] == 1 and m['kind'] == 'waterbox-standalone-preview' and m['paths'] == PATHS
            and m['platform'] == 'macos-arm64' and m['managed_configuration'] == 'Release'
            and m['binary_reproducibility_verified'] is False and re.fullmatch('[0-9a-f]{40}', m['revision']), 'Preview manifest profile')
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    require(not any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in directory.rglob('*'))
            and {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_dir()} == {'bin', 'config', 'licenses'}
            and actual == set(m['files']) | {'manifest.json'}, 'Unexpected/missing/linked payload')
    for name, digest in m['files'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and '\\' not in name and sha(directory / name) == digest, 'Changed payload')
    with tarfile.open(directory / 'source.tar.gz') as archive:
        members = archive.getmembers(); names = [n.name for n in members if n.isfile()]
        require(len(members) <= 50000 and len(members) == len({n.name for n in members}) and sum(n.size for n in members) <= 2**30
                and all(n.isfile() or n.isdir() or n.issym() for n in members)
                and all(not Path(n.name).is_absolute() and '..' not in Path(n.name).parts and '\\' not in n.name for n in members), 'Source archive type/duplicates/path')
        read = lambda p: archive.extractfile('source/' + p).read()
        require({'source/tests/fixtures/microrom/' + n for n in ('build_rom.py', 'boot.s')} <= set(names), 'Missing installed micro-ROM sources')
        composed = policy.compose(read=read)
        require(composed['rust_std_external_complete'] and not composed['rust_std_external_pending'], 'Incomplete source/notice policy')
        overlay = 'integration/waterbox-guest/0001-setjmp-x28.patch'
        required = {overlay, 'tools/build_waterbox_guest_sysroot.py', 'plan/toolchains.json', 'references/musl-darwin.manifest.json',
                    *('references/musl-darwin/src/setjmp/waterbox_aarch64/' + n + '.s' for n in ('setjmp', 'longjmp'))}
        require({'source/' + n for n in required} <= set(names), 'Missing derived sysroot recipe/patch/sources')
        pin = json.loads(read('plan/toolchains.json'))['waterbox_guest_sysroot']['patch']
        require(pin['file'] == overlay and policy.digest(read(overlay)) == pin['sha256'], 'Derived sysroot patch differs')
        captured = json.loads(read('references/musl-darwin.manifest.json'))['files']
        for name in ('setjmp', 'longjmp'):
            path = 'src/setjmp/waterbox_aarch64/' + name + '.s'; entries = [n for n in captured if n['path'] == path]
            require(len(entries) == 1 and entries[0]['mode'] == '100644'
                    and policy.digest(read('references/musl-darwin/' + path)) == entries[0]['sha256'], 'Derived sysroot captured ASM differs')
        source_links(members, read)
        require(m['managed_sdk_version'] == json.loads(read('tests/ffi/global.json'))['sdk']['version'], 'Managed SDK source differs')
        for name in names:
            path = name.removeprefix('source/')
            require(name.startswith('source/') and Path(name).as_posix() == name and '..' not in Path(path).parts and '\\' not in path
                    and any(path == n or path.startswith(n + '/') for n in composed['source_paths'])
                    and not path.lower().endswith(('.rom', '.wbx', '.hdf', '.adf', '.ipf', '.lha', '.dll', '.dylib', '.pdb')), 'Private/unselected source')
        notices = package.notice_files(composed, read)
        for inventory in (m['managed_inputs'], m['guest_manual_inputs']):
            for name, digest in inventory.items(): require(policy.digest(read(name)) == digest, 'Build source differs')
        for name, entry in m['managed_outputs'].items():
            require(name in BINS[:6] and sha(directory / 'bin' / name) == entry['sha256']
                    and (directory / 'bin' / name).stat().st_size == entry['bytes'], 'Managed Release output differs')
        require(set(m['managed_outputs']) == set(BINS[:6]), 'Incomplete managed Release receipt')
        require(set(m['files']) == {'bin/' + n for n in BINS} | {PATHS['memory'], 'source.tar.gz', 'LEEME.md'}
                | {'licenses/' + n for n in notices}, 'Wrong standalone closure')
        for n, data in notices.items(): require((directory / 'licenses' / n).read_bytes() == data, 'Changed original notice')
        require((directory / PATHS['memory']).read_bytes() == read('manifests/waterbox-memory.json')
                and (directory / 'LEEME.md').read_bytes() == read('docs/user/waterbox-preview.md'), 'Changed config/guide')
    binaries(directory, m)
    return m


def unpack(archive, directory):
    directory = Path(directory)
    require(not directory.is_symlink() and (not directory.exists() or not any(directory.iterdir())), 'Extraction requires an empty directory')
    with zipfile.ZipFile(archive) as z:
        names = [n.filename for n in z.infolist()]
        require(len(names) == len(set(names)) <= 64 and sum(n.file_size for n in z.infolist()) <= 2**30, 'Archive size/duplicates')
        require(all(not n.is_dir() and Path(n.filename).as_posix() == n.filename and '\\' not in n.filename
                    and ((n.external_attr >> 16) & 0o170000) in (0, 0o100000) for n in z.infolist()), 'Archive links/type')
    package.unpack(archive, directory)


def smoke(directory):
    require((platform.system(), platform.machine(), os.sysconf('SC_PAGE_SIZE')) == ('Darwin', 'arm64', 16384), 'Installed smoke requires macOS ARM64/16KiB')
    directory = Path(directory).resolve(); m = verify(directory)
    with tempfile.TemporaryDirectory(prefix='p6-wb-installed-') as temp:
        work = Path(temp)
        with tarfile.open(directory / 'source.tar.gz') as archive:
            (work / 'build_rom.py').write_bytes(archive.extractfile('source/tests/fixtures/microrom/build_rom.py').read())
            assembly = archive.extractfile('source/tests/fixtures/microrom/boot.s').read().decode()
        spec = importlib.util.spec_from_file_location('installed_microrom', work / 'build_rom.py'); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        (work / 'boot.rom').write_bytes(module.build(assembly))
        def session(lines):
            (work / 'session.txt').write_text('\n'.join(lines) + '\n')
            return ffi(directory, 'session', '--runtime', directory / PATHS['runtime'], '--guest', directory / PATHS['guest'],
                       '--sha', m['guest_sha256'], '--firmware', work / 'boot.rom', '--program', 'session.txt', cwd=work)
        first = session(['core A', 'run A 3', 'save A s', 'savefile A state.bin', 'run A 4', 'digest A first',
                         'load A s', 'run A 4', 'digest A replay', 'core B seed=2', 'run B 1', 'digest B before', 'load B s', 'digest B after'])
        later = session(['core C', 'loadfile C state.bin', 'run C 4', 'digest C fresh'])
        units = lambda out, name: [s.split(' ', 2)[2] for s in out.splitlines() if s.startswith('u ' + name + ' ')]
        digests = lambda out: dict(re.findall(r'digest \w+ (\w+) sha=([0-9a-f]{64})', out))
        a, c, d, f = units(first, 'A'), units(later, 'C'), digests(first), digests(later)
        require(len(a) == 11 and len(c) == 4 and a[3:7] == a[7:] == c and d['first'] == d['replay'] == f['fresh'], 'Installed restore/advance differs')
        require('loaded A s' in first and 'loaded C state.bin' in later and 'rejected B s reason=OtherConfig' in first
                and d['before'] == d['after'], 'Incompatible state rejection changed the machine')
    return {'same_sandbox_units': 4, 'new_process_units': 4, 'foreign_config_unchanged': True}


def build(output, managed_dir, managed_receipt):
    revision = package.clean_revision(); composed = policy.compose()
    require(composed['rust_std_external_complete'] and not composed['rust_std_external_pending'], 'Incomplete distribution policy')
    guest, runtime, ids = preflight(managed_dir, managed_receipt)
    require(not Path(output).is_symlink(), 'Output must not be a link'); output = Path(output).resolve(); output.relative_to(ROOT / 'build')
    require(not output.exists(), 'Output already exists'); output.parent.mkdir(parents=True, exist_ok=True)
    epoch = int(run(['git', 'show', '-s', '--format=%ct', revision], cwd=ROOT))
    with tempfile.TemporaryDirectory(prefix='wb-package-', dir=output.parent) as temp:
        stage = Path(temp); (stage / 'bin').mkdir(); (stage / 'config').mkdir(); (stage / 'licenses').mkdir()
        for n in BINS: shutil.copyfile(guest if n == 'puae6.wbx' else runtime if n == 'libwaterboxhost.dylib' else Path(managed_dir) / n, stage / 'bin' / n)
        shutil.copyfile(ROOT / 'manifests/waterbox-memory.json', stage / PATHS['memory'])
        shutil.copyfile(ROOT / 'docs/user/waterbox-preview.md', stage / 'LEEME.md')
        for n, data in package.notice_files(composed, lambda p: (ROOT / p).read_bytes()).items(): (stage / 'licenses' / n).write_bytes(data)
        with (stage / 'source.tar.gz').open('wb') as stream:
            subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=source/', revision, '--', *composed['source_paths']], cwd=ROOT, stdout=stream, check=True, timeout=180)
        manifest = {'schema_version': 1, 'kind': 'waterbox-standalone-preview', 'platform': 'macos-arm64', 'revision': revision,
                    'paths': PATHS, 'managed_configuration': 'Release', 'binary_reproducibility_verified': False, **ids,
                    'files': {p.relative_to(stage).as_posix(): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        verify(stage); require(package.clean_revision() == revision, 'Sources changed during packaging')
        files = sorted(p for p in stage.rglob('*') if p.is_file()); candidate = stage / 'preview.zip'
        with zipfile.ZipFile(candidate, 'x') as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), time.gmtime(max(epoch, 315532800))[:6])
                info.external_attr = (0o100755 if path.parent.name == 'bin' else 0o100644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        os.link(candidate, output)
    return {'archive': str(output), 'sha256': sha(output), 'revision': revision, 'installed_smoke_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('command', choices=('build', 'check'))
    parser.add_argument('--archive', type=Path, required=True); parser.add_argument('--managed-dir', type=Path); parser.add_argument('--managed-receipt', type=Path)
    args = parser.parse_args()
    if args.command == 'build':
        require(args.managed_dir is not None and args.managed_receipt is not None, 'Supply the explicitly rebuilt Release directory and its receipt')
        result = build(args.archive, args.managed_dir, args.managed_receipt)
    else:
        with tempfile.TemporaryDirectory(prefix='p6-wb-preview-') as temp: unpack(args.archive, temp); result = smoke(temp)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except FileNotFoundError as error: print('BLOCKED: ' + str(error), file=sys.stderr); sys.exit(2)
