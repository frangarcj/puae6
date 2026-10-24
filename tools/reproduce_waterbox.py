#!/usr/bin/env python3
"""Compare two clean builds of eight standalone products, at fixed output paths."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from tools import package_waterbox as pkg, prepare_hrtmon
from tools.ci import managed_release, prepare_managed_inputs
wb = pkg.wb


def regular(path):
    path = Path(path)
    pkg.import_sources.no_links(ROOT, path)
    if not path.exists(): raise FileNotFoundError('Missing reproducibility input/output: ' + str(path))
    if not path.is_file() or path.stat().st_nlink != 1 or not path.stat().st_size:
        raise ValueError('Missing/nonregular reproducibility input/output: ' + str(path))
    return path


def cargo_inputs(home):
    """Authenticate archives against Cargo.lock, excluding the audited Windows-only set."""
    lock = ROOT / 'references/machawk/waterbox/waterboxhost/Cargo.lock'
    packages = tomllib.loads(lock.read_text())['package']
    result = {}
    for item in packages:
        if not item.get('checksum') or item['name'] in pkg.policy.WINDOWS:
            continue
        found = list((home / 'registry/cache').glob(f"*/{item['name']}-{item['version']}.crate"))
        if not found: raise FileNotFoundError('Missing offline Cargo archive: ' + item['name'])
        if len(found) != 1 or pkg.sha(regular(found[0])) != item['checksum']:
            raise ValueError('Missing/changed offline Cargo archive: ' + item['name'])
        result[found[0].relative_to(home).as_posix()] = item['checksum']
    index = home / 'registry/index'
    if not index.is_dir(): raise FileNotFoundError('Missing offline Cargo index')
    for entry in wb.inventory(index):
        if entry['mode'] == '120000': raise ValueError('Linked Cargo index')
        result['registry/index/' + entry['path']] = entry['sha256']
    if not result or not any(n.startswith('registry/index/') for n in result):
        raise ValueError('Missing offline Cargo index')
    return result


def tree_proof():
    """Require materialize()'s cache-hit proof before the guest builder can call it."""
    materialize = pkg.materialize
    pristine = pkg.import_sources.verify_source(ROOT)
    patches, series = materialize._series(ROOT, pristine['source']['commit'])
    identity = dict(pristine=pristine, series=series, implementation=[
        pkg.sha(Path(materialize.__file__)), pkg.sha(Path(materialize.import_sources.__file__))])
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    proof = regular(ROOT / 'build/.upstream-materialize.json')
    if json.loads(proof.read_text()) != {'identity': expected}:
        raise ValueError('Materialized engine proof is stale; prepare official inputs first')
    expected = materialize._expected_receipt(ROOT, pristine, patches, series)
    if pkg.import_sources.inventory(ROOT / 'build/upstream') != expected['files']:
        raise ValueError('Official patched engine is stale')
    recipe = ROOT / 'manifests/codegen.json'
    names = [n for g in json.loads(recipe.read_text())['generators'] for n in g['outputs']]
    generated = pkg.generate_cpu._cached_output(ROOT / 'build/generated', names)
    identity = dict(upstream_commit=expected['upstream_commit'], series=series, recipe=pkg.sha(recipe),
                    source_manifest=pkg.sha(ROOT / 'manifests/source-manifest.json'))
    if generated is None or any(generated['identity'].get(k) != v for k, v in identity.items()):
        raise ValueError('Official generated engine is stale')
    return {'engine': expected, 'generated': generated}


def preflight(home):
    if wb.platform.system() != 'Darwin' or wb.platform.machine() != 'arm64':
        raise FileNotFoundError('Real Darwin ARM64 runner required')
    if wb.runtime_profile() != 'release': raise ValueError('Release runtime required')
    revision = pkg.package.clean_revision()
    prepare_managed_inputs.prepare(root=ROOT, fetch=False)
    inputs = {'trees': tree_proof(), 'manual': wb.puae6_manual_inputs(ROOT), 'managed': managed_release.capture_inputs(ROOT),
              'sources': {n: pkg.sha(regular(ROOT / n)) for n in wb.puae6_sources()}}
    for name in ('upstream', 'generated', 'assets/hrtmon_builtin.cpp'):
        path = ROOT / 'build' / name
        if not path.exists(): raise FileNotFoundError('Prepare official input first: ' + str(path))
    cargo = cargo_inputs(home)
    lock = wb.selected_toolchain()
    wb.verify_sysroot(lock)
    # Runtime uses the base, but build_puae6 also requires this authenticated
    # derived input. Capture it before allocating/building either clean pass.
    inputs['guest_abi'] = wb.puae6_guest_abi(ROOT)
    inputs['captures'] = {n: wb.source_spec(n, wb.source_audit_mode())[1] for n in ('machawk', lock['sysroot']['associated_source'])}
    tools, versions = wb.locked_tools(lock)
    dotnet = shutil.which('dotnet')
    if not dotnet: raise FileNotFoundError('Existing pinned .NET SDK required')
    sdk = subprocess.check_output([dotnet, '--version'], cwd=ROOT, text=True).strip()
    if sdk != managed_release.expected_sdk(ROOT): raise ValueError('Managed SDK differs from pin')
    derived = [p for n in ('upstream', 'generated') for p in (ROOT / 'build' / n).rglob('*') if p.is_file()]
    derived += [ROOT / 'build/assets/hrtmon_builtin.cpp', ROOT / 'build/.upstream-materialize.json']
    return dict(revision=revision, inputs=inputs, cargo=cargo, sdk=sdk,
                tools={n: pkg.sha(p) for n, p in tools.items()}, versions=versions,
                derived_mtimes={str(p): p.stat().st_mtime_ns for p in derived},
                dotnet=dotnet, dotnet_sha256=pkg.sha(Path(dotnet).resolve()), environment=dict(os.environ))


def verify_hrtmon(work):
    """Render with the real helper in our staging; never repair an official asset."""
    source = regular(ROOT / 'upstream/winuae/hrtmon.rom.cpp')
    copy = work / 'hrtmon/upstream/winuae/hrtmon.rom.cpp'
    copy.parent.mkdir(parents=True); shutil.copyfile(source, copy)
    expected = prepare_hrtmon.prepare(work / 'hrtmon')
    if pkg.sha(expected) != pkg.sha(regular(ROOT / 'build/assets/hrtmon_builtin.cpp')):
        raise ValueError('Official HRTMon asset is stale; prepare it separately')


def build_once(work, suffix, home, before, jobs):
    """All mutable compiler outputs belong to this pass; the source root stays fixed."""
    original = wb.BUILD
    try:
        wb.BUILD = work / 'waterbox'
        tc = wb.toolchain()
        for relative, digest in before['cargo'].items():
            source = regular(home / relative)
            if pkg.sha(source) != digest: raise ValueError('Cargo source cache changed')
            target = wb.BUILD / 'cargo-home' / relative
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        if (wb.BUILD / 'cargo').exists(): raise ValueError('Cargo target must initially be absent')
        runtime = wb.cargo_build_runtime(tc, 'release')
        guest = wb.build_puae6(tc, jobs)['guest']
        guest_path = regular(Path(guest['path']))
        if pkg.sha(guest_path) != guest['sha256']: raise ValueError('Fresh guest receipt differs')
        env = {**wb.managed_env(), 'DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE': 'true'}
        feed = ROOT / 'build/hosted-managed-feed'
        wb.run_logged([before['dotnet'], 'build', str(ROOT / 'tests/ffi/Ffi.csproj'), '-c', 'Release', '--nologo',
                       f"-p:Puae6GuestSha256={guest['sha256']}", f'-p:P6ManagedTestSuffix={suffix}',
                       f'-p:RestoreSources={feed}'], env, ROOT)
        managed = ROOT / ('build/ffi' + suffix) / 'bin/Release/net8.0'
        pin = subprocess.check_output([before['dotnet'], str(managed / 'Ffi.dll'), 'pin'], env=env, cwd=managed, text=True).strip()
        if pin != 'pin ' + guest['sha256']: raise ValueError('Fresh managed guest pin differs')
        paths = {n: managed / n for n in managed_release.OUTPUTS}
        paths.update({'libwaterboxhost.dylib': runtime, 'puae6.wbx': guest_path})
        if set(paths) != set(pkg.BINS): raise ValueError('Incomplete standalone inventory')
        return {n: {'bytes': regular(p).stat().st_size, 'sha256': pkg.sha(p)} for n, p in paths.items()}
    finally:
        wb.BUILD = original


def archive_matches(archive, products):
    with zipfile.ZipFile(regular(archive)) as source:
        names = source.namelist()
        if len(names) != len(set(names)): raise ValueError('Duplicate ZIP entries')
        matches = True
        for name, item in products.items():
            info = source.getinfo('bin/' + name)
            if info.file_size != item['bytes']: matches = False; continue
            digest = hashlib.sha256()
            with source.open(info) as stream:
                for block in iter(lambda: stream.read(65536), b''): digest.update(block)
            matches &= digest.hexdigest() == item['sha256']
        return bool(matches)


def reproduce(*, jobs=2, archive=None, check_only=False):
    if type(jobs) is not int or not 1 <= jobs <= 2: raise ValueError('Use one or two build jobs')
    home = wb.BUILD / 'cargo-home'
    before = preflight(home)  # All required official inputs are checked before allocating outputs.
    with tempfile.TemporaryDirectory(prefix='waterbox-repro-', dir=ROOT / 'build') as temp:
        work = Path(temp); suffix = '-' + work.name
        owned = [ROOT / ('build/' + n + suffix) for n in ('ffi', 'bizhawk-core')]
        verify_hrtmon(work)
        if check_only: return {'status': 'READY', 'scope': 'preflight-only-no-builds'}
        claimed = []
        try:
            for path in owned:
                pkg.import_sources.no_links(ROOT, path); path.mkdir(); claimed.append(path)
            products = None
            for number in range(2):
                current = build_once(work, suffix, home, before, jobs)
                if set(current) != set(pkg.BINS): raise ValueError('Incomplete standalone inventory')
                if preflight(home) != before: raise ValueError('Reproducibility inputs/environment changed')
                if products is not None and current != products:
                    raise ValueError('Clean Waterbox builds differ: ' + ', '.join(n for n in products if current[n] != products[n]))
                products = current
                shutil.rmtree(work / 'waterbox')
                for path in owned:
                    shutil.rmtree(path)
                    if number == 0: path.mkdir()
            result = dict(status='PASS', scope='waterbox-standalone-eight-products-fixed-paths', builds=2,
                          fixed_paths=[str(work / 'waterbox'), *map(str, owned)], sdk_version=before['sdk'], tool_hashes=before['tools'],
                          products=products, revision=before['revision'], archive_payload_matches=None)
            if archive is not None: result['archive_payload_matches'] = archive_matches(archive, products)
            return result
        finally:
            for path in claimed:
                if path.exists(): shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--archive', type=Path, help='Optionally compare all eight installed products; never claims ZIP byte identity')
    parser.add_argument('--preflight', action='store_true', help='Check inputs only; owned HRTMon scratch is removed')
    args = parser.parse_args()
    try:
        result, code = reproduce(jobs=args.jobs, archive=args.archive, check_only=args.preflight), 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as error:
        code = 2 if isinstance(error, (FileNotFoundError, prepare_managed_inputs.MissingFeed)) else 1
        result = {'status': 'BLOCKED' if code == 2 else 'FAIL', 'diagnostic': str(error)}
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
