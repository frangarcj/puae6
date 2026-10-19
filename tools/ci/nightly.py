#!/usr/bin/env python3
"""Accumulated CI lanes. Missing assets, opt-ins, preparations or suites never pass."""
import argparse
import json
import os
import re
import shlex
from pathlib import Path
import sys
import tempfile

sys.path[:0] = [str(Path(__file__).resolve().parents[2])]
from tools.ci import run as ci

LANES = ("clean", "functional", "matrix", "sanitizers", "optimization", "performance")
ENV = dict(ci.ENV, P6_TEST_PORTABILITY="1", P6_TEST_SIMD_MATRIX="1", P6_TEST_ENGINE_FLAGS="1", P6_TEST_RGB565_FRONTEND="1",
           P6_TEST_COMPILERS="1", P6_TEST_FPU_MATRIX="1", P6_TEST_MUTANTS="1", P6_EMUHAWK_BUILD="1", P6_TEST_REAL_FRONTEND="1", P6_TEST_DET01_UNITS="10000",
           P6_A600_UNITS="1800", P6_A1200_UNITS="2200", P6_A1200_DMS_UNITS="1500", P6_GAME_UNITS="420", P6_HDF_UNITS="700", P6_HDF_UNITS_13="700")
ASSETS = ("P6_KICKSTART_ROM", "P6_KICKSTART_A600_ZIP", "P6_WB205_ZIP", "P6_KICKSTART_A1200_ROM", "P6_AGA_ADF", "P6_AGA_DMS",
          "P6_GAME_ADF", "P6_WHDLOAD_LHA", "P6_FATALMISSION_LHA", "P6_FDI_FILE", "P6_IPF_FILE", "P6_PACKAGE_ZIP")
SIMD = {
    "test_decision": {"functional": ("DecisionTests",), "performance": ("PerformanceTests",)},
    "test_engine_flags": {"optimization": ("ConfigurationTests", "EquivalenceTests", "SafetyTests"), "performance": ("PerformanceTests", "DecisionTests")},
    "test_frontend_rgb565": {"optimization": ("EquivalenceTests", "SafetyTests"), "performance": ("PerformanceTests", "DecisionTests")},
    "test_frontend_statistics": {"functional": (None,)}, "test_rgb565": {"optimization": (None,)},
    "test_matrix": {"optimization": (None,)},
}


def suites(lane):
    if lane not in LANES:
        raise ValueError("unknown CI lane")
    selected = []
    for path in sorted((ci.ROOT / "tests").rglob("test_*.py")):
        relative = path.relative_to(ci.ROOT).with_suffix("")
        module = ".".join(relative.parts)
        group = relative.parts[1] if len(relative.parts) > 2 else "metadata"
        if group == "simd":
            if path.stem not in SIMD:
                raise ValueError("unclassified SIMD tests: " + path.stem)
            selected += [module + ("." + cls if cls else "") for cls in SIMD[path.stem].get(lane, ())]
        elif lane == {"bench": "performance", "portability": "matrix", "fuzz": "sanitizers"}.get(group, "functional"):
            selected.append(module)
    if lane != "clean" and not selected:
        raise ValueError("empty CI lane")
    return selected


def images():
    from tests.simd.matrix import IMAGES
    from tests.portability.rows import ROWS
    found = {}
    for spec in ROWS.values():
        if spec["kind"] != "docker":
            continue
        p = ci.command(["docker", "image", "inspect", spec["image"]], timeout=30)
        if p.returncode:
            raise ValueError("required offline Docker image absent")
        image = json.loads(p.stdout)[0]
        if image["Id"] != IMAGES[spec["platform"]] or "linux/" + image["Architecture"] != spec["platform"]:
            raise ValueError("Docker image differs from the frozen matrix")
        found[spec["image"]] = image["Id"]
    return found


def execute(argv, timeout=14400):
    print(ci.redact("RUN " + shlex.join(list(map(str, argv)))), file=sys.stderr, flush=True)
    p = ci.command(list(map(str, argv)), ENV, timeout=timeout)
    if p.returncode:
        raise RuntimeError(ci.redact(p.stdout[-2000:] + p.stderr[-6000:]))


def identity():
    facts = ci.identity()
    facts["test_environment"] = ENV
    return facts


def validate_result(payload, lane):
    if not isinstance(payload, dict) or payload.get("lane") != lane or payload.get("status") not in ("PASS", "FAIL", "BLOCKED"):
        raise ValueError("invalid CI verdict")
    if payload["status"] != "PASS":
        return
    facts = payload.get("identity") or {}
    if not re.fullmatch("[0-9a-f]{64}", facts.get("source_tree_sha256", "")) or not facts.get("inputs"):
        raise ValueError("CI verdict lacks source/toolchain identity")
    completed = payload.get("completed", [])
    if len(completed) != len(suites(lane)) or {r.get("module") for r in completed} != set(suites(lane)):
        raise ValueError("CI verdict lacks required suites")
    for record in completed:
        process = ci.subprocess.CompletedProcess([], 0, json.dumps(record.get("counts")), "")
        if ci.verdict(process)[0] != "PASS":
            raise ValueError("CI verdict has incomplete assertions")
    if lane == "clean" and set(payload.get("preparation", {})) != {"p6_headless", "puae6_libretro.dylib"}:
        raise ValueError("CI verdict lacks clean build artifacts")


def prepare(lane):
    from tests.simd.engine_flags import VARIANTS
    py = sys.executable
    if lane in ("optimization", "performance"):
        # Generate/train strictly before measuring. No old PASS files authorize reuse.
        for variant in VARIANTS if lane == "optimization" else VARIANTS[:4]:
            if variant == "pgo":
                execute([py, "-m", "tests.simd.engine_flags", "merge"])
            execute([py, "-m", "tests.simd.engine_flags", "build", "--variant", variant])
            execute([py, "-m", "tests.simd.engine_flags", "verify", "--variant", variant])
        execute([py, "-m", "tests.simd.frontend_matrix", "prepare"])
        if lane == "optimization":
            execute([py, "-m", "tests.simd.frontend_matrix", "prepare", "--sanitizers"])
        else:
            execute([py, "-m", "tests.simd.engine_flags", "measure"])
            execute([py, "-m", "tests.simd.frontend_matrix", "measure"])
    if lane == "clean":
        execute([py, "tools/generate_cpu.py", "--check"])
        root = ci.ROOT / "build"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ci-clean-", dir=root) as tree:
            execute(["cmake", "-S", ci.ROOT, "-B", tree, "-G", "Ninja", "-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/macos-appleclang.cmake",
                     "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -g", "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2 -g"])
            execute(["cmake", "--build", tree, "--parallel", "3", "--target", "p6_headless", "puae6_libretro"])
            return {name: ci.sha(Path(tree) / name) for name in ("p6_headless", "puae6_libretro.dylib")}
    return {}


def run(lane):
    completed, facts = [], None
    try:
        selected = suites(lane)
        ci.preflight(ASSETS if lane == "functional" else ())
        docker = images() if lane != "clean" else {}
        execute([sys.executable, "tools/generate_cpu.py"])
        facts = identity()
        prepared = prepare(lane)
        for module in selected:
            print("RUN " + module, file=sys.stderr, flush=True)
            p = ci.command([sys.executable, "-c", ci.check.DRIVER, module], ENV, timeout=6 * 3600)
            status, counts = ci.verdict(p)
            if status != "PASS":
                return dict(status=status, lane=lane, module=module, reason=counts, diagnostic=ci.redact(p.stderr[-6000:]), completed=completed, identity=facts)
            completed.append(dict(module=module, counts=counts))
            if lane != "clean" and images() != docker:
                raise RuntimeError("Docker inputs changed during CI")
        if identity() != facts:
            raise RuntimeError("source/codegen/toolchain inputs changed during CI")
        return dict(status="PASS", lane=lane, completed=completed, preparation=prepared, identity=facts, images=docker,
                    budget_policy="advisory, unchanged plan/performance-budgets.json; not remote CI evidence")
    except (OSError, ValueError, RuntimeError, ci.subprocess.SubprocessError) as error:
        return dict(status="BLOCKED" if isinstance(error, ValueError) else "FAIL", lane=lane, diagnostic=ci.redact(str(error)), completed=completed, identity=facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    result = dict(status="NOT_RUN", lane=args.lane, suites=suites(args.lane)) if args.list else run(args.lane)
    print(ci.redact(json.dumps(result, ensure_ascii=False, sort_keys=True)))
    return 0 if result["status"] in ("PASS", "NOT_RUN") else 2 if result["status"] == "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
