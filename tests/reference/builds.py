"""The two builds REF01 compares: the product's p6_state_probe (build/state-opt, shared with tests/state) and the same probe built against the
reference tree (build/reference-opt, tools/build_reference.py). Both are -O2 -g with the assertions on and no sanitizers, so the only difference
between them is the set of engine patches.

`probe(tree, rom, *args)` runs one process and returns its stdout. The environment is the same for both trees and carries nothing of the host
that the machine could read (the engine takes its clock from the emulated cycle counter, its seed from the configuration).
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests.build_support import build_lock, build_targets, state_probe_tree  # noqa: E402
import build_reference  # noqa: E402

REFERENCE_TREE = ROOT / "build/reference-opt"
FLAGS = "-O2 -g"
_BUILT = {}


def _command(args, timeout):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")


def product_probe():
    directory, optimized = state_probe_tree("optimized")
    return build_targets(directory, ["p6_state_probe"], optimized=optimized)["p6_state_probe"]


def reference_probe():
    """Materializes the reference tree if needed and returns the path of the reference build of p6_state_probe."""
    if "reference" in _BUILT:
        return _BUILT["reference"]
    build_reference.materialize_reference(ROOT)
    product_probe()   # the codegen of both trees is the product's, built once and published before the reference configures
    with build_lock():
        if not (REFERENCE_TREE / "CMakeCache.txt").is_file():
            _command(["cmake", "-S", str(ROOT), "-B", str(REFERENCE_TREE), "-G", "Ninja", "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
                      "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={FLAGS}", f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={FLAGS}",
                      "-DP6_SANITIZERS=OFF", "-DP6_UNIT_TRACE=OFF", "-DP6_UPSTREAM_TREE=reference"], 300)
        _command(["cmake", "--build", str(REFERENCE_TREE), "--target", "p6_state_probe"], 3600)
    path = REFERENCE_TREE / "p6_state_probe"
    _BUILT["reference"] = path
    return path


def tree_probe(tree):
    if tree == "product":
        return product_probe()
    if tree == "reference":
        return reference_probe()
    raise ValueError(f"unknown tree {tree!r}")


def probe(tree, rom, *args, env=None, timeout=3600):
    """One process of the probe of `tree`; returns (returncode, stdout, stderr)."""
    environment = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", **(env or {})}
    result = subprocess.run([str(tree_probe(tree)), str(rom), *map(str, args)], capture_output=True, text=True, env=environment, timeout=timeout)
    return result.returncode, result.stdout, result.stderr
