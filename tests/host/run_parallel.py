#!/usr/bin/env python3
"""Run host or native-state tests as one process per module (state: per test class), in parallel.

The serial `unittest discover` spends its time compiling small probes under
sanitizers, and those modules do not share state (each uses its own temporary
directory). Generated sources are prepared once here; workers skip the repeat
through P6_TESTS_GENERATED=1.

    python3 tests/host/run_parallel.py            # every tests/host/test_*.py
    python3 tests/host/run_parallel.py -j 4 test_disk_source test_tchar_numeric
    python3 tests/host/run_parallel.py --suite state -j 3 test_custom_programmed_access test_custom_palette_access test_probe
    python3 tests/host/run_parallel.py --suite state --tier iter     # the iteration tier of the state suite (docs/testing.md)

The state suite is split below the module (T48e): a module with several test classes is run as one `unittest module.Class` process per class, so
that its slowest class, not its sum, bounds the wall time; the summary still adds the classes up per module and checks that the classes ran as many
tests as the module has. The probe the state modules drive is the -O2 build/state-opt one unless P6_STATE_PROBE_BUILD=debug.

Exit status is non-zero if any module fails. The serial command remains valid:
    python3 -m unittest discover -s tests/host -t .
"""
from __future__ import annotations

import argparse
import json
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
from tests.build_support import RECEIPT_ENV, STATE_TREES, build_lock, build_targets, state_probe_tree, write_receipt

SHARED_TARGETS = {
    "test_bridge_lifecycle": "p6_lifecycle_probe",
    "test_bridge_memory": "p6_memory_probe",
    "test_a500_profile": "p6_profile_probe",
    "test_a600_profile": "p6_profile_probe",
    "test_a1200_profile": "p6_profile_probe",
    "test_machine_profiles": "p6_profile_probe",
    "test_cpu_010": "p6_lifecycle_probe",
    "test_cpu_020": "p6_lifecycle_probe",
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


def shared_targets(modules: list[str]) -> list[str]:
    """The host modules' targets (the state suite's go through preparations: they depend on the tree)."""
    return [target for name, target in SHARED_TARGETS.items() if name in modules]


# Named selections of the state suite. "iter" is what each iteration of T15d-T15g is validated with; the full suite is the gate that closes a task.
TIERS = {"iter": ("test_state_container", "test_state_upstream", "test_det02", "test_det02_kickstart", "test_state_bridge")}
# Modules that name their own tree: test_probe builds p6_state_probe in build/link-audit itself, and the envelope probes live there too.
STATE_OWN_TREE = {"test_probe": "p6_state_probe", "test_envelope_native": None}
# Modules that need both builds of p6_state_probe.
STATE_BOTH_TREES = {"test_build_parity"}
HISTORY = "build/.test-durations.json"
STATE_WORKER_BYTES = 1 << 30


def preparations(suite: str, modules: list[str]) -> list[tuple[Path, list[str], bool]]:
    """(build directory, targets, optimized) to prepare before the workers start: the modules of the state suite that drive the harness share the tree P6_STATE_PROBE_BUILD
    selects, the ones that build their own probe keep build/link-audit, and the parity test needs both trees."""
    if suite == "host":
        return [(ROOT / "build/link-audit", targets, False)] if (targets := shared_targets(modules)) else []
    wanted: dict[Path, tuple[list[str], bool]] = {}

    def add(directory, optimized, targets):
        names, _ = wanted.setdefault(directory, ([], optimized))
        names.extend(t for t in targets if t not in names)

    harness = [m for m in modules if m not in STATE_OWN_TREE and m not in {"test_components", "test_envelope"}]
    if any(m not in STATE_BOTH_TREES for m in harness):
        add(*state_probe_tree(), ["p6_state_probe"])
    if STATE_BOTH_TREES & set(harness):
        for variant in STATE_TREES:
            add(*state_probe_tree(variant), ["p6_state_probe"])
    if any(m in STATE_OWN_TREE and STATE_OWN_TREE[m] for m in modules):
        add(ROOT / "build/link-audit", False, ["p6_state_probe"])
    if "test_envelope_native" in modules:
        add(ROOT / "build/link-audit", False, ["p6_envelope_probe", "p6_archive_probe"])
    return [(directory, targets, optimized) for directory, (targets, optimized) in wanted.items()]


def default_jobs(suite: str, cpus: int | None = None, memory: int | None = None) -> int:
    """Host: CPUs minus two (compilers). State: CPUs minus one, and no more workers than STATE_WORKER_BYTES fit in memory (unknown memory does not limit)."""
    cpus = cpus or os.cpu_count() or 2
    if suite == "host":
        return max(1, cpus - 2)
    if memory is None:
        try:
            memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, ValueError, OSError):
            memory = None
    return max(1, min(cpus - 1, memory // STATE_WORKER_BYTES) if memory else cpus - 1)


DISCOVERY = """
import importlib, json, sys, unittest
def flat(suite):
    for test in suite:
        yield from flat(test) if isinstance(test, unittest.TestSuite) else [test]
out = {}
for name in sys.argv[1:]:
    try:
        module = importlib.import_module(name)
        counts = {}
        for test in flat(unittest.defaultTestLoader.loadTestsFromModule(module)):
            cls = type(test)
            if getattr(module, cls.__name__, None) is not cls:
                raise LookupError(cls.__name__)             # not reachable as module.Class: run the module whole
            counts[cls.__name__] = counts.get(cls.__name__, 0) + 1
        out[name] = counts
    except Exception:
        pass
print(json.dumps(out))
"""


def discover_classes(modules: list[str]) -> dict[str, dict[str, int]]:
    """module name -> {class name: tests}, from unittest's own loader in a child process. A module that does not load, or has a test that is not
    a class of the module, is left out and runs whole (a broken import then fails where it always did)."""
    try:
        result = subprocess.run([sys.executable, "-c", DISCOVERY, *modules], cwd=ROOT, capture_output=True, text=True, timeout=300)
        return {name.rsplit(".", 1)[-1]: counts for name, counts in json.loads(result.stdout).items()}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def split_jobs(modules: list[str], classes: dict[str, dict[str, int]]) -> list[tuple[str, str, int | None]]:
    """(module, unit to run, tests expected or None) where the unit is the module, or module.Class when it has more than one class."""
    jobs = []
    for module in modules:
        counts = classes.get(module) or {}
        if len(counts) > 1:
            jobs += [(module, f"{module}.{name}", number) for name, number in counts.items()]
        else:
            jobs.append((module, module, sum(counts.values()) or None))
    return jobs


def load_history(root: Path) -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads((root / HISTORY).read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


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
    parser.add_argument("--tier", choices=sorted(TIERS), help="a named selection of the state suite (modules given as well are added to it)")
    parser.add_argument("-j", "--jobs", type=int, help="workers (state: CPUs minus 1 within memory, a GiB each; host: CPU count minus 2)")
    parser.add_argument("--timeout", type=int, help="seconds per module or class (state: 7200; host: 900)")
    args = parser.parse_args()
    if args.tier and args.suite != "state":
        parser.error("--tier selects modules of the state suite")
    if args.jobs is None:
        args.jobs = default_jobs(args.suite)
    if args.timeout is None:
        args.timeout = 7200 if args.suite == "state" else 900
    if args.jobs < 1:
        parser.error("jobs must be at least 1")
    if args.timeout < 1:
        parser.error("timeout must be at least 1")

    try:
        modules = select_modules(args.suite, [*(TIERS[args.tier] if args.tier else ()), *args.modules])
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
    sanitizer_targets = [target for name, target in SHARED_TARGETS.items()
                         if args.suite == "host" and name in modules and name != "test_a500_profile"]
    # The state modules with several test classes are split by class; a new target for a state module has to be added to preparations().
    classes = discover_classes([f"tests.state.{name}" for name in modules]) if args.suite == "state" else {}
    try:
        for directory, targets, optimized in preparations(args.suite, modules):
            build_targets(directory, targets, optimized=optimized)
        if os.environ.get("P6_TEST_SANITIZERS") == "1" and sanitizer_targets:
            build_targets(ROOT / "build/native-debug", sanitizer_targets, sanitizers=True)
    except (RuntimeError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    prepared_at = time.monotonic() - started

    # Longest first: what took longest last time (build/.test-durations.json), else the most tests, is started first so that it does not end up last.
    history = load_history(ROOT)
    jobs = sorted(split_jobs(modules, classes), key=lambda job: (-history.get(job[1], 0), -(job[2] or 0)))
    prefix = "tests.state." if args.suite == "state" else ""
    results = []                 # (module, unit, code, seconds, output, tests, skipped, expected failures, tests expected)
    with tempfile.TemporaryDirectory(prefix="test-build-receipt-", dir=ROOT / "build") as tmp:
        receipt = str(Path(tmp) / "prepared.json")
        write_receipt(receipt)
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_module, prefix + unit, args.timeout, receipt): (module, unit, expected)
                       for module, unit, expected in jobs}
            for future in as_completed(futures):
                module, unit, expected = futures[future]
                _, code, seconds, output = future.result()
                ran = re.search(r"^Ran (\d+) tests?", output, re.M)
                count = int(ran.group(1)) if ran else 0
                skipped = re.search(r"skipped=(\d+)", output)
                skipped = int(skipped.group(1)) if skipped else 0
                pending = re.search(r"expected failures=(\d+)", output)
                pending = int(pending.group(1)) if pending else 0
                if not count:
                    code = code or 1
                    output += "\nNo tests executed; an empty module is not a passed check.\n"
                elif expected is not None and count != expected:
                    code = code or 1
                    output += f"\nRan {count} tests where the loader found {expected}: the split changed what runs.\n"
                print(f"{'ok  ' if code == 0 else 'FAIL'} {seconds:6.1f}s  {unit}"
                      f"  ({count} tests, {skipped} skipped, {pending} expected failures)", flush=True)
                results.append((module, unit, code, seconds, output, count, skipped, pending))

    # The report adds the classes up per module, as it did when a module was a worker.
    by_module: dict[str, list] = {}
    for item in results:
        by_module.setdefault(item[0], []).append(item)
    failed = [item for item in results if item[2] != 0]
    total = sum(item[5] for item in results)
    skipped = sum(item[6] for item in results)
    pending = sum(item[7] for item in results)
    timeouts = sum(1 for item in results if item[2] == 124)
    wall = time.monotonic() - started
    busy = sum(item[3] for item in results)
    summary = sorted(((module, sum(i[3] for i in items), sum(i[5] for i in items), sum(i[6] for i in items), sum(i[7] for i in items),
                       sum(1 for i in items if i[2]), len(items)) for module, items in by_module.items()), key=lambda row: -row[1])
    if args.suite == "state":
        print("\nper module (the sum of its classes):")
        for module, seconds, count, skip, expect, bad, parts in summary:
            print(f"  {'ok  ' if not bad else 'FAIL'} {seconds:6.1f}s  {module}  ({count} tests, {skip} skipped, {expect} expected failures"
                  f"{f', {parts} classes' if parts > 1 else ''})")
    print(f"\n{len(by_module)} modules, {total} tests, {skipped} skipped, {pending} expected failures, {len(failed)} failed"
          f"{f' ({timeouts} timeouts)' if timeouts else ''}; wall {wall:.0f}s "
          f"(preparation {prepared_at:.0f}s, sum of modules {busy:.0f}s, {args.jobs} jobs)")
    longest = sorted(results, key=lambda item: -item[3])[:5]
    print("slowest: " + ", ".join(f"{item[1]} {item[3]:.0f}s" for item in longest))
    try:
        # Only a unit that passed says how long it takes; a timeout or a crash must not reorder the next run.
        previous = {**history, **{item[1]: round(item[3], 1) for item in results if item[2] == 0}}
        (ROOT / HISTORY).write_text(json.dumps(previous, indent=0, sort_keys=True) + "\n")
    except OSError:
        pass
    for _, unit, _, _, output, _, _, _ in failed:
        print(f"\n===== {unit} =====\n{output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
