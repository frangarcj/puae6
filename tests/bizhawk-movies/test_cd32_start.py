"""Real Client.Common net48 BK2/TASproj, real CD32 core and Waterbox; temp assets only."""
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'integration/bizhawk/patches'))
import build_tree
import build_waterbox as wb


@functools.cache
def movie_runner():
    assets = [os.environ.get(k) for k in ('P6_CD_CUE_FILE', 'P6_CD32_ROM_MAIN', 'P6_CD32_ROM_EXT')]
    receipt = wb.BUILD / 'puae6/puae6-receipt.json'
    if not all(assets) or not receipt.is_file() or not wb.runtime_receipt_path().is_file() or not shutil.which('dotnet'):
        raise unittest.SkipTest('BLOCKED: authorized CD32 firmware, CD and built guest required')
    wb.check_guest_matches_trees(receipt)
    sha = json.loads(receipt.read_text())['guest']['sha256']
    build_tree.check_patches()
    build_tree.build_emuhawk(sha)  # Compiles the real MainForm capture hook too; does not run WinForms.
    common = build_tree.TREE_EMUHAWK / 'src/BizHawk.Client.Common/bin/Debug'
    core = build_tree.TREE_EMUHAWK / 'src/BizHawk.Emulation.Cores/bin/Debug'
    runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
    if wb.sha256(Path(runtime['path'])) != runtime['sha256']:
        raise AssertionError('runtime dylib differs from the verified receipt')
    runtime = runtime['path']
    tmp = tempfile.TemporaryDirectory(prefix='cd-movies-', dir=ROOT / 'build')
    unittest.addModuleCleanup(tmp.cleanup)
    work = Path(tmp.name)
    env = {**os.environ, 'DOTNET_NOLOGO': '1', 'DOTNET_ROLL_FORWARD': 'Major',
           'NUGET_PACKAGES': str(ROOT / 'build/bizhawk-core/nuget'), 'TMPDIR': str(work)}
    # Pinned BizHawk requests the Linux soname on Unix. Use the installed codec, without copying/installing binaries.
    if sys.platform == 'darwin':
        zstd = Path('/opt/homebrew/lib/libzstd.dylib')
        if not zstd.is_file():
            raise unittest.SkipTest('BLOCKED: installed libzstd required by real BK2/TAS codec')
        (work / 'libzstd.so.1').symlink_to(zstd)
    project = work / 'Cd32Loader.csproj'
    shutil.copyfile(ROOT / 'tests/media/csharp-disc/Cd32Loader.csproj', project)
    result = subprocess.run(['dotnet', 'build', str(project), '-m:1', '-v:q', f'-p:Puae6Root={ROOT}',
                             f'-p:CoreAssemblyDir={core}', f'-p:MovieAssemblyDir={common}',
                             f'-p:RestoreSources={Path.home() / ".nuget/packages"}'], env=env, capture_output=True, text=True)
    if result.returncode: raise AssertionError(result.stdout + result.stderr)
    return work, env, assets, runtime, sha


def run_movies(test, modes, assertion, count):
    work, env, assets, runtime, sha = movie_runner()
    for mode in modes:
        result = subprocess.run(['dotnet', str(work / 'bin/Debug/net8.0/Cd32Loader.dll'), str(work), assets[0], runtime,
                                 str(wb.BUILD / 'puae6/release/puae6.wbx'), sha, mode, *assets[1:]],
                                cwd=work, env=env, capture_output=True, text=True, timeout=240)
        test.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
        test.assertEqual(result.stdout.count(assertion), count)
        print('\n'.join(line for line in result.stdout.splitlines() if line.startswith('Real CD32 movie')))


class Cd32MovieStartTests(unittest.TestCase):
    def test_real_bk2_tas_starts_and_seek_in_new_process(self):
        run_movies(self, ('moviesave', 'movieload'), 'record/load/seek/suppressed/new process PASS', 6)


class Cd32MovieSwapTests(unittest.TestCase):
    def test_real_bk2_tas_cd_swap_seek_branch_and_new_process(self):
        # SwapMovies creates four legal synthetic ISOs; it does not open the legacy CD asset.
        run_movies(self, ('movieswapsave', 'movieswapload'), 'record/load/seek/branch/suppressed/new process PASS', 2)
