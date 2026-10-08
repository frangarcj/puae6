"""Synthetic parser fixtures; none of these records demonstrates emulation."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import scenario_schema as schema
from tests.scenarios.ecs import ecs

ECS_DIR = ROOT / 'tests/scenarios/ecs'


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

    def a600(self):
        scenario = schema.load_json(ECS_DIR / 'ecs-lores-bars-pal.json')
        return scenario

    def test_a600_is_a_preset_of_the_same_profile_with_its_own_memory(self):
        scenario = self.a600()
        self.assertEqual((scenario['profile'], scenario['config']['machine']), ('deterministic-a500', 'A600'))
        self.assertEqual((scenario['config']['chip_ram_bytes'], scenario['config']['slow_ram_bytes']), (1048576, 0))
        schema.validate_scenario(scenario)
        good = copy.deepcopy(scenario)
        good['probes'] = [dict(region='chip', offset=1048574, length=2)]       # above the 512 KiB of the A500
        good['program'] = dict(sha256='c' * 64, load_address=0x80000, entry_address=0x80000, size_bytes=64)
        schema.validate_scenario(good)

    def test_each_machine_rejects_the_memory_of_the_other(self):
        mutations = [
            lambda s: s['config'].update(slow_ram_bytes=524288),                    # the A600 has no Slow RAM
            lambda s: s['config'].update(chip_ram_bytes=524288),                    # and 1 MiB of Chip
            lambda s: s['config'].update(chip_ram_bytes=2097152),
            lambda s: s['config'].update(machine='A500'),                           # A500 with the A600 memory
            lambda s: s['config'].update(machine='A1200'),
            lambda s: s['config'].update(machine=['A600']),
            lambda s: s.update(probes=[dict(region='slow', offset=0, length=1)]),   # no Slow region to probe
            lambda s: s.update(probes=[dict(region='chip', offset=1048575, length=2)]),
            lambda s: s.update(profile='deterministic-a600'),                       # the A600 is not a second profile
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(self.a600())
                mutate(bad)
                with self.assertRaises(ValueError):
                    schema.validate_scenario(bad)
        a500 = copy.deepcopy(self.scenario)
        a500['config']['chip_ram_bytes'] = 1048576
        with self.assertRaises(ValueError):
            schema.validate_scenario(a500)
        a500 = copy.deepcopy(self.scenario)
        a500['probes'] = [dict(region='slow', offset=524287, length=1)]            # the A500 keeps its Slow region
        schema.validate_scenario(a500)

    def test_ecs_scenario_files_are_valid_and_name_the_program_they_run(self):
        from tests.step.test_step import builder
        programs = ecs.scenario_programs()
        files = {path.stem: path for path in ECS_DIR.glob('ecs-*.json') if path.stem != 'ecs-baselines'}
        self.assertEqual(set(files), set(programs))
        self.assertEqual(len(files), 2 * (len(ecs.DISPLAYS) + len(ecs.BEAM) + 1))
        for scenario_id, (source, units, standard) in programs.items():
            with self.subTest(scenario=scenario_id):
                text = files[scenario_id].read_text(encoding='utf-8')
                scenario = schema.validate_scenario(schema.load_json(files[scenario_id]))
                self.assertEqual(scenario['scenario_id'], scenario_id)
                rom = builder.build(source)
                self.assertEqual(scenario['firmware'], [hashlib.sha256(rom).hexdigest()])
                self.assertEqual(scenario['config']['machine'], 'A600')
                self.assertEqual(scenario['config']['video_standard'], standard.upper())
                self.assertEqual((scenario['units'], scenario['render'], scenario['program'], scenario['media']),
                                 (units, True, None, []))
                self.assertEqual(text, json.dumps(ecs.scenario_document(scenario_id, hashlib.sha256(rom).hexdigest(), units, standard),
                                                  indent=2) + '\n')

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
