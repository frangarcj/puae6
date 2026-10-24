"""Diagnose one tiny LLVM ASan executable; never claim RGB565/emulator coverage."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

from tests.simd import evaluate
from tools.ci import run as ci

ROOT = Path(__file__).resolve().parents[2]
MARKER = 'P6_RGB565_LOADER_PROBE '
SOURCE = '''#include <cstdlib>
int main(int argc, char**) {
    if (argc != 1) std::abort();
    volatile int* values = new int[8];
    values[0] = argc;
    const int result = values[0] == 1 ? 0 : 1;
    delete[] values;
    return result;
}
'''
SYMBOLS = ('___asan_handle_no_return', '___asan_init', '___asan_version_mismatch_check_v8')


def command(argv, env=None):
    try:
        process = subprocess.run(list(map(str, argv)), env=env, capture_output=True, text=True, timeout=90)
        return dict(argv=list(map(str, argv)), returncode=process.returncode,
                    stdout=process.stdout[:131072], stderr=process.stderr[:131072],
                    truncated=len(process.stdout) > 131072 or len(process.stderr) > 131072)
    except subprocess.TimeoutExpired:
        return dict(argv=list(map(str, argv)), returncode=None, stdout='', stderr='diagnostic timeout', truncated=False)


def dyld(env):
    return {key: value for key,value in env.items() if key.startswith('DYLD_')}


def artifact(path):
    path = Path(path)
    result = dict(path=str(path), present=path.is_file())
    if result['present']:
        result.update(bytes=path.stat().st_size, sha256=evaluate.digest(path))
    return result


def image(path):
    result = artifact(path)
    if result['present']:
        result['dependencies'] = command(['/usr/bin/otool', '-L', path])
        result['load_commands'] = command(['/usr/bin/otool', '-l', path])
        listing = command(['/usr/bin/nm', '-arch', 'arm64', '-gU', path])
        lines = listing.pop('stdout').splitlines()
        result['exports'] = {**listing, 'symbols': {name: [line for line in lines if line.split() and line.split()[-1] == name] for name in SYMBOLS}}
    return result


def install_names(text):
    result = []
    active = False
    for line in text.splitlines():
        value = line.strip()
        if value == 'cmd LC_ID_DYLIB': active = True
        elif active and value.startswith('name '):
            result.append(value[5:].split(' (offset ', 1)[0]); active = False
    return sorted(set(result))


def probe():
    result = dict(status='DIAGNOSTIC_FAIL', observation_complete=False, scope='tiny ASan loader only; no RGB565 or emulator verdict',
                  host=dict(os=platform.system(), isa=platform.machine(), macos=platform.mac_ver()[0]), execution_dyld=dyld(os.environ))
    try:
        if (platform.system(), platform.machine()) != ('Darwin', 'arm64') or 'P6_WATERBOX_HOSTED_TOOLS' not in os.environ:
            raise ValueError('requires Darwin ARM64 and the authenticated hosted bundle')
        driver, compile_env, compiler = evaluate.compiler_configuration()
        result.update(compiler=compiler, compile_dyld=dyld(compile_env))
        result['inputs'] = {name: evaluate.digest(ROOT/name) for name in
                            ('tools/ci/rgb565_loader_probe.py', 'tools/ci/hosted-tools.json', 'tools/ci/hosted_environment.py', 'tests/simd/evaluate.py')}
        result['resource_query'] = command([compiler['compiler_path'], '--driver-mode=g++', '-print-resource-dir'], compile_env)
        if result['resource_query']['returncode'] != 0:
            raise ValueError('compiler resource query failed')
        resource = Path(result['resource_query']['stdout'].strip())
        expected = Path(compiler['compiler_path']).parents[1]/'lib/clang/23'
        if resource.resolve(strict=True) != expected.resolve(strict=True):
            raise ValueError('compiler resource directory differs from relocated LLVM')
        runtime = resource/'lib/darwin/libclang_rt.asan_osx_dynamic.dylib'
        result['bundled_runtime'] = image(runtime)
        if not result['bundled_runtime']['present']:
            raise ValueError('bundled ASan runtime missing')
        names = install_names(result['bundled_runtime']['load_commands']['stdout'])
        result['runtime_install_names'] = names
        result['installed_runtime_candidates'] = [image(name) if Path(name).is_absolute() else
                                                  dict(path=name, present=False, unresolved=True) for name in names]
        build = ROOT/'build'; build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='asan-loader-probe-', dir=build) as temporary:
            directory = Path(temporary); source = directory/'own.cpp'; source.write_text(SOURCE)
            exe = directory/'own-san'
            argv = [*driver, *evaluate.flags('san'), source, '-o', exe]
            result['source_sha256'] = hashlib.sha256(SOURCE.encode()).hexdigest()
            result['flags'] = evaluate.flags('san')
            result['driver_trace'] = command([*argv, '-###'], compile_env)
            result['compile'] = command(argv, compile_env)
            if result['compile']['returncode'] != 0:
                result['status'] = 'BUILD_FAIL'
                return result
            result['executable'] = image(exe)
            env = {**os.environ, 'ASAN_OPTIONS': os.environ.get('ASAN_OPTIONS', 'detect_leaks=1')}
            result['asan_options'] = env['ASAN_OPTIONS']
            result['execution'] = command([exe], env)
            images = [result['bundled_runtime'], result['executable'], *result['installed_runtime_candidates']]
            observations = [result['driver_trace'], result['compile']]
            observations += [item[key] for item in images if item['present'] for key in ('dependencies', 'load_commands', 'exports')]
            result['observation_complete'] = (result['execution']['returncode'] is not None and not result['execution']['truncated']
                and all(item['returncode'] == 0 and not item['truncated'] for item in observations))
            result['status'] = ('PASS' if result['execution']['returncode'] == 0 else 'LOAD_FAIL') if result['observation_complete'] else 'DIAGNOSTIC_FAIL'
        if evaluate.compiler_configuration()[2] != compiler:
            raise ValueError('compiler changed during diagnostic')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result.update(status='DIAGNOSTIC_FAIL', diagnostic=str(error), observation_complete=False)
    return result


if __name__ == '__main__':
    result = probe()
    print(MARKER + ci.redact(json.dumps(result, sort_keys=True)))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
