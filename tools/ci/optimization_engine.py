"""One hosted Apple Clang job: original engine training, equivalence and safety."""
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

from tests.simd import engine_flags as experiment
from tools.ci import hosted_matrix as matrix

ROOT = experiment.ROOT
PROFILE = 'hosted-native-optimization-v1'
MARKER = 'P6_OPTIMIZATION_RESULT '
ROW = 'macos-arm64-appleclang'
MODULE = 'tests.simd.test_engine_flags'
SELECTORS = {MODULE+'.ConfigurationTests': 2, MODULE+'.EquivalenceTests': 1, MODULE+'.SafetyTests': 1}
VARIANTS = ('base', 'lto', 'instrument', 'pgo', 'lto-san', 'pgo-san')
VERSION = json.loads((ROOT/'plan/toolchains.json').read_text())['portability_matrix']['rows'][ROW]['compiler_version']
require = matrix.require


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preparation():
    steps = []
    require(experiment.VARIANTS == VARIANTS, 'engine variants changed')
    for variant in VARIANTS:
        if variant == 'pgo': steps.append(dict(action='merge'))
        steps.extend(dict(action=action, variant=variant) for action in ('build', 'verify'))
    return steps


def restored():
    return [f'{entry.id}@{point}/{mode}' for entry in experiment.corpus.ENTRIES for point in entry.points for mode in entry.modes]


def fact(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0, 'missing/linked/empty engine input: ' + str(path))
    return dict(bytes=path.stat().st_size, sha256=experiment.sha(path))


def toolchain():
    matrix.native.preflight(ROW, 'O2')
    tools = matrix.tools(ROW)
    require(all(tools[name]['version'].splitlines()[0] == VERSION for name in ('cc', 'cxx')), 'requires exact pinned Apple Clang version')
    path = Path(matrix.native.execute(['/usr/bin/xcrun', '--find', 'llvm-profdata']).strip())
    tools['llvm-profdata'] = dict(path=str(path.resolve()), version=matrix.native.execute([path, '--version']).strip(), **fact(path))
    return tools


def dependencies(directory):
    # CMake's Ninja depfile database lists headers actually used by this build,
    # including SDK headers outside source_identity's project-only inventory.
    text = matrix.native.execute(['ninja', '-C', directory, '-t', 'deps'])
    paths = {Path(line[4:]).resolve() if Path(line[4:]).is_absolute() else (directory/line[4:]).resolve()
             for line in text.splitlines() if line.startswith('    ') and line[4:]}
    require(paths and '(STALE)' not in text, 'missing/stale compiler dependency inventory')
    return dict(files=len(paths), sha256=checksum({str(path): fact(path)['sha256'] for path in sorted(paths)}))


def variant_record(variant, source):
    directory = experiment.WORK/variant
    built = json.loads((directory/'build.json').read_text())
    verified = json.loads((directory/'verification.json').read_text())
    binaries = {name: fact(directory/name) for name in ('p6_state_probe', 'p6_bench_probe')}
    require(built['source_identity'] == verified['source_identity'] == source, 'foreign engine source/environment')
    require(built['binaries'] == binaries and verified['probe_sha256'] == binaries['p6_state_probe']['sha256'], 'engine binary changed')
    compile_hash = fact(directory/'compile_commands.json')['sha256']
    require(built['compile_commands_sha256'] == compile_hash, 'compile commands changed')
    canonical = verified['canonical']
    require(canonical['errors'] == {} and set(canonical['cases']) == {entry.id for entry in experiment.corpus.ENTRIES}
            and verified['restored'] == restored(), 'incomplete original corpus/restoration')
    hashes = {name: {key: case[key] for key in ('hardware', 'av', 'per_unit', 'units', 'lines')}
              for name, case in canonical['cases'].items()}
    return dict(build=fact(directory/'build.json')['sha256'], verification=fact(directory/'verification.json')['sha256'],
                compile_commands=compile_hash, binaries=binaries, canonical=dict(cases=sorted(hashes), sha256=checksum(hashes)),
                restored=verified['restored'], dependencies=dependencies(directory))


def training_record(tools):
    path = experiment.WORK/'training.json'; merged = json.loads(path.read_text())
    instrument = experiment.WORK/'instrument/verification.json'
    raw = json.loads(instrument.read_text())['raw_profiles']
    require(raw and len(raw) == len(set(raw)) and merged['raw_profiles'] == len(raw) and merged['private_assets'] is False,
            'incomplete/nonpublic training')
    for name in raw:
        path_raw = Path(name)
        require(path_raw.resolve().is_relative_to((experiment.WORK/'instrument/profiles').resolve()), 'foreign training profile')
        fact(path_raw)
    profile = fact(experiment.PROFDATA)
    require(merged['corpus'] == fact(instrument)['sha256'] and merged['profile_sha256'] == profile['sha256']
            and merged['tool_sha256'] == tools['llvm-profdata']['sha256'] and merged['version'].strip() == tools['llvm-profdata']['version'], 'training identity mismatch')
    return dict(receipt=fact(path)['sha256'], instrument_verification=merged['corpus'], profile=profile,
                raw_profiles=len(raw), private_assets=False, tool_sha256=merged['tool_sha256'])


def validate(record, expected):
    """Pure record validation against current declarations; no build/tool execution."""
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == 'engine'
            and record['common'] == expected, 'foreign/failed engine result')
    sha64 = lambda value: type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None
    require(sha64(record['source_environment_sha256']), 'missing source/environment identity')
    require(record['preparation'] == preparation() and set(record['variants']) == set(VARIANTS), 'incomplete engine preparation')
    tools = record['tools']
    require(set(tools) == {'cc', 'cxx', 'git', 'cmake', 'ninja', 'patch', 'python', 'llvm-profdata'}, 'incomplete tools')
    require(all(sha64(value['sha256']) and bool(value['path']) and bool(value['version']) for value in tools.values()), 'invalid tools')
    require(all(tools[name]['version'].splitlines()[0] == VERSION and sha64(tools[name]['implementation']['sha256']) for name in ('cc', 'cxx')), 'wrong Apple Clang')
    generated = record['generated']
    require(set(generated['files']) == matrix.generated_names() and all(sha64(value) for value in generated['files'].values())
            and sha64(generated['receipt_sha256']), 'incomplete codegen')
    require(set(generated['compilers']) == {'c', 'c++'} and all(generated['compilers'][language]['sha256'] == tools[name]['sha256']
            and generated['compilers'][language]['version'] == tools[name]['version'] for language, name in (('c', 'cc'), ('c++', 'cxx'))), 'foreign codegen compilers')
    reference = record['variants']['base']['canonical']
    require(reference['cases'] == sorted(entry.id for entry in experiment.corpus.ENTRIES) and sha64(reference['sha256']), 'incomplete engine corpus')
    for result in record['variants'].values():
        require(result['canonical'] == reference and result['restored'] == restored(), 'engine equivalence/restore mismatch')
        require(all(sha64(result[key]) for key in ('build', 'verification', 'compile_commands')), 'missing engine receipts')
        require(set(result['binaries']) == {'p6_state_probe', 'p6_bench_probe'} and all(type(value['bytes']) is int and value['bytes'] > 0
                and sha64(value['sha256']) for value in result['binaries'].values()), 'incomplete engine binaries')
        require(type(result['dependencies']['files']) is int and result['dependencies']['files'] > 0
                and sha64(result['dependencies']['sha256']), 'missing header identity')
    training = record['training']
    require(training['private_assets'] is False and type(training['raw_profiles']) is int and training['raw_profiles'] > 0
            and training['instrument_verification'] == record['variants']['instrument']['verification']
            and training['tool_sha256'] == tools['llvm-profdata']['sha256'] and sha64(training['receipt'])
            and sha64(training['profile']['sha256']) and type(training['profile']['bytes']) is int and training['profile']['bytes'] > 0, 'incomplete training')
    require([item['selector'] for item in record['completed']] == list(SELECTORS), 'missing/duplicate engine selectors')
    for item in record['completed']:
        process = matrix.ci.subprocess.CompletedProcess([], 0, json.dumps(item['counts']), '')
        status, counts = matrix.ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == SELECTORS[item['selector']], 'engine selector omitted/failed assertions')
    return record


def run():
    steps = preparation()
    require(not experiment.WORK.exists() and not experiment.WORK.is_symlink(), 'engine workspace must be fresh for this run')
    before, tools, source = matrix.common(), toolchain(), experiment.source_identity()
    codegen = None; variants = {}
    for step in steps:
        args = [sys.executable, '-m', MODULE.removesuffix('.test_engine_flags')+'.engine_flags', step['action']]
        if 'variant' in step: args += ['--variant', step['variant']]
        print('RUN engine ' + str(step), file=sys.stderr, flush=True)
        matrix.native.execute(args, 14400)
        if step['action'] == 'build' and codegen is None: codegen = matrix.generated()
        if step['action'] == 'verify': variants[step['variant']] = variant_record(step['variant'], source)
    training = training_record(tools); completed = []
    for selector, count in SELECTORS.items():
        require(unittest.defaultTestLoader.loadTestsFromName(selector).countTestCases() == count, 'engine selector count changed')
        process = matrix.ci.command([sys.executable, '-c', matrix.ci.check.DRIVER, selector], env={'P6_TEST_ENGINE_FLAGS': '1'}, timeout=14400)
        status, counts = matrix.ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == count, selector + ': ' + str(counts) + process.stderr[-3000:])
        completed.append(dict(selector=selector, counts=counts))
    require((matrix.common(), toolchain(), experiment.source_identity(), matrix.generated()) == (before, tools, source, codegen), 'engine inputs changed')
    require({variant: variant_record(variant, source) for variant in VARIANTS} == variants and training_record(tools) == training, 'engine outputs/headers/training changed')
    return validate(dict(status='PASS', profile=PROFILE, kind='engine', common=before, source_environment_sha256=source,
                         tools=tools, generated=codegen, variants=variants, training=training, preparation=steps, completed=completed), before)


if __name__ == '__main__':
    try:
        result = run()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', profile=PROFILE, kind='engine', diagnostic=str(error))
    print(MARKER + matrix.ci.redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result['status'] == 'PASS' else 1)
