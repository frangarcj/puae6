#!/usr/bin/env python3
"""Run explicit assertion sets; an unavailable emulator check never passes."""
import argparse
import json
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


def _run_suite(module):
    return subprocess.run([sys.executable, '-c', DRIVER, module], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def dispatch(check_id, task='T04', profile='deterministic-a500', scenario=None, required=()):
    assertions = list(REQUIRED_ASSERTIONS) + sorted(set(required) - set(REQUIRED_ASSERTIONS))
    executed = []
    def finish(status, diagnostic, evidence='NONE'):
        return dict(schema_version=1, check_id=check_id, task=task, profile=profile,
                    status=status, exit_code=EXIT[status], evidence=evidence,
                    core_build_id=None, scenario_executed=False,
                    assertion_set=assertions if check_id == 'HARNESS01' else [],
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
