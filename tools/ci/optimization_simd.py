"""Fresh hosted SIMD executions; the local frozen Docker profile is unchanged."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from tests.build_support import source_identity
from tests.simd import evaluate, matrix
from tools.ci import hosted_matrix as hosted, run as ci

ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'hosted-native-optimization-v1'
MARKER = 'P6_OPTIMIZATION_RESULT '
MODES = ('opt', 'fallback', 'san')
SELECTOR = 'tests.simd.test_rgb565'
SOURCES = evaluate.sources()
LLVM = json.loads((ROOT/'plan/toolchains.json').read_text())['tools']['clang']
require = hosted.require


def context(common):
    actual = hosted.common()
    require(common is None or common == actual, 'foreign optimization context')
    return actual


def descriptor(record, common, kind):
    require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['kind'] == kind
            and record['common'] == common, 'foreign/failed SIMD record')
    require(re.fullmatch('[0-9a-f]{64}', record['source_environment_sha256']) is not None, 'missing worker environment identity')


def validate_rows(records, common):
    """Validate current-attempt worker observations without executing or reading files."""
    found, expected = set(), None
    for record in records:
        descriptor(record, common, 'simd-row')
        row = record['row']
        require(row in matrix.ROWS and row not in found and set(record['results']) == set(MODES), 'missing/duplicate SIMD row/mode')
        found.add(row)
        compiler = record['compiler']
        require(compiler['driver_mode'] == 'g++' and Path(compiler['compiler_path']).is_absolute()
                and Path(compiler['compiler_implementation']['path']).is_absolute()
                and re.fullmatch('[0-9a-f]{64}', compiler['compiler_implementation']['sha256']) is not None, 'invalid raw C++ compiler identity')
        for mode, result in record['results'].items():
            require(all(result[k] == v for k, v in compiler.items()), 'SIMD compiler changed between modes')
            require(re.fullmatch('[0-9a-f]{64}', result['binary_sha256']) is not None, 'missing executed SIMD binary')
            expected = matrix.validate_record(result, row, mode, expected, SOURCES)
    require(found == set(matrix.ROWS), 'incomplete SIMD rows')
    return dict(rows=len(found), modes=len(found)*len(MODES), verified_rgb_values=1 << 24, rgb_digest=expected)


def validate_standalone(record, common):
    descriptor(record, common, 'simd-standalone')
    compiler = record['compiler']
    path = compiler['compiler_path']
    suffix = 'build/hosted-tools/llvm/23.1.4/bin/clang-23'
    location = path == '@repo/' + suffix or (Path(path).is_absolute() and str(Path(path)) == path
               and '..' not in Path(path).parts and path.endswith('/' + suffix))
    require(record['selector'] == SELECTOR and compiler['driver_mode'] == 'g++'
            and compiler['compiler'] == LLVM['version'] and compiler['compiler_sha256'] == LLVM['sha256']
            and compiler['compiler_implementation']['sha256'] == LLVM['sha256']
            and compiler['compiler_implementation']['path'] == compiler['compiler_path']
            and location, 'standalone LLVM pin differs')
    process = ci.subprocess.CompletedProcess([], 0, json.dumps(record['counts']), '')
    status, counts = ci.verdict(process)
    require(status == 'PASS' and counts['tests'] == 3, 'standalone SIMD omitted/failed assertions')
    return counts


def row(row, common=None):
    before = context(common)
    hosted.native.preflight(row, 'O2')
    source = source_identity()
    build = ROOT/'build'; build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hosted-simd-', dir=build) as temporary:
        results = {mode: evaluate.evaluate(Path(temporary), mode, row) for mode in MODES}
    keys = ('compiler', 'compiler_sha256', 'compiler_path', 'compiler_implementation', 'driver_mode')
    compiler = {key: results['opt'][key] for key in keys}
    result = dict(status='PASS', profile=PROFILE, kind='simd-row', common=before, row=row,
                  source_environment_sha256=source, compiler=compiler, results=results)
    expected = None
    for mode, record in results.items():
        require(all(record[k] == v for k, v in compiler.items()), 'SIMD compiler changed between modes')
        expected = matrix.validate_record(record, row, mode, expected, SOURCES)
    require(source_identity() == source and context(before) == before and evaluate.sources() == SOURCES
            and evaluate.compiler_configuration(row)[2] == compiler, 'SIMD inputs changed')
    return result


def standalone(common=None):
    before = context(common)
    require('P6_WATERBOX_HOSTED_TOOLS' in os.environ, 'standalone requires the authenticated hosted LLVM bundle')
    _, _, compiler = evaluate.compiler_configuration()
    source = source_identity()
    build = ROOT/'build'; build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hosted-rgb565-', dir=build) as temporary:
        process = ci.command([sys.executable, '-B', '-c', ci.check.DRIVER, SELECTOR], env={'TMPDIR': temporary}, timeout=900)
    status, counts = ci.verdict(process)
    require(status == 'PASS', 'standalone SIMD failed: ' + str(counts) + process.stderr[-3000:])
    result = dict(status='PASS', profile=PROFILE, kind='simd-standalone', common=before, selector=SELECTOR,
                  source_environment_sha256=source, compiler=compiler, counts=counts)
    validate_standalone(result, before)
    require(source_identity() == source and context(before) == before and evaluate.compiler_configuration()[2] == compiler, 'standalone SIMD inputs changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('row', 'standalone'))
    parser.add_argument('--row', choices=tuple(matrix.ROWS))
    args = parser.parse_args()
    try:
        result = row(args.row) if args.action == 'row' else standalone()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ci.subprocess.SubprocessError) as error:
        result = dict(status='FAIL', profile=PROFILE, diagnostic=str(error))
    print(MARKER + ci.redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result['status'] == 'PASS' else 1)
