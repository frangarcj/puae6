"""Compiler fixtures cover orchestration; the explicit opt-in runs real builds."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools import package


class ReproducibleTests(unittest.TestCase):
    def setUp(self):
        (package.ROOT / 'build').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='native-repro-fixture-', dir=package.ROOT / 'build'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.target = 'macos-arm64'; self.trees = []; self.commands = []
        self.identity = {'revision': 'a' * 40, 'source_sha256': 'b' * 64}
        for patch in (mock.patch.object(package, 'ROOT', self.root), mock.patch.object(package, 'native_platform', side_effect=lambda: self.target),
                      mock.patch.object(package, 'reproducible_inputs', return_value=self.identity), mock.patch.object(package, 'run', side_effect=self.compile),
                      mock.patch.object(package, 'verify_binaries'), mock.patch.object(package, 'build'), mock.patch.object(package, 'smoke')):
            patch.start(); self.addCleanup(patch.stop)

    def compile(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if command[1] == '-S':
            tree = Path(command[command.index('-B') + 1]); self.assertEqual(list(tree.iterdir()), [])
            self.assertEqual(command, package.configure_command(self.target, tree)); self.trees.append(tree)
            (tree / 'CMakeCache.txt').write_bytes(b'own compiler fixture')
        else:
            self.assertEqual(command, ['cmake', '--build', self.trees[-1], '--parallel', '2', '--target', 'puae6_libretro', 'p6_headless', 'p6_libretro_host'])
            for name in package.products(self.target): (self.trees[-1] / name).write_bytes(b'own binary ' + name.encode())
        return ''

    def test_two_empty_distinct_trees_use_the_package_flags_and_compare_all_products(self):
        for target in package.PLATFORMS:
            self.target = target; self.commands.clear(); self.trees.clear(); package.verify_binaries.reset_mock()
            result = package.reproducible()
            self.assertEqual(result['clean_object_builds'], 2); self.assertEqual(set(result['products']), set(package.products(target)))
            self.assertEqual(len(self.commands), 4); self.assertNotEqual(*self.trees)
            self.assertEqual(self.commands[0][1]['env'], self.commands[-1][1]['env'])
            self.assertIn('-DCMAKE_CXX_FLAGS_RELEASE=-O2', self.commands[0][0])
            self.assertEqual(package.verify_binaries.call_args_list, [mock.call(tree, target, subdirectory='.') for tree in self.trees])
            self.assertTrue(all(not tree.exists() for tree in self.trees))
        package.build.assert_not_called(); package.smoke.assert_not_called()

    def test_binary_mismatch_is_reported_and_both_trees_are_removed(self):
        def mismatch(command, **kwargs):
            self.compile(command, **kwargs)
            if command[1] == '--build' and len(self.trees) == 2: (self.trees[-1] / 'p6_headless').write_bytes(b'different own output')
        package.run.side_effect = mismatch
        with self.assertRaisesRegex(ValueError, 'binary mismatch p6_headless:'): package.reproducible()
        self.assertTrue(all(not tree.exists() for tree in self.trees))

    def test_source_or_tool_identity_drift_stops_before_the_second_build(self):
        package.reproducible_inputs.side_effect = [self.identity, {'revision': 'changed'}]
        with self.assertRaisesRegex(ValueError, 'inputs changed'): package.reproducible()
        self.assertEqual(len(self.commands), 2); self.assertFalse(self.trees[0].exists())

    def test_compile_error_cleans_only_its_own_temporary_tree(self):
        keep = self.root / 'keep'; keep.write_bytes(b'own unrelated data')
        def fail(command, **kwargs):
            self.compile(command, **kwargs)
            if command[1] == '--build': raise RuntimeError('own failed compiler')
        package.run.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'failed compiler'): package.reproducible()
        self.assertEqual(keep.read_bytes(), b'own unrelated data'); self.assertFalse(self.trees[0].exists())

    def test_missing_or_linked_product_cannot_pass_as_a_reproducible_build(self):
        for linked in (False, True):
            def absent(command, **kwargs):
                self.compile(command, **kwargs)
                if command[1] == '--build':
                    path = self.trees[-1] / 'p6_headless'; path.unlink()
                    if linked: path.symlink_to('CMakeCache.txt')
            package.run.side_effect = absent
            with self.assertRaisesRegex(ValueError, 'Missing/linked'): package.reproducible()
            self.assertTrue(all(not tree.exists() for tree in self.trees))

    def test_a_compiler_cache_launcher_is_refused_before_any_build(self):
        with mock.patch.dict(os.environ, {'CMAKE_CXX_COMPILER_LAUNCHER': 'own-cache'}):
            with self.assertRaisesRegex(ValueError, 'launchers'): package.reproducible()
        package.run.assert_not_called(); package.reproducible_inputs.assert_not_called()

    def test_the_cli_and_real_gate_select_the_real_helper(self):
        with mock.patch('sys.argv', ['package.py', 'reproducible']), mock.patch.object(package, 'reproducible', return_value={'own_fixture': True}) as real, mock.patch('builtins.print'):
            package.main(); real.assert_called_once_with()
        from tools import scenario_schema
        with mock.patch.dict('sys.modules', {'scenario_schema': scenario_schema}):
            from tools import check
        for gate in ('PKG01', 'BUILD01'):
            self.assertEqual(list(check.TASK_SUITES[(gate, 'T40h')].values()), ['tests.packages.test_reproducible.RealReproducibleTests'])

    def test_the_cli_blocks_when_a_required_input_is_missing(self):
        with mock.patch('sys.argv', ['package.py', 'reproducible']), mock.patch.object(package, 'reproducible', side_effect=FileNotFoundError('own missing input')), mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit) as stopped: package.main()
        self.assertEqual(stopped.exception.code, 2); package.run.assert_not_called(); package.build.assert_not_called()


@unittest.skipUnless(os.environ.get('P6_TEST_NATIVE_REPRODUCIBLE') == '1', 'P6_TEST_NATIVE_REPRODUCIBLE=1 requires two real clean native builds')
class RealReproducibleTests(unittest.TestCase):
    def test_three_native_products_are_byte_identical_after_two_clean_object_builds(self):
        try: result = package.reproducible()
        except FileNotFoundError as error: self.skipTest(str(error))
        self.assertEqual(result['status'], 'PASS'); self.assertEqual(result['clean_object_builds'], 2)
        self.assertEqual(set(result['products']), set(package.products(package.native_platform())))
        print(json.dumps(result, sort_keys=True))
