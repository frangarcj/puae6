"""T35c full-frontend rows, with frozen offline containers and isolated codegen."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from tests.portability.rows import ROWS
from tests.simd import matrix
from tests.simd import frontend_rgb565 as frontend

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/simd/frontend"
MEASURED = ("macos-arm64-appleclang", "linux-arm64-clang", "linux-arm64-gcc")
SAN_FLAGS = ('-fsanitize=address,undefined', '-fno-sanitize-recover=all')
SAN_TARGETS = ('p6_engine', 'puae6_libretro', 'puae6_libretro_rgb565_alternate')


def require(condition, message):
    if not condition: raise AssertionError(message)


def case_names():
    return {name + '/' + resolution for name in [*(s.corpus_id for s in frontend.WORKLOADS), *frontend.EXTRA]
            for resolution in ('lores', 'hires', 'superhires')}


def validate_result(row, result, product_sha=None):
    cases, built = result['cases'], result['build']
    require(row in ROWS and len(case_names()) == 27 and set(cases) == case_names(), 'incomplete frontend corpus: ' + row)
    variant = 'candidate' if row in frontend.NEON_PRODUCT_ROWS else 'current'
    require(built['row'] == row and built['product_variant'] == variant and set(built['binaries']) == {'current', 'candidate', 'host'}, 'frontend product inventory')
    require(all(re.fullmatch('[0-9a-f]{64}', h) for h in built['binaries'].values()), 'frontend binary hash')
    if product_sha is not None: require(built['binaries'][variant] == product_sha, 'frontend product bytes changed')
    for case in cases.values():
        runs = case['runs']
        require(len(runs) == 14 and runs[6:10] == runs[10:14] and case['same_process_restore'] is True
                and case['new_process_restore'] is True and re.fullmatch('[0-9a-f]{64}', case['state_sha256']), 'frontend restore/runs incomplete')
        for values in runs:
            require(len(values) == 12 and all(isinstance(v, str) and re.fullmatch('[0-9a-f]+' if i in (4,5,9) else '[0-9]+', v)
                                            for i,v in enumerate(values)), 'malformed frontend run')
    return result


def compare_rows(results):
    require(set(results) == set(ROWS), 'missing/duplicate frontend rows')
    reference = results['macos-arm64-appleclang']['cases']
    for row, result in results.items():
        validate_result(row, result)
        require(all(case['runs'] == reference[name]['runs'] for name, case in result['cases'].items()), 'frontend pixels/PCM/ticks differ across rows: ' + row)


def compare_variants(row, optimized, sanitized):
    validate_result(row, optimized); validate_result(row, sanitized)
    require(all(case['runs'] == optimized['cases'][name]['runs'] for name,case in sanitized['cases'].items()), 'sanitizers change frontend: ' + row)


def sanitizer_commands(commands, sha256):
    counts, seen = dict.fromkeys(SAN_TARGETS, 0), set()
    for item in commands:
        command = item['command']; matches = [t for t in SAN_TARGETS if t + '.dir/' in command]
        if not matches: continue
        require(len(matches) == 1, 'ambiguous sanitizer target')
        target = matches[0]; key = (target, item['file'])
        require(key not in seen and all(flag in shlex.split(command) for flag in SAN_FLAGS), 'missing/duplicate sanitizer instrumentation')
        seen.add(key); counts[target] += 1
    require(all(counts.values()) and re.fullmatch('[0-9a-f]{64}', sha256), 'incomplete sanitizer compile commands')
    return dict(count=sum(counts.values()), sha256=sha256, targets=counts, flags=list(SAN_FLAGS))


def current_inputs(row, work=None):
    tree = (directory(row) if work is None else Path(work)) / 'opt'
    binaries = frontend.core_paths(row, tree)
    (ROOT/'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='frontend-inputs-', dir=ROOT/'build') as fresh:
        frontend.assets.write_differential(fresh)
        return frontend.timing_inputs({**binaries, 'host': tree/'p6_libretro_host'}, fresh)


def validate_measurements(result, verified_inputs, current):
    """Original performance assertions on actual arrays, shared by local/hosted."""
    from tests.simd.frontend_statistics import compare
    require(result['inputs'] == verified_inputs == current, 'frontend timing/verification/current inputs differ')
    require(len(result['runs']) == 50, 'frontend requires fifty measured windows')
    comparisons = {}
    for scenario in frontend.WORKLOADS:
        facts = []
        for variant in ('current', 'candidate'):
            samples = [x for x in result['runs'] if x['scenario'] == scenario.id and x['variant'] == variant]
            require({x['repeat'] for x in samples} == set(range(5)), 'frontend repetitions missing')
            for sample in samples:
                require(sample['warmup'] == 300 and sample['units'] == (10000 if scenario.primary else 3000), 'frontend protocol changed')
                raw = sample['raw']
                require(len(raw['run_cpu_ns']) == 300 + sample['units'] and all(n > 0 for n in raw['run_cpu_ns'])
                        and len(raw['state_load_ns']) == 20, 'frontend timing samples incomplete')
                facts.append(tuple(raw[k] for k in ('state_bytes', 'pictures', 'audio_frames')) +
                             (tuple(sample['last_run']), tuple(sample['final_state'])))
        require(len(set(facts)) == 1, 'frontend timed machines differ: ' + scenario.id)
        comparison = compare([x for x in result['runs'] if x['scenario'] == scenario.id])
        require(len(comparison['paired_reductions_percent']) == 5, 'frontend paired comparisons missing')
        for variant in ('current', 'candidate'):
            require(comparison['variants'][variant]['median_cpu_ns'] == result['summary'][scenario.id][variant]['median_cpu_ns'], 'frontend statistics differ')
        comparisons[scenario.id] = comparison
    return comparisons


def validate_decision(row, inputs, current, adr):
    require(row in MEASURED and '## Decision de frontend T35c' in adr and row in adr, 'frontend platform decision missing')
    require(inputs == current, 'frontend decision uses stale timing inputs')


def directory(row):
    return BUILD / row / ("experiment" if ROWS[row]["kind"] == "docker" else "")


def execute(row, action, sanitizers=False):
    spec = ROWS[row]
    target = BUILD / row
    target.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", "-m", "tests.simd.frontend_rgb565", "--row", row, "--work", str(directory(row)), "--action", action]
    if sanitizers:
        command += ["--sanitizers"]
    if spec["kind"] == "docker":
        image = matrix.IMAGES[spec["platform"]]
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
        if info["Os"] != "linux" or info["Architecture"] != spec["platform"].split("/")[1]:
            raise ValueError("wrong container architecture")
        command[0], command[command.index("--work") + 1] = "python3", "/src/build/experiment"
        command = ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform", spec["platform"], "--user", f"{os.getuid()}:{os.getgid()}",
                   "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{ROOT}:/src:ro", "-v", f"{target}:/src/build", "-w", "/src", image, *command]
    result = subprocess.run(command, cwd=ROOT, timeout=14400)
    if result.returncode:
        raise RuntimeError(f"frontend {row}/{action}: exit {result.returncode}")


def verify(sanitizers=False):
    results = {}
    for row in ROWS:
        execute(row, "verify", sanitizers)
        results[row] = json.loads((directory(row) / ("san" if sanitizers else "opt") / "verification.json").read_text())
        extension = '.dylib' if row.startswith('macos-') else '.so'
        validate_result(row, results[row], frontend.digest(directory(row) / ('san' if sanitizers else 'opt') / ('puae6_libretro' + extension)))
    compare_rows(results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "prepare", "verify", "measure"))
    parser.add_argument("--row", choices=tuple(ROWS), action="append")
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    if args.action == "verify" and not args.row:
        verify(args.sanitizers)
    else:
        for row in args.row or (MEASURED if args.action == "measure" else tuple(ROWS)):
            for action in (("build", "verify") if args.action == "prepare" else (args.action,)):
                execute(row, action, args.sanitizers)


if __name__ == "__main__":
    main()
