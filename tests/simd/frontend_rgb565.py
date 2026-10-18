"""T35c: the real libretro frontend, two converters over shared engine objects.

Run inside the selected row (the container wrapper mounts a separate build root).
No timing starts until pixel/PCM/tick and same/new-process restore checks pass.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import tempfile
import time

from tests.build_support import source_identity
from tests.portability import corpus as assets
from tests.portability.rows import ROWS
from tests.scenarios.corpus import corpus
from tools.bench import SCENARIOS, describe

ROOT = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "LC_ALL": "C", "TZ": "UTC", "ASAN_OPTIONS": "detect_leaks=0"}
RUN = importlib.import_module("tests.libretro-host.test_libretro").RUN
ROM_NAMES = {"a500": "kick34005.A500", "a600": "kick37350.A600", "a1200": "kick40068.A1200"}
WORKLOADS = [s for s in SCENARIOS if s.kind == "microrom"]
EXTRA = ("ecs-shres-ntsc", "aga-ham8-ntsc", "aga-sprite64-pal", "ocs-hires-lace-ntsc")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timing_inputs(binaries, directory):
    """Executable bytes, actual assets and timing protocol; no receipt metadata."""
    return {"binaries": {name: digest(path) for name, path in binaries.items()},
            "corpus": {p.name: digest(p) for p in sorted(Path(directory).iterdir()) if p.is_file()},
            "harness": {p: digest(ROOT / p) for p in ("tests/simd/frontend_rgb565.py", "tools/bench.py")}}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def execute(command, log=None):
    r = subprocess.run(list(map(str, command)), cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=7200)
    if log:
        log.write_text(r.stdout + r.stderr)
    if r.returncode or "runtime error:" in r.stderr or "Sanitizer" in r.stderr:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr[-2000:]}\n{r.stdout[-1000:]}")
    return r.stdout


class Frontend:
    def __init__(self, row, work, sanitizers=False):
        self.row, self.work, self.sanitizers = row, Path(work).resolve(), sanitizers
        self.tree = self.work / ("san" if sanitizers else "opt")
        self.work.mkdir(parents=True, exist_ok=True)
        self.host = self.tree / "p6_libretro_host"
        extension = ".dylib" if platform.system() == "Darwin" else ".so"
        self.cores = {v: self.tree / ("puae6_libretro" + ("_rgb565_candidate" if v == "candidate" else "") + extension)
                      for v in ("current", "candidate")}
        self.corpus = self.work / "corpus"
        assets.write_differential(self.corpus)

    def build(self):
        compiler = "/usr/bin/g++" if self.row.endswith("gcc") else "/usr/bin/clang++"
        version = execute([compiler, "--version"]).splitlines()[0]
        pin = json.loads((ROOT / "plan/toolchains.json").read_text())["portability_matrix"]["rows"][self.row]
        if version != pin["compiler_version"]:
            raise ValueError(f"wrong compiler: {version}")
        actual = "arm64" if platform.machine() == "aarch64" else platform.machine()
        if actual != ROWS[self.row]["isa"]:
            raise ValueError(f"wrong ISA: {actual}")
        source = source_identity()
        configure = ["cmake", "-S", ROOT, "-B", self.tree, "-G", "Ninja", f"-DCMAKE_TOOLCHAIN_FILE={ROOT / ROWS[self.row]['toolchain']}",
                     "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_FLAGS_RELEASE=-O2", "-DCMAKE_CXX_FLAGS_RELEASE=-O2", "-DP6_NATIVE_IPO=OFF",
                     "-DP6_PGO_MODE=OFF", "-DP6_RGB565_EXPERIMENT=ON", f"-DP6_SANITIZERS={'ON' if self.sanitizers else 'OFF'}"]
        self.tree.mkdir(parents=True, exist_ok=True)
        execute(configure, self.tree / "configure.log")
        execute(["cmake", "--build", self.tree, "--parallel", "3", "--target", "p6_libretro_host", "puae6_libretro_rgb565_candidate"], self.tree / "build.log")
        if source != source_identity():
            raise ValueError("source changed during build")
        save(self.tree / "build.json", {"row": self.row, "compiler": version, "compiler_sha256": digest(compiler),
             "os": platform.platform(), "isa": actual, "configure": list(map(str, configure)), "source_identity": source,
             "binaries": {v: digest(p) for v, p in {**self.cores, "host": self.host}.items()},
             "inputs": {p: digest(ROOT / p) for p in ("CMakeLists.txt", "adapters/libretro/present.h", "tests/simd/rgb565_candidate.h", "tests/simd/frontend_rgb565.py")}})
        print(f"built {self.row} {'san' if self.sanitizers else 'opt'}", flush=True)

    def arguments(self, variant, entry, system, *extra):
        system.mkdir(exist_ok=True)
        (system / ROM_NAMES[entry.machine]).write_bytes((self.corpus / (entry.program + ".rom")).read_bytes())
        options = ["--system", system, "--set", "puae6_machine=" + entry.machine.upper(), "--set", "puae6_video_color_depth=16bit",
                   "--set", "puae6_video_standard=" + entry.standard.upper()]
        return [self.host, self.cores[variant], *options, *extra]

    def verify(self):
        built = json.loads((self.tree / "build.json").read_text())
        if built["source_identity"] != source_identity():
            raise ValueError("rebuild current inputs first")
        for name, path in {**self.cores, "host": self.host}.items():
            if digest(path) != built["binaries"][name]:
                raise ValueError(f"rebuilt or replaced executable: {name}")
        inputs = timing_inputs({**self.cores, "host": self.host}, self.corpus)
        entries = [corpus.by_id()[s.corpus_id] for s in WORKLOADS] + [corpus.by_id()[s] for s in EXTRA]
        records = {}
        with tempfile.TemporaryDirectory(prefix="p6-rgb-frontend-") as temporary:
            directory = Path(temporary)
            for entry in entries:
                for resolution in ("lores", "hires", "superhires"):
                    key, compared, states = entry.id + "/" + resolution, {}, {}
                    for variant in self.cores:
                        state, system = directory / (variant + ".state"), directory / variant
                        out = execute(self.arguments(variant, entry, system, "--runs", "14", "--set", "puae6_video_resolution=" + resolution,
                                      "--do", f"6:savefile:{state}", "--do", f"10:loadfile:{state}:pos=6"))
                        views = [m.groups()[1:] for m in RUN.finditer(out)]
                        if len(views) != 14 or "pixel-format RGB565" not in out or views[6:10] != views[10:14]:
                            raise AssertionError(f"{key} {variant}: incomplete/replayed frame differs")
                        fresh = execute(self.arguments(variant, entry, system, "--runs", "4", "--set", "puae6_video_resolution=" + resolution,
                                        "--do", f"0:loadfile:{state}:pos=6"))
                        if [m.groups()[1:] for m in RUN.finditer(fresh)] != views[6:10]:
                            raise AssertionError(f"{key} {variant}: new-process restore differs")
                        if "ok=0" in out + fresh:
                            raise AssertionError(f"{key} {variant}: state refused")
                        compared[variant], states[variant] = views, digest(state)
                    if compared["current"] != compared["candidate"] or states["current"] != states["candidate"]:
                        raise AssertionError(f"{key}: converter changes pixels/PCM/ticks/state")
                    records[key] = {"runs": compared["current"], "state_sha256": states["current"], "same_process_restore": True, "new_process_restore": True}
                    print(f"verified {self.row} {key}", flush=True)
        if inputs != timing_inputs({**self.cores, "host": self.host}, self.corpus):
            raise ValueError("verification inputs changed during execution")
        save(self.tree / "verification.json", {"build": built, "inputs": inputs, "cases": records})

    def measure(self):
        if self.sanitizers:
            raise ValueError("do not time a sanitizer build")
        verified = self.tree / "verification.json"
        record = json.loads(verified.read_text())
        if record["build"]["source_identity"] != source_identity():
            raise ValueError("verification inputs changed")
        for name, path in {**self.cores, "host": self.host}.items():
            if digest(path) != record["build"]["binaries"][name]:
                raise ValueError(f"verify the current executable before timing: {name}")
        inputs = timing_inputs({**self.cores, "host": self.host}, self.corpus)
        if record.get("inputs") != inputs:
            raise ValueError("verify the current corpus and timing protocol before timing")
        runs = []
        result = {"row": self.row, "inputs": inputs, "runs": runs, "summary": {}, "dedicated_runner": False}
        for repetition in range(5):
            for scenario in WORKLOADS:
                entry = corpus.by_id()[scenario.corpus_id]
                for variant in (("current", "candidate") if repetition % 2 == 0 else ("candidate", "current")):
                    raw_path = self.work / "timing.json"
                    units = 10000 if scenario.primary else 3000
                    started, load = time.time(), os.getloadavg()
                    text = execute(self.arguments(variant, entry, self.work / "system", "--runs", str(300 + units), "--bench", raw_path, "--bench-state", "20"))
                    raw = json.loads(raw_path.read_text())
                    if len(raw["run_wall_ns"]) != 300 + units or "pixel-format RGB565" not in text:
                        raise AssertionError("timing did not run RGB565 or all units")
                    last = RUN.findall(text)[-1]
                    final = re.search(r"^state final ok=1 size=(\d+) hash=(\w+)$", text, re.M)
                    if not final:
                        raise AssertionError("timed frontend did not save its final state")
                    runs.append({"scenario": scenario.id, "variant": variant, "repeat": repetition, "warmup": 300, "units": units,
                                 "started": started, "load_before": load, "load_after": os.getloadavg(), "last_run": last,
                                 "final_state": final.groups(), "raw": raw})
                    save(self.tree / "measurements.json", result)
                    print(f"measured {self.row} {scenario.id} {variant} #{repetition+1}", flush=True)
        if inputs != timing_inputs({**self.cores, "host": self.host}, self.corpus):
            raise ValueError("timing inputs changed during execution")
        for scenario in WORKLOADS:
            result["summary"][scenario.id] = {v: {"cpu": describe([n for x in runs if x["variant"] == v and x["scenario"] == scenario.id for n in x["raw"]["run_cpu_ns"][300:]]),
                 "median_cpu_ns": statistics.median(statistics.median(x["raw"]["run_cpu_ns"][300:]) for x in runs if x["variant"] == v and x["scenario"] == scenario.id)} for v in self.cores}
        save(self.tree / "measurements.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row", choices=tuple(ROWS), required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--action", choices=("build", "verify", "measure", "all"), required=True)
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    frontend = Frontend(args.row, args.work, args.sanitizers)
    for action in (("build", "verify", "measure") if args.action == "all" else (args.action,)):
        getattr(frontend, action)()
