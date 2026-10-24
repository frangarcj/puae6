"""Static scope/pin checks; never downloads or executes proprietary software."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NeroProducerProbeTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / 'tools/ci/nero_producer_probe.ps1').read_text()
        self.workflow = (ROOT / '.github/workflows/nero-producer-probe.yml').read_text()

    def test_manual_readonly_windows_job_has_pinned_checkout_and_no_publication(self):
        self.assertRegex(self.workflow, r'on:\s+workflow_dispatch:\s+permissions:')
        self.assertIn('contents: read', self.workflow)
        self.assertIn('runs-on: windows-2025', self.workflow)
        self.assertIn('timeout-minutes: 15', self.workflow)
        self.assertIn('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683', self.workflow)
        self.assertIn('persist-credentials: false', self.workflow)
        self.assertIn('shell: pwsh', self.workflow)
        self.assertIn('./tools/ci/nero_producer_probe.ps1', self.workflow)
        for token in ('pull_request:', 'push:', 'schedule:', 'upload-artifact', 'continue-on-error', 'secrets.', 'write'):
            self.assertNotIn(token, self.workflow)

    def test_fixed_archives_and_component_identity_precede_documented_discovery(self):
        pin = json.loads((ROOT / 'plan/upstream.lock.json').read_text())['reference_tools']['nero_2025']
        self.assertEqual(pin['version'], '27.0.1000')
        self.assertEqual((pin['bytes'], pin['sha256']), (275434744, '3fbc9dd234eb1dec0d5a51bd2c9df2dfad0144c5cdb3d1d353ba91c9f2618f94'))
        self.assertEqual((pin['payload']['bytes'], pin['payload']['sha256']), (80677840, '9fffbea66c939f3933cd4c1efe5b6e44491f21865a35035e8eb75a10611ebd52'))
        expected = {'Nero Burning ROM/NeroAPIEngine.dll': (1999184, '5c09c57a441d34af2a75d82e74acce412c1bbeb6d31af7645849a7f060039879'),
                    'Nero Burning ROM/uimage.dll': (79696, '59522845afe8a67110b5934cb8583b1e172994f948420b67c2abcbbbd77ba623'),
                    'Nero Burning ROM/uImageGen.dll': (46928, '5d6523cf6dc558667ea3546a9b893d2b0cf4e1ee413aaf4586d3e92e1133587c')}
        self.assertEqual({n['path']: (n['bytes'], n['sha256']) for n in pin['components']}, expected)
        self.assertEqual(len(pin['components']), 3)
        for sha in (pin['sha256'], pin['payload']['sha256'], *(v[1] for v in expected.values())): self.assertNotIn(sha, self.script)
        self.assertIn('$pin = $lock.reference_tools.nero_2025', self.script)
        self.assertEqual(re.findall(r'https://[^\s\']+', self.script), ['https://ecloud.nero.com/public.php/dav/files/pBxLJzgPaNtAJBi'])
        self.assertEqual(pin['url'], 'https://ecloud.nero.com/public.php/dav/files/pBxLJzgPaNtAJBi')
        self.assertEqual(pin['payload']['path'], 'NeroInstaller/NeroBurningROM.zip')
        self.assertEqual(re.findall(r'ArgumentList\.Add\(([^)]*)\)', self.script),
                         ["'--listdrives'", "'--no_user_interaction'", "'--no_error_log'"])
        self.assertEqual(pin['command_path'], 'Nero Burning ROM/NeroCmd.exe')
        self.assertIn('$_.FullName -ceq $CommandMember', self.script)
        self.assertIn('$CommandMember = $pin.command_path', self.script)
        self.assertIn('Read-Discovery $command ([IO.Path]::GetDirectoryName($command))', self.script)
        main = self.script[self.script.index('try {\n    Require ($IsWindows'):]
        self.assertLess(main.index('Verify-File $archive'), main.index('Extract-App $archive'))
        self.assertLess(main.index('Extract-App $archive'), main.index('Read-Discovery $command'))

    def test_no_installer_or_system_mutation_fallback_and_failures_clean_owned_temp(self):
        for token in ('Invoke-Expression', 'Start-Process', 'msiexec', 'winget', 'choco', 'Set-ItemProperty',
                      'New-Service', 'Start-Service', 'reg.exe', 'AcceptEula', 'ExecutionPolicy', '--write', '--help'):
            self.assertNotIn(token.lower(), self.script.lower())
        self.assertIn('$handler.AllowAutoRedirect = $false', self.script)
        self.assertIn('$total -le $InstallerBytes', self.script)
        self.assertIn('$commands.Count -eq 1', self.script)
        self.assertIn('$file.Length -eq $commands[0].Length', self.script)
        self.assertIn('ReparsePoint', self.script)
        self.assertIn('$process.WaitForExit(60000)', self.script)
        self.assertIn('if ($started -and -not $process.HasExited)', self.script)
        self.assertNotIn('$process.Id', self.script)
        self.assertIn('P6_NERO_PROBE BLOCKED:', self.script)
        self.assertIn("'NeroCmd discovery ExitCode={0} (0x{1:X8})'", self.script)
        self.assertIn('[BitConverter]::GetBytes([int]$exitCode)', self.script)
        self.assertIn('$exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($text)', self.script)
        self.assertIn('DISCOVERY_ONLY; no producer, TOC or image generation capability established', self.script)
        self.assertNotIn('dependencies/license/COM', self.script)
        self.assertIn('exit 2', self.script)
        self.assertRegex(self.script, r'(?s)finally \{\s+if \(\$Created.*Remove-Item -LiteralPath \$Owned -Recurse -Force')

    def test_registration_is_authenticated_readonly_and_precedes_download_or_execution(self):
        pin = json.loads((ROOT / 'plan/upstream.lock.json').read_text())['reference_tools']['nero_2025']['registration']
        self.assertEqual(pin, {
            'hive': 'ClassesRoot', 'view': 'Registry32',
            'key': r'CLSID\{8c3eda3f-f182-4617-a352-00b48a8e49f2}\InprocServer32',
            'value_name': '', 'value_kind': 'REG_SZ', 'filename': 'AdvrCntr6.dll', 'bytes': 12027720,
            'sha256': '05580111f2f7604b3c468badc1c2519a5664168c9371aabc2a66b620372e4f9c', 'export_ordinal': 5,
            'source_payload': {'path': 'NeroInstaller/AdvrCntr6.zip', 'bytes': 11150970,
                               'sha256': '561d672e263a26732dadf23f79c8f32a544e8286010bc371071a6bb9a4f2f1fc',
                               'member': 'AdvrCntr6/AdvrCntr6.dll'}})
        self.assertNotIn(pin['sha256'], self.script)
        main = self.script[self.script.index('try {\n    Require ($IsWindows'):]
        for operation in ('Download-Fixed $installer', '$seven = Get-Command', '$null = New-Item', 'Extract-App $archive', 'Read-Discovery $command'):
            self.assertLess(main.index('Verify-Registration $pin.registration'), main.index(operation))
        self.assertIn('[Microsoft.Win32.RegistryHive]::ClassesRoot', self.script)
        self.assertIn('[Microsoft.Win32.RegistryView]::Registry32', self.script)
        self.assertIn('$hive.OpenSubKey($Registration.key, $false)', self.script)
        self.assertIn('[Microsoft.Win32.RegistryValueKind]::String', self.script)
        self.assertIn('[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames', self.script)
        self.assertIn('[IO.Path]::IsPathFullyQualified($path)', self.script)
        self.assertIn('Verify-File $path ([long]$Registration.bytes) $Registration.sha256', self.script)
        self.assertIn('Prerequisite absent: HKCR Registry32', self.script)
        for forbidden in ('SetValue(', 'CreateSubKey(', 'DeleteSubKey', 'LoadLibrary', 'regsvr32', '40135161543'):
            self.assertNotIn(forbidden, self.script)


if __name__ == '__main__': unittest.main()
