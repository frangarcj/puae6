"""Public corpus orchestration for a prebuilt hosted row; existing T25 oracles."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile

from tests.build_support import digest
from tests.portability import corpus, row_driver, run_matrix
from tools import package
from tools.ci.matrix_checks import check_observations

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def recipe_identity(execute):
    # Include Python generators/fixtures/goldens which source_identity omits.
    revision = package.clean_revision()
    names = execute(['git', 'ls-files', '-z', '--', 'tests', 'tools/ci', '.github/workflows/native-linux.yml']).split('\0')
    value = hashlib.sha256()
    for name in sorted(filter(None, names)):
        value.update(name.encode() + b'\0' + bytes.fromhex(digest(ROOT / name)))
    return dict(revision=revision, recipe_fixtures_sha256=value.hexdigest())


def clocks(base, shifted):
    parsed = []
    for text in (base, shifted):
        fields = text.split()
        require(len(fields) == 4 and all(re.fullmatch(r'(time|gettimeofday|clock_gettime|monotonic)=-?\d+', field) for field in fields), 'incomplete clock check')
        values = {k: int(v) for k, v in (field.split('=') for field in fields)}
        require(set(values) == {'time', 'gettimeofday', 'clock_gettime', 'monotonic'}, 'missing clock')
        parsed.append(values)
    a, b = parsed
    for key in ('time', 'gettimeofday', 'clock_gettime'):
        require(abs(b[key] - a[key] - run_matrix.FAKE_OFFSET) <= 5, 'civil clock was not shifted: ' + key)
    require(0 <= b['monotonic'] - a['monotonic'] <= 5, 'monotonic clock changed')
    return dict(offset=run_matrix.FAKE_OFFSET, civil_clocks=3, monotonic_unchanged=True)


def compare(expected, baseline, host, *, headless):
    try:
        check_observations(expected, baseline, host)
    except AssertionError as error:
        raise RuntimeError(str(error)) from error
    wanted = expected['cases']
    for variant, record in (('baseline', baseline), ('host', host)):
        require(record['variant'] == variant, 'wrong corpus variant')
        for name, case in record['cases'].items():
            require(all(re.fullmatch('[0-9a-f]{64}', case[key]) and re.fullmatch('[0-9a-f]{64}', wanted[name][key]) for key in ('hardware', 'av')), 'malformed corpus hash')
            require(type(case['units']) is int and case['units'] == wanted[name]['units'] > 0 and case['lines'] > 0, 'incomplete corpus units')
            require(len(case['per_unit']) == case['units'] and all(re.fullmatch(r'\d+', unit) and len(pair) == 2 and all(re.fullmatch('[0-9a-f]{16}', h) for h in pair) for unit, pair in case['per_unit'].items()), 'incomplete per-unit hashes')
        wanted_headless = {name for name, _, _ in row_driver.HEADLESS_CASES} if headless else set()
        require(set(record['headless']) == wanted_headless and all(re.fullmatch('[0-9a-f]{64}', value) for value in record['headless'].values()), 'missing/failed headless corpus')
    # Golden fixtures contain full hashes, but no per-unit detail. Preserve the
    # existing comparator and its rejection; only first-unit diagnostics are absent.
    reference = {'cases': {name: dict(case, per_unit={}) for name, case in wanted.items()}}
    for a, b in ((reference, baseline), (baseline, host)):
        result = run_matrix.compare(a, b)
        require(not result['errors'] and not result['differences'] and result['hardware_equal'] == result['av_equal'] == len(wanted), 'corpus mismatch: ' + str(result))
    require(baseline['headless'] == host['headless'], 'headless host environment mismatch')
    return dict(probe_schema=row_driver.PROBE_SCHEMA, cases=len(wanted), variants=['baseline', 'host'], hardware_equal=len(wanted), av_equal=len(wanted), headless=len(baseline['headless']))


def run(tree, cc, mode, execute, *, clock_execute=None):
    with tempfile.TemporaryDirectory(prefix='public-corpus-', dir=tree) as temporary:
        work = Path(temporary)
        library = work / ('faketime.dylib' if sys.platform == 'darwin' else 'faketime.so')
        check = work / 'clock-check'
        source = ROOT / 'tests/portability/faketime.c'
        execute([cc, *(['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']), source, '-o', library])
        flags = shlex.split(mode['flags'] or '-O0 -g')
        if mode['sanitizers']:
            flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        execute([cc, *flags, '-DFAKETIME_CHECK', source, '-o', check])
        clock_execute = clock_execute or execute
        plain = clock_execute([check])
        preload = dict(row_driver.preload_environment(check, library), P6_FAKE_TIME_OFFSET=str(run_matrix.FAKE_OFFSET))
        clock = clocks(plain, clock_execute([check], env=preload))
        results = {}
        for dataset, writer in corpus.CORPORA.items():
            name = dataset or 'corpus'
            expected = json.loads((ROOT / f'tests/portability/{name}.golden.json').read_text())
            directory = work / name
            listing = writer(directory)
            value = hashlib.sha256()
            for path in sorted(directory.iterdir()):
                value.update(path.name.encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
            require(expected['schema_version'] == 1 and len(listing) == expected['corpus']['cases'] == len(expected['cases']) and {c['id'] for c in listing} == set(expected['cases']) and value.hexdigest() == expected['corpus']['sha256'], 'corpus fixture changed: ' + name)
            runs = {}
            for variant in ('baseline', 'host'):
                output = work / f'{name}-{variant}.json'
                argv = [sys.executable, '-I', ROOT / 'tests/portability/row_driver.py', '--probe', tree / 'p6_state_probe', '--corpus', directory, '--out', output, '--variant', variant, '--jobs', '2']
                if not dataset:
                    argv += ['--headless', tree / 'p6_headless']
                if variant == 'host':
                    argv += ['--preload', library, '--fake-offset', str(run_matrix.FAKE_OFFSET)]
                execute(argv, 1800, env={"TMPDIR": str(work)})
                runs[variant] = json.loads(output.read_text())
            results[name] = dict(compare(expected, runs['baseline'], runs['host'], headless=not dataset), corpus_sha256=value.hexdigest())
        return dict(corpora=results, clock=clock, leak_detection='off in existing row_driver; ASan/UBSan retained')
