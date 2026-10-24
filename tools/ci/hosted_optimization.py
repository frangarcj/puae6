#!/usr/bin/env python3
"""Join the complete optimization recipe from this Actions attempt only."""
import argparse
import importlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.ci import hosted_matrix as matrix, nightly, run as ci
from tools.ci.action_logs import read_job_log

PROFILE = 'hosted-native-optimization-v1'
MARKER = 'P6_OPTIMIZATION_RESULT '
SELECTORS = {
    'tests.simd.test_engine_flags.ConfigurationTests': 2,
    'tests.simd.test_engine_flags.EquivalenceTests': 1,
    'tests.simd.test_engine_flags.SafetyTests': 1,
    'tests.simd.test_frontend_rgb565.EquivalenceTests': 1,
    'tests.simd.test_frontend_rgb565.SafetyTests': 1,
    'tests.simd.test_matrix': 2,
    'tests.simd.test_rgb565': 3,
}
ROWS = tuple(matrix.native.HOSTED_RUNNERS)
JOBS = {'optimization-engine': ('engine',), 'optimization-standalone': ('simd-standalone',),
        **{'optimization / ' + row: ('frontend', 'simd-row') for row in ROWS}}
require = matrix.require


def partition():
    def ids(suite):
        if isinstance(suite, unittest.TestCase): return [suite.id()]
        return [name for item in suite for name in ids(item)]
    selected = nightly.suites('optimization')
    require(len(selected) == len(SELECTORS) and set(selected) == set(SELECTORS), 'optimization selectors changed')
    methods = []
    for selector, count in SELECTORS.items():
        found = ids(unittest.defaultTestLoader.loadTestsFromName(selector))
        require(len(found) == count and not any('_FailedTest' in name for name in found), 'optimization method count changed: ' + selector)
        methods.extend(found)
    require(len(methods) == len(set(methods)) == 11, 'optimization omitted or duplicated methods')
    return dict(SELECTORS)


def validators():
    return {kind: importlib.import_module('tools.ci.optimization_' + kind) for kind in ('engine', 'frontend', 'simd')}


def label(record):
    kind = record['kind']
    if kind == 'engine': return 'optimization-engine'
    if kind == 'simd-standalone': return 'optimization-standalone'
    require(kind in ('frontend', 'simd-row') and record['row'] in ROWS, 'unknown optimization worker')
    return 'optimization / ' + record['row']


def validate(records, expected):
    selectors = partition(); found = set(); groups = {kind: [] for kind in ('engine', 'frontend', 'simd-row', 'simd-standalone')}
    for record in records:
        require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['common'] == expected, 'foreign or failed optimization record')
        worker = label(record); key = (worker, record['kind'])
        require(key not in found, 'duplicate optimization record'); found.add(key)
        groups[record['kind']].append(record)
    wanted = {(job, kind) for job, kinds in JOBS.items() for kind in kinds}
    require(found == wanted, 'incomplete optimization recipe')
    checks = validators()
    checks['engine'].validate(groups['engine'][0], expected)
    for record in groups['frontend']: checks['frontend'].validate(record, expected)
    checks['frontend'].validate_rows(groups['frontend'])
    checks['simd'].validate_rows(groups['simd-row'], expected)
    checks['simd'].validate_standalone(groups['simd-standalone'][0], expected)
    return dict(status='PASS', profile=PROFILE, common=expected, workers=records,
                coverage=dict(original_selectors=selectors, original_methods=11,
                              engine_unittest_tests=4, rgb565_unittest_tests=3,
                              frontend_rows=5, frontend_modes=10, simd_rows=5, simd_modes=15),
                scope='all original optimization assertions on pinned native hosted rows; no performance or other lane claim')


def records_from_log(log, job):
    require('VIOLATION' not in log, 'worker log reported a protocol violation')
    matches = re.findall(r'^(?:\S+Z )?' + MARKER + r'(\{[^\n]+\})\r?$', log, re.M)
    require(len(matches) == len(JOBS[job]), 'missing or duplicate terminal optimization JSON: ' + job)
    records = [json.loads(item) for item in matches]
    require({item['kind'] for item in records} == set(JOBS[job])
            and all(label(item) == job for item in records), 'job/optimization descriptor mismatch')
    return records


def aggregate():
    before = matrix.common(); repo, run, attempt = (before[key] for key in ('repository', 'run_id', 'run_attempt'))
    listing = json.loads(matrix.native.execute(['gh', 'api', f'repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100']))
    require(listing['total_count'] == len(listing['jobs']) < 100, 'incomplete Actions job listing')
    selected = [job for job in listing['jobs'] if job['name'] in JOBS]
    require(len(selected) == len(JOBS) and {job['name'] for job in selected} == set(JOBS), 'missing or duplicate current-attempt jobs')
    require(len({job['id'] for job in selected}) == len(selected), 'duplicate current-attempt job ID')
    records = []
    for job in selected:
        require(job['status'] == 'completed' and job['conclusion'] == 'success', 'optimization job not successful: ' + job['name'])
        records.extend(records_from_log(read_job_log(repo, job['id']), job['name']))
    result = validate(records, before)
    require(matrix.common() == before, 'aggregate sources changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('partition', 'aggregate'))
    args = parser.parse_args()
    try:
        result = aggregate() if args.action == 'aggregate' else dict(status='PASS', profile=PROFILE, selectors=partition(), scope='selector partition only')
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', profile=PROFILE, diagnostic=str(error))
    print(MARKER + ci.redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result['status'] == 'PASS' else 1)
