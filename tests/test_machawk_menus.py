"""Compile menu bindings against the actual integrated MacHawk core types."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('P6_TEST_MACHAWK_MENUS') == '1',
                     'explicit real EmuHawk build required')
class MacHawkMenuTests(unittest.TestCase):
    def test_real_emuhawk_menu_bindings_compile(self):
        tree = Path(os.environ.get('P6_MACHAWK_TREE', ROOT / 'build/machawk-puae6')).resolve()
        result = subprocess.run([
            'dotnet', 'build', 'src/BizHawk.Client.EmuHawk/BizHawk.Client.EmuHawk.csproj',
            '-c', 'Release', '--nologo', '-p:InstallGitHooks=false',
            '-p:RestoreSources=' + str(Path.home() / '.nuget/packages'),
        ], cwd=tree, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-7000:])
        self.assertTrue((tree / 'output/EmuHawk.exe').is_file())
        self.assertTrue((tree / 'output/dll/BizHawk.Emulation.Cores.dll').is_file())
        print('MacHawk Release: real generic settings, Amiga menu and HDF API bindings compile; no WinForms GUI execution claimed')


if __name__ == '__main__':
    unittest.main()
