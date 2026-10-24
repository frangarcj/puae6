"""Source/output provenance for the Release FFI wrapper; verification never builds."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ('Ffi.dll', 'Puae6.Core.dll', 'CommunityToolkit.HighPerformance.dll', 'Newtonsoft.Json.dll',
           'Ffi.deps.json', 'Ffi.runtimeconfig.json')
EXTRA_INPUTS = ('plan/toolchains.json', 'tools/ci/managed-inputs.json', 'tools/ci/managed_release.py', 'tools/bench.py')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(root, relative):
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts or name.as_posix() != relative:
        raise ValueError('Invalid managed provenance path')
    path = root
    for part in name.parts:
        path /= part
        if path.is_symlink(): raise ValueError('Linked managed provenance input/output: ' + relative)
    return path


def regular(root, relative):
    path = checked_path(root, relative)
    if not path.is_file(): raise ValueError('Missing managed provenance file: ' + relative)
    return path


def capture_inputs(root=ROOT):
    root = Path(root).resolve()
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'], timeout=30).decode().split('\0')
    selected = {n for n in names if Path(n).suffix in ('.cs', '.csproj', '.props', '.targets') or Path(n).name == 'global.json'}
    selected.update(EXTRA_INPUTS)
    return {n: file_hash(regular(root, n)) for n in sorted(selected)}


def expected_sdk(root):
    sdk = json.loads(regular(root, 'tests/ffi/global.json').read_text())['sdk']['version']
    feed_sdk = json.loads(regular(root, 'tools/ci/managed-inputs.json').read_text())['sdk_version']
    if sdk != '10.0.402' or feed_sdk != sdk: raise ValueError('Managed SDK pins disagree')
    return sdk


def recipe(root, guest_sha, sources, inputs):
    allowed = ([f'-p:RestoreSources={Path.home() / ".nuget/packages"}'],
               [f'-p:RestoreSources={root / "build/hosted-managed-feed"}'])
    if sources not in allowed: raise ValueError('Unexpected managed Release restore sources')
    return dict(project='tests/ffi/Ffi.csproj', configuration='Release', guest_sha256=guest_sha,
                restore_sources=sources, helper_sha256=inputs['tools/ci/managed_release.py'], bench_sha256=inputs['tools/bench.py'])


def output_hashes(directory):
    directory = Path(directory)
    if any(p.is_symlink() for p in (directory, *directory.parents)) or not directory.is_dir():
        raise ValueError('Missing/linked managed Release output directory')
    return {name: dict(bytes=(path := regular(directory, name)).stat().st_size, sha256=file_hash(path)) for name in OUTPUTS}


def receipt_path(root=ROOT):
    return checked_path(Path(root), 'build/ffi/managed-release-receipt.json')


def invalidate(root=ROOT):
    """An attempted rebuild must not retain an apparently current prior receipt."""
    path = receipt_path(root)
    if path.is_symlink(): raise ValueError('Linked managed Release receipt')
    if path.exists(): path.unlink()


def verify_data(data, output_dir, guest_sha, root):
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'configuration', 'target_framework', 'guest_sha256',
                                                 'sdk_version', 'inputs', 'recipe', 'outputs'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or data['configuration'] != 'Release' or data['target_framework'] != 'net8.0'
            or not isinstance(guest_sha, str) or not re.fullmatch('[0-9a-f]{64}', guest_sha)
            or data['guest_sha256'] != guest_sha or data['sdk_version'] != expected_sdk(root)):
        raise ValueError('Invalid managed Release receipt identity/profile/SDK')
    inputs = capture_inputs(root)
    if data['inputs'] != inputs: raise ValueError('Managed Release source inputs changed')
    description = data['recipe']
    if not isinstance(description, dict) or description != recipe(root, guest_sha, description.get('restore_sources'), inputs):
        raise ValueError('Managed Release recipe changed')
    if (not isinstance(data['outputs'], dict) or set(data['outputs']) != set(OUTPUTS)
            or any(not isinstance(row, dict) or set(row) != {'bytes', 'sha256'} or type(row['bytes']) is not int
                   or row['bytes'] <= 0 or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                   for row in data['outputs'].values()) or data['outputs'] != output_hashes(output_dir)):
        raise ValueError('Managed Release output inventory/hash changed')
    return data


def verify_receipt(receipt_path, output_dir, guest_sha, root=ROOT):
    """Read-only check against current sources and all six Release products; no SDK execution."""
    root = Path(root).resolve()
    path = Path(receipt_path)
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > 1 << 20:
        raise ValueError('Missing/linked/oversized managed Release receipt')
    return verify_data(json.loads(path.read_text()), output_dir, guest_sha, root)


def record(inputs, sdk_version, guest_sha, sources, root=ROOT):
    """Called only after a successful build and stable before/after source and SDK checks."""
    root = Path(root).resolve()
    outputs = root / 'build/ffi/bin/Release/net8.0'
    data = dict(schema_version=1, configuration='Release', target_framework='net8.0', guest_sha256=guest_sha,
                sdk_version=sdk_version, inputs=inputs, recipe=recipe(root, guest_sha, sources, inputs), outputs=output_hashes(outputs))
    verify_data(data, outputs, guest_sha, root)
    path = receipt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', prefix='.managed-release-', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True); stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return data
