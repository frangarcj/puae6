#!/usr/bin/env python3
"""Run host or native-state tests as one process per module, in parallel.

The serial `unittest discover` spends its time compiling small probes under
sanitizers, and those modules do not share state (each uses its own temporary
directory). Generated sources are prepared once here; workers skip the repeat
through P6_TESTS_GENERATED=1.

    python3 tests/host/run_parallel.py            # every tests/host/test_*.py
    python3 tests/host/run_parallel.py -j 4 test_disk_source test_tchar_numeric
    python3 tests/host/run_parallel.py --suite state -j 3 test_custom_programmed_access test_custom_palette_access test_probe

Exit status is non-zero if any module fails. The serial command remains valid:
    python3 -m unittest discover -s tests/host -t .
"""
from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.build_support import RECEIPT_ENV, build_lock, build_targets, write_receipt

SHARED_TARGETS = {
    "test_bridge_lifecycle": "p6_lifecycle_probe",
    "test_bridge_memory": "p6_memory_probe",
    "test_a500_profile": "p6_profile_probe",
}


def select_modules(suite: str, requested: list[str]) -> list[str]:
    available = sorted(path.stem for path in (ROOT / f"tests/{suite}").glob("test_*.py"))
    prefix = f"tests.{suite}."
    names = [name[len(prefix):] if name.startswith(prefix) else name for name in requested] or available
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise ValueError("unknown modules: " + ", ".join(unknown))
    if not names:
        raise ValueError(f"no test modules in tests/{suite}")
    return list(dict.fromkeys(names))


def shared_targets(suite: str, modules: list[str]) -> list[str]:
    if suite == "host":
        return [target for name, target in SHARED_TARGETS.items() if name in modules]
    # Los otros modulos usan ProbeCase o ProbeTests. Un target nuevo exige actualizar esta lista.
    targets = ["p6_state_probe"] if set(modules) - {"test_components", "test_envelope", "test_envelope_native"} else []
    if "test_envelope_native" in modules:
        targets += ["p6_envelope_probe", "p6_archive_probe"]
    return targets


def run_module(name: str, timeout: int, receipt: str) -> tuple[str, int, float, str]:
    started = time.monotonic()
    env = {**os.environ, "P6_TESTS_GENERATED": "1", RECEIPT_ENV: receipt}
    module = name if name.startswith("tests.") else f"tests.host.{name}"
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    process = subprocess.Popen([sys.executable, "-m", "unittest", module], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **group)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        # The unittest process can own native probes. Stop the whole worker tree.
        cleanup_error = ""
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, text=True, check=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = f"worker tree cleanup failed: {error}; children may still be active\n"
                try:
                    process.kill()
                except OSError as kill_error:
                    cleanup_error += f"worker termination failed: {kill_error}\n"
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired as error:
            # A failed tree cleanup can leave a child holding a pipe open.
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            stdout, stderr = decoded(error.stdout), decoded(error.stderr)
            cleanup_error += "worker pipes did not close; children may still be active\n"
            process.stdout.close()
            process.stderr.close()
        stderr += cleanup_error
        stdout = f"timeout after {timeout}s\n" + stdout
        code = 124
    return name, code, time.monotonic() - started, stdout + stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("modules", nargs="*", help="module names (default: all in the selected suite)")
    parser.add_argument("--suite", choices=("host", "state"), default="host")
    parser.add_argument("-j", "--jobs", type=int, help="workers (state: 2; host: CPU count minus 2)")
    parser.add_argument("--timeout", type=int, help="seconds per module (state: 7200; host: 900)")
    args = parser.parse_args()
    if args.jobs is None:
        args.jobs = 2 if args.suite == "state" else max(1, (os.cpu_count() or 2) - 2)
    if args.timeout is None:
        args.timeout = 7200 if args.suite == "state" else 900
    if args.jobs < 1:
        parser.error("jobs must be at least 1")
    if args.timeout < 1:
        parser.error("timeout must be at least 1")

    try:
        modules = select_modules(args.suite, args.modules)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    started = time.monotonic()
    # A parent always verifies preparation; only its workers may reuse it.
    os.environ.pop(RECEIPT_ENV, None)
    with build_lock():
        prepared = subprocess.run([sys.executable, "tools/generate_cpu.py"], cwd=ROOT,
                                  capture_output=True, text=True)
        if prepared.returncode:
            print(prepared.stdout + prepared.stderr, file=sys.stderr)
            return 1
    targets = shared_targets(args.suite, modules)
    sanitizer_targets = [target for name, target in SHARED_TARGETS.items()
                         if args.suite == "host" and name in modules and name != "test_a500_profile"]
    try:
        if targets:
            build_targets(ROOT / "build/link-audit", targets)
        if os.environ.get("P6_TEST_SANITIZERS") == "1" and sanitizer_targets:
            build_targets(ROOT / "build/native-debug", sanitizer_targets, sanitizers=True)
    except (RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    prepared_at = time.monotonic() - started

    results = []
    with tempfile.TemporaryDirectory(prefix="test-build-receipt-", dir=ROOT / "build") as tmp:
        receipt = str(Path(tmp) / "prepared.json")
        write_receipt(receipt)
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_module, f"tests.state.{name}" if args.suite == "state" else name,
                                   args.timeout, receipt) for name in modules]
            for future in as_completed(futures):
                name, code, seconds, output = future.result()
                ran = re.search(r"^Ran (\d+) tests?", output, re.M)
                count = int(ran.group(1)) if ran else 0
                skipped = re.search(r"skipped=(\d+)", output)
                skipped = int(skipped.group(1)) if skipped else 0
                pending = re.search(r"expected failures=(\d+)", output)
                pending = int(pending.group(1)) if pending else 0
                if not count:
                    code = code or 1
                    output += "\nNo tests executed; an empty module is not a passed check.\n"
                print(f"{'ok  ' if code == 0 else 'FAIL'} {seconds:6.1f}s  {name}"
                      f"  ({count} tests, {skipped} skipped, {pending} expected failures)", flush=True)
                results.append((name, code, seconds, output, count, skipped, pending))

    failed = [item for item in results if item[1] != 0]
    total = sum(item[4] for item in results)
    skipped = sum(item[5] for item in results)
    pending = sum(item[6] for item in results)
    wall = time.monotonic() - started
    busy = sum(item[2] for item in results)
    print(f"\n{len(results)} modules, {total} tests, {skipped} skipped, {pending} expected failures, {len(failed)} failed; wall {wall:.0f}s "
          f"(preparation {prepared_at:.0f}s, sum of modules {busy:.0f}s, {args.jobs} jobs)")
    slowest = sorted(results, key=lambda item: -item[2])[:5]
    print("slowest: " + ", ".join(f"{name} {seconds:.0f}s" for name, _, seconds, _, _, _, _ in slowest))
    for name, _, _, output, _, _, _ in failed:
        print(f"\n===== {name} =====\n{output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
