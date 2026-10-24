"""Own state bytes and mocked process transport; real EXE is explicitly opt-in."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest
from unittest import mock
from tools import emuhawk_state_probe as p
from tests.packages import test_emuhawk_selfcheck as bootstrap

spec = importlib.util.spec_from_file_location('mini_state_fixtures', p.c.ROOT / 'tests/waterbox-smoke/test_state_probe.py')
mini = importlib.util.module_from_spec(spec); spec.loader.exec_module(mini)


class StateProbeTests(unittest.TestCase):
    def native(self): return {name: {'sha256': 'a' * 64} for name in ('libwaterboxhost.dylib', 'uae.wbx', 'puae6.wbx')}

    def capture(self, frame=0, cycles=0):
        capture = mini.StateProbeTests().capture(); raw = base64.b64decode(capture['data']); chunks = list(capture['chunks'])
        for name, size in p.FIELDS:
            value = frame if name == 'Frame' else cycles if name == 'CycleCount' else 0
            chunks.append([len(raw), size]); raw += value.to_bytes(size, 'little')
        return {**capture, 'data': base64.b64encode(raw).decode(), 'chunks': chunks}

    def record(self):
        return {'runtime': '10.0.13', 'format': 'bk2', 'frames': 12, 'cores': ['UAE', 'PUAE6'], 'isolation': True,
            'native': {n: v['sha256'] for n, v in self.native().items()}, 'av': {'UAE': 'b' * 64, 'PUAE6': 'c' * 64},
            'captures': {name: self.capture(0 if '.initial' in name else 12) for name in p.NAMES}}

    def test_every_runtime_and_managed_byte_is_compared_and_named(self):
        record = self.record(); self.assertEqual(p.validate(record, self.native())['conclusion'], 'identical-in-this-path')
        record['captures']['replay.final0'] = self.capture(12, 8)
        result = p.validate(record, self.native())['observations']['record.final0:replay.final0']
        self.assertFalse(result['equal']); self.assertEqual({r['section'] for r in result['ranges']}, {'managed.CycleCount'})
        data, regions = p.decode(record['captures']['record.final0'], 12)
        self.assertEqual(sum(end-start for start, end, *_ in regions), len(data))
        self.assertEqual(sum(end-start for start, end, kind, *_ in regions if kind.startswith('managed.')), 69)
        changed = bytearray(data); page = next(r for r in regions if r[2] == 'guest-page'); changed[page[0]+32] ^= 1
        other = {**record['captures']['record.final0'], 'data': base64.b64encode(changed).decode()}
        self.assertEqual(p.compare(p.decode(record['captures']['record.final0'], 12), p.decode(other, 12))['ranges'][0]['guest'], page[3]+32)

    def test_truncation_wrong_map_managed_writes_booleans_and_coverage_reject(self):
        for mode in ('truncate', 'write', 'bool', 'map'):
            capture = self.capture(); raw = bytearray(base64.b64decode(capture['data']))
            if mode == 'truncate': raw = raw[:-1]
            if mode == 'write': capture['chunks'][-1][1] = 8
            if mode == 'bool': raw[len(raw)-69+8] = 2
            if mode == 'map': capture['pages'] = base64.b64encode(b'\x13').decode()
            capture['data'] = base64.b64encode(raw).decode()
            with self.subTest(mode=mode), self.assertRaises(ValueError): p.decode(capture, 0)
        for key, value in (('frames', True), ('format', 'tasproj'), ('cores', ['UAE']), ('native', {}), ('av', {}), ('captures', {})):
            with self.subTest(key=key), self.assertRaises(ValueError): p.validate({**self.record(), key:value}, self.native())
        with self.assertRaises(ValueError): p.decode(self.capture(), 12)

    def test_shared_staging_explicit_diagnostic_marker_bound_context_and_cleanup(self):
        fixture = bootstrap.BootstrapTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        def execute(command, **kwargs):
            stage = Path(kwargs['cwd']); fixture.stages.append(stage); self.assertEqual(command[-1], '--puae6-state-probe')
            self.assertEqual(kwargs['env']['HOME'], str(stage)); self.assertEqual(kwargs['env']['TMPDIR'], str(stage/'tmp'))
            return subprocess.CompletedProcess(command, 0, 'x'*(1<<20)+'\n'+p.c.s.MARKER+json.dumps(fixture.record(stage))+'\n'+p.MARKER+json.dumps(self.record())+'\n', '')
        with mock.patch.object(p, 'prepare', return_value=self.native()), mock.patch.object(p, 'inputs', return_value=self.native()) as sources:
            fixture.process.side_effect = execute
            result = p.c.s.run(state_probe=True); self.assertEqual(result['status'], 'OBSERVATION'); self.assertEqual(len(result['observations']), 6)
            sources.return_value = {}
            with self.assertRaises(ValueError): p.c.s.run(state_probe=True)
            with self.assertRaises(ValueError): p.c.s.run(state_probe=True, coexistence=True)
        self.assertTrue(all(not stage.exists() for stage in fixture.stages))


@unittest.skipUnless(os.environ.get('P6_TEST_EMUHAWK_STATE_PROBE') == '1', 'requires current original EmuHawk EXE and both authorized guests')
class RealStateProbeTests(unittest.TestCase):
    def test_real_legacy_state_is_localized_with_both_cores_alive(self):
        result = p.c.s.run(state_probe=True); self.assertEqual(result['status'], 'OBSERVATION')
        print(json.dumps(result, sort_keys=True))
