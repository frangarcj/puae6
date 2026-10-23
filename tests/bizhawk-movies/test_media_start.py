"""T80b: pure startup planning and real MovieSession/Waterbox replay are separate gates."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from tests.hdf import hdf_image, hdf_rom
from tests.hdf.volume_support import CONTENT, oracle, lha_archive, zip_file
from tests.scenarios.disk import disk

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'integration/bizhawk/patches')]
import build_tree
import build_waterbox as wb


def run(command):
    p = subprocess.run(command, cwd=WORK, env=ENV, capture_output=True, text=True, timeout=300)
    if p.returncode:
        raise AssertionError(f'exit {p.returncode}\n{p.stdout[-6000:]}\n{p.stderr[-6000:]}')
    return p.stdout


def setUpModule():
    global WORK, ENV, EXE, BEFORE
    if sys.platform != 'darwin' or not shutil.which('dotnet'):
        raise unittest.SkipTest('BLOCKED: Darwin and cached dotnet dependencies required')
    stack = ExitStack(); unittest.addModuleCleanup(stack.close)
    common = Path(subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT, text=True).strip())
    main = (ROOT / common).resolve().parent
    source = main.parent / 'MacHawk'
    old_git, old_source = build_tree.git, build_tree.MACHAWK
    stack.callback(setattr, build_tree, 'git', old_git); stack.callback(setattr, build_tree, 'MACHAWK', old_source)
    build_tree.MACHAWK = source; build_tree.git = lambda *a, **kw: old_git(*a, **{'cwd': source, **kw})
    BEFORE = (build_tree.git('rev-parse', 'HEAD').stdout, build_tree.git('diff', '--no-ext-diff').stdout)
    build_tree.check_patches(); (ROOT / 'build').mkdir(exist_ok=True)
    WORK = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='media-movies-', dir=ROOT / 'build')))
    # Cache only the immutable build inputs, keyed by all core/patch/recipe bytes.
    guest_receipt = os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT')
    guest_sha = json.loads(Path(guest_receipt).read_text())['guest']['sha256'] if guest_receipt else ''
    tree = ROOT / 'build/bizhawk-tree' / (build_tree.tree_key() + '-media-movies' + ('-' + guest_sha[:12] if guest_sha else ''))
    if build_tree.export_tree(tree, build_tree.EXPORT_EMUHAWK):
        build_tree.apply_patches(tree); build_tree.install_sources(tree)
    ENV = {**os.environ, 'DOTNET_NOLOGO': '1', 'DOTNET_ROLL_FORWARD': 'Major', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1',
           'DOTNET_CLI_HOME': str(WORK / 'dotnet'), 'BIZHAWK_HOME': str(WORK), 'NUGET_PACKAGES': str(main / 'build/bizhawk-core/nuget'), 'TMPDIR': str(WORK)}
    for name in ('DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
        ENV.pop(name, None)
    offline = f'-p:RestoreSources={Path.home() / ".nuget/packages"}'
    run(['dotnet', 'build', str(tree / 'src/BizHawk.Client.Common/BizHawk.Client.Common.csproj'), '-m:2', '-v:q', '-p:InstallGitHooks=false', f'-p:Puae6GuestSha256={guest_sha}', offline])
    project = WORK / 'MediaMovies.csproj'; shutil.copyfile(ROOT / 'tests/bizhawk-movies/MediaMovies.csproj', project)
    run(['dotnet', 'build', str(project), '-m:2', '-v:q', f'-p:Puae6Root={ROOT}', f'-p:AssemblyDir={tree / "src/BizHawk.Client.Common/bin/Debug"}', offline])
    EXE = WORK / 'bin/Debug/net8.0/MediaMovies.dll'
    bases = [disk.make_adf(0x41), hdf_image.rdb_image(), oracle(CONTENT)]
    for name, data in zip(('A.adf', 'H.hdf', 'V.ffs'), bases):
        (WORK / name).write_bytes(data)
    (WORK / 'loop.rom').write_bytes(hdf_rom.loop_rom()); (WORK / 'volume.lha').write_bytes(lha_archive(CONTENT))
    zip_file(WORK / 'volume.zip', CONTENT); zip_file(WORK / 'other.zip', {**CONTENT, 'Game/readme': b'other'})
    for seed in ('A', 'B'):
        current = [bytearray(b) for b in bases]
        for i, offset in enumerate((512 * 5 + 5, 4096 * 5 + 11, 4096 * 7 + 23)):
            current[i][offset] ^= 17 if seed == 'A' else 42
            if seed == 'B': (WORK / f'B{i}.bin').write_bytes(current[i])
        blob = b'P6SR' + struct.pack('<II', 2, 1) + struct.pack('<I', 0) + hashlib.sha256(bases[0]).digest() + struct.pack('<IH', 1, 5) + current[0][2560:3072] + struct.pack('<I', 2)
        for index, chunk in enumerate((5, 7)):
            blob += struct.pack('<I', index) + hashlib.sha256(bases[index + 1]).digest() + struct.pack('<QIII', len(bases[index + 1]), 4096, 1, chunk) + current[index + 1][chunk * 4096:(chunk + 1) * 4096]
        blob += hashlib.sha256(blob).digest()
        (WORK / f'{seed}.sram').write_bytes(blob + bytes(37))
        if seed == 'B': (WORK / 'B.sha').write_text(hashlib.sha256(blob).hexdigest())
    from importlib import import_module
    cartridges = import_module('tests.waterbox-state.test_cartridge_transport')
    for model in (1, 2, 3, 1200):
        firmware, raw = cartridges.images(model)
        (WORK / f'{model}.kick').write_bytes(firmware); (WORK / f'{model}.cart').write_bytes(raw)
    if guest_receipt:
        (WORK / 'dll').mkdir()
        shutil.copy2(json.loads(Path(guest_receipt).read_text())['guest']['path'], WORK / 'dll/puae6.wbx')


class MediaMovieIdentityTests(unittest.TestCase):
    def test_canonical_initial_media_without_guest(self):
        output = run(['dotnet', str(EXE), 'identity', str(WORK)])
        self.assertEqual(1, output.count(' PASS'), output); print(output, end='')
        self.assertEqual(BEFORE, (build_tree.git('rev-parse', 'HEAD').stdout, build_tree.git('diff', '--no-ext-diff').stdout))


@unittest.skipUnless(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT'), 'BLOCKED: falta guest coordinado actual')
class MediaMovieStartTests(unittest.TestCase):
    def test_real_bk2_tas_power_sram_replay_seek_and_new_process(self):
        receipt = Path(os.environ['P6_MEDIA_MOVIE_GUEST_RECEIPT']); data = json.loads(receipt.read_text())
        wb.check_guest_matches_trees(receipt, root=ROOT)
        self.assertTrue(all(wb.sha256(ROOT / path) == digest for path, digest in data['sources'].items()))
        for key, path in {'makefile': 'ports/waterbox/Makefile', 'common_mak': 'references/machawk/waterbox/common.mak',
                          'linkscript_aarch64': 'references/machawk/waterbox/linkscript_aarch64.T', 'build_recipe': 'tools/build_waterbox.py'}.items():
            self.assertEqual(data['recipe'][key], wb.sha256(ROOT / path))
        guest = Path(data['guest']['path']); self.assertEqual(data['guest']['sha256'], wb.sha256(guest)); wb.validate_elf(guest); wb.check_no_compiler_tls(guest)
        runtime = json.loads(wb.runtime_receipt_path().read_text())['runtime_dylib']; self.assertEqual(runtime['sha256'], wb.sha256(Path(runtime['path'])))
        for mode in ('save', 'load'):
            output = run(['dotnet', str(EXE), mode, str(WORK), runtime['path'], str(guest), data['guest']['sha256']])
            self.assertEqual(5, output.count(' PASS'), output); print(output, end='')


class HrtMonManagedConfigTests(unittest.TestCase):
    def test_public_setting_flags_controls_and_pinned_abi_without_guest(self):
        from tools.prepare_hrtmon import RAW_SHA
        output = run(['dotnet', str(EXE), 'hrtconfig', RAW_SHA])
        self.assertEqual(1, output.count(' PASS'), output); print(output, end='')


@unittest.skipUnless(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT') and os.environ.get('P6_KICKSTART_A1200_ROM'),
                     'BLOCKED: falta guest coordinado actual o Kickstart A1200 autorizado')
class HrtMonWrapperTests(unittest.TestCase):
    def test_public_freeze_bk2_tas_and_held_snapshot_in_new_process(self):
        receipt = Path(os.environ['P6_MEDIA_MOVIE_GUEST_RECEIPT']); data = json.loads(receipt.read_text())
        wb.check_guest_matches_trees(receipt, root=ROOT)
        self.assertTrue(all(wb.sha256(ROOT / path) == digest for path, digest in data['sources'].items()))
        for key, path in {'makefile': 'ports/waterbox/Makefile', 'common_mak': 'references/machawk/waterbox/common.mak',
                          'linkscript_aarch64': 'references/machawk/waterbox/linkscript_aarch64.T', 'build_recipe': 'tools/build_waterbox.py'}.items():
            self.assertEqual(data['recipe'][key], wb.sha256(ROOT / path))
        guest = Path(data['guest']['path']); self.assertEqual(data['guest']['sha256'], wb.sha256(guest)); wb.validate_elf(guest); wb.check_no_compiler_tls(guest)
        runtime = json.loads(wb.runtime_receipt_path().read_text())['runtime_dylib']; self.assertEqual(runtime['sha256'], wb.sha256(Path(runtime['path'])))
        for mode in ('hrtsave', 'hrtload'):
            output = run(['dotnet', str(EXE), mode, str(WORK), runtime['path'], str(guest), data['guest']['sha256'], os.environ['P6_KICKSTART_A1200_ROM']])
            self.assertEqual(2, output.count(' PASS'), output); print(output, end='')


class ActionReplayManagedConfigTests(unittest.TestCase):
    def test_public_models_provider_flags_identity_and_preflight(self):
        output = run(['dotnet', str(EXE), 'arconfig', str(WORK)])
        self.assertEqual(output.count(' PASS'), 1, output)
        self.assertEqual(BEFORE, (build_tree.git('rev-parse', 'HEAD').stdout, build_tree.git('diff', '--no-ext-diff').stdout))


@unittest.skipUnless(os.environ.get('P6_MEDIA_MOVIE_GUEST_RECEIPT'), 'BLOCKED: current coordinated guest required')
class ActionReplayWrapperTests(unittest.TestCase):
    def test_public_cartridges_held_state_cold_and_saveram_reboot(self):
        receipt = Path(os.environ['P6_MEDIA_MOVIE_GUEST_RECEIPT']); data = json.loads(receipt.read_text())
        wb.check_guest_matches_trees(receipt)
        self.assertTrue(all(wb.sha256(ROOT / path) == digest for path, digest in data['sources'].items()))
        guest = Path(data['guest']['path']); self.assertEqual(wb.sha256(guest), data['guest']['sha256'])
        wb.validate_elf(guest); wb.check_no_compiler_tls(guest)
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
        self.assertEqual(wb.sha256(Path(runtime['path'])), runtime['sha256'])
        for model in (1, 2, 3, 1200):
            for mode in ('arsave', 'arload'):
                output = run(['dotnet', str(EXE), mode, str(WORK), runtime['path'], str(guest), data['guest']['sha256'], str(model)])
                self.assertEqual(output.count(' PASS'), 1, output)
