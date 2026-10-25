"""Exercise the real Cocoa window of the isolated self-contained MacHawk app."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('P6_TEST_MACHAWK_GUI') == '1', 'explicit macOS GUI package required')
class MacHawkGuiTests(unittest.TestCase):
    def test_visible_frame_input_and_exact_state_replay(self):
        from tests.fixtures.microrom import build_rom
        default = ROOT / 'build/machawk-puae6/waterbox/puae6/build/gui-probe/BizHawk.Client.MacHawk.app'
        app = Path(os.environ.get('P6_MACHAWK_APP', default)).resolve()
        executable = app / 'Contents/MacOS/MacHawk'
        self.assertTrue(executable.is_file(), 'publish the app explicitly before the GUI gate')
        with tempfile.TemporaryDirectory(prefix='machawk-gui-', dir=ROOT / 'build') as temporary:
            directory = Path(temporary)
            source = (ROOT / 'tests/fixtures/microrom/input_all.s').read_text()
            # Original MIT test ROM, with an explicit red background for the native bitmap check.
            from tests.scenarios.audio.audio import Tone, rom_source
            tone = rom_source(Tone('gui', (0, 1), volume=16))
            tone_setup = tone[tone.index('    MOVE.W #$000F'):tone.index('loop:')]
            source = source.replace('    MOVE.L #kbd', tone_setup + '    MOVE.L #kbd', 1)
            source = source.replace('loop:\n', 'loop:\n    MOVE.W #$0F00,$DFF180.L\n', 1)
            rom = directory / 'Own.rom'; rom.write_bytes(build_rom.build(source))
            disk = directory / 'Own.adf'; disk.write_bytes(bytes(901120))
            environment = {**os.environ, 'MACHAWK_DATA_DIRECTORY': str(directory / 'user'),
                           'DOTNET_ROOT': str(directory / 'no-installed-dotnet'), 'DOTNET_MULTILEVEL_LOOKUP': '0'}
            result = subprocess.run([str(executable), '--gui-check', str(rom), str(disk)],
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            markers = [line for line in result.stdout.splitlines() if line.startswith('MACHAWK_GUI_PASS ')]
            self.assertEqual(len(markers), 1, result.stdout[-2000:] + result.stderr[-2000:])
            self.assertIn('replay=4 state/video/PCM=exact input=observed mouse=observed audio=played+cleared', markers[0])
            self.assertNotIn('System.Exception', result.stderr)
            print(markers[0])


if __name__ == '__main__':
    unittest.main()
