#!/usr/bin/env python3
"""Current-attempt distributed matrix; public native runners, never Docker pins."""
import argparse
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.ci import hosted_corpus as corpus, linux_native as native, run as ci

PROFILE = 'hosted-native-distributed-v1'
MARKER = 'P6_MATRIX_RESULT '
INFRA = {'tests.portability.test_check': 7, 'tests.portability.test_clock.PreloadTests': 2,
         'tests.portability.test_portability.MatrixCoherenceTests': 4,
         'tests.portability.test_portability.DifferentialMatrixTests': 1,
         'tests.portability.test_portability.MatrixBuildReuseTests': 4}
HEAVY = {'tests.portability.test_clock.ClockStartupTests': 1,
         'tests.portability.test_portability.RowExecutionTests': 1,
         'tests.portability.test_portability.DifferentialRowExecutionTests': 1}
require = native.require


def partition():
    def ids(suite):
        return {item.id() for item in suite} if all(isinstance(item, unittest.TestCase) for item in suite) else set().union(*(ids(item) for item in suite))
    selected = []
    for selector, count in {**INFRA, **HEAVY}.items():
        group = ids(unittest.defaultTestLoader.loadTestsFromName(selector))
        require(len(group) == count, 'matrix selector count changed: ' + selector)
        selected.extend(group)
    modules = {name.rsplit('.', 1)[0] for name in HEAVY} | {'tests.portability.test_check'}
    found = set().union(*(ids(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name))) for name in modules))
    require(len(selected) == len(set(selected)) and set(selected) == found, 'matrix selectors omitted or duplicated')


def common():
    context = {key: os.environ.get('GITHUB_' + key.upper(), '') for key in ('repository', 'run_id', 'run_attempt', 'sha')}
    require(re.fullmatch(r'[\w.-]+/[\w.-]+', context['repository']) is not None
            and all(re.fullmatch(r'[1-9]\d*', context[key]) for key in ('run_id', 'run_attempt')), 'missing Actions run/attempt context')
    identity = ci.identity()
    require(not identity['dirty'] and identity['revision'] == context.pop('sha'), 'Actions revision is not the clean checkout')
    return dict(**context, revision=identity['revision'], source_tree_sha256=identity['source_tree_sha256'],
                recipe=corpus.recipe_identity(native.execute),
                goldens={name: native.digest(ROOT/f'tests/portability/{name}.golden.json') for name in ('corpus', 'differential')})


def tools(row):
    paths = dict(zip(('cc', 'cxx'), native.compilers(row)))
    paths.update({name: shutil.which(name) for name in ('git', 'cmake', 'ninja', 'patch')}, python=sys.executable)
    result = {}
    for name, path in paths.items():
        require(bool(path) and Path(path).is_file(), 'missing worker tool: ' + name)
        result[name] = dict(path=str(Path(path).resolve()), sha256=native.digest(path),
                            version=native.execute([path, '--version']).strip())
        if name in ('cc', 'cxx'):
            effective = native.execute(['/usr/bin/xcrun', '--find', Path(path).name]).strip() if native.platform.system() == 'Darwin' else path
            result[name]['implementation'] = dict(path=str(Path(effective).resolve()), sha256=native.digest(effective))
    return result


def generated_names():
    manifest = json.loads((ROOT/'manifests/codegen.json').read_text())
    names = [name for generator in manifest['generators'] for name in generator['outputs']]
    require(manifest['schema_version'] == 1 and names and len(names) == len(set(names)), 'invalid codegen output manifest')
    return set(names)


def generated():
    path = ROOT/'build/generated/codegen-receipt.json'
    receipt = json.loads(path.read_text())
    names, expected = [item['path'] for item in receipt['files']], generated_names()
    require(len(names) == len(expected) and set(names) == expected, 'incomplete/duplicate codegen inventory')
    for item in receipt['files']:
        target = path.parent/item['path']
        require(target.resolve().is_relative_to(path.parent.resolve()) and native.digest(target) == item['sha256'], 'generated source drift')
    return dict(receipt_sha256=native.digest(path), files={item['path']: item['sha256'] for item in receipt['files']},
                compilers=receipt['identity']['compilers'])


def strict_clock(argv, timeout=180, env=None):
    process = ci.command(list(map(str, argv)), timeout=timeout,
                         env={**(env or {}), 'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                              'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'})
    require(process.returncode == 0 and not process.stderr, 'clock sanitizer startup failed: ' + process.stderr[-3000:])
    return process.stdout


def cell(row, opt):
    partition(); native.preflight(row, opt)
    before, toolset, source = common(), tools(row), native.source_identity()
    require(re.fullmatch('[0-9a-f]{64}', source) is not None, 'invalid source/environment identity')
    tree = ROOT/'build/hosted-matrix'/row/opt
    targets = ('p6_state_probe', 'p6_headless')
    _, mode = native.selection(row, opt)
    for argv in native.build_commands(row, opt, tree, targets): native.execute(argv, 1800)
    built, codegen = native.binary_identity(tree, row, targets), generated()
    observations = corpus.run(tree, native.compilers(row)[0], mode, native.execute, clock_execute=strict_clock)
    require((common(), tools(row), native.source_identity(), native.binary_identity(tree, row, targets), generated())
            == (before, toolset, source, built, codegen), 'worker sources/tools/generated outputs/binaries changed')
    return dict(status='PASS', profile=PROFILE, kind='cell', common=before, row=row, optimization=opt,
                tools=toolset, generated=codegen, build=built, observations=observations, source_environment_sha256=source,
                runner=dict(os=native.platform.system(), isa=native.platform.machine(), description=native.platform.platform()),
                flags=mode, scope='native hosted execution; no local Docker image identity')


def infrastructure():
    partition(); before = common(); completed = []
    for selector, count in INFRA.items():
        process = ci.command([sys.executable, '-c', ci.check.DRIVER, selector], env={'P6_TEST_PORTABILITY': '0'})
        status, counts = ci.verdict(process)
        require(status == 'PASS' and counts['tests'] == count, selector + ': ' + str(counts) + process.stderr[-3000:])
        completed.append(dict(selector=selector, counts=counts))
    require(common() == before, 'infrastructure sources changed')
    return dict(status='PASS', profile=PROFILE, kind='infrastructure', common=before, completed=completed)


def validate(records, expected):
    partition()
    cells = {(row, opt) for row in native.HOSTED_RUNNERS for opt in native.VARIANTS}
    found = set(); infra = []
    for record in records:
        require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['common'] == expected, 'foreign/failed matrix record')
        if record['kind'] == 'infrastructure':
            infra.append(record)
            require([item['selector'] for item in record['completed']] == list(INFRA), 'missing/duplicate infrastructure selector')
            for item in record['completed']:
                process = ci.subprocess.CompletedProcess([], 0, json.dumps(item['counts']), '')
                status, counts = ci.verdict(process)
                require(status == 'PASS' and counts['tests'] == INFRA[item['selector']], 'infrastructure omitted/failed assertions')
            continue
        require(record['kind'] == 'cell', 'unknown matrix record')
        key = (record['row'], record['optimization'])
        require(key in cells and key not in found, 'unknown/duplicate matrix cell'); found.add(key)
        require(re.fullmatch('[0-9a-f]{64}', record.get('source_environment_sha256', '')) is not None, 'missing source/environment identity')
        _, mode = native.selection(*key)
        require(record['flags'] == mode and record['runner']['os'] == ('Darwin' if key[0].startswith('macos-') else 'Linux')
                and record['runner']['isa'] in (('arm64', 'aarch64') if native.ROWS[key[0]]['isa'] == 'arm64' else ('x86_64',)), 'wrong worker ISA/flags')
        require(set(record['tools']) == {'cc', 'cxx', 'git', 'cmake', 'ninja', 'patch', 'python'}, 'incomplete tool identity')
        for item in record['tools'].values():
            require(Path(item['path']).is_absolute() and bool(item['version']) and re.fullmatch('[0-9a-f]{64}', item['sha256']) is not None, 'invalid tool identity')
        for name in ('cc', 'cxx'):
            implementation = record['tools'][name]['implementation']
            require(Path(implementation['path']).is_absolute() and re.fullmatch('[0-9a-f]{64}', implementation['sha256']) is not None, 'invalid compiler implementation')
        hashes = [record['build'][name] for name in ('cmake_cache_sha256', 'codegen_sha256')]
        require(set(record['build']['binaries']) == {'p6_state_probe', 'p6_headless'}, 'missing binaries')
        hashes += list(record['build']['binaries'].values()) + list(record['generated']['files'].values())
        require(set(record['generated']['files']) == generated_names() and len(record['generated']['files']) == len(generated_names())
                and record['generated']['receipt_sha256'] == record['build']['codegen_sha256']
                and all(re.fullmatch('[0-9a-f]{64}', h) for h in hashes), 'incomplete build/codegen identity')
        compilers = record['generated']['compilers']
        require(set(compilers) == {'c', 'c++'} and all(compilers[language]['sha256'] == record['tools'][tool]['sha256']
                and compilers[language]['version'] == record['tools'][tool]['version'] for language, tool in (('c', 'cc'), ('c++', 'cxx'))), 'codegen compiler identity mismatch')
        observations = record['observations']
        require(set(observations['corpora']) == {'corpus', 'differential'}, 'missing corpus')
        for name, result in observations['corpora'].items():
            golden = json.loads((ROOT/f'tests/portability/{name}.golden.json').read_text())
            wanted = dict(probe_schema=2, cases=len(golden['cases']), variants=['baseline', 'host'], hardware_equal=len(golden['cases']),
                          av_equal=len(golden['cases']), headless=len(corpus.row_driver.HEADLESS_CASES) if name == 'corpus' else 0,
                          corpus_sha256=golden['corpus']['sha256'])
            require(result == wanted, 'incomplete corpus comparison: ' + name)
        require(observations['clock'] == dict(offset=corpus.run_matrix.FAKE_OFFSET, civil_clocks=3, monotonic_unchanged=True), 'missing clock startup/shift checks')
    require(found == cells and len(infra) == 1, 'incomplete distributed matrix')
    return dict(status='PASS', profile=PROFILE, common=expected, workers=records,
                coverage=dict(original_methods=21, unittest_tests=18, corpus_cells=30, sanitized_clock_rows=5, clock_checks=15),
                scope='complete matrix assertions on distributed native runners; not Docker or other accumulated lanes')


def read_job_log(repository, job_id):
    """Treat log bodies as bounded data; never forward API credentials to storage."""
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is not None
            and type(job_id) is int and job_id > 0, 'invalid GitHub job descriptor')
    token = os.environ.get('GH_TOKEN', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', token) is not None, 'missing/invalid Actions read token')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs',
                                     headers={'Authorization': 'Bearer ' + token, 'User-Agent': PROFILE, 'Accept-Encoding': 'identity'})
    limit = 8 * 1024 * 1024
    for redirected in (False, True):
        try:
            with opener.open(request, timeout=30) as response:
                require(response.status == 200 and response.headers.get_content_type() in ('text/plain', 'application/octet-stream'), 'unexpected log response type/status')
                length = response.headers.get('Content-Length')
                require(length is None or (length.isdecimal() and int(length) <= limit), 'oversized/invalid log length')
                data = response.read(limit + 1)
                require(len(data) <= limit, 'oversized log body')
            text = data.decode('utf-8')
            text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]', '', text)
            return ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
        except urllib.error.HTTPError as error:
            location = error.headers.get('Location', '')
            code = error.code; error.close()
            require(code == 302 and not redirected, 'GitHub log request failed')
            target = urllib.parse.urlsplit(location)
            require(len(location) <= 8192 and target.scheme == 'https' and target.hostname
                    and target.username is None and target.password is None and not any(ord(c) < 33 for c in location), 'unsafe log redirect')
            # A new request, not a redirected authenticated request. No token,
            # cookies or API headers are sent to the signed storage URL.
            request = urllib.request.Request(location, headers={'User-Agent': PROFILE, 'Accept-Encoding': 'identity'})
        except urllib.error.URLError as error:
            raise RuntimeError('GitHub log transport failed') from error


def aggregate():
    before = common(); repo = before['repository']; run = before['run_id']; attempt = before['run_attempt']
    listing = json.loads(native.execute(['gh', 'api', f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100']))
    require(listing['total_count'] == len(listing['jobs']) < 100, 'incomplete Actions job listing')
    names = {f'{row} / {opt}' for row in native.HOSTED_RUNNERS for opt in native.VARIANTS} | {'matrix-infrastructure'}
    selected = [job for job in listing['jobs'] if job['name'] in names]
    require(len(selected) == len(names) and {job['name'] for job in selected} == names, 'missing/duplicate current-attempt jobs')
    records = []
    for job in selected:
        require(job['status'] == 'completed' and job['conclusion'] == 'success', 'matrix job not successful: ' + job['name'])
        log = read_job_log(repo, job['id'])
        matches = re.findall(r'^(?:\S+Z )?' + MARKER + r'(\{[^\n]+\})\r?$', log, re.M)
        require(len(matches) == 1, 'missing/duplicate terminal worker JSON: ' + job['name'])
        record = json.loads(matches[0])
        label = 'matrix-infrastructure' if record['kind'] == 'infrastructure' else f'{record["row"]} / {record["optimization"]}'
        require(label == job['name'], 'job/worker descriptor mismatch'); records.append(record)
    result = validate(records, before)
    require(common() == before, 'aggregate sources changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('cell', 'infrastructure', 'aggregate'))
    parser.add_argument('--row', choices=tuple(native.HOSTED_RUNNERS))
    parser.add_argument('--opt', choices=native.VARIANTS)
    args = parser.parse_args()
    try:
        result = cell(args.row, args.opt) if args.action == 'cell' else infrastructure() if args.action == 'infrastructure' else aggregate()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', profile=PROFILE, diagnostic=str(error))
    print(MARKER + ci.redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result['status'] == 'PASS' else 1)
