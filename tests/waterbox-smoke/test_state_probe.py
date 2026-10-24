"""Owned byte fixtures; the opt-in alone runs the actual mini-guest/runtime."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('state_probe', ROOT / 'tools/waterbox_state_probe.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


class StateProbeTests(unittest.TestCase):
    def capture(self, pointer=0x10000000, threading=0):
        page = bytearray(4096); struct.pack_into('<Q', page, 16, pointer)
        parts = [b'ActivatedWaterboxHost_v1', b'FileSystem', b'FileSystemEnd', bytes(8), b'ElfLoader', bytes(32),
            b'ActivatedMemoryBlock', bytes(32), struct.pack('<Q', 0x30000000), struct.pack('<Q', 4096), b'X', b'\1', bytes(page),
            b'GuestThreadSet', struct.pack('<I', 2), struct.pack('<I', 1), struct.pack('<Q', 1), bytes([threading]) + bytes(39),
            bytes(8), b'GuestThreadSet', 'ʇsoHxoqɹǝʇɐMpǝʇɐʌᴉʇɔ∀'.encode()]
        chunks = []; offset = 0
        for part in parts: chunks.append([offset, len(part)]); offset += len(part)
        return {'data': base64.b64encode(b''.join(parts)).decode(), 'chunks': chunks, 'pages': base64.b64encode(b'\x93').decode()}

    def test_exact_regions_and_buffer_intervention_are_distinct_from_threading(self):
        a = self.capture(); b = self.capture(0x20000000)
        self.assertTrue(p.compare(a, a, [0x10000000, 0x20000000])['equal'])
        changed = p.compare(a, b, [0x10000000, 0x20000000])
        self.assertEqual(changed['identified_buffer_spills'], [{'guest': 0x30000010, 'offset': a['chunks'][12][0] + 16, 'buffer_offset': 0}])
        self.assertEqual({r['section'] for r in changed['ranges']}, {'guest-page'})
        thread = p.compare(a, self.capture(threading=1), [0x10000000, 0x20000000])
        self.assertFalse(thread['identified_buffer_spills']); self.assertEqual({r['section'] for r in thread['ranges']}, {'threading'})
        self.assertFalse(p.compare(a, b, [0x10000000, 0x21000000])['identified_buffer_spills'])

    def test_fragmented_truncated_and_ambiguous_capture_are_rejected(self):
        for mode in ('truncate', 'offset', 'fragment', 'extra'):
            c = self.capture()
            if mode == 'truncate': c['data'] = base64.b64encode(base64.b64decode(c['data'])[:-1]).decode()
            if mode == 'offset': c['chunks'][3][0] += 1
            if mode == 'fragment':
                at, size = c['chunks'][6]; c['chunks'][6:7] = [[at, 1], [at + 1, size - 1]]
            if mode == 'extra': c['chunks'] = c['chunks'][:-1]
            with self.subTest(mode=mode), self.assertRaises(ValueError): p.decode(c)

    def test_page_map_and_schema_cannot_invent_a_stack_or_omit_bytes(self):
        for info in (b'\x13', b'\x94', b'\x93\x93', b'\xd3'):
            c = self.capture(); c['pages'] = base64.b64encode(info).decode()
            with self.subTest(info=info), self.assertRaises(ValueError): p.decode(c)
        c = self.capture(); raw = bytearray(base64.b64decode(c['data'])); raw[c['chunks'][12][0]:c['chunks'][12][0]+20] = b'ActivatedMemoryBlock'
        c['data'] = base64.b64encode(raw).decode(); p.decode(c)  # magic inside a page is ordinary data
        data, regions = p.decode(c)
        self.assertEqual(sum(end-start for start, end, *_ in regions), len(data))
        self.assertEqual([r[3] for r in regions if r[2] == 'guest-page'], [0x30000000])


    def test_historical_producer_requires_exact_blobs_guest_sources_and_sysroot(self):
        with tempfile.TemporaryDirectory(prefix='state-producer-fixture-', dir=ROOT / 'build') as tmp:
            root = Path(tmp); recorded = {}
            for key, name in p.RECORDED_INPUTS.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(('own data ' + key).encode()); recorded[key] = p.wb.sha256(path)
            for name in ('build/own-sysroot', 'build/waterbox/sysroot'):
                path = root / name; path.mkdir(parents=True); (path / 'own.h').write_bytes(b'own header')
            inventory = p.wb.inventory(root / 'build/own-sysroot')
            lock = {'profile': 'own', 'tools': {'own': 'tool'}, 'runtime': {}, 'guest': {}, 'sysroot': {
                'source_path': 'build/own-sysroot', 'file_count': 1, 'inventory_sha256': p.wb.canonical_hash(inventory)}}
            blobs = {'tools/build_waterbox.py': b'own producer recipe, never executed', 'plan/toolchains.json': json.dumps(lock).encode()}
            pin = {'commit': '1' * 40, 'guest_sha256': 'a' * 64, 'recipe_sha256': hashlib.sha256(blobs['tools/build_waterbox.py']).hexdigest(), 'toolchain_sha256': hashlib.sha256(blobs['plan/toolchains.json']).hexdigest()}
            recorded.update(build_recipe=pin['recipe_sha256'], toolchains_json=pin['toolchain_sha256'])
            receipt = {'guest': {'sha256': pin['guest_sha256']}, 'input_sha256': recorded, 'sysroot': lock['sysroot']}
            current = root / 'plan/toolchains.json'; current.parent.mkdir(); current.write_text(json.dumps({**lock, 'toolchain_profiles': {'unused': 'own'}}))
            with mock.patch.object(p, 'GUEST_PRODUCER', pin), mock.patch.object(p, 'git_blob', side_effect=lambda _, commit, name: blobs[name]) as read, mock.patch.dict(os.environ, {p.wb.TOOLCHAIN_PROFILE_ENV: 'local'}):
                self.assertEqual(p.provenance(receipt, root)['guest_producer'], pin)
                self.assertTrue(all(call.args[1] == pin['commit'] for call in read.call_args_list))
                for mutate, restore in (
                    (lambda: receipt['guest'].update(sha256='b' * 64), lambda: receipt['guest'].update(sha256=pin['guest_sha256'])),
                    (lambda: recorded.update(build_recipe='c' * 64), lambda: recorded.update(build_recipe=pin['recipe_sha256'])),
                    (lambda: blobs.update({'tools/build_waterbox.py': b'wrong producer'}), lambda: blobs.update({'tools/build_waterbox.py': b'own producer recipe, never executed'})),
                    (lambda: current.write_text(json.dumps({**lock, 'tools': {}})), lambda: current.write_text(json.dumps(lock))),
                    (lambda: (root / p.RECORDED_INPUTS['guest_wrapper']).write_bytes(b'changed wrapper'), lambda: (root / p.RECORDED_INPUTS['guest_wrapper']).write_bytes(b'own data guest_wrapper')),
                    (lambda: (root / 'build/waterbox/sysroot/own.h').write_bytes(b'changed header'), lambda: (root / 'build/waterbox/sysroot/own.h').write_bytes(b'own header'))):
                    mutate()
                    with self.assertRaises(ValueError): p.provenance(receipt, root)
                    restore()

    def test_orchestrator_is_offline_isolated_pinned_and_rejects_context_or_input_drift(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='state-probe-fixture-', dir=ROOT / 'build') as tmp:
            root = Path(tmp); (root / 'build').mkdir(); pin = root / 'integration/bizhawk/smoke/global.json'
            pin.parent.mkdir(parents=True); pin.write_text('{"sdk":{"version":"10.0.402","rollForward":"disable"}}')
            native = {'runtime': root / 'own-dylib-data', 'guest': root / 'own-wbx-data'}
            record = {'runtime': '10.0.13', 'frames': 3, 'eviction': True, 'buffers': [0x10000000, 0x20000000],
                'captures': {name + str(i): self.capture(0x20000000 if name == 'interventionA' else 0x10000000)
                    for name in ('initialA', 'initialB', 'afterA', 'afterB', 'interventionA') for i in (0, 1)}}
            stages = []
            def execute(command, **kwargs):
                stage = Path(kwargs['cwd']); stages.append(stage)
                self.assertEqual(kwargs['env']['P6_EVICT'], '1'); self.assertEqual(kwargs['env']['HOME'], str(stage))
                self.assertNotIn('DOTNET_STARTUP_HOOKS', kwargs['env'])
                self.assertEqual(json.loads((stage / 'global.json').read_text())['sdk']['version'], '10.0.402')
                if 'restore' in command: self.assertEqual(command[-2:], ['--source', str(stage / 'empty-feed')])
                if 'build' in command: (stage / 'out').mkdir()
                output = 'P6_STATE_PROBE ' + json.dumps(record) if command[1].endswith('Probe.dll') else ''
                return subprocess.CompletedProcess(command, 0, output, '')
            with mock.patch.object(p, 'ROOT', root), mock.patch.object(p.sys, 'platform', 'darwin'), mock.patch.object(p.os, 'uname', return_value=SimpleNamespace(machine='arm64')), mock.patch.object(p.subprocess, 'run', side_effect=execute) as child, mock.patch.object(p, 'inputs', return_value=(native, {'own-fixture': 'a', 'guest_producer': 'own-fixture'}, root / 'own-dotnet')) as source, mock.patch.dict(os.environ, {'DOTNET_STARTUP_HOOKS': '/foreign'}):
                result = p.run(); self.assertEqual(result['conclusion'], 'host-buffer-spill-proven'); self.assertEqual(child.call_count, 3)
                record['eviction'] = False
                with self.assertRaisesRegex(ValueError, 'context'): p.run()
                record['eviction'] = True; source.side_effect = [(native, {'own-fixture': 'a', 'guest_producer': 'own-fixture'}, root / 'own-dotnet'), (native, {'own-fixture': 'changed', 'guest_producer': 'own-fixture'}, root / 'own-dotnet')]
                with self.assertRaisesRegex(ValueError, 'Inputs changed'): p.run()
            self.assertTrue(all(not stage.exists() for stage in stages))


@unittest.skipUnless(os.environ.get('P6_TEST_WATERBOX_STATE_PROBE') == '1', 'requires current offline Darwin ARM64 runtime and mini-guest')
class RealStateProbeTests(unittest.TestCase):
    def test_real_mini_guest_localizes_all_snapshot_differences(self):
        result = p.run(); self.assertEqual(result['scope'], 'mini-guest-only'); self.assertTrue(result['eviction'])
        self.assertEqual(len(result['observations']), 8)
        print(json.dumps(result, sort_keys=True))
