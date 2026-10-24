"""Own worker records and mocked API only; no emulator, compiler or network."""
import copy
import json
import unittest
from unittest import mock

from tools.ci import hosted_optimization as hosted


class HostedOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.common = dict(repository='own/repo', run_id='123', run_attempt='2', revision='a'*40, source_tree_sha256='b'*64)
        self.checks = {kind: mock.Mock() for kind in ('engine', 'frontend', 'simd')}

    def records(self):
        return [dict(status='PASS', profile=hosted.PROFILE, kind=kind, common=copy.deepcopy(self.common),
                     **({'row': name.removeprefix('optimization / ')} if kind in ('frontend', 'simd-row') else {}))
                for name, kinds in hosted.JOBS.items() for kind in kinds]

    def test_original_seven_selectors_and_eleven_methods_cannot_drift(self):
        self.assertEqual(len(hosted.partition()), 7)
        self.assertEqual(sum(hosted.SELECTORS.values()), 11)
        for fault in ('missing', 'duplicate', 'extra'):
            selected = list(hosted.SELECTORS)
            if fault == 'missing': selected.pop()
            if fault == 'duplicate': selected[-1] = selected[0]
            if fault == 'extra': selected.append('own.extra')
            with self.subTest(fault=fault), mock.patch.object(hosted.nightly, 'suites', return_value=selected), self.assertRaises(RuntimeError):
                hosted.partition()
        with mock.patch.dict(hosted.SELECTORS, {'tests.simd.test_rgb565': 4}), self.assertRaisesRegex(RuntimeError, 'method count'):
            hosted.partition()

    def test_complete_recipe_calls_all_shared_worker_oracles_without_inventing_unittest_counts(self):
        records = self.records()
        with mock.patch.object(hosted, 'validators', return_value=self.checks): result = hosted.validate(records, self.common)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['coverage']['original_methods'], 11)
        self.assertEqual(result['coverage']['engine_unittest_tests'] + result['coverage']['rgb565_unittest_tests'], 7)
        self.checks['engine'].validate.assert_called_once_with(records[0], self.common)
        self.assertEqual(self.checks['frontend'].validate.call_count, 5)
        self.assertEqual(len(self.checks['frontend'].validate_rows.call_args.args[0]), 5)
        self.assertEqual(len(self.checks['simd'].validate_rows.call_args.args[0]), 5)
        self.checks['simd'].validate_standalone.assert_called_once_with(records[1], self.common)
        for kind, method in (('engine', 'validate'), ('frontend', 'validate_rows'), ('simd', 'validate_rows'), ('simd', 'validate_standalone')):
            with self.subTest(kind=kind, method=method), mock.patch.object(self.checks[kind], method, side_effect=AssertionError('own oracle rejection')), \
                 mock.patch.object(hosted, 'validators', return_value=self.checks), self.assertRaisesRegex(AssertionError, 'oracle rejection'):
                hosted.validate(records, self.common)

    def test_omissions_duplicates_foreign_attempts_or_sources_fail_before_worker_validation(self):
        for fault in ('missing', 'duplicate', 'kind', 'row', 'profile', 'failure', 'attempt', 'revision', 'source'):
            records = self.records()
            if fault == 'missing': records.pop()
            if fault == 'duplicate': records[-1] = copy.deepcopy(records[2])
            if fault == 'kind': records[0]['kind'] = 'historical'
            if fault == 'row': records[2]['row'] = 'undeclared'
            if fault == 'profile': records[0]['profile'] = 'local-docker'
            if fault == 'failure': records[0]['status'] = 'FAIL'
            if fault == 'attempt': records[0]['common']['run_attempt'] = '1'
            if fault == 'revision': records[0]['common']['revision'] = 'f'*40
            if fault == 'source': records[0]['common']['source_tree_sha256'] = 'f'*64
            with self.subTest(fault=fault), mock.patch.object(hosted, 'validators') as validators, self.assertRaises(RuntimeError):
                hosted.validate(records, self.common)
            validators.assert_not_called()

    def test_parser_requires_exactly_one_record_of_each_expected_kind(self):
        name = 'optimization / ' + hosted.ROWS[0]
        original = [record for record in self.records() if hosted.label(record) == name]
        for fault in ('none', 'missing', 'duplicate', 'extra', 'kind', 'row', 'violation'):
            records = copy.deepcopy(original)
            if fault == 'missing': records.pop()
            if fault == 'duplicate': records[-1] = copy.deepcopy(records[0])
            if fault == 'extra': records.append(copy.deepcopy(records[0]))
            if fault == 'kind': records[0]['kind'] = 'engine'
            if fault == 'row': records[0]['row'] = hosted.ROWS[1]
            log = ''.join('2026-10-24T00:00:00Z ' + hosted.MARKER + json.dumps(record) + '\n' for record in records)
            if fault == 'violation': log += 'VIOLATION own fixture\n'
            with self.subTest(fault=fault):
                if fault == 'none': self.assertEqual(hosted.records_from_log(log, name), original)
                else:
                    with self.assertRaises(RuntimeError): hosted.records_from_log(log, name)

    def test_api_uses_current_attempt_only_and_requires_complete_successful_jobs_and_stable_sources(self):
        records = self.records()
        for fault in ('none', 'missing', 'duplicate-id', 'duplicate-name', 'failed', 'running', 'truncated', 'drift'):
            jobs = [dict(id=i+1, name=name, status='completed', conclusion='success') for i, name in enumerate(hosted.JOBS)]
            if fault == 'missing': jobs.pop()
            if fault == 'duplicate-id': jobs[-1]['id'] = jobs[0]['id']
            if fault == 'duplicate-name': jobs[-1]['name'] = jobs[0]['name']
            if fault == 'failed': jobs[0]['conclusion'] = 'failure'
            if fault == 'running': jobs[0]['status'] = 'in_progress'
            listing = dict(jobs=jobs, total_count=len(jobs) + (fault == 'truncated'))
            def execute(argv):
                self.assertEqual(argv, ['gh', 'api', 'repos/own/repo/actions/runs/123/attempts/2/jobs?per_page=100'])
                return json.dumps(listing)
            def log(repo, job_id):
                self.assertEqual(repo, 'own/repo')
                name = jobs[job_id-1]['name']
                return ''.join(hosted.MARKER + json.dumps(record) + '\n' for record in records if hosted.label(record) == name)
            with self.subTest(fault=fault), mock.patch.object(hosted.matrix, 'common', side_effect=[self.common, {} if fault == 'drift' else self.common]), \
                 mock.patch.object(hosted.matrix.native, 'execute', side_effect=execute), mock.patch.object(hosted, 'read_job_log', side_effect=log), \
                 mock.patch.object(hosted, 'validators', return_value=self.checks):
                if fault == 'none': self.assertEqual(hosted.aggregate()['status'], 'PASS')
                else:
                    with self.assertRaises(RuntimeError): hosted.aggregate()
