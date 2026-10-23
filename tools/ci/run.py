#!/usr/bin/env python3
"""Local PR gate. JSON goes to stdout; no reports or private assets are saved."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check
import build_waterbox as wb

GATES = ("PLAN01", "BUILD01", "DET02", "WB03", "WB13", "WB14", "WB15")
COUNTS = {"tests", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"}
ENV = {"P6_RELEASE_GATE": "1", "P6_TEST_SANITIZERS": "1", "P6_TEST_WATERBOX": "1", "P6_TEST_WB_UNITS": "10000",
       "P6_WATERBOX_RUNTIME": "release", "P6_EVICT": "0", "P6_STATE_PROBE_BUILD": "optimized"}


def recipe():
    """A required gate with no implementation fails before any work, never disappears."""
    steps = {}
    for gate in GATES:
        suites = check.TASK_SUITES.get((gate, "T39a"))
        if not suites:
            raise ValueError(f"missing required gate: {gate}/T39a")
        for module in suites.values():
            steps.setdefault(module, []).append(gate)
    return steps


def redact(text):
    replacements = [(str(ROOT), "@repo"), (tempfile.gettempdir(), "@tmp"), (str(Path.home()), "@home")]
    replacements += [(v, "@private-asset") for k, v in os.environ.items() if k.startswith("P6_") and v and Path(v).is_absolute()]
    for value, replacement in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        text = text.replace(value, replacement)
    return text


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    tree = hashlib.sha256()
    # Versioned public sources only. Never scan untracked firmware, HOME or the environment.
    for name in sorted(git("ls-files", "-z").split(b"\0")):
        if not name:
            continue
        path = ROOT / os.fsdecode(name)
        data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        tree.update(name + b"\0" + hashlib.sha256(data).digest())
    files = ("plan/upstream.lock.json", "patches/winuae/series.json", "plan/toolchains.json", "build/generated/codegen-receipt.json")
    result = dict(revision=git("rev-parse", "HEAD").decode().strip(), source_tree_sha256=tree.hexdigest(),
                  dirty=bool(git("status", "--porcelain")), profile="deterministic-a500",
                  runner=dict(os=platform.system(), release=platform.release(), isa=platform.machine(), page_bytes=os.sysconf("SC_PAGE_SIZE")),
                  inputs={p: sha(ROOT / p) for p in files if (ROOT / p).is_file()},
                  test_environment={**ENV, wb.SOURCE_AUDIT_ENV: wb.source_audit_mode(),
                                      wb.TOOLCHAIN_PROFILE_ENV: wb.selected_toolchain()["toolchain_profile"]})
    receipt = ROOT / "build/generated/codegen-receipt.json"
    if receipt.is_file():
        codegen = json.loads(receipt.read_text())
        for entry in codegen["files"]:
            if sha(receipt.parent / entry["path"]) != entry["sha256"]:
                raise ValueError("generated sources differ from their receipt")
        result["compilers"] = {name: {k: v[k] for k in ("sha256", "version")} for name, v in codegen["identity"]["compilers"].items()}
    for name, path, key in (("guest", "build/waterbox/puae6/puae6-receipt.json", "guest"), ("runtime", "build/waterbox/receipt.json", "runtime_dylib")):
        p = ROOT / path
        if p.is_file():
            item = json.loads(p.read_text())[key]
            if sha(item["path"]) != item["sha256"]:
                raise ValueError(f"{name} differs from its receipt")
            result[name + "_sha256"] = item["sha256"]
    return result


def preflight(assets=()):
    if (platform.system(), platform.machine(), os.sysconf("SC_PAGE_SIZE")) != ("Darwin", "arm64", 16384):
        raise ValueError("requires the declared macOS ARM64 runner with 16 KiB pages")
    for tool in ("git", "cmake", "ninja", "clang", "clang++", "dotnet"):
        if not shutil.which(tool):
            raise ValueError(f"missing tool: {tool}")
    for name in assets:
        if not os.environ.get(name) or not Path(os.environ[name]).is_file():
            raise ValueError(f"missing required asset: {name}")
    for receipt in ("puae6/puae6-receipt.json", "receipt.json"):
        if not (ROOT / "build/waterbox" / receipt).is_file():
            raise ValueError("missing actual Waterbox guest/runtime: prepare tools/build_waterbox.py --puae6")


def verdict(process):
    """A successful exit without complete, non-empty unittest counts proves nothing."""
    try:
        counts = json.loads(process.stdout.strip().splitlines()[-1])
        if not isinstance(counts, dict) or set(counts) != COUNTS or any(type(n) is not int or n < 0 for n in counts.values()):
            raise ValueError()
    except (ValueError, IndexError):
        return "FAIL", "missing or invalid test counts"
    if type(process.returncode) is not int or process.returncode != 0 or counts["failures"] or counts["errors"] or counts["unexpected_successes"] or not counts["tests"]:
        return "FAIL", "failed or empty test suite"
    if counts["skipped"] or counts["expected_failures"]:
        return "BLOCKED", "omitted or pending assertions"
    return "PASS", counts


def command(argv, env=None, timeout=3600):
    process = subprocess.Popen(argv, cwd=ROOT, env={**os.environ, **(env or {})}, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        stderr += "\nCI timeout: terminated the entire worker process group"
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run(source_audit=None):
    try:
        with wb.source_audit_scope(source_audit):
            return _run()
    except wb.BuildError as error:
        return dict(status="BLOCKED", step="source_audit", diagnostic=redact(str(error)), identity=None, completed=[])


def _run():
    completed = []
    facts = None
    current = "preflight"
    try:
        steps = recipe()
        preflight()
        # Pin the opt-ins and boundary count; inherited quick-test overrides cannot shrink CI.
        current = "codegen"
        generated = command([sys.executable, "tools/generate_cpu.py"], ENV, timeout=900)
        if generated.returncode:
            raise RuntimeError(redact(generated.stdout + generated.stderr))
        facts = identity()
        for module, gates in steps.items():
            current = module
            print("RUN " + module, file=sys.stderr, flush=True)
            process = command([sys.executable, "-c", check.DRIVER, module], ENV)
            status, detail = verdict(process)
            if status != "PASS":
                return dict(status=status, step=module, diagnostic=redact(process.stderr[-6000:]), reason=detail, identity=facts, completed=completed)
            completed.append(dict(module=module, gates=gates, counts=detail))
        after = identity()
        if after != facts:
            raise RuntimeError("inputs changed during CI execution")
        binaries = ("build/link-audit/p6_lifecycle_probe", "build/link-audit/p6_headless", "build/state-opt/p6_state_probe",
                    "build/link-audit/CMakeCache.txt", "build/state-opt/CMakeCache.txt")
        return dict(status="PASS", identity=facts, artifacts={p: sha(ROOT / p) for p in binaries}, completed=completed,
                    gates=list(GATES), scope="local public PR gate; not nightly/release or remote CI")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(status="BLOCKED" if isinstance(error, ValueError) else "FAIL", step=current, diagnostic=redact(str(error)), identity=facts, completed=completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # CI pins checkout unless explicitly asked; builders and workers inherit ENV.
    parser.add_argument("--source-audit", choices=("checkout", "snapshot"), default="checkout")
    parser.add_argument("--list", action="store_true", help="list the required suites without claiming execution")
    args = parser.parse_args()
    result = dict(status="NOT_RUN", source_audit=args.source_audit, suites=recipe()) if args.list else run(args.source_audit)
    print(redact(json.dumps(result, ensure_ascii=False, sort_keys=True)))
    return 0 if result["status"] in ("PASS", "NOT_RUN") else 2 if result["status"] == "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
