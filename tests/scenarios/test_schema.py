"""Synthetic parser fixtures; none of these records demonstrates emulation."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import scenario_schema as schema


class ScenarioSchemaTests(unittest.TestCase):
    def setUp(self):
        self.scenario = schema.load_json(ROOT / 'tests/scenarios/counter68k.json')

    def test_normalization_and_valid_input(self):
        self.assertEqual(schema.validate_scenario(self.scenario), self.scenario)
        self.assertEqual(schema.canonical_hash({'a': 1, 'b': 2}),
                         schema.canonical_hash({'b': 2, 'a': 1}))
        self.scenario['events'] = [dict(unit=0, sequence=0, type='reset'),
            dict(unit=0, sequence=1, type='eject', drive=0, media_index=None)]
        schema.validate_scenario(self.scenario)

    def test_rejects_unknown_fields_ranges_and_sequence_errors(self):
        mutations = [
            lambda s: s.update(unknown=True), lambda s: s.update(units=True),
            lambda s: s.update(units=0), lambda s: s.update(inputs=''),
            lambda s: s['config'].update(jit=True),
            lambda s: s['config'].update(random_seed=1 << 64),
            lambda s: s['config'].update(rtc_epoch_seconds=1.5),
            lambda s: s['probes'][0].update(offset=524287, length=2),
            lambda s: s['probes'][0].update(offset=-1),
            lambda s: s['program'].update(entry_address=4102),
            lambda s: s['program'].update(load_address=4097),
            lambda s: s['inputs'].append(copy.deepcopy(s['inputs'][0])),
            lambda s: s['inputs'][0]['ports'][0].update(dx=1),
            lambda s: s['inputs'][0]['ports'][0].update(type='mouse', dx=32768),
            lambda s: s['inputs'][0].update(keys='0' * 31),
            lambda s: s.update(checkpoints=[1]),
            lambda s: s.update(events=[dict(unit=0, sequence=0, type='mount', drive=0, media_index=0)]),
            lambda s: s.update(events=[dict(unit=0, sequence=0, type='reset')] * 2),
            lambda s: s.update(events=[dict(unit=0, sequence=0, type='eject', drive=0, media_index=None),
                                      dict(unit=0, sequence=1, type='reset')]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(self.scenario)
                mutate(bad)
                with self.assertRaises(ValueError):
                    schema.validate_scenario(bad)

    def result(self):
        return dict(schema_version=1, scenario_id='counter68k', status='PASS', exit_code=0,
            evidence='EMULATION', core_build_id='a' * 64,
            config_hash=schema.canonical_hash(self.scenario['config']),
            media_hash=schema.canonical_hash({k: self.scenario[k] for k in ('firmware', 'media', 'program')}),
            boundaries=[dict(unit=i, master_ticks=i * 100, samples_total=i * 960,
                hashes={key: 'b' * 64 for key in ('ram', 'cpu', 'events', 'video', 'pcm')}) for i in (1, 2)],
            host_metrics=dict(elapsed_ns=1, peak_memory_bytes=1024))

    def test_telemetry_parser_not_execution_proof(self):
        schema.validate_result(self.result(), self.scenario)
        mutations = [lambda r: r['boundaries'].pop(), lambda r: r.update(evidence='HARNESS'),
            lambda r: r.update(config_hash='a' * 64), lambda r: r.update(media_hash='a' * 64),
            lambda r: r['boundaries'][1].update(master_ticks=100),
            lambda r: r['boundaries'][1].update(unit=1),
            lambda r: r['boundaries'][1]['hashes'].pop('cpu'),
            lambda r: r['boundaries'][0]['hashes'].update(pcm='truncated')]
        for mutate in mutations:
            bad = self.result()
            mutate(bad)
            with self.assertRaises(ValueError):
                schema.validate_result(bad, self.scenario)

    def test_json_rejects_duplicates_nonfinite_and_truncated_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.json'
            for text in ('{"units":1,"units":2}', '{"x":NaN}', '{"units":'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    schema.load_json(path)
