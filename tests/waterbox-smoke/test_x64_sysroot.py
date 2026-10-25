"""Own command/artifact fixtures; these never certify a compiled sysroot."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import build_waterbox_x64_sysroot as recipe


class X64SysrootTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='x64-sysroot-fixture-', dir=ROOT / 'build')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.spec = json.loads((ROOT / 'plan/extensions/waterbox-x64.json').read_text())
        self.pin = self.spec['sysroot_rebuild']
        self.provenance = dict(musl='own musl fixture', machawk='own recipe fixture', llvm={'commit':'own'})

    def commands(self):
        inspected = dict(Id=self.pin['image_id'], Os='linux', Architecture='amd64')
        return patch.object(recipe.frozen, 'command', side_effect=[json.dumps(inspected), json.dumps(self.pin['tools']), ''])

    def test_offline_command_mounts_inputs_readonly_and_does_not_execute(self):
        calls = self.commands()
        with (calls as command, patch.object(recipe, 'verify_inputs', return_value=(self.work, self.work, self.provenance))):
            result = recipe.rebuild(self.work, self.work / 'output')
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertFalse(result['source_rebuild_verified'])
        self.assertFalse((self.work / 'output').exists())
        self.assertEqual(command.call_count, 2) # Inspect/preflight only, never the build shell.
        cmd = result['command']
        self.assertIn('--pull=never', cmd); self.assertIn('--network=none', cmd)
        mounts = [cmd[i+1] for i, value in enumerate(cmd) if value == '--mount']
        self.assertEqual(sum(m.endswith(',readonly') for m in mounts), 3)
        self.assertEqual(len(mounts), 4)
        self.assertIn('exec /usr/bin/ld', cmd[-1])
        self.assertNotIn('apt-get', cmd[-1])
        self.assertIn('git apply --check', cmd[-1])
        self.assertNotIn('@MUSL_FLAGS@', cmd[-1])

    def test_reconstruction_can_have_new_identity_without_mutating_product_pin(self):
        before = (ROOT / 'plan/extensions/waterbox-x64.json').read_bytes()
        rebuilt = dict(inventory_sha256='own new inventory', file_count=1)
        calls = self.commands()
        with (calls, patch.object(recipe, 'verify_inputs', return_value=(self.work, self.work, self.provenance)),
              patch.object(recipe, 'validate_result', return_value=rebuilt)):
            result = recipe.rebuild(self.work, self.work / 'output', execute=True)
        self.assertTrue(result['source_rebuild_verified'])
        self.assertFalse(result['matches_historical_inventory'])
        self.assertFalse(result['publication_ready'])
        self.assertFalse(result['runtime_verified'])
        self.assertEqual((ROOT / 'plan/extensions/waterbox-x64.json').read_bytes(), before)
        self.assertTrue((self.work / 'output/receipt.json').is_file())

    def test_bad_docker_tools_fail_before_reserving_output(self):
        inspected = dict(Id=self.pin['image_id'], Os='linux', Architecture='amd64')
        with (patch.object(recipe, 'verify_inputs', return_value=(self.work, self.work, self.provenance)),
              patch.object(recipe.frozen, 'command', side_effect=[json.dumps(inspected), '{}'])):
            with self.assertRaisesRegex(recipe.frozen.Blocked, 'tools differ'):
                recipe.rebuild(self.work, self.work / 'output')
        self.assertFalse((self.work / 'output').exists())

    def test_source_root_verifier_restores_arm_helper_and_rejects_another_lock(self):
        previous = recipe.frozen.ROOT
        with recipe.input_root(ROOT):
            self.assertEqual(recipe.frozen.ROOT, ROOT)
        self.assertEqual(recipe.frozen.ROOT, previous)
        (self.work / 'plan').mkdir(); (self.work / 'plan/upstream.lock.json').write_text('{}')
        with self.assertRaisesRegex(recipe.frozen.Blocked, 'another upstream lock'):
            with recipe.input_root(self.work):
                self.fail('unreachable')
        self.assertEqual(recipe.frozen.ROOT, previous)

    def test_only_new_owned_build_output_and_x64_archive_closure(self):
        with self.assertRaises(recipe.frozen.Blocked): recipe.output_path(ROOT / 'outside-build')
        with self.assertRaises(recipe.frozen.Blocked): recipe.output_path(self.work)
        sysroot = self.work / 'sysroot'; (sysroot / 'lib/linux').mkdir(parents=True)
        crt = bytearray(64); crt[:6] = b'\x7fELF\x02\x01'; struct.pack_into('<H', crt, 18, 183)
        (sysroot / 'lib/crt1.o').write_bytes(crt)
        with self.assertRaisesRegex(recipe.frozen.Failed, 'x86_64'): recipe.validate_result(sysroot)
        struct.pack_into('<H', crt, 18, 62); (sysroot / 'lib/crt1.o').write_bytes(crt)
        for name in ('libc.a', 'libc++.a', 'libc++abi.a', 'libunwind.a', 'linux/libclang_rt.builtins-x86_64.a'):
            (sysroot / 'lib' / name).write_bytes(b'!<arch>\n')
        self.assertEqual(recipe.validate_result(sysroot)['file_count'], 6)
        (sysroot / 'lib/libc++.a').write_bytes(b'bad archive')
        with self.assertRaisesRegex(recipe.frozen.Failed, 'static library'): recipe.validate_result(sysroot)


if __name__ == '__main__':
    unittest.main()
