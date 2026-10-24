"""Lexical guard and inventory tests. Not emulator or Waterbox tests."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_surface', ROOT / 'tools/check_host_surface.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostSurfaceTests(unittest.TestCase):
    def test_forbidden_headers(self):
        for name in ['windows.h', 'Windows.h', 'SDL2/SDL.h', 'pthread.h', 'thread',
                     'libretro.h', 'QtCore/QTimer', 'sys/socket.h']:
            for delimiter in [('"', '"'), ('<', '>')]:
                with self.subTest(name=name, delimiter=delimiter):
                    self.assertTrue(host.scan(f'# include {delimiter[0]}{name}{delimiter[1]}'))

    def test_host_operations_and_inactive_branches(self):
        for call in ['Sleep(1)', 'pthread_create(a,b,c,d)', 'clock_gettime(0,0)',
                     'SDL_Init(0)', 'retro_run()', 'fopen("x","r")', 'sched_yield()']:
            with self.subTest(call=call):
                self.assertTrue(host.scan('#if 0\n' + call + ';\n#endif'))

    def test_comments_strings_and_substrings_are_not_calls(self):
        source = '''// Sleep(0); #include <windows.h>
/* pthread_create(0); */ const char *a = "retro_run() \\" SDL_Init";
auto raw = R"tag("Sleep(1)" /* SDL_Init() */)tag";
#include "custom.h"
emulated_time += 1; do_cycles(4); // no host clock
'''
        self.assertEqual([], host.scan(source))

    def test_splicing_and_commented_directive(self):
        self.assertTrue(host.scan('#/**/include/**/<windows.h>'))
        self.assertTrue(host.scan('Sle\\\nep(1);'))
        self.assertEqual([], host.scan('// continued \\\nSleep(1);'))
        self.assertTrue(host.scan('%:include <windows.h>'))

    def test_macro_include_requires_review(self):
        self.assertIn('no literal', host.scan('#include HOST_HEADER')[0][1])

    def test_diagnostic_line(self):
        self.assertEqual(3, host.scan('/* comment\n */\nSleep(1);')[0][0])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / 'fixture.cpp'
            path.write_text('do_cycles(4);')
            self.assertEqual(0, host.main(['scan', str(path)]))
            path.write_text('#include <windows.h>')
            self.assertEqual(1, host.main(['scan', str(path)]))
            self.assertEqual(2, host.main(['scan', str(path) + '.missing']))
            self.assertEqual(2, host.main(['scan', directory]))

    def test_real_inventory(self):
        self.assertGreater(host.validate_inventory(ROOT), 0)

    def test_invalid_inventory_rejected(self):
        real_read = Path.read_text
        original = json.loads((ROOT / 'manifests/source-manifest.json').read_text())
        mutations = [{'commit': '0' * 40}, {'audit_sources': []},
                     {'audit_sources': ['../escape.cpp']},
                     {'audit_sources': ['*.cpp']},
                     {'audit_sources': ['p6_nonexistent_fixture.cpp']},
                     {'audit_sources': ['audio.cpp', 'audio.cpp']}]
        for name, reason in [('audio.cpp', 'Contradice seleccion'), ('ersatz.cpp', ''),
                             ('p6_nonexistent_fixture.cpp', 'No existe')]:
            selection = copy.deepcopy(original['selection'])
            selection['excluded_units'][name] = reason
            mutations.append({'selection': selection})
        for mutation in mutations:
            data = copy.deepcopy(original)
            data.update(mutation)
            def read(path, *args, **kwargs):
                return json.dumps(data) if path.name == 'source-manifest.json' else real_read(path, *args, **kwargs)
            with self.subTest(mutation=mutation), patch.object(Path, 'read_text', read):
                with self.assertRaises(ValueError):
                    host.validate_inventory(ROOT)

    def audit_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'manifests').mkdir()
        (root / 'upstream/winuae/include').mkdir(parents=True)
        (root / 'manifests/source-manifest.json').write_text(json.dumps({
            'commit': '1' * 40, 'audit_sources': ['entry.cpp']}))
        surface = {'schema_version': 1, 'commit': '1' * 40,
                   'watch_pattern': r'target_\w+', 'declaration_headers': ['include/a.h'],
                   'contracts': {'fixture': {'semantics': 'Synthetic callback', 'native': 'Fixture',
                       'waterbox': 'Fixture', 'test': 'Fixture only', 'symbols': ['target_output'], 'headers': []}}}
        (root / 'manifests/host-surface.json').write_text(json.dumps(surface))
        (root / 'upstream/winuae/entry.cpp').write_text('#include <a.h>\ntarget_output();')
        (root / 'upstream/winuae/include/a.h').write_text('#include "b.h"\nextern void target_output();')
        (root / 'upstream/winuae/include/b.h').write_text('#include "a.h"')
        return root, surface

    def test_transitive_include_cycle_and_callers(self):
        root, _ = self.audit_fixture()
        report = host.audit_surface(root)
        self.assertEqual(3, report['sources'])
        self.assertEqual(3, report['edges'])
        self.assertEqual({'target_output': ['entry.cpp', 'include/a.h']}, report['symbols'])
        (root / 'upstream/winuae/include/b.h').write_text('#include "unclassified_driver.h"')
        with self.assertRaisesRegex(ValueError, 'include sin contrato: unclassified_driver.h'):
            host.audit_surface(root)

    def test_unknown_symbol_and_new_header_api_rejected(self):
        root, _ = self.audit_fixture()
        entry = root / 'upstream/winuae/entry.cpp'
        for text in ['target_uninventoried();', '#define WAIT() target_uninventoried()']:
            entry.write_text('#include <a.h>\n' + text)
            with self.assertRaisesRegex(ValueError, 'simbolo sin contrato: target_uninventoried'):
                host.audit_surface(root)
        header = root / 'upstream/winuae/include/a.h'
        header.write_text('extern void unexpected_driver();')
        entry.write_text('#include <a.h>\nunexpected_driver();')
        with self.assertRaisesRegex(ValueError, 'simbolo sin contrato: unexpected_driver'):
            host.audit_surface(root)

    def test_surface_pin_missing_test_and_duplicate_symbol_rejected(self):
        root, surface = self.audit_fixture()
        for mutation in ('pin', 'test', 'duplicate'):
            data = copy.deepcopy(surface)
            if mutation == 'pin':
                data['commit'] = '0' * 40
            elif mutation == 'test':
                data['contracts']['fixture']['test'] = ''
            else:
                data['contracts']['duplicate'] = data['contracts']['fixture']
            (root / 'manifests/host-surface.json').write_text(json.dumps(data))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                host.audit_surface(root)

    def test_include_graph_rejects_macro_and_escape(self):
        root, _ = self.audit_fixture()
        for header in ['HOST_HEADER', '"/tmp/host.h"', '"../../outside.h"']:
            (root / 'upstream/winuae/entry.cpp').write_text('#include ' + header)
            with self.subTest(header=header), self.assertRaises(ValueError):
                host.include_graph(root / 'upstream/winuae', ['entry.cpp'])

    def test_known_forbidden_operations_in_transitive_header_rejected(self):
        root, _ = self.audit_fixture()
        for path in ['entry.cpp', 'include/b.h']:
            for call in ['Sleep(1)', 'pthread_create(0,0,0,0)', 'SDL_Init(0)', 'dlopen(0,0)',
                         'clock_gettime(0,0)', 'target_new_service()']:
                entry = root / 'upstream/winuae/entry.cpp'
                header = root / 'upstream/winuae/include/b.h'
                entry.write_text('#include <a.h>\ntarget_output();')
                header.write_text('#include "a.h"')
                changed = root / 'upstream/winuae' / path
                changed.write_text(changed.read_text() + '\ninline void probe(){' + call + ';}')
                with self.subTest(path=path, call=call), self.assertRaisesRegex(ValueError, 'sin contrato'):
                    host.audit_surface(root)
