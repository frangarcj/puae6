"""T35c full-frontend rows, with frozen offline containers and isolated codegen."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

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
