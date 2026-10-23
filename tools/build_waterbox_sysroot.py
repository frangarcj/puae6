#!/usr/bin/env python3
"""Offline sysroot reconstruction candidate; only the exact locked inventory passes.

No downloads or installs. LLVM must be a clean, populated checkout of the existing
18.1.8 pin. Compiler versions describe observed inputs, never binary equivalence.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import build_waterbox as wb
import import_sources as sources

ROOT = Path(__file__).resolve().parents[1]
LLVM_DIRS = ('cmake', 'compiler-rt', 'libunwind', 'libcxx', 'libcxxabi', 'runtimes', 'llvm/cmake')
MARKERS = ('.musl-aarch64-darwin-registers', '.musl-aarch64-darwin-auxv-v2',
           '.musl-aarch64-darwin-runtime-v3', '.musl-aarch64-darwin-runtime-v4',
           '.libcxx-aarch64-darwin-registers-llvm-18.1.8')
MUSL_FLAGS = ('-target aarch64-linux-gnu --sysroot=/usr/aarch64-linux-gnu -Werror '
              '-fvisibility=hidden -mcmodel=large -mstack-protector-guard=global '
              '-fno-pic -fno-pie -fcf-protection=none -ffixed-x18 -ffixed-x28 -DWBX_HOST_PAGE_SIZE=16384')
PREFLIGHT = '''import json,platform,subprocess
tools={}
for tool in ['clang','ld.lld','cmake','make','ar','ranlib','ld','gcc']:
 tools[tool]=subprocess.check_output([tool,'--version'],text=True).splitlines()[0]
if platform.system()!='Linux' or platform.machine()!='aarch64': raise SystemExit('requires Linux/aarch64')
if tools['clang']!='Ubuntu clang version 18.1.3 (1ubuntu1)': raise SystemExit('different compiler')
if '18.1.3' not in tools['ld.lld']: raise SystemExit('different linker')
print(json.dumps({'os':platform.system(),'architecture':platform.machine(),'tools':tools}))
'''
# Sources stay read-only. All configuration and compiler outputs live in /work.
SCRIPT = r'''set -eu
cp -a /inputs/musl /work/musl-waterbox-aarch64-darwin
cp -a /inputs/libcxx /work/libcxx
mkdir /work/notices
cp /inputs/musl/COPYRIGHT /work/notices/musl-COPYRIGHT
cp /inputs/machawk-LICENSE /work/notices/BizHawk-LICENSE
for component in compiler-rt libcxx libcxxabi libunwind; do
 cd /work/llvm-project/$component
 find . -type f \( -iname '*license*' -o -iname '*licence*' -o -iname '*notice*' -o -iname 'copying*' -o -iname 'copyright*' \) |
 while IFS= read -r notice; do
  mkdir -p "/work/notices/$component/$(dirname "$notice")"
  cp "$notice" "/work/notices/$component/$notice"
 done
done
export SYSROOT=/work/musl-sysroot-aarch64-darwin
export CC=clang
cd /work/musl-waterbox-aarch64-darwin
export CFLAGS=''' + "'" + MUSL_FLAGS + "'" + '''
export LDFLAGS='--target=aarch64-linux-musl --sysroot=/usr/aarch64-linux-gnu'
# The captured configure wrapper lost the 16-KiB macro; invoke configure directly.
./configure --target=waterbox_aarch64 --build=waterbox_aarch64 --disable-shared --prefix="$SYSROOT" --syslibdir="$SYSROOT/syslib"
make -s -j2
make -s install
cd /work/libcxx
export LLVMDIR=/work/llvm-project
export TARGET_TRIPLE=aarch64-unknown-linux-gnu
export TARGET_FLAGS='--target=aarch64-unknown-linux-gnu -ffixed-x18 -ffixed-x28'
for phase in - 0 1 2; do
 sh ./configure-for-waterbox-phase-$phase
 cmake --build build$phase -j2 --target install
done
'''


class Blocked(ValueError):
    pass


class Failed(RuntimeError):
    pass


def command(args, *, build=False):
    try:
        p = subprocess.run(args, text=True, stdout=None if build else subprocess.PIPE,
                           stderr=None if build else subprocess.PIPE, timeout=28800 if build else 180)
    except subprocess.TimeoutExpired as error:
        raise (Failed if build else Blocked)(f"command timeout after {error.timeout}s: {args[:4]}") from error
    if p.returncode:
        raise (Failed if build else Blocked)(f"command failed ({p.returncode}): {args[:4]} " + (p.stderr or '')[-1000:])
    return p.stdout


def capture(sid):
    data = sources.verify_source(ROOT, sid)
    entries = {f['path']: f['sha256'] for f in data['files']}
    for name, digest in data['source']['snapshot'].get('overlay_sha256', {}).items():
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or entries.get(name) != digest:
            raise Blocked(f'captured overlay differs: {sid}/{name}')
    return ROOT / data['destination'], wb.canonical_hash(data)


@contextmanager
def offline_git():
    # Partial clones must fail on missing objects instead of lazy-fetching them.
    values = {'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    previous = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, value in previous.items():
            if value is None: os.environ.pop(k, None)
            else: os.environ[k] = value


@offline_git()
def llvm_capture(path):
    pin = sources.source(ROOT, 'compiler_rt_emutls')
    sources.check_pins(path, pin)
    if command(['git', '--no-optional-locks', '-C', str(path), 'status', '--porcelain', '--untracked-files=all']).strip():
        raise Blocked('LLVM checkout is dirty')
    selection = {**pin, 'local_path': str(path), 'snapshot': {'include': list(LLVM_DIRS)}}
    expected = [{'path': n, 'mode': m, 'sha256': hashlib.sha256(b).hexdigest()}
                for n, m, b in sources.blobs(path, selection)]
    actual = []
    for name in LLVM_DIRS:
        actual += [{**f, 'path': name + '/' + f['path']} for f in sources.inventory(path / name)]
    if sorted(actual, key=lambda f: f['path']) != expected:
        raise Blocked('LLVM closure differs from pinned Git blobs (including extra files)')
    for component in ('compiler-rt', 'libcxx', 'libcxxabi', 'libunwind'):
        if not (path / component / 'LICENSE.TXT').is_file():
            raise Blocked(f'missing LLVM license: {component}')
    return {'commit': pin['commit'], 'tag_object': pin['tag_object'], 'inventory_sha256': wb.canonical_hash(expected)}


def docker(image, *args):
    return ['docker', 'run', '--rm', '--platform', 'linux/arm64', '--pull=never', '--network=none', *args, image]


def rebuild(llvm, output, image=None, execute=False):
    lock = json.loads((ROOT / 'plan/toolchains.json').read_text())
    image = image or lock['portability_matrix']['image_ids']['linux/arm64']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise Blocked('an immutable Docker image ID is required')
    output = output if output.is_absolute() else ROOT / output
    sources.no_links(ROOT, output)
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output == ROOT / 'build' or output.exists():
        raise Blocked('output must be a new directory below build/')
    musl, musl_hash = capture(lock['sysroot']['associated_source'])
    machawk, machawk_hash = capture('machawk')
    required = [musl / 'COPYRIGHT', musl / 'configure', machawk / 'LICENSE']
    required += [machawk / f'waterbox/libcxx/configure-for-waterbox-phase-{p}' for p in ('-', '0', '1', '2')]
    if any(not p.is_file() for p in required):
        raise Blocked('captured recipe or license input is missing')
    llvm = llvm.resolve(strict=True)
    identity = llvm_capture(llvm)
    inspected = json.loads(command(['docker', 'image', 'inspect', image, '--format', '{{json .}}']))
    if (inspected['Id'], inspected['Os'], inspected['Architecture']) != (image, 'linux', 'arm64'):
        raise Blocked('Docker image identity/OS/ISA mismatch')
    observed = json.loads(command(docker(image) + ['python3', '-c', PREFLIGHT]))
    mounts = [(musl, '/inputs/musl', True), (machawk / 'waterbox/libcxx', '/inputs/libcxx', True),
              (machawk / 'LICENSE', '/inputs/machawk-LICENSE', True), (output, '/work', False),
              (llvm, '/work/llvm-project', True)]
    options = []
    for src, dst, readonly in mounts:
        if ',' in str(src): raise Blocked('commas in bind mount paths are unsupported')
        options += ['--mount', f'type=bind,source={src},target={dst}' + (',readonly' if readonly else '')]
    build = docker(image, *options) + ['sh', '-c', SCRIPT]
    result = {'status': 'NOT_RUN', 'image_id': image, 'observed': observed, 'llvm': identity,
              'capture_sha256': {'musl': musl_hash, 'machawk': machawk_hash},
              'recipe_sha256': wb.sha256(Path(__file__)), 'command': build,
              'marker_semantics': 'literal inventory compatibility only; no historical execution claims'}
    if not execute:
        return result
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Exclusive reservation: never reuse or overwrite another build.
    command(build, build=True)
    sysroot = output / 'musl-sysroot-aarch64-darwin'
    if not sysroot.is_dir():
        raise Failed('compiler did not produce the sysroot directory')
    for name in MARKERS:
        (sysroot / name).touch(exist_ok=False)  # Only after every phase succeeded.
    if (capture(lock['sysroot']['associated_source'])[1], capture('machawk')[1], llvm_capture(llvm)) != (musl_hash, machawk_hash, identity):
        raise Failed('source inputs changed during build')
    entries = sources.inventory(sysroot)
    result.update(inventory_sha256=wb.canonical_hash(entries), file_count=len(entries))
    if (result['inventory_sha256'], len(entries)) != (lock['sysroot']['inventory_sha256'], lock['sysroot']['file_count']):
        raise Failed('sysroot inventory mismatch: ' + json.dumps({k: result[k] for k in ('inventory_sha256', 'file_count')}))
    result['status'] = 'PASS'
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--image', help='explicit immutable build input; does not change product pins')
    p.add_argument('--execute', action='store_true', help='actually compile; default is NOT_RUN')
    args = p.parse_args()
    try:
        result = rebuild(args.llvm_source, args.output, args.image, args.execute)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'PASS' else 3
    except (Blocked, OSError, KeyError, StopIteration, subprocess.CalledProcessError, ValueError) as e:
        print('BLOCKED: ' + str(e))
        return 2
    except Failed as e:
        print('FAIL: ' + str(e))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
