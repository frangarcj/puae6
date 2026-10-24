#!/usr/bin/env python3
"""Recover only an original matrix aggregator, replacing its job-log transport."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MARKER = 'P6_MATRIX_RECOVERY '
WORKFLOW = '.github/workflows/native-linux.yml'


def require(condition, diagnostic):
    if not condition: raise ValueError(diagnostic)


def api(endpoint):
    process = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True, timeout=60)
    require(process.returncode == 0, 'GitHub metadata request failed')
    return json.loads(process.stdout)


def inspect_run(repository, run, attempt):
    require(isinstance(repository, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'invalid repository')
    require(all(isinstance(v, str) and re.fullmatch(r'[1-9][0-9]{0,17}', v) for v in (run, attempt)), 'invalid source run/attempt')
    workflow = api(f'repos/{repository}/actions/workflows/native-linux.yml')
    data = api(f'repos/{repository}/actions/runs/{run}/attempts/{attempt}')
    require(type(workflow['id']) is int and workflow['id'] > 0 and workflow['path'] == WORKFLOW
            and type(data['workflow_id']) is int and data['workflow_id'] == workflow['id'] and data['path'] == WORKFLOW, 'foreign workflow')
    require(data['repository']['full_name'] == data['head_repository']['full_name'] == repository, 'foreign source repository')
    require(type(data['id']) is int and data['id'] == int(run) and type(data['run_attempt']) is int
            and data['run_attempt'] == int(attempt) and data['event'] == 'workflow_dispatch' and data['status'] == 'completed', 'source run/attempt is not completed manual matrix')
    sha = data['head_sha']
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha), 'invalid API source revision')
    return dict(repository=repository, run_id=run, run_attempt=attempt, revision=sha,
                workflow_id=workflow['id'], workflow_path=WORKFLOW, workflow_conclusion=data['conclusion'])


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True, timeout=60).strip()


def source_checkout(source, revision):
    source = Path(source).absolute()
    require(source == source.resolve() and source.is_dir(), 'source checkout must be a real directory')
    require(not source.is_relative_to(ROOT) and not ROOT.is_relative_to(source), 'source and tooling checkouts must be disjoint')
    require(Path(git(source, 'rev-parse', '--show-toplevel')).resolve() == source
            and git(source, 'rev-parse', 'HEAD') == revision, 'source checkout revision differs from API')
    require(not git(source, 'status', '--porcelain', '--untracked-files=all'), 'original source checkout is dirty')
    return source


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def project_isolation(source):
    for name, module in list(sys.modules.items()):
        if name.split('.')[0] in ('tools', 'tests'):
            locations = [module.__file__] if getattr(module, '__file__', None) else list(getattr(module, '__path__', ()))
            require(locations and all(Path(p).resolve().is_relative_to(source) for p in locations), 'mixed tooling/original project imports: ' + name)


def log_bridge(execute, reader, repository, jobs):
    endpoint = re.compile(r'repos/' + re.escape(repository) + r'/actions/jobs/([1-9][0-9]*)/logs')
    def bridged(argv, *args, **kwargs):
        if len(argv) == 3 and list(argv[:2]) == ['gh', 'api'] and (match := endpoint.fullmatch(str(argv[2]))):
            job = int(match[1]); require(job in jobs, 'log job is outside verified source attempt')
            text = reader.read_job_log(repository, job)
            require('VIOLATION' not in text, 'libretro host reported a protocol violation')
            return text
        return execute(argv, *args, **kwargs)
    return bridged


def aggregate_original(source, descriptor, recovery):
    source = source_checkout(source, descriptor['revision'])
    require(not any(n.split('.')[0] in ('tools', 'tests') for n in sys.modules), 'child already imported a project')
    reader = load_file('_p6_recovery_log_reader', ROOT / 'tools/ci/action_logs.py')
    require(not any(n.split('.')[0] in ('tools', 'tests') for n in sys.modules), 'log reader imported a project')
    sys.path[:0] = [str(source), str(source / 'tools')]
    original = load_file('_p6_original_matrix', source / 'tools/ci/hosted_matrix.py')
    project_isolation(source)
    repository, run, attempt = (descriptor[k] for k in ('repository', 'run_id', 'run_attempt'))
    require(all(os.environ.get('GITHUB_' + key) == value for key, value in
                [('REPOSITORY', repository), ('RUN_ID', run), ('RUN_ATTEMPT', attempt), ('SHA', descriptor['revision'])]), 'child source context differs')
    execute = original.native.execute
    listing = json.loads(execute(['gh', 'api', f'repos/{repository}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100']))
    jobs = [j['id'] for j in listing['jobs']]
    require(listing['total_count'] == len(jobs) < 100 and len(jobs) == len(set(jobs))
            and all(type(j) is int and j > 0 for j in jobs), 'invalid source-attempt job whitelist')
    original.native.execute = log_bridge(execute, reader, repository, set(jobs))
    try:
        result = original.aggregate()
    finally:
        original.native.execute = execute
    source_checkout(source, descriptor['revision']); project_isolation(source)
    require(result['status'] == 'PASS', 'original aggregator did not pass')
    return dict(status='PASS', kind='directed-log-transport-recovery', source=descriptor, recovery=recovery,
                original_aggregate=result, scope='original assertions with repaired log transport; original workflow conclusion unchanged')


def recover(source, descriptor):
    source = source_checkout(source, descriptor['revision'])
    recovery = {key: os.environ.get('GITHUB_' + key.upper(), '') for key in ('repository', 'run_id', 'run_attempt', 'sha')}
    recovery['tooling_revision'] = git(ROOT, 'rev-parse', 'HEAD')
    require(recovery['tooling_revision'] == recovery['sha'] and not git(ROOT, 'status', '--porcelain'), 'tooling checkout differs from recovery context')
    env = {**os.environ, **{'GITHUB_' + k: descriptor[v] for k, v in
            [('REPOSITORY', 'repository'), ('RUN_ID', 'run_id'), ('RUN_ATTEMPT', 'run_attempt'), ('SHA', 'revision')]}}
    env.pop('GITHUB_TOKEN', None)
    return subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '_aggregate', '--source', str(source)],
                          input=json.dumps(dict(source=descriptor, recovery=recovery)), text=True, cwd=source, env=env).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'recover', '_aggregate'))
    parser.add_argument('--source-run'); parser.add_argument('--source-attempt'); parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    try:
        if args.action == '_aggregate':
            data = json.loads(sys.stdin.read(16384)); result = aggregate_original(args.source, data['source'], data['recovery'])
        else:
            descriptor = inspect_run(os.environ.get('GITHUB_REPOSITORY', ''), args.source_run, args.source_attempt)
            if args.action == 'recover': return recover(args.source, descriptor)
            output = os.environ.get('GITHUB_OUTPUT')
            if output:
                with Path(output).open('a') as stream: stream.write('source_sha=' + descriptor['revision'] + '\n')
            result = dict(status='VERIFIED', kind='source-descriptor-only', source=descriptor)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AssertionError, subprocess.SubprocessError) as error:
        result = dict(status='FAIL', kind='directed-log-transport-recovery', diagnostic=str(error))
    print(MARKER + json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('PASS', 'VERIFIED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
