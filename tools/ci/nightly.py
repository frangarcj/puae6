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
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[2])]
from tools.ci import run as ci
from tools.ci import retroarch_image
from tools import package
from tools import build_waterbox as wb

LANES = ("clean", "functional", "matrix", "sanitizers", "optimization", "performance")
CPU_PERF_DIR = ci.ROOT / "build/baselines/ci-cpu"
GUEST_RECEIPT = ci.ROOT / "build/waterbox/puae6/puae6-receipt.json"
ENV = dict(ci.ENV, P6_TEST_PORTABILITY="1", P6_TEST_SIMD_MATRIX="1", P6_TEST_ENGINE_FLAGS="1", P6_TEST_RGB565_FRONTEND="1",
           P6_TEST_COMPILERS="1", P6_TEST_FPU_MATRIX="1", P6_TEST_MUTANTS="1", P6_EMUHAWK_BUILD="1", P6_TEST_REAL_FRONTEND="1", P6_TEST_DET01_UNITS="10000",
           P6_A600_UNITS="1800", P6_A1200_UNITS="2200", P6_A1200_DMS_UNITS="2200", P6_GAME_UNITS="420", P6_HDF_UNITS="700", P6_HDF_UNITS_13="700",
           P6_CPU_PERF_DIR=str(CPU_PERF_DIR), P6_RUN_CPU_PERF="0", P6_MEDIA_MOVIE_GUEST_RECEIPT=str(GUEST_RECEIPT))
ASSETS = ("P6_KICKSTART_ROM", "P6_KICKSTART_A600_ZIP", "P6_WB205_ZIP", "P6_KICKSTART_A1200_ROM", "P6_AGA_ADF", "P6_AGA_DMS",
          "P6_GAME_ADF", "P6_WHDLOAD_LHA", "P6_FATALMISSION_LHA", "P6_FDI_FILE", "P6_IPF_FILE", "P6_PACKAGE_ZIP",
          "P6_KICKSTART_A4000_ROM", "P6_CD32_ROM_MAIN", "P6_CD32_ROM_EXT", "P6_CD32_GAME_CUE",
          "P6_CDTV_ROM_MAIN", "P6_CDTV_ROM_EXT", "P6_CDTV_BOOT_CUE", "P6_KICKSTART_A500_ROM",
          "P6_ACTION_REPLAY1_ROM", "P6_ACTION_REPLAY2_ROM", "P6_ACTION_REPLAY3_ROM", "P6_ACTION_REPLAY1200_ROM",
          "P6_CD_CUE_FILE", "P6_CD_CCD_FILE", "P6_CD_NRG_FILE", "P6_CD_ISO_FILE", "P6_CD_WAV_FILE", "P6_LEGACY_UAE_HOST")
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


def images(retroarch=False):
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
    if retroarch:
        image = retroarch_image.verify(ci.command)
        found[image["tag"]] = image["id"]
    return found


def lane_images(lane):
    # Native clean/fuzz suites do not consume any container inputs.
    return images(retroarch=lane == "functional") if lane in ("functional", "matrix", "optimization", "performance") else {}


def execute(argv, timeout=14400):
    print(ci.redact("RUN " + shlex.join(list(map(str, argv)))), file=sys.stderr, flush=True)
    p = ci.command(list(map(str, argv)), ENV, timeout=timeout)
    if p.returncode:
        raise RuntimeError(ci.redact(p.stdout[-2000:] + p.stderr[-6000:]))


def candidate_package():
    """Reject an unrelated preview before preparing the expensive functional lane."""
    revision = package.clean_revision()
    try:
        with zipfile.ZipFile(os.environ["P6_PACKAGE_ZIP"]) as archive:
            entry = archive.getinfo("manifest.json")
            if entry.file_size > 64 * 1024:
                raise ValueError("oversized manifest")
            manifest = json.loads(archive.read(entry))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError("invalid candidate package manifest") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or \
            manifest.get("kind") != "local-preview" or manifest.get("platform") != "macos-arm64":
        raise ValueError("invalid candidate package manifest")
    if manifest.get("revision") != revision:
        raise ValueError("candidate package revision differs from the current clean sources; rebuild the preview")


def waterbox_inputs():
    """Enable movies only with the canonical guest and runtime of these sources."""
    try:
        data = json.loads(GUEST_RECEIPT.read_text())
        if data["schema_version"] != 1 or data["status"] != "BUILD01_PASS_GUEST_NOT_EXECUTED":
            raise ValueError("invalid guest receipt")
        if set(data["sources"]) != set(wb.puae6_sources()):
            raise ValueError("guest source selection differs from the current sources")
        for name, digest in data["sources"].items():
            if wb.sha256(ci.ROOT / name) != digest:
                raise ValueError("guest was built from other sources: " + name)
        recipe = {"makefile": "ports/waterbox/Makefile", "common_mak": "references/machawk/waterbox/common.mak",
                  "linkscript_aarch64": "references/machawk/waterbox/linkscript_aarch64.T", "build_recipe": "tools/build_waterbox.py"}
        if data["recipe"] != {key: wb.sha256(ci.ROOT / name) for key, name in recipe.items()}:
            raise ValueError("guest was built with another recipe")
        wb.ensure_current_trees(root=ci.ROOT)
        guest = Path(data["guest"]["path"])
        if wb.sha256(guest) != data["guest"]["sha256"]:
            raise ValueError("guest differs from its receipt")
        wb.validate_elf(guest)
        wb.check_no_compiler_tls(guest)
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path(ENV["P6_WATERBOX_RUNTIME"]))["runtime_dylib"]
        if wb.sha256(Path(runtime["path"])) != runtime["sha256"]:
            raise ValueError("runtime differs from its receipt")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("invalid current Waterbox inputs: " + str(error)) from error


def identity(docker=None):
    facts = ci.identity()
    facts["test_environment"] = {**ENV, wb.SOURCE_AUDIT_ENV: wb.source_audit_mode(),
                                 wb.TOOLCHAIN_PROFILE_ENV: wb.selected_toolchain()["toolchain_profile"]}
    if docker is not None:
        facts["images"] = docker
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
            execute([py, "tools/bench.py", "build", "--jobs", "2"])
            execute([py, "-m", "tests.simd.engine_flags", "measure"])
            execute([py, "-m", "tests.simd.frontend_matrix", "measure"])
            execute([py, "tools/bench.py", "run", "--root", CPU_PERF_DIR.parent, "--id", CPU_PERF_DIR.name,
                     "--compare-cpu-levels", "--force"])
    if lane == "clean":
        execute([py, "tools/generate_cpu.py", "--check"])
        root = ci.ROOT / "build"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ci-clean-", dir=root) as tree:
            execute(["cmake", "-S", ci.ROOT, "-B", tree, "-G", "Ninja", "-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/macos-appleclang.cmake",
                     "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -g", "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2 -g"])
            execute(["cmake", "--build", tree, "--parallel", "2", "--target", "p6_headless", "puae6_libretro"])
            return {name: ci.sha(Path(tree) / name) for name in ("p6_headless", "puae6_libretro.dylib")}
    return {}


def run(lane, source_audit=None):
    try:
        with wb.source_audit_scope(source_audit):
            return _run(lane)
    except wb.BuildError as error:
        return dict(status="BLOCKED", lane=lane, diagnostic=ci.redact(str(error)), completed=[], identity=None)


def _run(lane):
    completed, facts = [], None
    try:
        selected = suites(lane)
        ci.preflight(ASSETS if lane == "functional" else ())
        if lane == "functional":
            candidate_package()
            waterbox_inputs()
        docker = lane_images(lane)
        execute([sys.executable, "tools/generate_cpu.py"])
        facts = identity(docker)
        prepared = prepare(lane)
        for module in selected:
            print("RUN " + module, file=sys.stderr, flush=True)
            p = ci.command([sys.executable, "-c", ci.check.DRIVER, module], ENV, timeout=6 * 3600)
            status, counts = ci.verdict(p)
            if status != "PASS":
                return dict(status=status, lane=lane, module=module, reason=counts, diagnostic=ci.redact(p.stderr[-6000:]), completed=completed, identity=facts)
            completed.append(dict(module=module, counts=counts))
            if lane_images(lane) != docker:
                raise RuntimeError("Docker inputs changed during CI")
        if identity(lane_images(lane)) != facts:
            raise RuntimeError("source/codegen/toolchain inputs changed during CI")
        return dict(status="PASS", lane=lane, completed=completed, preparation=prepared, identity=facts, images=docker,
                    budget_policy="advisory, unchanged plan/performance-budgets.json; not remote CI evidence")
    except (OSError, ValueError, RuntimeError, ci.subprocess.SubprocessError) as error:
        return dict(status="BLOCKED" if isinstance(error, ValueError) else "FAIL", lane=lane, diagnostic=ci.redact(str(error)), completed=completed, identity=facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-audit", choices=("checkout", "snapshot"), default="checkout")
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    result = dict(status="NOT_RUN", lane=args.lane, source_audit=args.source_audit, suites=suites(args.lane)) if args.list else run(args.lane, args.source_audit)
    print(ci.redact(json.dumps(result, ensure_ascii=False, sort_keys=True)))
    return 0 if result["status"] in ("PASS", "NOT_RUN") else 2 if result["status"] == "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
