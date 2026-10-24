#!/usr/bin/env python3
"""Complete hosted performance assertions; timing budgets remain advisory."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.ci import hosted_optimization as shared, nightly

PROFILE, MARKER = 'hosted-native-performance-v1', 'P6_PERFORMANCE_RESULT '
SELECTORS = {'tests.bench.test_budgets': 1, 'tests.bench.test_core_io': 5, 'tests.bench.test_cpu_arguments': 4,
             'tests.bench.test_cpu_campaign': 5, 'tests.bench.test_native': 5, 'tests.bench.test_statistics': 12,
             'tests.bench.test_wb12': 6, 'tests.simd.test_decision.PerformanceTests': 1,
             'tests.simd.test_engine_flags.PerformanceTests': 1, 'tests.simd.test_engine_flags.DecisionTests': 1,
             'tests.simd.test_frontend_rgb565.PerformanceTests': 1, 'tests.simd.test_frontend_rgb565.DecisionTests': 1}
ROWS = shared.ROWS
JOBS = {'performance-bench': ('bench',), 'performance-engine': ('engine',),
        **{'performance / '+row: ('frontend',) for row in ROWS}}
require = shared.require


def partition():
    selected = nightly.suites('performance')
    require(len(selected) == len(SELECTORS) and set(selected) == set(SELECTORS), 'performance selectors changed')
    def ids(suite):
        if isinstance(suite, unittest.TestCase): return [suite.id()]
        return [name for item in suite for name in ids(item)]
    methods = []
    for name, count in SELECTORS.items():
        found = ids(unittest.defaultTestLoader.loadTestsFromName(name))
        require(len(found) == count and not any('_FailedTest' in method for method in found), 'performance method count changed: '+name)
        methods.extend(found)
    require(len(methods) == len(set(methods)) == 43, 'performance methods omitted/duplicated')
    return dict(SELECTORS)


def label(record):
    kind = record['kind']
    if kind in ('bench', 'engine'): return 'performance-'+kind
    require(kind == 'frontend' and record['row'] in ROWS, 'unknown performance worker')
    return 'performance / '+record['row']


def validators():
    return {kind: importlib.import_module('tools.ci.performance_'+kind) for kind in ('bench', 'engine', 'frontend')}


def validate(records, expected):
    selectors = partition(); groups = {'bench': [], 'engine': [], 'frontend': []}; found = set()
    for record in records:
        require(record['status'] == 'PASS' and record['profile'] == PROFILE and record['common'] == expected, 'foreign/failed performance record')
        job = label(record); require(job not in found, 'duplicate performance worker'); found.add(job)
        groups[record['kind']].append(record)
    require(found == set(JOBS), 'incomplete performance recipe')
    checks = validators()
    checks['bench'].validate(groups['bench'][0], expected)
    checks['engine'].validate(groups['engine'][0], expected)
    frontend = checks['frontend'].validate_rows(groups['frontend'], expected)
    require(frontend == dict(verified_rows=5, measured_rows=3, measured_windows=150, original_methods=2, unittest_tests=0), 'incomplete frontend timing assertions')
    return dict(status='PASS', profile=PROFILE, common=expected, workers=records,
                coverage=dict(original_selectors=selectors, original_methods=43, unittest_tests=41,
                              cpu_rows=27, cpu_windows=135, engine_windows=75, frontend=frontend),
                scope='complete original performance assertions on pinned hosted rows; advisory budgets, no dedicated-runner or other lane claim')


def aggregate():
    before = shared.matrix.common()
    result = validate(shared.collect(JOBS, MARKER, label, before), before)
    require(shared.matrix.common() == before, 'performance aggregate sources changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('action', choices=('partition', 'aggregate'))
    args = parser.parse_args()
    try:
        result = aggregate() if args.action == 'aggregate' else dict(status='PASS', selectors=partition(), scope='partition only')
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', profile=PROFILE, diagnostic=str(error))
    print((MARKER if args.action == 'aggregate' else 'P6_PERFORMANCE_PARTITION ') + shared.ci.redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result['status'] == 'PASS' else 1)
