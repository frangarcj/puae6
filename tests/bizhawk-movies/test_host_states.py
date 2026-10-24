"""T80a: real pinned Client.Common state managers on Darwin, without a guest."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'integration/bizhawk/patches'))
import build_tree


def main_checkout():
    common = Path(subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT, text=True).strip())
    return (ROOT / common).resolve().parent


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('dotnet'), 'BLOCKED: Darwin and installed dotnet required')
class DarwinHostStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT.parent / 'MacHawk'
        if not (source / '.git').exists():
            source = main_checkout().parent / 'MacHawk'
        if not (source / '.git').exists():
            raise unittest.SkipTest('BLOCKED: pinned MacHawk checkout required')
        try:
            ctypes.CDLL('libzstd.dylib')
        except OSError as error:
            raise unittest.SkipTest(f'BLOCKED: installed libzstd.dylib required: {error}')
        cls.addClassCleanup(setattr, build_tree, 'MACHAWK', build_tree.MACHAWK)
        build_tree.MACHAWK = source
        original_git = build_tree.git
        build_tree.git = lambda *args, **kwargs: original_git(*args, **{'cwd': source, **kwargs})
        cls.addClassCleanup(setattr, build_tree, 'git', original_git)
        cls.before = (build_tree.git('rev-parse', 'HEAD').stdout, build_tree.git('diff', '--no-ext-diff').stdout)
        build_tree.check_patches()
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='host-states-', dir=ROOT / 'build')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        tree = cls.work / 'bizhawk'
        build_tree.export_tree(tree, build_tree.EXPORT_EMUHAWK)
        build_tree.apply_patches(tree)
        build_tree.install_sources(tree)
        # Client.Common references other managed projects; none builds an engine or guest.
        cache = main_checkout() / 'build/bizhawk-core/nuget'
        cls.env = {**os.environ, 'DOTNET_NOLOGO': '1', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1',
                   'DOTNET_ROLL_FORWARD': 'Major', 'DOTNET_CLI_HOME': str(cls.work / 'dotnet'),
                   'NUGET_PACKAGES': str(cache), 'TMPDIR': str(cls.work)}
        for name in ('DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
            cls.env.pop(name, None)
        cls.run_command(['dotnet', 'build', str(tree / 'src/BizHawk.Client.Common/BizHawk.Client.Common.csproj'),
                         '-m:2', '-v:q', '-p:InstallGitHooks=false', f'-p:RestoreSources={Path.home() / ".nuget/packages"}'])
        assemblies = tree / 'src/BizHawk.Client.Common/bin/Debug'
        project = cls.work / 'HostStates.csproj'
        shutil.copyfile(ROOT / 'tests/bizhawk-movies/HostStates.csproj', project)
        cls.run_command(['dotnet', 'build', str(project), '-m:2', '-v:q', f'-p:Puae6Root={ROOT}',
                         f'-p:AssemblyDir={assemblies}', f'-p:RestoreSources={Path.home() / ".nuget/packages"}'])
        cls.exe = cls.work / 'bin/Debug/net8.0/HostStates.dll'

    @classmethod
    def run_command(cls, command):
        result = subprocess.run(command, cwd=cls.work, env=cls.env, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise AssertionError(result.stdout[-5000:] + result.stderr[-5000:])
        return result.stdout

    def test_paged_and_compressed_zwinder_history_in_new_process(self):
        for mode in ('mmap', 'codec', 'save', 'load'):
            output = self.run_command(['dotnet', str(self.exe), mode, str(self.work)])
            if mode in ('save', 'load'):
                self.assertEqual(4, output.count(' PASS'), output)
                print(output, end='')
        self.assertFalse((self.work / 'libzstd.so.1').exists())
        self.assertEqual(self.before, (build_tree.git('rev-parse', 'HEAD').stdout, build_tree.git('diff', '--no-ext-diff').stdout))


if __name__ == '__main__':
    unittest.main()
