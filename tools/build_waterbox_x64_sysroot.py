#!/usr/bin/env python3
"""Reconstruct a new x64 sysroot offline; no install, historical equivalence or runtime claim."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shlex
import struct
import subprocess
import sys

import build_waterbox_sysroot as frozen
import build_waterbox as wb
import import_sources as sources

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = '''import hashlib,json,platform,shutil,subprocess
if (platform.system(),platform.machine())!=('Linux','x86_64'): raise SystemExit('wrong build host')
print(json.dumps({k:dict(path=shutil.which(k),version=subprocess.check_output([k,'--version'],text=True).splitlines()[0],
sha256=hashlib.sha256(open(shutil.which(k),'rb').read()).hexdigest())
for k in ['clang','gcc','cmake','make','ar','ranlib','ld','python3','git']}))
'''
SCRIPT = r'''set -eu
cp -a /inputs/musl /work/musl
cp -a /inputs/machawk/waterbox/libcxx /work/libcxx
cd /work/musl
git apply --check /inputs/machawk/waterbox/musl-waterbox-getcwd.patch
git apply /inputs/machawk/waterbox/musl-waterbox-getcwd.patch
git apply --check /inputs/machawk/waterbox/musl-waterbox-x86_64-atomics.patch
git apply /inputs/machawk/waterbox/musl-waterbox-x86_64-atomics.patch
export SYSROOT=/work/sysroot-x86_64
export CC=/usr/bin/clang
export CFLAGS='@MUSL_FLAGS@'
./configure --target=waterbox --build=waterbox --disable-shared --prefix="$SYSROOT" --syslibdir="$SYSROOT/syslib"
''' + frozen.MUSL_VERSION_CONTEXT + r'''
make -s -j2
make -s install
python3 - <<'PYWRAP'
from pathlib import Path
p=Path('/work/sysroot-x86_64/bin/ld.musl-clang')
s=p.read_text(); old='exec $($cc -print-prog-name=ld.lld)'; tail=' -dynamic-linker "$ldso"'
if s.count(old)!=1 or s.count(tail)!=1: raise SystemExit('unexpected musl linker wrapper')
p.write_text(s.replace(old,'exec /usr/bin/ld').replace(tail,''))
PYWRAP
cd /work/libcxx
export LLVMDIR=/inputs/llvm
export TARGET_TRIPLE=x86_64-unknown-linux-gnu
export TARGET_FLAGS=
for phase in - 0 1 2; do
 sh ./configure-for-waterbox-phase-$phase
 cmake --build build$phase -j2 --target install
done
mkdir /work/notices
cp /inputs/musl/COPYRIGHT /work/notices/musl-COPYRIGHT
cp /inputs/machawk/LICENSE /work/notices/BizHawk-LICENSE
for component in compiler-rt libcxx libcxxabi libunwind; do
 cd /inputs/llvm/$component
 find . -type f \( -iname '*license*' -o -iname '*licence*' -o -iname '*notice*' -o -iname 'copying*' -o -iname 'copyright*' \) |
 while IFS= read -r notice; do
  mkdir -p "/work/notices/$component/$(dirname "$notice")"
  cp "$notice" "/work/notices/$component/$notice"
 done
done
'''


@contextmanager
def input_root(root):
    """Reuse the frozen ARM source verifiers, without changing their recipe or files."""
    root = Path(root).resolve(strict=True)
    if wb.sha256(root / 'plan/upstream.lock.json') != wb.sha256(ROOT / 'plan/upstream.lock.json'):
        raise frozen.Blocked('source root has another upstream lock')
    previous = frozen.ROOT
    frozen.ROOT = root
    try:
        yield root
    finally:
        frozen.ROOT = previous


def docker(image, *args):
    return ['docker', 'run', '--rm', '--platform', 'linux/amd64', '--pull=never', '--network=none', *args, image]


def output_path(output):
    output = output if output.is_absolute() else ROOT / output
    sources.no_links(ROOT, output)
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output == ROOT / 'build' or output.exists():
        raise frozen.Blocked('output must be a new directory below this checkout build/')
    return output


def verify_inputs(root, llvm, spec):
    with input_root(root):
        musl, musl_hash = frozen.capture('waterbox_musl')
        machawk, machawk_hash = frozen.capture('machawk')
        identity = frozen.llvm_capture(llvm)
    if identity['commit'] != spec['llvm_source_commit'] or identity['inventory_sha256'] != spec['sysroot_rebuild']['llvm_inventory_sha256']:
        raise frozen.Blocked('LLVM identity differs from x64 recipe')
    required = ['LICENSE', 'waterbox/musl-waterbox-getcwd.patch', 'waterbox/musl-waterbox-x86_64-atomics.patch']
    required += ['waterbox/libcxx/configure-for-waterbox-phase-' + p for p in ('-', '0', '1', '2')]
    if any(not (machawk / name).is_file() for name in required):
        raise frozen.Blocked('missing captured x64 recipe/license')
    return musl, machawk, dict(musl=musl_hash, machawk=machawk_hash, llvm=identity)


def validate_result(sysroot):
    crt = (sysroot / 'lib/crt1.o').read_bytes()
    if crt[:6] != b'\x7fELF\x02\x01' or len(crt) < 64 or struct.unpack_from('<H', crt, 18)[0] != 62:
        raise frozen.Failed('rebuilt CRT is not ELF64LE x86_64')
    for name in ('libc.a', 'libc++.a', 'libc++abi.a', 'libunwind.a', 'linux/libclang_rt.builtins-x86_64.a'):
        if (sysroot / 'lib' / name).read_bytes()[:8] != b'!<arch>\n':
            raise frozen.Failed('missing rebuilt static library: ' + name)
    entries = sources.inventory(sysroot)
    return dict(inventory_sha256=wb.canonical_hash(entries), file_count=len(entries))


def rebuild(llvm, output, source_root=ROOT, execute=False):
    spec = json.loads((ROOT / 'plan/extensions/waterbox-x64.json').read_text())
    pin = spec['sysroot_rebuild']; image = pin['image_id']
    if len(image) != 71 or not image.startswith('sha256:') or any(c not in '0123456789abcdef' for c in image[7:]):
        raise frozen.Blocked('immutable Docker image ID required')
    if pin['platform'] != 'linux/amd64' or spec['host_page_size'] != 4096 or spec['target'] != 'x86_64-linux-gnu':
        raise frozen.Blocked('wrong x64 target/page/build platform')
    output = output_path(output)
    llvm = llvm.resolve(strict=True)
    musl, machawk, provenance = verify_inputs(source_root, llvm, spec)
    inspected = json.loads(frozen.command(['docker', 'image', 'inspect', image, '--format', '{{json .}}']))
    if (inspected['Id'], inspected['Os'], inspected['Architecture']) != (image, 'linux', 'amd64'):
        raise frozen.Blocked('Docker identity/OS/architecture differs')
    observed = json.loads(frozen.command(docker(image) + ['python3', '-c', PREFLIGHT]))
    if observed != pin['tools']:
        raise frozen.Blocked('Docker tools differ from frozen x64 inputs')
    mounts = [(musl, '/inputs/musl', True), (machawk, '/inputs/machawk', True),
              (llvm, '/inputs/llvm', True), (output, '/work', False)]
    options = []
    for src, dst, readonly in mounts:
        if ',' in str(src):
            raise frozen.Blocked('commas in mount paths unsupported')
        options += ['--mount', f'type=bind,source={src},target={dst}' + (',readonly' if readonly else '')]
    script = SCRIPT.replace("'@MUSL_FLAGS@'", shlex.quote(pin['musl_flags']))
    command = docker(image, *options) + ['sh', '-c', script]
    result = dict(status='NOT_RUN', publication_ready=False, runtime_verified=False, source_rebuild_verified=False,
                  image_id=image, tools=observed, sources=provenance, source_directories=dict(musl=str(musl), machawk=str(machawk), llvm=str(llvm)),
                  recipe_inputs={name: wb.sha256(ROOT / name) for name in (
                      'tools/build_waterbox_x64_sysroot.py', 'tools/build_waterbox_sysroot.py',
                      'tools/build_waterbox.py', 'tools/import_sources.py', 'plan/extensions/waterbox-x64.json')},
                  script_sha256=hashlib.sha256(script.encode()).hexdigest(), command=command)
    if not execute:
        return result
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir() # Exclusive reservation; never reuse or delete other outputs.
    frozen.command(command, build=True)
    if verify_inputs(source_root, llvm, spec)[2] != provenance:
        raise frozen.Failed('source inputs changed during reconstruction')
    sysroot = output / 'sysroot-x86_64'
    rebuilt = validate_result(sysroot)
    result.update(status='SOURCE_REBUILD_PASS_RUNTIME_NOT_EXECUTED', source_rebuild_verified=True,
                  sysroot=str(sysroot), **rebuilt, matches_historical_inventory=rebuilt == {k: spec['sysroot'][k] for k in rebuilt})
    (output / 'receipt.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llvm-source', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT, help='read-only capture root with identical upstream lock')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='actually build; default verifies/prepares command only')
    args = parser.parse_args()
    try:
        result = rebuild(args.llvm_source, args.output, args.source_root, args.execute)
        print(json.dumps(result, indent=2))
        return 0 if result['source_rebuild_verified'] else 3
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr)
        return 2
    except frozen.Failed as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
