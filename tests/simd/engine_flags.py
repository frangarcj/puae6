"""T35b: opt-in full-engine experiments, public training corpus only.

python3 -m tests.simd.engine_flags build|verify|merge|measure [--variant ...]
Each build is isolated. Verification precedes timing; no production default changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

from tests.portability import corpus as assets
from tests.build_support import source_identity
from tests.reference import corpus_run
from tests.scenarios.corpus import corpus
from tests.state import test_det02 as det

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "build/optimization"
VARIANTS = ("base", "lto", "instrument", "pgo", "lto-san", "pgo-san")
ENV = {**os.environ, "LC_ALL": "C", "TZ": "UTC", "ASAN_OPTIONS": "detect_leaks=0"}
ENV.pop("LLVM_PROFILE_FILE", None)
PROFDATA = WORK / "training.profdata"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def verification_harness(root=None):
    root = ROOT if root is None else Path(root)
    return {str(p.relative_to(root)): sha(p) for p in (root / "tests").rglob("*.py") if p.is_file()}


def validate_receipt(variant, record, commands, source, probe_sha, harness):
    """Original equivalence receipt assertions, shared without executing an engine."""
    def require(condition, message):
        if not condition: raise AssertionError(message)
    require(variant in VARIANTS and record["source_identity"] == source, "engine receipt source/variant differs")
    require(record["probe_sha256"] == probe_sha, "engine receipt probe differs")
    require(record["harness"] == harness and harness, "engine receipt harness differs or is incomplete")
    expected = [f"{e.id}@{n}/{mode}" for e in corpus.ENTRIES for n in e.points for mode in e.modes]
    require(record["restored"] == expected and set(record["canonical"]["cases"]) == {e.id for e in corpus.ENTRIES}
            and record["canonical"]["errors"] == {}, "engine receipt corpus/restoration differs")
    require(commands, "missing engine compile commands")
    for command in commands:
        text = command["command"]
        require("-fno-fast-math" in text and "-ffp-contract=off" in text and "-march=native" not in text, "engine precision flags differ")
        if variant.startswith("lto"): require("-flto" in text, "engine LTO flag missing")
        if variant.startswith("pgo"): require("-fprofile-instr-use=" in text, "engine PGO flag missing")
        if variant.endswith("-san"): require("-fsanitize=address,undefined" in text, "engine sanitizer flag missing")
    return record


def run(command, log=None):
    result = subprocess.run(list(map(str, command)), cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=7200)
    if log:
        log.write_text(result.stdout + result.stderr)
    if result.returncode or "runtime error:" in result.stderr or "ERROR: AddressSanitizer" in result.stderr:
        raise RuntimeError(f"{command[0]} exit {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout


def identity():
    pin = json.loads((ROOT / "plan/toolchains.json").read_text())["portability_matrix"]["rows"]["macos-arm64-appleclang"]
    version = run(["/usr/bin/clang++", "--version"]).splitlines()[0]
    if version != pin["compiler_version"] or platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("T35b experiment requires the declared native Apple Clang ARM64 row")
    return {"compiler": version, "compiler_sha256": sha("/usr/bin/clang++"), "os": platform.platform(), "isa": platform.machine(),
            "dedicated_runner": False, "load": os.getloadavg(), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def build(variant):
    metadata = identity()
    source = source_identity()
    directory = WORK / variant
    directory.mkdir(parents=True, exist_ok=True)
    command = ["cmake", "-S", ROOT, "-B", directory, "-G", "Ninja", "-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/macos-appleclang.cmake",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_FLAGS_RELEASE=-O2", "-DCMAKE_CXX_FLAGS_RELEASE=-O2",
               f"-DP6_NATIVE_IPO={'ON' if variant.startswith('lto') else 'OFF'}",
               f"-DP6_PGO_MODE={'GENERATE' if variant == 'instrument' else 'USE' if variant.startswith('pgo') else 'OFF'}",
               f"-DP6_PGO_DATA={PROFDATA if variant.startswith('pgo') else ''}", f"-DP6_SANITIZERS={'ON' if variant.endswith('-san') else 'OFF'}"]
    if variant == "instrument":
        # Both diskutil entry points disappear from the training image otherwise.
        # Retain their counters, at zero, without ever executing them. USE builds
        # keep normal dead stripping and both strict profile diagnostics.
        command += ["-DP6_PGO_TRAIN_ROOTS=__Z12isamigatrackPtPhiS0_S0_iPi;__Z9ispctrackPtPhiS0_S0_iPi"]
    run(command, directory / "configure.log")
    started = time.monotonic()
    run(["cmake", "--build", directory, "--parallel", "2", "--target", "p6_state_probe", "p6_bench_probe"], directory / "build.log")
    if source != source_identity():
        raise RuntimeError("source inputs changed during the experimental build")
    save(directory / "build.json", {**metadata, "configure": list(map(str, command)), "build_seconds": time.monotonic() - started,
                                  "source_identity": source, "compile_commands_sha256": sha(directory / "compile_commands.json"),
                                  "binaries": {name: {"sha256": sha(directory / name), "bytes": (directory / name).stat().st_size}
                                               for name in ("p6_state_probe", "p6_bench_probe")}})
    print(f"built {variant}", flush=True)


def verify(variant):
    directory = WORK / variant
    built = json.loads((directory / "build.json").read_text())
    if built["source_identity"] != source_identity():
        raise ValueError("rebuild after changing source inputs")
    previous_profiles = set((directory / "profiles").glob("*.profraw"))
    target = directory / "corpus"
    listing = assets.write_differential(target)
    run([sys.executable, ROOT / "tests/portability/row_driver.py", "--probe", directory / "p6_state_probe", "--corpus", target,
         "--out", directory / "canonical.json", "--jobs", "2"], directory / "canonical.log")
    canonical = json.loads((directory / "canonical.json").read_text())
    if canonical["errors"] or len(canonical["cases"]) != len(corpus.ENTRIES):
        raise AssertionError(canonical["errors"])
    if variant != "base":
        reference = json.loads((WORK / "base/canonical.json").read_text())["cases"]
        for name, result in canonical["cases"].items():
            for key in ("hardware", "av", "per_unit", "units", "lines"):
                if result[key] != reference[name][key]:
                    raise AssertionError(f"{variant} {name}: {key} differs from -O2")

    class BoundCase(det.Det02Case):
        SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"
        exe, dir = directory / "p6_state_probe", target
        roms = {c["rom"]: target / (c["rom"] + ".rom") for c in listing}

        def arguments(self, extra):
            return self.assets.expand(extra)

        @classmethod
        def probe_on(cls, machine, rom, *arguments):
            from tests.state.harness import Run
            output = run([cls.exe, cls.roms[rom], "--machine", machine, *arguments])
            return Run(output)

    case = BoundCase()
    case.assets = corpus_run.Assets(target)
    restored = []
    for entry in corpus.ENTRIES:
        case.MACHINE, BoundCase.MACHINE, case.cache = entry.machine, entry.machine, {}
        for n in entry.points:
            for mode in entry.modes:
                found = case.first_divergence(entry.program, entry.extra, n, mode)
                if found:
                    raise AssertionError(f"{variant} {entry.id}@{n}/{mode}: {found}")
                restored.append(f"{entry.id}@{n}/{mode}")
        print(f"verified {variant} {entry.id}", flush=True)
    if variant == "instrument":
        from tools.bench import SCENARIOS
        for scenario in (s for s in SCENARIOS if s.kind == "microrom"):
            command = [directory / "p6_bench_probe", target / (corpus.by_id()[scenario.corpus_id].program + ".rom"),
                       "--machine", scenario.machine, "--warmup", "300", "--units", "300", "--state-rounds", "20", "--alloc-units", "0"]
            if scenario.standard == "ntsc":
                command += ["--ntsc"]
            run(command, directory / (scenario.id + "-training.json"))
    save(directory / "verification.json", {"identity": identity(), "canonical": canonical, "restored": restored,
                                          "source_identity": source_identity(),
                                          "harness": verification_harness(),
                                          "probe_sha256": sha(directory / "p6_state_probe"),
                                          "raw_profiles": [str(p) for p in sorted(set((directory / "profiles").glob("*.profraw")) - previous_profiles)],
                                          "corpus_files": {p.name: sha(p) for p in sorted(target.iterdir()) if p.suffix != ".state"}})


def merge():
    if not (WORK / "instrument/verification.json").is_file():
        raise ValueError("verify the training build before merging profiles")
    tool = Path(run(["/usr/bin/xcrun", "--find", "llvm-profdata"]).strip())
    profiles = json.loads((WORK / "instrument/verification.json").read_text())["raw_profiles"]
    if not profiles:
        raise ValueError("no training profiles")
    run([tool, "merge", "-o", PROFDATA, *profiles], WORK / "merge.log")
    save(WORK / "training.json", {"tool": str(tool), "version": run([tool, "--version"]), "tool_sha256": sha(tool),
                                 "profile_sha256": sha(PROFDATA), "raw_profiles": len(profiles),
                                 "corpus": sha(WORK / "instrument/verification.json"), "private_assets": False})


def measure():
    # Same fixed public workloads as T33, with five interleaved repetitions.
    from tools.bench import SCENARIOS, system_load, describe
    scenarios = [s for s in SCENARIOS if s.kind == "microrom"]
    variants = ("base", "lto", "pgo")
    for variant in variants:
        verified = json.loads((WORK / variant / "verification.json").read_text())
        if verified["probe_sha256"] != sha(WORK / variant / "p6_state_probe"):
            raise ValueError("verification belongs to another binary")
    output = {"identity": identity(), "runs": [], "summary": {}, "verified": {v: sha(WORK / v / "verification.json") for v in variants},
              "benchmarks": {v: sha(WORK / v / "p6_bench_probe") for v in variants}}
    for repetition in range(5):
        for scenario in scenarios:
            for variant in variants[repetition % 3:] + variants[:repetition % 3]:
                arguments = [WORK / variant / "p6_bench_probe", WORK / variant / "corpus" / (corpus.by_id()[scenario.corpus_id].program + ".rom"),
                             "--machine", scenario.machine, "--warmup", "300", "--units", str(10000 if scenario.primary else 3000),
                             "--state-rounds", "20", "--alloc-units", "0"]
                if scenario.standard == "ntsc":
                    arguments += ["--ntsc"]
                before = system_load()
                raw = json.loads(run(arguments))
                output["runs"].append({"variant": variant, "scenario": scenario.id, "repeat": repetition,
                                       "before": before, "after": system_load(), "raw": raw})
                save(WORK / "measurements.json", output)
                print(f"measured {variant} {scenario.id} #{repetition + 1}", flush=True)
    for scenario in scenarios:
        rows = {}
        for variant in variants:
            samples = [r["raw"] for r in output["runs"] if r["variant"] == variant and r["scenario"] == scenario.id]
            rows[variant] = {"median_wall_ns": statistics.median(statistics.median(s["unit_wall_ns"]) for s in samples),
                             "median_cpu_ns": statistics.median(statistics.median(s["unit_cpu_ns"]) for s in samples),
                             "wall": describe([n for s in samples for n in s["unit_wall_ns"]]),
                             "cpu": describe([n for s in samples for n in s["unit_cpu_ns"]]),
                             "save": describe([n for s in samples for n in s["state_save_ns"]]),
                             "load": describe([n for s in samples for n in s["state_load_ns"]])}
        output["summary"][scenario.id] = rows
    save(WORK / "measurements.json", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify", "merge", "measure"))
    parser.add_argument("--variant", choices=VARIANTS, default="base")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if args.action in ("build", "verify"):
        globals()[args.action](args.variant)
    else:
        globals()[args.action]()
