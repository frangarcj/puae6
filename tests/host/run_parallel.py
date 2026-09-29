#!/usr/bin/env python3
"""Run the host tests as one process per module, in parallel.

The serial `unittest discover` spends its time compiling small probes under
sanitizers, and those modules do not share state (each uses its own temporary
directory). Generated sources are prepared once here; workers skip the repeat
through P6_TESTS_GENERATED=1.

    python3 tests/host/run_parallel.py            # every tests/host/test_*.py
    python3 tests/host/run_parallel.py -j 4 test_disk_source test_tchar_numeric

Exit status is non-zero if any module fails. The serial command remains valid:
    python3 -m unittest discover -s tests/host -t .
"""
from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def run_module(name: str, timeout: int) -> tuple[str, int, float, str]:
    started = time.monotonic()
    env = {**os.environ, "P6_TESTS_GENERATED": "1"}
    try:
        result = subprocess.run([sys.executable, "-m", "unittest", f"tests.host.{name}"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        return name, result.returncode, time.monotonic() - started, result.stdout + result.stderr
    except subprocess.TimeoutExpired as error:
        return name, 124, time.monotonic() - started, f"timeout after {timeout}s\n{error.stdout or ''}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("modules", nargs="*", help="module names (default: all)")
    parser.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--timeout", type=int, default=900, help="seconds per module")
    args = parser.parse_args()

    available = sorted(path.stem for path in (ROOT / "tests/host").glob("test_*.py"))
    modules = [name.rsplit(".", 1)[-1] for name in args.modules] or available
    unknown = sorted(set(modules) - set(available))
    if unknown:
        print("unknown modules: " + ", ".join(unknown), file=sys.stderr)
        return 2

    started = time.monotonic()
    if os.environ.get("P6_TESTS_GENERATED") != "1":
        prepared = subprocess.run([sys.executable, "tools/generate_cpu.py"], cwd=ROOT,
                                  capture_output=True, text=True)
        if prepared.returncode:
            print(prepared.stdout + prepared.stderr, file=sys.stderr)
            return 1
    prepared_at = time.monotonic() - started

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_module, name, args.timeout) for name in modules]
        for future in as_completed(futures):
            name, code, seconds, output = future.result()
            ran = re.search(r"^Ran (\d+) tests?", output, re.M)
            print(f"{'ok  ' if code == 0 else 'FAIL'} {seconds:6.1f}s  {name}"
                  f"  ({ran.group(1) if ran else '?'} tests)", flush=True)
            results.append((name, code, seconds, output, int(ran.group(1)) if ran else 0))

    failed = [item for item in results if item[1] != 0]
    total = sum(item[4] for item in results)
    wall = time.monotonic() - started
    busy = sum(item[2] for item in results)
    print(f"\n{len(results)} modules, {total} tests, {len(failed)} failed; wall {wall:.0f}s "
          f"(generation {prepared_at:.0f}s, sum of modules {busy:.0f}s, {args.jobs} jobs)")
    slowest = sorted(results, key=lambda item: -item[2])[:5]
    print("slowest: " + ", ".join(f"{name} {seconds:.0f}s" for name, _, seconds, _, _ in slowest))
    for name, _, _, output, _ in failed:
        print(f"\n===== {name} =====\n{output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
