"""Prepare immutable test executables, not emulator instances, once per input/configuration.

The parallel host runner publishes a temporary receipt before launching workers.
Workers validate it and never configure/build a shared tree. Standalone suites
keep working, with process-local reuse and a lock around shared native builds.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
_BUILDS = {}
_THREAD_LOCK = threading.RLock()
RECEIPT_ENV = "P6_TEST_BUILD_RECEIPT"
# The tree the native-state tests run p6_state_probe from (T48e). The probe costs ~165 ms per unit at -O0, so by default it is an -O2 build with the
# assertions on (no NDEBUG) and no sanitizers; P6_STATE_PROBE_BUILD=debug goes back to the -O0 tree that the other native tests share. The two are
# kept equal byte for byte by tests/state/test_build_parity.py, which is what makes the faster one a valid substitute.
STATE_PROBE_ENV = "P6_STATE_PROBE_BUILD"
STATE_TREES = {"optimized": "build/state-opt", "debug": "build/link-audit"}
OPTIMIZED_FLAGS = "-O2 -g"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def output_path(directory, target):
    # The shared core's CMake target is not its filename (Libretro.cmake sets PREFIX="").
    suffix = ".exe" if os.name == "nt" else ""
    if target == "puae6_libretro":
        suffix = ".dll" if os.name == "nt" else ".dylib" if sys.platform == "darwin" else ".so"
    return directory / (target + suffix)


def source_identity():
    """Content identity, including uncommitted code; never an mtime-only shortcut."""
    paths = {ROOT / name for name in ("CMakeLists.txt", "CMakePresets.json", "plan/upstream.lock.json", "tests/build_support.py")}
    for name in ("adapters", "bridge", "cmake", "config", "manifests", "patches", "ports", "tools", "upstream/winuae", "references/libretro-api"):
        paths.update(p for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    paths.update(p for p in (ROOT / "tests").rglob("*") if p.suffix in (".c", ".cpp", ".h", ".hpp", ".s") and p.is_file())
    h = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            h.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
            h.update(bytes.fromhex(digest(path)))
    h.update(json.dumps({k: os.environ.get(k) for k in (
        "PATH", "SDKROOT", "DEVELOPER_DIR", "MACOSX_DEPLOYMENT_TARGET", "CPATH",
        "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH", "SOURCE_DATE_EPOCH")}, sort_keys=True).encode())
    return h.hexdigest()


@contextmanager
def build_lock():
    # All native build directories share build/upstream and build/generated.
    with _THREAD_LOCK:
        path = ROOT / "build/.test-build.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("test build lock must not be a symlink")
        with path.open("a+b") as lock:
            if os.name == "nt":
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b"0"); lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)


def _matches(record, directory, config, source):
    try:
        return (record["config"] == config and record["source"] == source
                and record["cache"] == digest(directory / "CMakeCache.txt")
                and record["generated"] == digest(ROOT / "build/generated/codegen-receipt.json"))
    except (KeyError, TypeError, OSError):
        return False


def _command(args, timeout):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Build command failed ({result.returncode}): {args}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")


def state_probe_variant():
    """"optimized" (default) or "debug": which tree the native-state tests take p6_state_probe from."""
    variant = os.environ.get(STATE_PROBE_ENV, "optimized")
    if variant not in STATE_TREES:
        raise ValueError(f"{STATE_PROBE_ENV} must be one of {', '.join(STATE_TREES)}, not {variant!r}")
    return variant


def state_probe_tree(variant=None):
    """(directory, optimized) of a variant of the state-probe tree; the default variant is the one the environment selects."""
    variant = variant or state_probe_variant()
    return ROOT / STATE_TREES[variant], variant == "optimized"


def build_targets(directory, targets, *, sanitizers=False, trace=False, optimized=False):
    directory = Path(directory).resolve()
    directory.relative_to((ROOT / "build").resolve())
    targets = tuple(dict.fromkeys(targets))
    if not targets or any(not name.replace("_", "").isalnum() for name in targets):
        raise ValueError("explicit CMake target names are required")
    # The recipe is part of the receipt: an -O0 build never stands for an -O2 one, whatever directory it was configured in.
    config = dict(sanitizers=bool(sanitizers), trace=bool(trace),
                  build_type="RelWithDebInfo" if optimized else "Debug", flags=OPTIMIZED_FLAGS if optimized else "")
    with build_lock():
        source = source_identity()
        receipt = os.environ.get(RECEIPT_ENV)
        if receipt:
            try:
                data = json.loads(Path(receipt).read_text())
                record = data["builds"][str(directory)]
                valid = data["root"] == str(ROOT.resolve()) and _matches(record, directory, config, source)
                valid = valid and all(record["executables"][t] == digest(output_path(directory, t)) for t in targets)
            except (OSError, ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                raise RuntimeError("Prepared test build receipt is stale, incomplete or has altered executables; rerun the parent runner")
            return {t: output_path(directory, t) for t in targets}

        record = _BUILDS.get(str(directory))
        if not _matches(record, directory, config, source):
            record = None
        if record is not None:
            for target in targets:
                artifact = output_path(directory, target)
                if target in record["executables"] and artifact.is_file() and record["executables"][target] != digest(artifact):
                    raise RuntimeError(f"Prepared executable was altered: {artifact}")
        missing = [t for t in targets if record is None or t not in record["executables"] or not output_path(directory, t).is_file()]
        if not missing:
            return {t: output_path(directory, t) for t in targets}
        # Invalidate before a potentially failing command: failure cannot leave a hit.
        _BUILDS.pop(str(directory), None)
        if record is None:
            _command(["cmake", "-S", str(ROOT), "-B", str(directory), "-G", "Ninja",
                      "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++", f"-DCMAKE_BUILD_TYPE={config['build_type']}",
                      # RelWithDebInfo's default is "-O2 -g -DNDEBUG": the flags are replaced, not extended, so that the asserts stay.
                      *([f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={OPTIMIZED_FLAGS}", f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={OPTIMIZED_FLAGS}"] if optimized else []),
                      f"-DP6_SANITIZERS={'ON' if sanitizers else 'OFF'}", f"-DP6_UNIT_TRACE={'ON' if trace else 'OFF'}"], 120)
        _command(["cmake", "--build", str(directory), "--target", *missing], 1800)
        if source_identity() != source:
            raise RuntimeError("Source inputs changed during the test build; stop concurrent edits")
        executables = {} if record is None else dict(record["executables"])
        executables.update({t: digest(output_path(directory, t)) for t in missing})
        _BUILDS[str(directory)] = dict(config=config, source=source, executables=executables,
                                       cache=digest(directory / "CMakeCache.txt"),
                                       generated=digest(ROOT / "build/generated/codegen-receipt.json"))
        return {t: output_path(directory, t) for t in targets}


def write_receipt(path):
    """Call in a parent-owned temporary directory; remove it after all workers exit."""
    Path(path).write_text(json.dumps(dict(root=str(ROOT.resolve()), builds=_BUILDS), sort_keys=True) + "\n")
