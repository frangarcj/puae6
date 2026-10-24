"""One pinned hosted engine: public training, 75 serial windows, original tests."""
import json
import re
import sys
import unittest

from tools.ci import optimization_engine as engine
from tools.bench import SCENARIOS

PROFILE, MARKER = 'hosted-native-performance-v1', 'P6_PERFORMANCE_RESULT '
VARIANTS = ('base', 'lto', 'instrument', 'pgo')
MEASURED = ('base', 'lto', 'pgo')
SELECTORS = {engine.MODULE+'.PerformanceTests': 1, engine.MODULE+'.DecisionTests': 1}
WORKLOADS = {s.id: 10000 if s.primary else 3000 for s in SCENARIOS if s.kind == 'microrom'}
require = engine.require


def preparation():
    require(engine.experiment.VARIANTS[:4] == VARIANTS, 'performance engine variants changed')
    return [step for step in engine.preparation() if step.get('variant') in VARIANTS or step['action'] == 'merge'] + [dict(action='measure')]


def measurements():
    path = engine.experiment.WORK/'measurements.json'
    data = json.loads(path.read_text()); groups = {}
    for scenario in WORKLOADS:
        for variant in MEASURED:
            rows = [r for r in data['runs'] if r['scenario'] == scenario and r['variant'] == variant]
            require(len(rows) == 5, 'missing engine measurement group')
            protocols = {(r['raw']['warmup_units'], r['raw']['units'], len(r['raw']['unit_cpu_ns']),
                          len(r['raw']['state_save_ns']), len(r['raw']['state_load_ns'])) for r in rows}
            require(protocols == {(300, WORKLOADS[scenario], WORKLOADS[scenario], 20, 20)}, 'engine measurement protocol changed')
            groups[scenario+'/'+variant] = dict(repeats=sorted(r['repeat'] for r in rows), windows=len(rows),
                                                warmup=300, units=WORKLOADS[scenario], state_rounds=20)
    return dict(**engine.fact(path), windows=len(data['runs']), groups=groups, identity=data['identity'],
                verified=data['verified'], benchmarks=data['benchmarks'], summary_sha256=engine.checksum(data['summary']))


def validate(record, expected):
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == 'engine'
            and record['common'] == expected and record['preparation'] == preparation(), 'foreign/incomplete performance engine')
    engine.validate_artifacts(record, VARIANTS)
    result = record['measurements']; identity = result['identity']
    require(identity['compiler'] == engine.VERSION and identity['compiler_sha256'] == record['tools']['cxx']['sha256']
            and identity['isa'] == 'arm64' and identity['dedicated_runner'] is False, 'wrong measured engine runner/compiler')
    require(type(result['bytes']) is int and result['bytes'] > 0 and result['windows'] == 75
            and all(re.fullmatch('[0-9a-f]{64}', result[k]) for k in ('sha256', 'summary_sha256')), 'missing engine measurements')
    wanted = {scenario+'/'+variant: dict(repeats=list(range(5)), windows=5, warmup=300, units=units, state_rounds=20)
              for scenario, units in WORKLOADS.items() for variant in MEASURED}
    require(len(WORKLOADS) == 5 and result['groups'] == wanted, 'incomplete engine workloads/repeats/protocol')
    require(result['verified'] == {v: record['variants'][v]['verification'] for v in MEASURED}
            and result['benchmarks'] == {v: record['variants'][v]['binaries']['p6_bench_probe']['sha256'] for v in MEASURED}, 'measured engine artifacts differ')
    require([r['selector'] for r in record['completed']] == list(SELECTORS), 'missing/duplicate performance selectors')
    for item in record['completed']:
        process = engine.matrix.ci.subprocess.CompletedProcess([], 0, json.dumps(item['counts']), '')
        status, counts = engine.matrix.ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == SELECTORS[item['selector']], 'performance engine omitted/failed assertions')
    return record


def run():
    require(not engine.experiment.WORK.exists() and not engine.experiment.WORK.is_symlink(), 'performance engine workspace must be fresh')
    before, tools, source = engine.matrix.common(), engine.toolchain(), engine.experiment.source_identity()
    steps = preparation(); variants = {}; codegen = training = None
    for step in steps:
        if step['action'] == 'measure':
            # Capture every artifact before timing and compare after the tests.
            training = engine.training_record(tools)
        argv = [sys.executable, '-B', '-m', 'tests.simd.engine_flags', step['action']]
        if 'variant' in step: argv += ['--variant', step['variant']]
        print('RUN performance engine ' + str(step), file=sys.stderr, flush=True)
        engine.matrix.native.execute(argv, 14400)
        if step['action'] == 'build' and codegen is None: codegen = engine.matrix.generated()
        if step['action'] == 'verify': variants[step['variant']] = engine.variant_record(step['variant'], source)
    observed = measurements(); completed = []
    for selector, count in SELECTORS.items():
        require(unittest.defaultTestLoader.loadTestsFromName(selector).countTestCases() == count, 'performance method count changed')
        process = engine.matrix.ci.command([sys.executable, '-B', '-c', engine.matrix.ci.check.DRIVER, selector],
                                          env={'P6_TEST_ENGINE_FLAGS': '1'}, timeout=14400)
        status, counts = engine.matrix.ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == count, selector + ': ' + str(counts) + process.stderr[-3000:])
        completed.append(dict(selector=selector, counts=counts))
    require((engine.matrix.common(), engine.toolchain(), engine.experiment.source_identity(), engine.matrix.generated())
            == (before, tools, source, codegen), 'performance engine inputs changed')
    require({v: engine.variant_record(v, source) for v in VARIANTS} == variants and engine.training_record(tools) == training
            and measurements() == observed, 'performance engine measurements/artifacts/headers changed')
    return validate(dict(status='PASS', profile=PROFILE, kind='engine', common=before, tools=tools, generated=codegen,
                         source_environment_sha256=source, variants=variants, training=training, preparation=steps,
                         measurements=observed, completed=completed), before)


if __name__ == '__main__':
    try: result = run()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', profile=PROFILE, kind='engine', diagnostic=str(error))
    print(MARKER + engine.matrix.ci.redact(json.dumps(result, sort_keys=True)))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
