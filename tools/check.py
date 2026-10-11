#!/usr/bin/env python3
"""Run explicit assertion sets; an unavailable emulator check never passes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scenario_schema import load_json, validate_scenario

ROOT = Path(__file__).resolve().parents[1]
EXIT = {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'NOT_IMPLEMENTED': 3}
# This is T04's complete HARNESS assertion set, not engine or FFI C# evidence.
REQUIRED_ASSERTIONS = ('native_contract', 'scenario_schema', 'fixture_rebuild', 'dispatcher_contract')
SUITES = {
    'native_contract': 'tests.contracts.test_api',
    'scenario_schema': 'tests.scenarios.test_schema',
    'fixture_rebuild': 'tests.fixtures.test_counter',
    'dispatcher_contract': 'tests.contracts.test_dispatcher',
}
# Checks whose assertion sets are real suites for one task (T26). The dispatcher still knows nothing about the
# emulator itself: these suites build and run the sanitizer canaries, the sanitizer builds of the bridge and the
# deterministic fuzz targets. A required assertion that has no suite (the state fuzz target, for T26, which predates the native state) is
# NOT_IMPLEMENTED, and so is any check/task/profile combination that is not listed. T15 closes the gap: FUZZ01 for T15 is the T26 targets plus
# `state`, the seeded mutation of states loaded through the public p6_state_load (truncated, tampered, other build/ROM/config/media), which
# also checks that a refusal leaves the machine bit for bit as it was. T17 adds what the libretro adapter takes from the frontend: `libretro_content`
# (M3U playlists, a VFS that lies and option values, through the adapter's own headers built with the sanitizers) and `normalize_config` (the option
# schema's normalization with random keys, values and types).
TASK_SUITES = {
    ('SAFE01', 'T26'): {
        'canary_self_test': 'tests.canaries.test_canaries',
        'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
        'sanitized_video_guests': 'tests.step.test_step.VideoGuestSanitizerTests',
    },
    # T24 closes the A1200: the T26 suites plus the video guests of the ECS (A600) and AGA (A1200, 68EC020) programs under ASan/UBSan.
    ('SAFE01', 'T24'): {
        'canary_self_test': 'tests.canaries.test_canaries',
        'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
        'sanitized_video_guests': 'tests.step.test_step.VideoGuestSanitizerTests',
        'sanitized_ecs_guests': 'tests.step.test_ecs.EcsGuestSanitizerTests',
        'sanitized_aga_guests': 'tests.step.test_aga.AgaGuestSanitizerTests',
    },
    ('FUZZ01', 'T26'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
    },
    ('FUZZ01', 'T15'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
    },
    ('FUZZ01', 'T17'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
    },
    # T27 adds the write overlay of the floppies: mutated states that carry it and the export / status calls with hostile arguments.
    ('FUZZ01', 'T27'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
    },
    # T29d adds the hard files of the UAE controller: Rigid Disk Blocks with mutated RDSK, PART, FSHD and LSEG blocks, parsed by the filesystem the way the boot ROM asks, then saved and loaded back.
    ('FUZZ01', 'T29d'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T63a adds the hard files of the libretro adapter: `libretro_content` now also decodes hostile .hdf and .hdz (gzip members cut, flipped, bombs, wrong sizes) with the adapter's own header.
    ('FUZZ01', 'T63a'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T25: the portability matrix. DET04 (the declared ISA/compiler rows agree, hardware and A/V apart) and DET06 (another locale, zone, clock, directory
    # and ROM path change nothing) are the one module tests.portability.test_portability. Its coherence tests always run (the report, plan/runners.json,
    # the toolchain files and the recorded evidence say the same thing); the tests that build and run the rows are opt-in (P6_TEST_PORTABILITY=1: the
    # macOS row natively, the Linux rows in Docker, which must be up) and are SKIPPED otherwise, which the dispatcher reports as BLOCKED, never as
    # PASS: a matrix that did not run is not a matrix that agreed. A full run builds five trees and runs ~90 cases per row (x86_64 is translated
    # and slow), hence TASK_SUITE_TIMEOUT.
    ('DET04', 'T25'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'rows_reproduce_hashes': 'tests.portability.test_portability.RowExecutionTests',
    },
    ('DET06', 'T25'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'rows_under_another_host': 'tests.portability.test_portability.RowExecutionTests',
    },
}
TASK_SUITE_TIMEOUT = {('DET04', 'T25'): 6 * 3600, ('DET06', 'T25'): 6 * 3600}
TASK_SUITE_ENV = {'P6_TEST_SANITIZERS': '1'}
DRIVER = '''import json, sys, unittest
suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = dict(tests=result.testsRun, failures=len(result.failures),
              errors=len(result.errors), skipped=len(result.skipped),
              expected_failures=len(result.expectedFailures),
              unexpected_successes=len(result.unexpectedSuccesses))
print(json.dumps(report))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def _run_suite(module, env=None, timeout=120):
    environment = None if env is None else {**os.environ, **env}
    return subprocess.run([sys.executable, '-c', DRIVER, module], cwd=ROOT, env=environment,
                          capture_output=True, text=True, timeout=timeout)


def _dispatch_task_suites(check_id, task, required, finish, executed):
    suites = TASK_SUITES[(check_id, task)]
    wanted = sorted(set(suites) | set(required))
    missing = [name for name in wanted if name not in suites]
    if missing:
        return finish('NOT_IMPLEMENTED', 'Falta cobertura requerida: ' + ', '.join(missing), wanted)
    if not all(shutil.which(tool) for tool in ('clang', 'clang++', 'cmake')):
        return finish('BLOCKED', 'Faltan compiladores HOST clang/clang++/cmake', wanted, 'HARNESS')
    for name in wanted:
        result = _run_suite(suites[name], TASK_SUITE_ENV, TASK_SUITE_TIMEOUT.get((check_id, task), 3600))
        try:
            counts = json.loads(result.stdout.strip().splitlines()[-1])
            if type(counts) is not dict or any(type(n) is not int or n < 0 for n in counts.values()):
                raise ValueError('Contadores de suite invalidos')
        except (ValueError, TypeError, IndexError) as error:
            return finish('FAIL', f'{name}: salida invalida/truncada: {error}\n{result.stderr[-4000:]}', wanted, 'HARNESS')
        if result.returncode or counts['failures'] or counts['errors'] or counts['unexpected_successes']:
            return finish('FAIL', f'{name}: fallo de suite\n{result.stderr[-4000:]}', wanted, 'HARNESS')
        if not counts['tests']:
            return finish('FAIL', f'{name}: suite vacia; no hay prueba', wanted, 'HARNESS')
        if counts['skipped'] or counts['expected_failures']:
            return finish('BLOCKED', f'{name}: assertions omitidas o pendientes', wanted, 'HARNESS')
        executed.append(name)
    return finish('PASS', 'Suites nativas ejecutadas (sanitizers, fuzzing, matriz de portabilidad segun la tarea); no es un resultado de emulacion', wanted, 'HARNESS')


def dispatch(check_id, task='T04', profile='deterministic-a500', scenario=None, required=()):
    assertions = list(REQUIRED_ASSERTIONS) + sorted(set(required) - set(REQUIRED_ASSERTIONS))
    executed = []
    def finish(status, diagnostic, evidence='NONE', set_override=None):
        return dict(schema_version=1, check_id=check_id, task=task, profile=profile,
                    status=status, exit_code=EXIT[status], evidence=evidence,
                    core_build_id=None, scenario_executed=False,
                    assertion_set=(set_override if set_override is not None else
                                   assertions if check_id == 'HARNESS01' else []),
                    assertions_executed=executed, diagnostic=diagnostic)
    try:
        plan = load_json(ROOT / 'plan/tasks.json')
        if check_id not in plan['check_ids']:
            return finish('FAIL', 'Check desconocido: ' + check_id)
        if task not in {t['id'] for t in plan['tasks']}:
            return finish('FAIL', 'Tarea desconocida: ' + task)
        if scenario is not None:
            validate_scenario(load_json(scenario))
            return finish('NOT_IMPLEMENTED', 'Escenario valido, pero no hay runner de emulacion conectado')
        if (check_id, task) in TASK_SUITES and profile == 'deterministic-a500':
            def task_finish(status, diagnostic, wanted, evidence='NONE'):
                return finish(status, diagnostic, evidence, wanted)
            return _dispatch_task_suites(check_id, task, required, task_finish, executed)
        if check_id != 'HARNESS01' or task != 'T04' or profile != 'deterministic-a500':
            return finish('NOT_IMPLEMENTED', 'No hay assertions implementadas para este check/tarea/perfil')
        missing = set(assertions) - set(SUITES)
        if missing:
            return finish('NOT_IMPLEMENTED', 'Falta cobertura requerida: ' + ', '.join(sorted(missing)))
        if not all(shutil.which(tool) for tool in ('clang', 'clang++')):
            return finish('BLOCKED', 'Faltan compiladores HOST clang/clang++ para contrato nativo', 'HARNESS')
        for assertion in assertions:
            result = _run_suite(SUITES[assertion])
            try:
                counts = json.loads(result.stdout)
                expected = {'tests', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'}
                if (type(counts) is not dict or set(counts) != expected
                        or any(type(n) is not int or n < 0 for n in counts.values())):
                    raise ValueError('Contadores de suite invalidos')
            except (ValueError, TypeError) as error:
                return finish('FAIL', f'{assertion}: salida invalida/truncada: {error}\n{result.stderr[-4000:]}', 'HARNESS')
            if result.returncode or counts['failures'] or counts['errors'] or counts['unexpected_successes']:
                return finish('FAIL', f'{assertion}: fallo de suite\n{result.stderr[-4000:]}', 'HARNESS')
            if not counts['tests']:
                return finish('FAIL', f'{assertion}: suite vacia; no hay prueba', 'HARNESS')
            if counts['skipped'] or counts['expected_failures']:
                return finish('BLOCKED', f'{assertion}: assertions omitidas o pendientes', 'HARNESS')
            executed.append(assertion)
        return finish('PASS', 'Contrato nativo, schemas, fixture y dispatcher probados; no emulacion', 'HARNESS')
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired) as error:
        return finish('FAIL', str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', required=True)
    parser.add_argument('--task', default='T04')
    parser.add_argument('--profile', default='deterministic-a500')
    parser.add_argument('--scenario', type=Path)
    parser.add_argument('--require', action='append', default=[], help='Assertion adicional exigida por la puerta')
    args = parser.parse_args()
    result = dispatch(args.check, args.task, args.profile, args.scenario, args.require)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())
