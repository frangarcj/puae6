"""Own protocol records and mocked commands; never train, compile or measure."""
import copy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from tests.ci import test_optimization_engine as fixtures
from tools.ci import performance_engine as performance


class PerformanceEngineTests(unittest.TestCase):
    def setUp(self):
        self.own = fixtures.OptimizationEngineTests(); self.own.setUp()
        self.addCleanup(self.own.doCleanups)

    def record(self):
        value = self.own.record(); value['profile'] = performance.PROFILE
        value['variants'] = {v:r for v,r in value['variants'].items() if v in performance.VARIANTS}
        value['preparation'] = performance.preparation()
        value['completed'] = [dict(selector=s, counts=dict(tests=n, failures=0, errors=0, skipped=0,
                                                          expected_failures=0, unexpected_successes=0)) for s,n in performance.SELECTORS.items()]
        value['measurements'] = dict(bytes=99, sha256='a'*64, summary_sha256='b'*64, windows=75,
            identity=dict(compiler=performance.engine.VERSION, compiler_sha256='a'*64, isa='arm64', dedicated_runner=False),
            groups={scenario+'/'+variant: dict(repeats=list(range(5)), windows=5, warmup=300, units=units, state_rounds=20)
                    for scenario,units in performance.WORKLOADS.items() for variant in performance.MEASURED},
            verified={v:value['variants'][v]['verification'] for v in performance.MEASURED},
            benchmarks={v:value['variants'][v]['binaries']['p6_bench_probe']['sha256'] for v in performance.MEASURED})
        return value

    def test_original_two_methods_four_variants_and_every_timing_group_are_required(self):
        record = self.record()
        self.assertEqual(performance.validate(record, self.own.context), record)
        self.assertEqual(len(record['preparation']), 10)
        self.assertEqual(sum(r['counts']['tests'] for r in record['completed']), 2)
        for fault in ('source', 'profile', 'variant', 'preparation', 'windows', 'group', 'repeat', 'units', 'rounds',
                      'compiler', 'dedicated', 'receipt', 'binary', 'hash', 'selector', 'count', 'skip'):
            value = self.record(); measured = value['measurements']; group = next(iter(measured['groups'].values()))
            if fault == 'source': value['common'] = {}
            if fault == 'profile': value['profile'] = 'local-docker'
            if fault == 'variant': value['variants'].pop('instrument')
            if fault == 'preparation': value['preparation'].pop()
            if fault == 'windows': measured['windows'] = 74
            if fault == 'group': measured['groups'].pop(next(iter(measured['groups'])))
            if fault == 'repeat': group['repeats'][-1] = 3
            if fault == 'units': group['units'] = 1
            if fault == 'rounds': group['state_rounds'] = 0
            if fault == 'compiler': measured['identity']['compiler'] = 'other'
            if fault == 'dedicated': measured['identity']['dedicated_runner'] = True
            if fault == 'receipt': measured['verified']['base'] = 'f'*64
            if fault == 'binary': measured['benchmarks']['base'] = 'f'*64
            if fault == 'hash': measured['sha256'] = 'missing'
            if fault == 'selector': value['completed'].pop()
            if fault == 'count': value['completed'][0]['counts']['tests'] = 2
            if fault == 'skip': value['completed'][0]['counts']['skipped'] = 1
            with self.subTest(fault=fault), self.assertRaises((RuntimeError, KeyError)):
                performance.validate(value, self.own.context)

    def test_run_prepares_original_public_training_then_measures_once_and_executes_exact_selectors(self):
        engine = performance.engine; record = self.record()
        for fault in ('none', 'measurements', 'source', 'skip', 'command'):
            events = []
            def execute(argv, timeout):
                events.append((argv[4], argv[6] if len(argv) > 5 else None))
                self.assertEqual(timeout, 14400)
                if fault == 'command': raise RuntimeError('own command failure')
            def suite(argv, env, timeout):
                self.assertEqual(env, {'P6_TEST_ENGINE_FLAGS': '1'})
                events.append(('selector', argv[-1]))
                counts = dict(tests=1, failures=0, errors=0, skipped=int(fault == 'skip'), expected_failures=0, unexpected_successes=0)
                return subprocess.CompletedProcess(argv, 0, json.dumps(counts), '')
            with self.subTest(fault=fault), mock.patch.object(engine.experiment, 'WORK', self.own.root/'fresh'), \
                 mock.patch.object(engine.matrix, 'common', return_value=self.own.context), mock.patch.object(engine, 'toolchain', return_value=self.own.tools), \
                 mock.patch.object(engine.experiment, 'source_identity', side_effect=['f'*64, ('e' if fault == 'source' else 'f')*64]), \
                 mock.patch.object(engine.matrix, 'generated', return_value=self.own.generated), \
                 mock.patch.object(engine, 'variant_record', return_value=self.own.variant), mock.patch.object(engine, 'training_record', return_value=self.own.training), \
                 mock.patch.object(performance, 'measurements', side_effect=[record['measurements'], {} if fault == 'measurements' else record['measurements']]), \
                 mock.patch.object(engine.matrix.native, 'execute', side_effect=execute), mock.patch.object(engine.matrix.ci, 'command', side_effect=suite) as selected, \
                 mock.patch.object(performance.sys, 'stderr', io.StringIO()):
                if fault == 'none': self.assertEqual(performance.run()['completed'], record['completed'])
                else:
                    with self.assertRaises(RuntimeError): performance.run()
                if fault == 'command': selected.assert_not_called(); continue
                self.assertEqual(events[:10], [(s['action'], s.get('variant')) for s in performance.preparation()])
                self.assertEqual(events.count(('measure', None)), 1)
                if fault != 'skip': self.assertEqual(events[10:], [('selector', s) for s in performance.SELECTORS])

    def test_transport_summary_is_derived_from_actual_own_file_and_rejects_protocol_changes(self):
        rows = [dict(scenario='own', variant=v, repeat=r,
                     raw=dict(warmup_units=300, units=2, unit_cpu_ns=[1,2], state_save_ns=[1]*20, state_load_ns=[1]*20))
                for v in performance.MEASURED for r in range(5)]
        path = self.own.root/'measurements.json'
        data = dict(runs=rows, identity={'own':'fixture'}, verified={}, benchmarks={}, summary={})
        with mock.patch.object(performance.engine.experiment, 'WORK', self.own.root), mock.patch.dict(performance.WORKLOADS, {'own':2}, clear=True):
            path.write_text(json.dumps(data)); result = performance.measurements()
            self.assertEqual(result['windows'], 15); self.assertEqual(result['sha256'], performance.engine.fact(path)['sha256'])
            changed = copy.deepcopy(data); changed['runs'][0]['raw']['state_load_ns'] = []
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(RuntimeError, 'protocol'): performance.measurements()

    def test_existing_workspaces_are_rejected_before_any_build(self):
        with mock.patch.object(performance.engine.experiment, 'WORK', self.own.root), \
             mock.patch.object(performance.engine.matrix, 'common') as common, self.assertRaisesRegex(RuntimeError, 'fresh'):
            performance.run()
        common.assert_not_called()
