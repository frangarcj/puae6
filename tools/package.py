#!/usr/bin/env python3
"""Build a local native preview with corresponding sources and an isolated smoke."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'manifests/release/native-preview.json'
PRODUCTS = ('puae6_libretro.dylib', 'p6_headless', 'p6_libretro_host')
# PAL single (287 rows) fits the NTSC double (484 rows) adapter bounds, so
# a valid foreign state reaches config_hash rather than an earlier geometry check.
PAL_SMOKE_OPTIONS = ('--set', 'puae6_video_line_mode=single')
PLATFORMS = {
    'macos-arm64': ('Darwin', 'arm64', 'cmake/toolchains/macos-appleclang.cmake'),
    'linux-arm64': ('Linux', 'arm64', 'cmake/toolchains/linux-clang.cmake'),
    'linux-x86_64': ('Linux', 'x86_64', 'cmake/toolchains/linux-clang.cmake'),
}


def native_platform():
    machine = 'arm64' if platform.machine() == 'aarch64' else platform.machine()
    for name, (system, isa, _) in PLATFORMS.items():
        if (platform.system(), machine) == (system, isa):
            return name
    raise ValueError('Preview requires macOS ARM64 or Linux ARM64/x86_64')


def products(target):
    if type(target) is not str or target not in PLATFORMS:
        raise ValueError('Unknown package platform')
    return (('puae6_libretro.so' if target.startswith('linux-') else PRODUCTS[0]), *PRODUCTS[1:])


def configure_command(target, tree):
    products(target)  # Validate before constructing a build command.
    command = ['cmake', '-S', ROOT, '-B', tree, '-G', 'Ninja', '-DCMAKE_TOOLCHAIN_FILE=' + PLATFORMS[target][2],
               '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_FLAGS_RELEASE=-O2', '-DCMAKE_CXX_FLAGS_RELEASE=-O2',
               '-DP6_NATIVE_IPO=OFF', '-DP6_PGO_MODE=OFF', '-DP6_SANITIZERS=OFF']
    if target == 'macos-arm64':
        command += ['-DCMAKE_INSTALL_NAME_DIR=@rpath', '-DCMAKE_BUILD_WITH_INSTALL_NAME_DIR=ON']
    return command


def verify_binaries(directory, target):
    for name in products(target):
        binary = directory / 'bin' / name
        if target == 'macos-arm64':
            if 'arm64' not in run(['file', '-b', binary]):
                raise ValueError('Wrong package architecture')
            for line in run(['otool', '-L', binary]).splitlines()[1:]:
                dependency = line.strip().split(' (')[0]
                if not dependency.startswith('/usr/lib/') and dependency != '@rpath/puae6_libretro.dylib':
                    raise ValueError('Non-system dependency: ' + dependency)
            continue
        with binary.open('rb') as stream:
            header = stream.read(64)
        types = (3,) if name.endswith('.so') else (2, 3)  # Shared core; EXEC or PIE executables.
        machine = 183 if target == 'linux-arm64' else 62
        if (len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01'
                or struct.unpack_from('<HHI', header, 16) not in [(kind, machine, 1) for kind in types]):
            raise ValueError('Wrong package ELF architecture/type: ' + name)
        phoff = struct.unpack_from('<Q', header, 32)[0]
        width, count = struct.unpack_from('<HH', header, 54)
        size = binary.stat().st_size
        if count and (width != 56 or phoff > size or count * width > size - phoff):
            raise ValueError('Invalid ELF program headers: ' + name)
        with binary.open('rb') as stream:
            for number in range(count):
                stream.seek(phoff + number * width); program = stream.read(width)
                if struct.unpack_from('<I', program)[0] != 3: continue  # PT_INTERP
                offset, length = struct.unpack_from('<Q', program, 8)[0], struct.unpack_from('<Q', program, 32)[0]
                interpreter = b'/lib/ld-linux-aarch64.so.1\0' if machine == 183 else b'/lib64/ld-linux-x86-64.so.2\0'
                if name.endswith('.so') or length != len(interpreter) or offset > size or length > size - offset:
                    raise ValueError('Non-system ELF interpreter: ' + name)
                stream.seek(offset)
                if stream.read(length) != interpreter:
                    raise ValueError('Non-system ELF interpreter: ' + name)
        # Inspect metadata, never use ldd (which can execute the inspected input).
        for line in run(['readelf', '--wide', '--dynamic', binary], env={**os.environ, 'LC_ALL': 'C'}).splitlines():
            if '(RPATH)' in line or '(RUNPATH)' in line:
                raise ValueError('Hidden package library search path: ' + name)
            if '(NEEDED)' in line:
                match = re.search(r'\(NEEDED\)[^\[]*\[([^\]]+)\]', line)
                # Distribution Clang's C++/unwind/math/C runtime; no bundled decoder/plugin dependency.
                if not match or match[1] not in {'libstdc++.so.6', 'libgcc_s.so.1', 'libm.so.6', 'libc.so.6'}:
                    raise ValueError('Non-system dependency: ' + line.strip())


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


def notice_files(policy, read):
    """Exact copies or inclusive line slices; read comes from ROOT or the archive."""
    files = {}
    for name, spec in policy['licenses'].items():
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError('Invalid notice filename')
        pieces = []
        for part in ([{'path': spec}] if isinstance(spec, str) else spec):
            path = part['path']
            if Path(path).is_absolute() or '..' in Path(path).parts or '\\' in path:
                raise ValueError('Invalid notice source')
            data = read(path)
            if 'lines' in part:
                bounds = part['lines']; lines = data.splitlines(keepends=True)
                if (len(bounds) != 2 or any(type(n) is not int for n in bounds)
                        or not 1 <= bounds[0] <= bounds[1] <= len(lines)):
                    raise ValueError('Invalid notice slice')
                data = ('Source: ' + path + '\n').encode() + b''.join(lines[bounds[0]-1:bounds[1]])
            pieces.append(data)
        if not pieces or not all(pieces):
            raise ValueError('Empty notice')
        files[name] = b'\n'.join(pieces)
    return files


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
    products(manifest.get('platform'))
    verify_binaries(directory, manifest['platform'])
    with tarfile.open(directory / 'source.tar.gz') as archive:
        members = {m.name for m in archive.getmembers() if m.isfile()}
        def read(path):
            return archive.extractfile('source/' + path).read()
        policy = json.loads(read('manifests/release/native-preview.json'))
        for name, data in notice_files(policy, read).items():
            notice = directory / 'licenses' / name
            if not notice.is_file() or notice.read_bytes() != data:
                raise ValueError('Missing or changed notice: ' + name)
    if 'source/tools/package.py' not in members or any('/whdload/' in p and p.endswith('.hdf') for p in members):
        raise ValueError('Incomplete or private source payload')
    if 'source/tools/generate_whdload.py' in members:
        shared = {'source/adapters/libretro/whdload_data.generated.h',
                  'source/integration/bizhawk/core/Puae6/WHDLoadData.generated.cs'}
        if not shared <= members:
            raise ValueError('Incomplete shared WHDLoad generator outputs')
    return manifest


def state_identity_smoke(host, core, system, work, env, pal_rows):
    """Valid foreign config, not a damaged header; only installed executables."""
    pal, pal_end = work / 'pal.state', work / 'pal-end.state'
    def checked(*args):
        output = run([host, core, '--system', system, *args], cwd=work, env=env)
        if 'VIOLATION' in output:
            raise RuntimeError('Installed state contract violation:\n' + output[-2000:])
        return output
    def views(output, count):
        rows = [line.split(' ', 2)[2] for line in output.splitlines() if line.startswith('run ')]
        if len(rows) != count or any(not all(key in row for key in ('ahash=', 'unit=', 'ticks=')) for row in rows):
            raise RuntimeError('Incomplete installed state future:\n' + output[-2000:])
        return rows
    def equal(left, right):
        if not left.is_file() or not right.is_file() or not left.stat().st_size or left.read_bytes() != right.read_bytes():
            raise RuntimeError('Installed fullstate/AV differs: ' + left.name + ' / ' + right.name)
    # A second PAL process must accept the actual file and reproduce its future.
    again = work / 'pal-again.state'
    positive = checked(*PAL_SMOKE_OPTIONS, '--runs', '3', '--do', f'0:loadfile:{pal}:pos=6', '--do', f'3:savefile:{again}')
    if f'state load {pal} pos=6 ok=1' not in positive or views(positive, 3) != pal_rows[6:9]:
        raise RuntimeError('Installed PAL file failed positive restore:\n' + positive[-2000:])
    equal(pal_end, again)
    outputs = {}
    for name in ('control', 'trial'):
        target = work / name; target.mkdir()
        for folder in ('audio', 'video'): (target / folder).mkdir()
        arguments = ['--set', 'puae6_video_standard=NTSC', '--set', 'puae6_video_line_mode=double', '--no-dupe', '--runs', '8',
                     '--dump-audio', target / 'audio', '--dump-pictures', target / 'video', '--dump-from', '6']
        if name == 'trial':
            arguments += ['--do', f'5:savefile:{target}/before.state', '--do', f'5:loadfile:{pal}',
                          '--do', f'5:savefile:{target}/after.state']
        arguments += ['--do', f'8:savefile:{target}/final.state']
        outputs[name] = checked(*arguments)
    refused = outputs['trial']
    if (f'state load {pal} exact ok=0' not in refused or
            'p6_state_load failed (10): state refused: config_hash:' not in refused):
        raise RuntimeError('Installed foreign state was not refused by config identity:\n' + refused[-2000:])
    equal(work / 'trial/before.state', work / 'trial/after.state')
    if views(outputs['control'], 8) != views(refused, 8):
        raise RuntimeError('Installed continuation changed after incompatible state')
    equal(work / 'control/final.state', work / 'trial/final.state')
    expected = {f'audio/audio-{n:03d}.bin' for n in range(1, 9)} | {f'video/picture-{n:03d}.bin' for n in range(6, 9)}
    for name in ('control', 'trial'):
        actual = {p.relative_to(work / name).as_posix() for folder in ('audio', 'video')
                  for p in (work / name / folder).iterdir() if p.is_file()}
        if actual != expected: raise RuntimeError('Incomplete installed AV dumps: ' + name)
    for name in sorted(expected): equal(work / 'control' / name, work / 'trial' / name)
    return dict(pal_file_restored_units=3, incompatible_config_rejected=True, rejection_state_unchanged=True,
                incompatible_future_units_compared=8, incompatible_av_dumps_compared=len(expected))


def micro_rom(directory, work):
    """Build only the own micro-ROM shipped in the selected source archive."""
    with tarfile.open(directory / 'source.tar.gz') as archive:
        for name in ('build_rom.py', 'boot.s'):
            with archive.extractfile('source/tests/fixtures/microrom/' + name) as member:
                (work / name).write_bytes(member.read())
    spec = importlib.util.spec_from_file_location('package_microrom', work / 'build_rom.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build((work / 'boot.s').read_text())


def legacy_diagnostics(text):
    # Wall times use the legacy process culture (e.g. 0.1s or 0,1s); discard
    # those tokens rather than changing its environment or comparing host time.
    done = re.findall(r'^done frames=3 nonzero_audio=(\d+) elapsed=\S+s$', text, re.M)
    frame = re.findall(r'^frame\s+0 t=\s*\S+s (.+)$', text, re.M)
    if len(done) != 1 or len(frame) != 1 or not re.fullmatch(
            r'[1-9]\d*x[1-9]\d* vis=\s*\d+% sig=[0-9a-f]{8} same=\d+ audio_nz=\d+ cd=[01]', frame[0]):
        raise RuntimeError('Incomplete legacy advancement diagnostics:\n' + text[-2000:])
    return frame[0], done[0]


def coexistence(directory, legacy_host):
    """Run the copied legacy UAE around an installed PUAE6 smoke, never in its source tree.

    The legacy CLI exposes video, PCM and the CD light; no cross-engine state or
    internal hardware digest equivalence is implied by this isolated coexistence.
    """
    directory, legacy_host = Path(directory).resolve(), Path(legacy_host)
    if legacy_host.is_symlink() or not legacy_host.is_file() or legacy_host.name != 'Waterbox.UaeCd32Headless':
        raise ValueError('P6_LEGACY_UAE_HOST requires the regular legacy apphost')
    legacy_host = legacy_host.resolve()
    origin = legacy_host.parent
    zstd = Path('/opt/homebrew/Cellar/zstd/1.5.7_1/lib/libzstd.1.5.7.dylib')
    zstd_sha = 'e2847c4613b386683c234913ae3b7b04299254096caf7616e3b3cd9bb97a39ab'
    def inventory(root):
        files = {}
        for path in sorted(root.rglob('*')):
            if root == origin and path == origin / 'libzstd.so.1':
                # This one observed dependency is copied as bytes, never a live link.
                if (not path.is_symlink() or path.readlink() != Path('/opt/homebrew/lib/libzstd.1.dylib')
                        or path.resolve(strict=True) != zstd or path.stat().st_size != 649648 or sha(path) != zstd_sha):
                    raise ValueError('Legacy Zstd dependency differs from its observed pin')
            elif path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('Unsupported legacy closure entry: ' + str(path))
            if path.is_file(): files[path.relative_to(root).as_posix()] = sha(path)
        return files
    originals = inventory(origin)
    required = {legacy_host.name, legacy_host.name + '.dll', legacy_host.name + '.deps.json',
                legacy_host.name + '.runtimeconfig.json', 'libwaterboxhost.dylib', 'dll/uae.wbx', 'libzstd.so.1'}
    if len(originals) != 19 or not required <= originals.keys():
        raise ValueError('Expected the complete 19-file legacy distribution')
    verify(directory)
    dotnet = shutil.which('dotnet')
    if not dotnet: raise RuntimeError('Legacy coexistence requires the existing dotnet host; no installation is attempted')
    dotnet = Path(dotnet).resolve()
    # Homebrew's bin wrapper invokes protected /bin/bash, which strips DYLD.
    if dotnet.read_bytes().startswith(b'#!'):
        dotnet = dotnet.parent.parent / 'libexec/dotnet'
    if not dotnet.is_file() or dotnet.read_bytes()[:8] != b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01':
        raise RuntimeError('Legacy coexistence requires the existing native ARM64 dotnet host')
    sdk = run([dotnet, '--version']).strip()
    runtimes = re.findall(r'^Microsoft.NETCore.App ([^ ]+) \[([^\]]+)\]$', run([dotnet, '--list-runtimes']), re.M)
    if sdk != '10.0.402' or len(runtimes) != 1 or runtimes[0][0] != '10.0.13':
        raise RuntimeError('Legacy coexistence is scoped to existing SDK10.0.402/runtime10.0.13')
    (ROOT / 'build').mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='p6-coexistence-', dir=ROOT / 'build') as temporary:
            work = Path(temporary)
            staged = work / 'legacy'
            shutil.copytree(origin, staged)
            if inventory(staged) != originals: raise RuntimeError('Legacy staging differs from original')
            system = work / 'system'; system.mkdir()
            (system / 'Own.A500.rom').write_bytes(micro_rom(directory, work))
            controls = {'STATE_TEST', 'STATE_SAVE', 'STATE_LOAD', 'SAVERAM', 'EXTRA_HDF', 'ROWDIFF',
                        'CD32_FMV', 'CD_VOLUME', 'PNG_INTERVAL', 'WAV_DUMP', 'AUDIO_LOG'}
            env = {k: v for k, v in os.environ.items()
                   if k not in controls and not k.startswith(('SYNC_', 'DYLD_', 'LD_'))}
            env.update(SYNC_MACHINE='A500_OCS_130_512K_512K', SYNC_CD_CONTROLLER='None', PNG_INTERVAL='1',
                       DOTNET_CLI_HOME=str(work / 'dotnet-home'), DOTNET_CLI_TELEMETRY_OPTOUT='1',
                       DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_ROOT=str(dotnet.parent),
                       DYLD_LIBRARY_PATH=str(staged), BIZHAWK_HOME=str(staged), BIZHAWK_DATA_HOME=str(work))
            def legacy(name):
                output = work / name; output.mkdir()
                # Run the unchanged net8 DLL on the explicitly authorized existing net10 host.
                text = run([dotnet, 'exec', '--roll-forward', 'Major', staged / (legacy_host.name + '.dll'),
                            system, 'none', '3', output], cwd=work,
                           env={**env, 'WAV_DUMP': str(output / 'audio.wav'), 'AUDIO_LOG': str(output / 'audio.csv')})
                diagnostics = legacy_diagnostics(text)
                expected = {f'frame-{n:06d}.png' for n in range(4)} | {'audio.wav', 'audio.csv'}
                if {p.name for p in output.iterdir()} != expected:
                    raise RuntimeError('Incomplete legacy AV outputs')
                for n in range(4):
                    png = (output / f'frame-{n:06d}.png').read_bytes()
                    if len(png) < 33 or png[:8] != b'\x89PNG\r\n\x1a\n' or min(struct.unpack_from('>II', png, 16)) == 0:
                        raise RuntimeError('Invalid legacy video output')
                rows = (output / 'audio.csv').read_text().splitlines()
                if len(rows) != 4 or rows[0] != 'frame,count,nz': raise RuntimeError('Incomplete legacy PCM log')
                samples = 0
                for n, row in enumerate(rows[1:]):
                    fields = row.split(',')
                    if len(fields) != 3 or not all(f.isdecimal() for f in fields): raise RuntimeError('Invalid legacy PCM log')
                    index, count, nz = map(int, fields)
                    if index != n or count <= 0 or nz > count * 2: raise RuntimeError('Missing legacy audio advance')
                    samples += count
                wav = (output / 'audio.wav').read_bytes()
                if (len(wav) != 44 + samples * 4 or wav[:4] != b'RIFF' or wav[8:16] != b'WAVEfmt '
                        or wav[36:40] != b'data' or struct.unpack_from('<I', wav, 40)[0] != samples * 4):
                    raise RuntimeError('Incomplete legacy PCM output')
                return inventory(output), diagnostics
            before = legacy('before')
            preview = smoke(directory)
            after = legacy('after')
            if before != after: raise RuntimeError('Legacy AV/CD diagnostics changed around installed PUAE6')
    finally:
        if inventory(origin) != originals: raise RuntimeError('Original legacy distribution changed')
    return {'legacy_frames': 3, 'legacy_files_unchanged': len(originals), 'legacy_outputs_compared': len(before[0]),
            'legacy_original_sha256': originals, 'legacy_dotnet_sdk': sdk, 'legacy_runtime': runtimes[0][0], 'legacy_dotnet_host': str(dotnet),
            'legacy_launch': 'dotnet exec --roll-forward Major (copied DLL)', 'preview': preview}


def smoke(directory):
    """Exercise only extracted binaries and the public micro-ROM sources shipped with them."""
    directory = Path(directory).resolve()
    manifest = verify(directory)
    if manifest['platform'] != native_platform():
        raise ValueError('Installed smoke requires the matching native package platform')
    with tempfile.TemporaryDirectory(prefix='p6-installed-') as temporary:
        work = Path(temporary)
        system = work / 'system'
        system.mkdir()
        rom = system / 'kick34005.A500'
        rom.write_bytes(micro_rom(directory, work))
        host, core = (directory / 'bin' / n for n in ('p6_libretro_host', products(manifest['platform'])[0]))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_'))}
        output = run([host, core, '--system', system, *PAL_SMOKE_OPTIONS, '--runs', '12', '--do', '6:save:s',
                      '--do', f'6:savefile:{work}/pal.state', '--do', '9:load:s',
                      '--do', f'12:savefile:{work}/pal-end.state'], cwd=work, env=env)
        rows = [line.split(' ', 2)[2] for line in output.splitlines() if line.startswith('run ')]
        if len(rows) != 12 or rows[6:9] != rows[9:12] or 'state load s exact ok=1' not in output or 'VIOLATION' in output:
            raise RuntimeError('Installed libretro advance/restore failed:\n' + output[-2000:])
        state_checks = state_identity_smoke(host, core, system, work, env, rows)
        empty = work / 'empty-system'
        empty.mkdir()
        missing = subprocess.run([str(host), str(core), '--system', str(empty)], cwd=work, env=env,
                                 capture_output=True, text=True, timeout=60)
        if missing.returncode != 3 or 'no Kickstart' not in missing.stdout + missing.stderr:
            raise RuntimeError('Installed core did not reject missing firmware')
        headless = run([directory / 'bin/p6_headless', '--run', rom, '--units', '3'], cwd=work, env=env)
        if len([line for line in headless.splitlines() if line.startswith('unit ')]) != 3:
            raise RuntimeError('Installed headless did not advance')
    return {'libretro_units': 12, 'restored_units_compared': 3, 'headless_units': 3, 'missing_firmware_rejected': True, **state_checks}


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
    target = native_platform()
    revision = clean_revision()
    paths = source_paths()
    tree = Path(tree).resolve()
    tree.relative_to(ROOT / 'build')
    run(configure_command(target, tree), cwd=ROOT)
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
        for name in products(target):
            shutil.copy2(tree / name, stage / 'bin' / name)
        (stage / 'licenses').mkdir()
        for name, data in notice_files(policy, lambda path: (ROOT / path).read_bytes()).items():
            (stage / 'licenses' / name).write_bytes(data)
        shutil.copyfile(ROOT / 'docs/user/native-preview.md', stage / 'LEEME.md')
        with (stage / 'source.tar.gz').open('wb') as stream:
            subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=source/', revision, '--', *paths],
                           cwd=ROOT, stdout=stream, check=True, timeout=120)
        manifest = {'schema_version': 1, 'kind': 'local-preview', 'platform': target, 'revision': revision,
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
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build/native-release')
    args = parser.parse_args()
    args.archive = args.archive or ROOT / ('build/product/puae6-preview-' + native_platform() + '.zip')
    if args.command == 'build':
        result = build(args.archive, args.build_dir)
    else:
        with tempfile.TemporaryDirectory(prefix='p6-preview-check-') as temporary:
            unpack(args.archive, temporary)
            result = smoke(temporary)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
