"""Actual hosted frontend measurements; raw arrays stay with their validator."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import statistics
import tempfile

from tests.simd import frontend_matrix as matrix, frontend_rgb565 as frontend
from tools.ci import optimization_frontend as shared

ROOT = Path(__file__).resolve().parents[2]
PROFILE, MARKER = 'hosted-native-performance-v1', 'P6_PERFORMANCE_RESULT '
PINS = json.loads((ROOT/'plan/toolchains.json').read_text())['portability_matrix']['rows']
ADR = (ROOT/'docs/adr/0004-native-optimizations.md').read_text()
ADR_SHA = hashlib.sha256(ADR.encode()).hexdigest()
CONFIGURATION = dict(CMAKE_BUILD_TYPE='Release', CMAKE_C_FLAGS_RELEASE='-O2', CMAKE_CXX_FLAGS_RELEASE='-O2',
                     P6_SANITIZERS='OFF', P6_NATIVE_IPO='OFF', P6_PGO_MODE='OFF', P6_RGB565_EXPERIMENT='ON')
require, digest, hosted = shared.require, shared.digest, shared.hosted


def validate(record, expected):
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == 'frontend'
            and record['common'] == expected and record['row'] in matrix.ROWS, 'foreign frontend performance record')
    row, proof, verified = record['row'], record['provenance'], record['verification']
    pin, tools, generated = PINS[row], record['tools'], record['generated']
    runner = record['runner']; isa = 'arm64' if runner['isa'] == 'aarch64' else runner['isa']
    require(isa == pin['isa'] and runner['os'] == ('Darwin' if row.startswith('macos-') else 'Linux')
            and (not row.startswith('macos-') or runner['macos'].split('.')[0] == '26'), 'wrong performance runner')
    matrix.validate_result(row, verified, proof['product_sha256'])
    require(verified['build']['binaries'] == proof['binaries'] and verified['build']['compiler'] == pin['compiler_version']
            and verified['build']['compiler_sha256'] == tools['cxx']['sha256'] and verified['build']['isa'] == pin['isa'], 'frontend compiler/binary identity differs')
    require(set(tools) == {'cc','cxx','git','cmake','ninja','patch','python'} and set(generated['files']) == hosted.generated_names(), 'missing performance tools/codegen')
    hashes = [record['source_environment_sha256'], generated['receipt_sha256'], *generated['files'].values()]
    for name, tool in tools.items():
        require(tool['path'] and tool['version'], 'missing frontend tool')
        hashes.append(tool['sha256'])
        if name in ('cc','cxx'):
            compiler = generated['compilers']['c' if name == 'cc' else 'c++']
            require(compiler['sha256'] == tool['sha256'] and compiler['version'] == tool['version'], 'codegen compiler differs')
            hashes.append(tool['implementation']['sha256'])
    require(tools['cxx']['version'].splitlines()[0] == pin['compiler_version'], 'frontend compiler pin changed')
    require(type(proof['headers']['count']) is int and proof['headers']['count'] > 0, 'effective headers missing')
    hashes += [proof[k] for k in ('build_receipt_sha256','cmake_cache_sha256','compile_commands_sha256','verification_sha256','inputs_sha256','product_sha256')]
    hashes.append(proof['headers']['sha256'])
    require(proof['configuration'] == CONFIGURATION, 'frontend flags/configuration changed')
    measured = record['measurement']
    require((measured is not None) == (row in matrix.MEASURED), 'frontend measured row coverage differs')
    if measured is not None:
        require(measured['runs'] == 50 and measured['warmup'] == 300 and measured['state_rounds'] == 20
                and measured['repetitions'] == 5 and type(measured['raw_bytes']) is int and measured['raw_bytes'] > 0
                and measured['inputs_sha256'] == proof['inputs_sha256'] and measured['verification_sha256'] == proof['verification_sha256'], 'frontend measurement provenance/protocol differs')
        require(set(measured['comparisons']) == {s.id for s in frontend.WORKLOADS}, 'frontend workloads omitted')
        for scenario in frontend.WORKLOADS:
            comparison = measured['comparisons'][scenario.id]
            require(len(comparison['paired_reductions_percent']) == 5 and set(comparison['variants']) == {'current','candidate'}, 'frontend pairs omitted')
            for values in comparison['variants'].values():
                medians = values['cpu_medians_ns']
                require(len(medians) == 5 and all(isinstance(n,(int,float)) and math.isfinite(n) and n > 0 for n in medians)
                        and statistics.median(medians) == values['median_cpu_ns'], 'frontend medians invalid')
                require(all(values[k]['n'] == 5*(10000 if scenario.primary else 3000) for k in ('cpu','wall'))
                        and values['state_load_ns']['n'] == 100 and values['state_save_ns']['n'] > 0, 'frontend statistics coverage differs')
                require(all(math.isfinite(values[k]['p95']) and values[k]['min'] > 0 and values[k]['p95'] > 0
                            for k in ('cpu','wall','state_save_ns','state_load_ns')), 'frontend statistical samples invalid')
            before, after = (comparison['variants'][v] for v in ('current','candidate'))
            pairs = [100*(1-b/a) for a,b in zip(before['cpu_medians_ns'],after['cpu_medians_ns'])]
            require(comparison['paired_reductions_percent'] == pairs and comparison['paired_wins'] == sum(p > 0 for p in pairs)
                    and comparison['cpu_reduction_percent'] == 100*(1-after['median_cpu_ns']/before['median_cpu_ns']), 'frontend paired statistics changed')
            require(comparison['state_p95_cost_percent'] == {k:100*(after[k]['p95']/before[k]['p95']-1)
                    for k in ('state_save_ns','state_load_ns')}, 'frontend state statistics changed')
        require(record['decision'] == {'adr_sha256': ADR_SHA}, 'frontend decision changed')
        for name in ('raw_sha256','inputs_sha256','verification_sha256'): hashes.append(measured[name])
    else: require(record['decision'] is None, 'unmeasured row claimed a decision')
    require(all(isinstance(h,str) and re.fullmatch('[0-9a-f]{64}',h) for h in hashes), 'frontend performance hash malformed')
    return record


def validate_rows(records, expected=None):
    require(len(records) == len(matrix.ROWS) and {r['row'] for r in records} == set(matrix.ROWS), 'missing/duplicate performance frontend rows')
    common = records[0]['common'] if expected is None else expected
    for record in records: validate(record, common)
    require({r['row'] for r in records if r['measurement'] is not None} == set(matrix.MEASURED), 'incomplete measured frontend rows')
    return dict(verified_rows=5, measured_rows=3, measured_windows=150, original_methods=2, unittest_tests=0)


def run(row):
    build = ROOT/'build'; build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hosted-frontend-performance-', dir=build) as work:
        return _run(row, Path(work))


def _run(row, work):
    hosted.native.preflight(row, 'O2')
    if row.startswith('macos-'): require(platform.mac_ver()[0].split('.')[0] == '26', 'requires declared macOS26 runner')
    common, tools, source = hosted.common(), hosted.tools(row), frontend.source_identity()
    instance = frontend.Frontend(row, work)
    instance.build(); before = shared.artifacts(instance); instance.verify()
    require(shared.artifacts(instance) == before, 'frontend inputs changed during verification')
    path = instance.tree/'verification.json'; verification_sha = frontend.digest(path)
    verified = json.loads(path.read_text()); actual = matrix.current_inputs(row, instance.work)
    require(verified['inputs'] == actual, 'verified/current frontend inputs differ')
    measurement, decision, measured_path = None, None, None
    if row in matrix.MEASURED:
        instance.measure()
        measured_path = instance.tree/'measurements.json'; raw_sha = frontend.digest(measured_path)
        measured = json.loads(measured_path.read_text())
        comparisons = matrix.validate_measurements(measured, verified['inputs'], matrix.current_inputs(row, instance.work))
        matrix.validate_decision(row, measured['inputs'], matrix.current_inputs(row, instance.work), ADR)
        measurement = dict(runs=50, repetitions=5, warmup=300, state_rounds=20, raw_bytes=measured_path.stat().st_size,
                           raw_sha256=raw_sha, inputs_sha256=digest(measured['inputs']), verification_sha256=verification_sha, comparisons=comparisons)
        decision = dict(adr_sha256=ADR_SHA)
    require(shared.artifacts(instance) == before and frontend.digest(path) == verification_sha
            and (measured_path is None or frontend.digest(measured_path) == measurement['raw_sha256'])
            and matrix.current_inputs(row, instance.work) == actual and (hosted.common(),hosted.tools(row),frontend.source_identity()) == (common,tools,source), 'frontend performance inputs changed')
    configuration = {line.split(':',1)[0]:line.split('=',1)[1] for line in (instance.tree/'CMakeCache.txt').read_text().splitlines()
                     if ':' in line and '=' in line and line.split(':',1)[0] in CONFIGURATION}
    build = {k:verified['build'][k] for k in ('row','compiler','compiler_sha256','isa','binaries','product_variant')}
    product = instance.cores['candidate' if row in frontend.NEON_PRODUCT_ROWS else 'current']
    result = dict(status='PASS', profile=PROFILE, kind='frontend', common=common, row=row, tools=tools, generated=before['generated'], source_environment_sha256=source,
                  verification=dict(build=build,cases=verified['cases']), provenance={**{k:v for k,v in before.items() if k != 'generated'},
                  'verification_sha256':verification_sha,'inputs_sha256':digest(actual),'product_sha256':frontend.digest(product),'configuration':configuration}, measurement=measurement, decision=decision,
                  runner=dict(os=platform.system(),isa=platform.machine(),macos=platform.mac_ver()[0]))
    validate(result,common)
    require(len(json.dumps(result,separators=(',',':')).encode()) < 256*1024, 'frontend summary unexpectedly contains bulk data')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--row',choices=tuple(matrix.ROWS),required=True)
    args=parser.parse_args()
    try: result=run(args.row)
    except (OSError,ValueError,RuntimeError,AssertionError,KeyError,TypeError) as error:
        result=dict(status='FAIL',profile=PROFILE,kind='frontend',diagnostic=str(error))
    print(MARKER+hosted.ci.redact(json.dumps(result,separators=(',',':'),sort_keys=True)))
    raise SystemExit(0 if result['status']=='PASS' else 1)
