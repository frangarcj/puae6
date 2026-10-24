"""Manual constructor-only drift diagnosis; never a performance gate result."""
import json
import re
import sys

from tools.ci import performance_bench as bench

MARKER = 'P6_PERFORMANCE_INPUTS_DIAGNOSTIC '
SCOPE = 'constructor-only; no CPU campaign, original selector or performance-gate verdict'
CONSTRUCTORS = (
    ('ffi-release', 'from tools import bench; bench.build_ffi_release()'),
    ('state-opt', "from tools.ci import performance_bench as p; p.build_support.build_targets(p.ROOT/'build/state-opt', p.TARGETS, optimized=True)"),
    ('smoke', 'from tools import build_waterbox as wb; wb.build_managed()'),
    ('runtime-debug', "from tools import build_waterbox as wb; wb.ensure_runtime_lane('debug')"),
)
NAMES = ('ffi-release', 'state-opt', 'smoke', 'runtime-debug')


class DiagnosticDrift(RuntimeError):
    def __init__(self, label, before, after, diagnostic):
        super().__init__(label+': '+diagnostic)
        # These are hash inventories/identities, not file contents. The CLI
        # redacts paths before emitting even a failed observation.
        self.observation = dict(constructor=label, before=before, after=after)


def identity():
    return dict(common=bench.hosted.common(), tools=bench.tools(),
                source_environment=bench.build_support.source_identity(), generated=bench.hosted.generated())


def snapshot():
    return dict(**identity(), inputs=bench.inputs())


def stable(label, before, after):
    try:
        bench.require_unchanged(before, after)
    except RuntimeError as error:
        raise DiagnosticDrift(label, before, after, str(error)) from error


def validate(record, expected):
    """Validate current observations only; no compiler, measurements or writes."""
    bench.require(record['status'] == 'DIAGNOSTIC_STABLE' and record['scope'] == SCOPE
                  and record['coverage'] == dict(constructors=4, campaign_windows=0, original_methods=0),
                  'not a complete constructor-only diagnostic')
    common = record['baseline']['common']
    bench.require(common == expected and re.fullmatch('[0-9a-f]{40}', common['revision'])
                  and re.fullmatch('[0-9a-f]{64}', common['source_tree_sha256'])
                  and all(re.fullmatch('[1-9][0-9]*', common[k]) for k in ('run_id', 'run_attempt')),
                  'foreign diagnostic source/run/attempt')
    bench.require(set(record['baseline']) == {'common','tools','source_environment','generated','inputs'}
                  and re.fullmatch('[0-9a-f]{64}', record['baseline']['source_environment'])
                  and all(isinstance(record['baseline'][k],dict) and record['baseline'][k]
                          for k in ('tools','generated','inputs')), 'incomplete diagnostic identity')
    bench.require(tuple(name for name,_ in CONSTRUCTORS) == NAMES
                  and [step['constructor'] for step in record['completed']] == list(NAMES),
                  'missing/duplicate diagnostic constructor')
    preparation = record['preparation']
    stable('preparation', preparation['before'], preparation['after'])
    stable('preparation boundary', preparation['after'], {k:v for k,v in record['baseline'].items() if k != 'inputs'})
    for step, (name, command) in zip(record['completed'], CONSTRUCTORS):
        bench.require(step['argv'][1:] == ['-B','-c',command] and step['argv'][0], 'foreign constructor command')
        stable(name+' boundary', record['baseline'], step['before'])
        stable(name, step['before'], step['after'])
    return record


def run():
    bench.require(tuple(name for name,_ in CONSTRUCTORS) == NAMES, 'diagnostic constructor coverage changed')
    bench.require(bench.os.environ.get('P6_TEST_BUILD_RECEIPT') is None, 'foreign native build receipt')
    before = identity()
    env = dict(bench.nightly.ENV, P6_RUN_CPU_PERF='0')
    # Reuse precisely the real preparation, without entering campaign()/selectors.
    bench.prepare(env)
    baseline = snapshot(); prepared = {k:v for k,v in baseline.items() if k != 'inputs'}
    stable('preparation', before, prepared)
    completed = []
    for name, command in CONSTRUCTORS:
        current = snapshot(); stable(name+' boundary', baseline, current)
        argv = [sys.executable,'-B','-c',command]
        print('DIAGNOSE constructor '+name, file=sys.stderr, flush=True)
        try:
            bench.execute(argv, env)
        except (OSError, ValueError, RuntimeError, bench.hosted.ci.subprocess.SubprocessError) as error:
            raise RuntimeError(name+': '+str(error)) from error
        after = snapshot(); stable(name, current, after)
        completed.append(dict(constructor=name, argv=argv, before=current, after=after))
    result = dict(status='DIAGNOSTIC_STABLE', scope=SCOPE, baseline=baseline,
                  preparation=dict(before=before, after=prepared), completed=completed,
                  coverage=dict(constructors=4, campaign_windows=0, original_methods=0))
    return validate(result, before['common'])


if __name__ == '__main__':
    try:
        result = run()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError) as error:
        result = dict(status='FAIL', scope=SCOPE, diagnostic=str(error))
        if isinstance(error, DiagnosticDrift): result['observation'] = error.observation
    print(MARKER+bench.hosted.ci.redact(json.dumps(result, sort_keys=True, separators=(',',':'))))
    raise SystemExit(0 if result['status'] == 'DIAGNOSTIC_STABLE' else 1)
