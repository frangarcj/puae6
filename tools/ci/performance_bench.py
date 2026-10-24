"""Hosted public benchmark campaign and original selectors; advisory measurements."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import unittest

from tools import bench
from tools.ci import hosted_matrix as hosted, managed_release, nightly, optimization_engine as engine
from tests import build_support

ROOT = hosted.ROOT
PROFILE, MARKER = 'hosted-native-performance-v1', 'P6_PERFORMANCE_RESULT '
SELECTORS = {'tests.bench.test_budgets': 1, 'tests.bench.test_core_io': 5, 'tests.bench.test_cpu_arguments': 4,
             'tests.bench.test_cpu_campaign': 5, 'tests.bench.test_native': 5, 'tests.bench.test_statistics': 12,
             'tests.bench.test_wb12': 6, 'tests.simd.test_decision.PerformanceTests': 1}
TARGETS = ('p6_bench_probe', 'puae6_libretro', 'p6_libretro_host')
PROTOCOL = 'T53/300/10000-primary-3000-other/5/20/v1'
require, checksum, fact = hosted.require, engine.checksum, engine.fact


def partition():
    expected = {'.'.join(p.relative_to(ROOT).with_suffix('').parts) for p in (ROOT/'tests/bench').glob('test_*.py')}
    require(expected == {s for s in SELECTORS if s.startswith('tests.bench.')}, 'benchmark selector coverage changed')
    for selector, count in SELECTORS.items():
        require(unittest.defaultTestLoader.loadTestsFromName(selector).countTestCases() == count, 'benchmark selector count changed: '+selector)


def policy():
    return dict(status='ADVISORY', dedicated_runner=False, budgets_sha256=fact(bench.BUDGETS)['sha256'],
                goals=dict(p95_unit_fraction_max=0.70, time_regression_investigate_above=0.05,
                           state_size_or_cost_regression_investigate_above=0.10))


def tools():
    hosted.native.preflight(engine.ROW, 'O2')
    require(platform.mac_ver()[0].split('.')[0] == '26', 'requires pinned macOS26')
    native = hosted.tools(engine.ROW)
    require(all(native[n]['version'].splitlines()[0] == engine.VERSION for n in ('cc','cxx')), 'wrong Apple Clang')
    wb = hosted.ci.wb; lock = wb.selected_toolchain()
    require(lock['toolchain_profile'] == 'hosted-macos26' and wb.source_audit_mode() == 'snapshot' and wb.runtime_profile() == 'release', 'requires explicit hosted snapshot Release inputs')
    require(os.environ.get(wb.MANAGED_FEED_ENV) in ('build/hosted-managed-feed',str(ROOT/'build/hosted-managed-feed')), 'requires prepared hosted managed feed')
    wb.managed_restore_sources()
    paths, versions = wb.locked_tools(lock)
    waterbox = {n:dict(path=str(p),sha256=fact(p)['sha256'],version=versions[n]) for n,p in paths.items()}
    dotnet = shutil.which('dotnet'); require(dotnet, 'missing prepared SDK')
    sdk = dict(path=str(Path(dotnet).resolve()),sha256=fact(Path(dotnet).resolve())['sha256'],version=hosted.native.execute([dotnet,'--version']).strip())
    require(sdk['version'] == managed_release.expected_sdk(ROOT), 'wrong managed SDK')
    return dict(native=native,waterbox=waterbox,sdk=sdk,runner=dict(os=platform.system(),isa=platform.machine(),macos=platform.mac_ver()[0]))


def output_files(directory):
    paths = sorted(p for p in directory.iterdir() if p.suffix in ('.dll','.json','.dylib'))
    require(paths, 'missing managed outputs')
    return {p.name:fact(p)['sha256'] for p in paths}


def inputs():
    wb = hosted.ci.wb
    wb.check_guest_matches_trees(bench.GUEST_RECEIPT)
    guest = json.loads(bench.GUEST_RECEIPT.read_text())['guest']
    require(fact(guest['path'])['sha256'] == guest['sha256'], 'guest differs from receipt')
    runtimes = {}
    for mode in ('release','debug'):
        path = wb.runtime_receipt_path(mode); receipt = wb.read_runtime_receipt(path)
        library = receipt['runtime_dylib']
        require(receipt['runtime_profile'] == mode and fact(library['path'])['sha256'] == library['sha256'], 'runtime differs from receipt')
        runtimes[mode] = dict(receipt_sha256=fact(path)['sha256'],sha256=library['sha256'])
    managed_release.verify_receipt(managed_release.receipt_path(ROOT),bench.FFI_RELEASE,guest['sha256'],ROOT)
    native = {}
    for name,tree in (('release',bench.NATIVE_TREE),('state-opt',ROOT/'build/state-opt')):
        native[name] = dict(binaries={n:fact(build_support.output_path(tree,n))['sha256'] for n in TARGETS},
                            cache_sha256=fact(tree/'CMakeCache.txt')['sha256'],commands_sha256=fact(tree/'compile_commands.json')['sha256'],
                            headers=engine.dependencies(tree))
    return dict(native=native,runtime=runtimes,guest=dict(receipt_sha256=fact(bench.GUEST_RECEIPT)['sha256'],sha256=guest['sha256']),
                manual_sha256=checksum(wb.puae6_manual_inputs(ROOT)),managed=dict(inputs_sha256=checksum(managed_release.capture_inputs(ROOT)),
                receipt_sha256=fact(managed_release.receipt_path(ROOT))['sha256'],release=output_files(bench.FFI_RELEASE),
                debug=output_files(bench.FFI_DEBUG),smoke=output_files(bench.SMOKE_DLL.parent)))


def execute(argv, env):
    process = hosted.ci.command(list(map(str,argv)),env,timeout=6*3600)
    require(process.returncode == 0, 'benchmark preparation/campaign failed: '+process.stdout[-1000:]+process.stderr[-3000:])


def prepare(env):
    require(bench.NATIVE_TREE == ROOT/'build/bench-release' and bench.FFI_RELEASE == ROOT/'build/ffi/bin/Release/net8.0', 'foreign benchmark output override')
    execute([sys.executable,'-B','tools/bench.py','build','--jobs','2'],env)
    build_support.build_targets(ROOT/'build/state-opt',TARGETS,optimized=True)


def campaign(directory):
    rows = bench.validate_cpu_campaign(directory)  # Validates every actual sample, sequence and current artifact identity.
    identity = bench.cpu_campaign_identity()
    raw = {p.name:fact(p)['sha256'] for p in sorted((directory/'raw').glob('*.json.gz'))}
    require(len(rows) == 27 and len(raw) == 135 and identity['protocol'] == PROTOCOL, 'incomplete CPU campaign')
    return dict(identity_sha256=bench.campaign_hash(identity),rows=len(rows),windows=len(raw),raw_files=len(raw),
                raw_sha256=checksum(raw),summaries_sha256=checksum({'/'.join(k):v for k,v in rows.items()}),protocol=PROTOCOL)


def require_unchanged(before, after):
    """Keep the exact boundary comparison; disclose paths and value hashes only."""
    changes = []
    missing = object()
    def fingerprint(value):
        # Presence is hashed too: an absent field differs from an explicit null.
        return checksum(dict(present=False) if value is missing else dict(present=True, value=value))
    def compare(old, new, path):
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                component = key.replace('~', '~0').replace('/', '~1')
                compare(old.get(key, missing), new.get(key, missing), path+'/'+component)
        elif old != new:
            changes.append(dict(field=path, before_sha256=fingerprint(old), after_sha256=fingerprint(new)))
    compare(before, after, '')
    require(not changes, 'benchmark inputs/outputs/headers changed: '+json.dumps(changes, sort_keys=True))


def validate(record, expected):
    """Pure validation of observations; no tools, compilation, measurements or writes."""
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == 'bench' and record['common'] == expected, 'foreign benchmark result')
    require(record['policy'] == policy(), 'benchmark policy changed')
    require([r['selector'] for r in record['completed']] == list(SELECTORS), 'missing/duplicate benchmark selectors')
    for item in record['completed']:
        process = hosted.ci.subprocess.CompletedProcess([],0,json.dumps(item['counts']),'')
        status, counts = hosted.ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == SELECTORS[item['selector']], 'benchmark omitted/failed assertions')
    measured = record['campaign']
    require(all(type(measured[n]) is int and measured[n] == value for n,value in (('rows',27),('windows',135),('raw_files',135)))
            and measured['protocol'] == PROTOCOL, 'incomplete CPU campaign proof')
    hashes = [record['source_environment_sha256']]+[measured[n] for n in ('identity_sha256','raw_sha256','summaries_sha256')]
    generated = record['generated']; hashes += [generated['receipt_sha256']]+list(generated['files'].values())
    require(set(generated['files']) == hosted.generated_names(), 'missing codegen')
    toolset = record['tools']; native = toolset['native']; runner = toolset['runner']
    require(runner['os'] == 'Darwin' and runner['isa'] == 'arm64' and runner['macos'].split('.')[0] == '26', 'wrong benchmark runner')
    require(set(native) == {'cc','cxx','git','cmake','ninja','patch','python'}, 'missing native tools')
    for name,tool in native.items():
        require(tool['path'] and tool['version'], 'invalid native tool')
        hashes.append(tool['sha256'])
        if name in ('cc','cxx'):
            require(tool['version'].splitlines()[0] == engine.VERSION, 'wrong native compiler')
            hashes.append(tool['implementation']['sha256'])
    require(set(generated['compilers']) == {'c','c++'} and all(generated['compilers'][language]['sha256'] == native[name]['sha256']
            and generated['compilers'][language]['version'] == native[name]['version'] for language,name in (('c','cc'),('c++','cxx'))), 'foreign codegen compiler')
    lock = json.loads((ROOT/'plan/toolchains.json').read_text()); pins = {**lock['tools'],**lock['toolchain_profiles']['hosted-macos26']['tools']}
    require(set(toolset['waterbox']) == set(hosted.ci.wb.TOOL_NAMES), 'missing Waterbox tools')
    for name,tool in toolset['waterbox'].items():
        require(tool['path'] and tool['sha256'] == pins[name]['sha256'] and tool['version'] == pins[name]['version'], 'Waterbox tool pin differs')
    require(toolset['sdk']['path'] and toolset['sdk']['version'] == '10.0.402', 'wrong SDK'); hashes.append(toolset['sdk']['sha256'])
    proof = record['inputs']; hashes.append(proof['manual_sha256'])
    require(set(proof['native']) == {'release','state-opt'} and set(proof['runtime']) == {'release','debug'}, 'missing native/runtime variant')
    for tree in proof['native'].values():
        require(set(tree['binaries']) == set(TARGETS) and type(tree['headers']['files']) is int and tree['headers']['files'] > 0, 'missing binaries/headers')
        hashes += list(tree['binaries'].values())+[tree[k] for k in ('cache_sha256','commands_sha256')]+[tree['headers']['sha256']]
    for artifact in (proof['guest'],*proof['runtime'].values()): hashes += [artifact['sha256'],artifact['receipt_sha256']]
    managed = proof['managed']; hashes += [managed['inputs_sha256'],managed['receipt_sha256']]
    for mode in ('release','debug','smoke'):
        require(set(managed_release.OUTPUTS if mode != 'smoke' else ('Smoke.dll','Smoke.deps.json','Smoke.runtimeconfig.json')) <= set(managed[mode]), 'incomplete managed outputs')
        hashes += list(managed[mode].values())
    require(managed['debug'].get('libwaterboxhost.dylib') == proof['runtime']['release']['sha256'], 'Debug wrapper runtime differs')
    require(all(type(h) is str and re.fullmatch('[0-9a-f]{64}',h) for h in hashes), 'invalid benchmark provenance hash')
    return record


def run():
    partition(); before, toolset, source = hosted.common(), tools(), build_support.source_identity()
    require(os.environ.get('P6_TEST_BUILD_RECEIPT') is None, 'foreign native build receipt')
    env = dict(nightly.ENV,P6_RUN_CPU_PERF='0')
    prepare(env); codegen, proof = hosted.generated(), inputs()
    build = ROOT/'build'; build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hosted-cpu-',dir=build) as temporary:
        directory = Path(temporary)/'cpu'; env['P6_CPU_PERF_DIR'] = str(directory)
        execute([sys.executable,'-B','tools/bench.py','run','--root',temporary,'--id','cpu','--compare-cpu-levels','--force'],env)
        measured = campaign(directory); completed = []
        for selector,count in SELECTORS.items():
            process = hosted.ci.command([sys.executable,'-B','-c',hosted.ci.check.DRIVER,selector],env,timeout=6*3600)
            status, counts = hosted.ci.verdict(process)
            require(status == 'PASS' and counts['tests'] == count, selector+': '+str(counts)+process.stderr[-3000:])
            completed.append(dict(selector=selector,counts=counts))
        require(campaign(directory) == measured, 'CPU campaign changed during selectors')
    require_unchanged(dict(common=before, tools=toolset, source_environment=source, generated=codegen, inputs=proof),
                      dict(common=hosted.common(), tools=tools(), source_environment=build_support.source_identity(),
                           generated=hosted.generated(), inputs=inputs()))
    result = validate(dict(status='PASS',profile=PROFILE,kind='bench',common=before,source_environment_sha256=source,
                           tools=toolset,generated=codegen,inputs=proof,campaign=measured,completed=completed,policy=policy()),before)
    require(len(json.dumps(result,separators=(',',':')).encode()) < 100*1024, 'benchmark observation exceeds100KiB')
    return result


if __name__ == '__main__':
    try: result = run()
    except (OSError,ValueError,RuntimeError,AssertionError,KeyError,TypeError,hosted.ci.subprocess.SubprocessError) as error:
        result = dict(status='FAIL',profile=PROFILE,kind='bench',diagnostic=str(error))
    print(MARKER+hosted.ci.redact(json.dumps(result,sort_keys=True,separators=(',',':'))))
    sys.exit(0 if result['status'] == 'PASS' else 1)
