#!/usr/bin/env python3
"""T33: the benchmark of PUAE6 without vsync, and its baseline.

The wall clock is read here and in the probes, around the calls, to MEASURE. It never advances the emulation: every adapter steps the machine only through p6_run_frame and
the same ROM, seed and input give the same ticks whatever the timings (the baseline checks it: all the runners of a scenario end the timed window on the same master tick).

What is measured, each as a layer of its own, with warmup and repetitions (docs/04-validation.md, "Protocolo de rendimiento"):
  native               tests/bench/bench_probe.cpp, p6_run_frame with picture and audio (render 3) in a Release -O2 tree; engine + the production of the output
  native-norender      the same with render 0: the engine alone (the difference is what producing the picture and the PCM costs)
  libretro             tests/libretro-host/host.cpp --bench: retro_run of the real core through a frontend that does nothing with the picture (adapter = this minus native)
  waterbox-guest-*     integration/bizhawk/smoke/Puae6Bench.cs: the guest ABI in a real Waterbox sandbox, one unit split in Activate / p6w_frame_advance / copy / Deactivate,
                       with the Release runtime (the default since T34a: what BizHawk ships, eviction off) and with the Debug lane (a DEBUG build of libwaterboxhost, forced eviction)
  waterbox-core-*      tests/ffi/Bench.cs: Puae6Core.FrameAdvance as BizHawk calls it (input packet, enter, unit, copy to int[]/short[], exit), Release managed code
State: p6_state_save / p6_state_load (native), retro_serialize / retro_unserialize (libretro), the runtime's Save/Load (guest) and SaveStateBinary / LoadStateBinary (core), each with the
first unit after a load. Memory: peak RSS of each process, the heaps of the sandbox (committed and dirty), the dirty unit of Darwin ARM64 (16 KiB groups of four 4 KiB logical pages).

    python3 tools/bench.py build                      build every runner (Release -O2 native tree, the Release and Debug lanes of the runtime, Release core); -j 3 at most
    python3 tools/bench.py run --id ID [--quick]      run the matrix, interleaved by repetition, raw data under build/baselines/ID/raw
    python3 tools/bench.py summarize --id ID          build/baselines/ID/summary.json and summary.md from the raw data
    python3 tools/bench.py compare ID1 ID2            row by row, two baselines taken at different times
    python3 tools/bench.py budgets --id ID            plan/performance-budgets.json from the summary (goals stay goals; nothing becomes a hard threshold)

Results taken on a machine that someone is using are ORIENTATIVE: the report says so, with the load that each sample ran under, and no threshold is derived from them.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_waterbox  # noqa: E402

BUILD = ROOT / "build"
NATIVE_TREE = BUILD / "bench-release"          # Release, -O2: the optimization of the Waterbox guest (tools/build_waterbox.py passes CCFLAGS_RELEASE=-O2)
WORK = BUILD / "bench"
BASELINES = BUILD / "baselines"
BUDGETS = ROOT / "plan/performance-budgets.json"
WATERBOX = BUILD / "waterbox"
RELEASE_RUNTIME = WATERBOX / "cargo/aarch64-apple-darwin/release/libwaterboxhost.dylib"
DEBUG_RUNTIME_RECEIPT = build_waterbox.runtime_receipt_path("debug")           # T34a: the two lanes of the runtime, each with its receipt (tools/build_waterbox.py ensure_runtime_lane)
RELEASE_RUNTIME_RECEIPT = build_waterbox.runtime_receipt_path("release")
GUEST_RECEIPT = WATERBOX / "puae6/puae6-receipt.json"
SMOKE_DLL = WATERBOX / "managed/bin/Debug/net8.0/Smoke.dll"
FFI_DEBUG = ROOT / "build/ffi/bin/Debug/net8.0"
FFI_RELEASE = Path(os.environ.get("P6_BENCH_FFI_RELEASE") or ROOT / "build/ffi/bin/Release/net8.0")      # T34: another build of the core (the "before" of an optimization) to run next to this one
MEMORY_MANIFEST = ROOT / "manifests/waterbox-memory.json"

# The goals of docs/04-validation.md. They are goals: a baseline compares them with what was measured and never turns them into results.
GOAL_P95_FRACTION = 0.70
GOAL_TIME_REGRESSION = 0.05
GOAL_STATE_REGRESSION = 0.10
# A run is "noisy" when its thread waited (mean wall time above NOISY_WAIT_RATIO x its mean CPU time: it was descheduled). The load average is recorded with every run, but a busy machine
# (the Mac of the person who works on it) does not by itself disturb a thread that has a core; --wait-quiet can wait for it to drop under NOISY_LOAD1 before each run.
NOISY_LOAD1 = 6.0
NOISY_WAIT_RATIO = 1.05

KICKSTART_NAME = {"a500": "kick34005.A500", "a600": "kick37350.A600", "a1200": "kick40068.A1200"}
LIBRETRO_MACHINE = {"a500": "A500", "a600": "A600", "a1200": "A1200"}


# --- statistics -----------------------------------------------------------------------------------------------------------------------------------------------

def percentile(sorted_values, q):
    """Linear interpolation between the closest ranks (the definition numpy calls 'linear'); q in [0, 100]."""
    if not sorted_values:
        raise ValueError("percentile of nothing")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * q / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low))


def describe(values):
    """n, min, p50, mean, p95, p99, max, the interquartile range, the median absolute deviation and the coefficient of dispersion (MAD / median) of a list of numbers."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    median = percentile(ordered, 50)
    mad = percentile(sorted(abs(v - median) for v in ordered), 50)
    mean = sum(ordered) / len(ordered)
    variance = sum((v - mean) ** 2 for v in ordered) / len(ordered)
    return {"n": len(ordered), "min": float(ordered[0]), "p50": median, "mean": mean, "p95": percentile(ordered, 95), "p99": percentile(ordered, 99), "max": float(ordered[-1]),
            "iqr": percentile(ordered, 75) - percentile(ordered, 25), "mad": mad, "cod": (mad / median) if median else 0.0, "stdev": math.sqrt(variance)}


def ms(value_ns):
    return value_ns / 1e6


# --- the machine and the conditions ---------------------------------------------------------------------------------------------------------------------------

def run_text(command, timeout=30):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def system_load():
    """The conditions under one sample: load averages, swap, free memory, thermal state and the five busiest processes. Read before and after every run."""
    load = os.getloadavg()
    vm = run_text(["vm_stat"])
    page = 16384
    stats = {}
    for line in vm.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().rstrip(".")
            if value.isdigit():
                stats[key.strip()] = int(value)
        elif "page size of" in line:
            digits = "".join(c for c in line.split("page size of")[1] if c.isdigit())
            page = int(digits) if digits else page
    swap = run_text(["sysctl", "-n", "vm.swapusage"])
    busiest = run_text(["ps", "-Ao", "pcpu,rss,comm", "-r"]).splitlines()[1:6]
    return {"load": [round(x, 2) for x in load], "free_mib": round(stats.get("Pages free", 0) * page / 1048576), "active_mib": round(stats.get("Pages active", 0) * page / 1048576),
            "swapouts": stats.get("Swapouts"), "swap": swap, "thermal": run_text(["pmset", "-g", "therm"]).replace("\n", " | "), "busiest": [" ".join(line.split()) for line in busiest]}


def git_state():
    commit = run_text(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    dirty = bool(run_text(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"]))
    return {"commit": commit, "uncommitted_changes": dirty}


def tool_versions():
    clang = run_text(["clang", "--version"]).splitlines()
    cache = (NATIVE_TREE / "CMakeCache.txt")
    flags = {}
    if cache.is_file():
        for line in cache.read_text().splitlines():
            for key in ("CMAKE_BUILD_TYPE", "CMAKE_CXX_FLAGS_RELEASE", "CMAKE_C_FLAGS_RELEASE", "CMAKE_CXX_COMPILER", "P6_SANITIZERS"):
                if line.startswith(key + ":"):
                    flags[key] = line.split("=", 1)[1]
    return {"clang": clang[0] if clang else None, "cmake": (run_text(["cmake", "--version"]).splitlines() or [None])[0], "python": platform.python_version(),
            "dotnet": run_text(["dotnet", "--version"]), "cargo": run_text(["cargo", "--version"]), "native_flags": flags}


def capture_environment():
    series = json.loads((ROOT / "patches/winuae/series.json").read_text())
    return {
        "date_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git_state(),
        "host": {"machine": platform.machine(), "os": run_text(["sw_vers", "-productVersion"]) + " " + run_text(["sw_vers", "-buildVersion"]), "kernel": platform.release(),
                 "cpu": run_text(["sysctl", "-n", "machdep.cpu.brand_string"]), "cores": int(run_text(["sysctl", "-n", "hw.ncpu"]) or 0),
                 "performance_cores": int(run_text(["sysctl", "-n", "hw.perflevel0.logicalcpu"]) or 0), "efficiency_cores": int(run_text(["sysctl", "-n", "hw.perflevel1.logicalcpu"]) or 0),
                 "memory_bytes": int(run_text(["sysctl", "-n", "hw.memsize"]) or 0), "host_page_bytes": int(run_text(["sysctl", "-n", "hw.pagesize"]) or 0),
                 "power": run_text(["pmset", "-g", "batt"]).splitlines()[:2]},
        "tools": tool_versions(),
        "profile": "deterministic-a500", "p6_config": {"audio_rate_hz": 48000, "random_seed": 1, "rtc_epoch_seconds": 946684800, "input": "idle (no key, no port movement)"},
        "engine": {"upstream_commit": json.loads((ROOT / "manifests/source-manifest.json").read_text())["commit"], "patches": len(series["patches"]),
                   "series_sha256": hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()},
        "artifacts": artifact_hashes(),
        "condition_when_started": system_load(),
        "measurement_status": "ORIENTATIVE: the Mac is used by someone while the benchmark runs; no dedicated runner exists (plan/runners.json). Nothing here is a hard threshold.",
    }


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def artifact_hashes():
    out = {"p6_bench_probe": sha256_of(NATIVE_TREE / "p6_bench_probe"), "puae6_libretro": sha256_of(NATIVE_TREE / "puae6_libretro.dylib"),
           "p6_libretro_host": sha256_of(NATIVE_TREE / "p6_libretro_host"), "puae6.wbx": None, "libwaterboxhost_debug": None, "libwaterboxhost_release": sha256_of(RELEASE_RUNTIME)}
    if GUEST_RECEIPT.is_file():
        out["puae6.wbx"] = json.loads(GUEST_RECEIPT.read_text())["guest"]["sha256"]
    if DEBUG_RUNTIME_RECEIPT.is_file():
        out["libwaterboxhost_debug"] = json.loads(DEBUG_RUNTIME_RECEIPT.read_text())["runtime_dylib"]["sha256"]
    return out


# --- scenarios ------------------------------------------------------------------------------------------------------------------------------------------------

class Scenario:
    """One machine, one standard and one firmware (+ disk). `warmup` and `units` are the protocol's numbers scaled by `scale` (the full protocol is 300 and 10000)."""

    def __init__(self, id, machine, standard, kind, summary, corpus_id=None, env_rom=None, env_disk=None, warmup=300, units=10000, primary=False):
        self.id, self.machine, self.standard, self.kind, self.summary = id, machine, standard, kind, summary
        self.corpus_id, self.env_rom, self.env_disk = corpus_id, env_rom, env_disk
        self.warmup, self.units, self.primary = warmup, units, primary

    def available(self):
        if self.kind == "microrom":
            return True
        return bool(os.environ.get(self.env_rom) and Path(os.environ[self.env_rom]).is_file() and os.environ.get(self.env_disk) and Path(os.environ[self.env_disk]).is_file())

    def rom(self):
        """The firmware file. A micro-ROM is built from its corpus program (tests/scenarios/corpus); a Kickstart is read in place from the environment variable and never copied or hashed."""
        if self.kind == "kickstart":
            return Path(os.environ[self.env_rom])
        target = WORK / "roms" / f"{self.id}.rom"
        if not target.is_file():
            from tests.scenarios.corpus import corpus
            entry = corpus.by_id()[self.corpus_id]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(corpus.build_rom(corpus.programs()[entry.program]))
        return target

    def disk(self):
        return Path(os.environ[self.env_disk]) if self.kind == "kickstart" else None

    def ntsc(self):
        return self.standard == "ntsc"


SCENARIOS = [
    Scenario("a500-ocs-busy-pal", "a500", "pal", "microrom", "A500 OCS: every DMA channel on, a blit in flight under contention, the disk motor running (corpus ocs-blitter-dma-pal)", "ocs-blitter-dma-pal", primary=True),
    Scenario("a500-ocs-copper-ntsc", "a500", "ntsc", "microrom", "A500 OCS NTSC: the Copper rewrites the display registers every line (corpus ocs-ring-copper-ntsc)", "ocs-ring-copper-ntsc", units=3000),
    Scenario("a600-ecs-copper-ntsc", "a600", "ntsc", "microrom", "A600 ECS NTSC: the Copper rewrites BPLCON3/DIWHIGH too (corpus ecs-ring-copper-ntsc)", "ecs-ring-copper-ntsc", units=3000),
    Scenario("a1200-aga-planes8-pal", "a1200", "pal", "microrom", "A1200 AGA (68EC020 cycle exact): eight bitplanes fetched 64 bits at a time (corpus aga-planes8-fmode3-pal)", "aga-planes8-fmode3-pal", units=3000),
    Scenario("a500-ocs-tone4-pal", "a500", "pal", "microrom", "A500 Paula + DMA: four audio channels on DMA (corpus audio-tone4-pal)", "audio-tone4-pal", units=3000),
    Scenario("a500-ks13-game-pal", "a500", "pal", "kickstart", "OPT-IN, A500 Kickstart 1.3 + a game disk, read in place from P6_KICKSTART_ROM and P6_GAME_ADF (not copied, not hashed)",
             env_rom="P6_KICKSTART_ROM", env_disk="P6_GAME_ADF", warmup=2500, units=3000),
    Scenario("a1200-ks31-demo-pal", "a1200", "pal", "kickstart", "OPT-IN, A1200 Kickstart 3.1 + an AGA demo disk, read in place from P6_KICKSTART_A1200_ROM and P6_AGA_ADF (not copied, not hashed)",
             env_rom="P6_KICKSTART_A1200_ROM", env_disk="P6_AGA_ADF", warmup=2500, units=3000),
]


def scenario_by_id(identifier):
    for scenario in SCENARIOS:
        if scenario.id == identifier:
            return scenario
    raise KeyError(identifier)


# --- runners --------------------------------------------------------------------------------------------------------------------------------------------------

def run_process(command, *, env=None, cwd=None, timeout=3600, stdout_path=None):
    """Runs a child, waits for it with wait4 so that its peak resident set comes back with it, and returns (returncode, stdout text, peak RSS in bytes)."""
    stdout_path = Path(stdout_path or (WORK / "stdout.tmp"))
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as out:
        process = subprocess.Popen([str(c) for c in command], stdout=out, stderr=subprocess.STDOUT, env=env, cwd=cwd)
        deadline = time.monotonic() + timeout          # a wall-clock deadline for the benchmark harness itself, never for the emulation
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                process.kill()
                os.wait4(process.pid, 0)
                raise TimeoutError(f"{command[0]} did not finish in {timeout} s")
            time.sleep(0.25)
    process.returncode = os.waitstatus_to_exitcode(status)
    text = stdout_path.read_bytes().decode("utf-8", "replace")
    peak = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    return process.returncode, text, int(peak)


class Runner:
    """A way of running a scenario. `run` returns the raw record: the arrays in nanoseconds plus the facts the probe printed."""
    id = ""
    layer = ""
    description = ""

    def available(self, scenario):
        return True

    def run(self, scenario, rep, units, warmup, state_rounds):
        raise NotImplementedError


def json_result(path):
    return json.loads(Path(path).read_text())


class NativeRunner(Runner):
    layer = "native"

    def __init__(self, id, render, description):
        self.id, self.render, self.description = id, render, description

    def available(self, scenario):
        return (NATIVE_TREE / "p6_bench_probe").is_file()

    def run(self, scenario, rep, units, warmup, state_rounds):
        command = [NATIVE_TREE / "p6_bench_probe", scenario.rom(), "--machine", scenario.machine, "--warmup", warmup, "--units", units, "--render", self.render,
                   "--state-rounds", state_rounds if self.render else 0, "--alloc-units", 200 if self.render == 3 else 0]
        if scenario.ntsc():
            command.append("--ntsc")
        if scenario.disk():
            command += ["--disk", scenario.disk()]
        code, text, peak = run_process(command, stdout_path=WORK / f"{self.id}.out")
        if code:
            raise RuntimeError(f"{self.id}: exit {code}: {text[-400:]}")
        data = json.loads(text)
        cadence = data["cadence"]
        return {"unit_wall_ns": data.pop("unit_wall_ns"), "unit_cpu_ns": data.pop("unit_cpu_ns"), "state_save_ns": data.pop("state_save_ns"), "state_load_ns": data.pop("state_load_ns"),
                "first_unit_after_load_ns": data.pop("first_unit_after_load_ns"), "peak_rss_bytes": peak, "cadence": cadence, "master_ticks_at_window_end": data["master_ticks_at_window_end"],
                "unit_index_at_window_end": data["unit_index_at_window_end"], "facts": data}


class LibretroRunner(Runner):
    id, layer = "libretro", "libretro"
    description = "retro_run of the core through a frontend that only counts the picture and the audio (tests/libretro-host/host.cpp --bench)"

    def available(self, scenario):
        return (NATIVE_TREE / "p6_libretro_host").is_file() and (NATIVE_TREE / "puae6_libretro.dylib").is_file()

    def run(self, scenario, rep, units, warmup, state_rounds):
        system = WORK / f"system-{scenario.id}"
        system.mkdir(parents=True, exist_ok=True)
        link = system / KICKSTART_NAME[scenario.machine]
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(scenario.rom().resolve())              # a link, never a copy: the core reads the Kickstart where it is
        out = WORK / f"libretro-{scenario.id}.json"
        command = [NATIVE_TREE / "p6_libretro_host", NATIVE_TREE / "puae6_libretro.dylib", "--system", system, "--runs", warmup + units, "--bench", out, "--bench-state", state_rounds,
                   "--set", f"puae6_machine={LIBRETRO_MACHINE[scenario.machine]}", "--set", f"puae6_video_standard={'NTSC' if scenario.ntsc() else 'PAL'}"]
        if scenario.disk():
            command += ["--content", scenario.disk()]
        code, text, peak = run_process(command, stdout_path=WORK / "libretro.out")
        if code:
            raise RuntimeError(f"libretro: exit {code}: {text[-600:]}")
        data = json_result(out)
        ticks = unit = None
        for line in text.splitlines():
            if line.startswith("run ") and f" unit={warmup + units} " in line + " ":
                fields = dict(item.split("=", 1) for item in line.split() if "=" in item)
                ticks, unit = int(fields["ticks"]), int(fields["unit"])
        return {"unit_wall_ns": data["run_wall_ns"][warmup:], "unit_cpu_ns": data["run_cpu_ns"][warmup:], "state_save_ns": data["state_save_ns"], "state_load_ns": data["state_load_ns"],
                "first_unit_after_load_ns": data["first_run_after_load_ns"], "peak_rss_bytes": peak, "master_ticks_at_window_end": ticks, "unit_index_at_window_end": unit,
                "facts": {"state_bytes": data["state_bytes"], "pictures": data["pictures"], "audio_frames": data["audio_frames"], "warmup_median_ns": describe(data["run_wall_ns"][:warmup]).get("p50")}}


def dotnet_env():
    return {**os.environ, "DOTNET_CLI_HOME": str(WATERBOX / "dotnet-home"), "NUGET_PACKAGES": str(WATERBOX / "nuget"), "DOTNET_ROLL_FORWARD": "Major", "DOTNET_NOLOGO": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}


class WaterboxGuestRunner(Runner):
    layer = "waterbox-guest"

    def __init__(self, id, runtime_receipt, evict, description):
        self.id, self.runtime_receipt, self.evict, self.description = id, runtime_receipt, evict, description

    def available(self, scenario):
        return SMOKE_DLL.is_file() and GUEST_RECEIPT.is_file() and self.runtime_receipt().is_file()

    def run(self, scenario, rep, units, warmup, state_rounds):
        out = WORK / f"{self.id}.json"
        command = ["dotnet", SMOKE_DLL, "--puae6-bench", "--runtime", self.runtime_receipt(), "--guest", GUEST_RECEIPT, "--manifest", MEMORY_MANIFEST, "--firmware", scenario.rom(),
                   "--units", units, "--warmup", warmup, "--state-rounds", state_rounds, "--machine", scenario.machine, "--evict", "1" if self.evict else "0", "--result", out]
        if scenario.ntsc():
            command.append("--ntsc")
        if scenario.disk():
            command += ["--media", scenario.disk()]
        code, text, peak = run_process(command, env=dotnet_env(), cwd=ROOT / "integration/bizhawk/smoke", stdout_path=WORK / f"{self.id}.out")
        if code:
            raise RuntimeError(f"{self.id}: exit {code}: {text[-600:]}")
        data = json_result(out)
        parts = {name: data.pop(name) for name in ("unit_enter_ns", "unit_guest_ns", "unit_copy_ns", "unit_exit_ns")}
        return {"unit_wall_ns": data.pop("unit_total_ns"), "unit_cpu_ns": data.pop("unit_cpu_ns"), "state_save_ns": data.pop("state_save_ns"), "state_load_ns": data.pop("state_load_ns"),
                "first_unit_after_load_ns": data.pop("first_unit_after_load_ns"), "parts": parts, "peak_rss_bytes": peak, "master_ticks_at_window_end": data["master_ticks_at_window_end"],
                "unit_index_at_window_end": data["unit_index_at_window_end"], "facts": data}


class WaterboxCoreRunner(Runner):
    layer = "waterbox-core"

    def __init__(self, id, directory, runtime_receipt, evict_off, description):
        self.id, self.directory, self.runtime_receipt, self.evict_off, self.description = id, directory, runtime_receipt, evict_off, description

    def available(self, scenario):
        return (self.directory / "Ffi.dll").is_file() and GUEST_RECEIPT.is_file() and self.runtime_receipt().is_file()

    def run(self, scenario, rep, units, warmup, state_rounds):
        runtime = json_result(self.runtime_receipt())["runtime_dylib"]["path"]
        host = WORK / f"ffi-{self.id}"                       # BizHawk dlopens the bare libwaterboxhost.dylib from the directory it runs in: a copy of the host with the runtime that this runner measures
        if not (host / "Ffi.dll").is_file() or sha256_of(host / "libwaterboxhost.dylib") != sha256_of(runtime) or sha256_of(host / "Ffi.dll") != sha256_of(self.directory / "Ffi.dll"):
            host.mkdir(parents=True, exist_ok=True)
            for item in self.directory.iterdir():
                if item.is_file():
                    shutil.copy2(item, host / item.name)
            shutil.copy2(runtime, host / "libwaterboxhost.dylib")
        guest = json_result(GUEST_RECEIPT)["guest"]
        out = WORK / f"{self.id}.json"
        command = ["dotnet", host / "Ffi.dll", "bench", "--runtime", "libwaterboxhost.dylib", "--guest", guest["path"], "--sha", guest["sha256"], "--firmware", scenario.rom(), "--units", units,
                   "--warmup", warmup, "--state-rounds", state_rounds, "--machine", scenario.machine, "--out", out]
        if scenario.ntsc():
            command += ["--ntsc", "1"]
        if scenario.disk():
            command += ["--adf", scenario.disk()]
        env = dotnet_env()
        env["NUGET_PACKAGES"] = str(ROOT / "build/bizhawk-core/nuget")
        if self.evict_off:
            env["P6_EVICT"] = "0"
        code, text, peak = run_process(command, env=env, cwd=host, stdout_path=WORK / f"{self.id}.out")
        if code:
            raise RuntimeError(f"{self.id}: exit {code}: {text[-600:]}")
        data = json_result(out)
        return {"unit_wall_ns": data.pop("unit_wall_ns"), "unit_cpu_ns": data.pop("unit_cpu_ns"), "state_save_ns": data.pop("state_save_ns"), "state_load_ns": data.pop("state_load_ns"),
                "first_unit_after_load_ns": data.pop("first_unit_after_load_ns"), "peak_rss_bytes": peak, "master_ticks_at_window_end": data["master_ticks_at_window_end"],
                "unit_index_at_window_end": data["unit_index_at_window_end"], "facts": data}


def debug_runtime():
    return DEBUG_RUNTIME_RECEIPT


def release_runtime():
    return RELEASE_RUNTIME_RECEIPT


RUNNERS = [
    NativeRunner("native", 3, "p6_run_frame with picture and audio, Release -O2, one process per run"),
    NativeRunner("native-norender", 0, "p6_run_frame with no picture and no audio: the engine alone"),
    LibretroRunner(),
    WaterboxGuestRunner("waterbox-guest-debug-runtime", debug_runtime, True, "puae6.wbx in a Waterbox sandbox of the Debug lane of the runtime (a DEBUG build of libwaterboxhost: assertions, eviction forced on)"),
    WaterboxGuestRunner("waterbox-guest-release-runtime", release_runtime, False, "the same guest on the Release runtime (the default since T34a) with eviction off, what BizHawk ships and sets"),
    WaterboxGuestRunner("waterbox-guest-release-runtime-evict", release_runtime, True, "Release runtime with the runtime's default eviction on (a sandbox mapped and unmapped around every call)"),
    WaterboxCoreRunner("waterbox-core-release", FFI_RELEASE, release_runtime, True, "Puae6Core.FrameAdvance as BizHawk calls it: Release managed code, Release runtime"),
    WaterboxCoreRunner("waterbox-core-debug", FFI_DEBUG, debug_runtime, False, "Debug managed code (the host the FFI tests use) and the Debug lane of the runtime"),
]
# Which runners run for which scenarios, and how many units they get (the full protocol: 300 warmup, 10000 units, five repetitions).
FULL_ONLY_PRIMARY = {"waterbox-guest-release-runtime-evict", "waterbox-core-debug"}
WATERBOX_SCENARIOS = {"a500-ocs-busy-pal", "a600-ecs-copper-ntsc", "a1200-aga-planes8-pal", "a500-ks13-game-pal", "a1200-ks31-demo-pal"}
SLOW_RUNNERS = {"waterbox-guest-debug-runtime", "waterbox-guest-release-runtime-evict", "waterbox-core-debug"}


def plan_matrix(scenarios, quick):
    """[(scenario, runner, units, warmup)] in the order of one repetition."""
    matrix = []
    for scenario in scenarios:
        if not scenario.available():
            continue
        for runner in RUNNERS:
            if runner.layer.startswith("waterbox") and scenario.id not in WATERBOX_SCENARIOS:
                continue
            if runner.id in FULL_ONLY_PRIMARY and not scenario.primary:
                continue
            if not runner.available(scenario):
                continue
            units, warmup = scenario.units, scenario.warmup
            if quick:
                units, warmup = min(units, 400), min(warmup, 100)
            matrix.append((scenario, runner, units, warmup))
    return matrix


def write_raw(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as stream:       # no name and no mtime: the bytes depend on the data only
            stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True).encode())


def read_raw(path):
    with gzip.open(path, "rb") as stream:
        return json.loads(stream.read())


def wait_for_quiet(max_load1, timeout):
    """Waits (up to `timeout` seconds of this tool's own time) for the 1-minute load average to drop under `max_load1`; returns what it found. The wait is the harness's, never the emulation's."""
    deadline = time.monotonic() + timeout
    while True:
        load1 = os.getloadavg()[0]
        if load1 <= max_load1 or time.monotonic() > deadline:
            return load1
        time.sleep(15)


AUTHORIZED = {      # the files the owner authorized for opt-in runs (memory: reference-authorized-kickstart-downloads); used in place through these variables, never copied, hashed or recorded
    "P6_KICKSTART_ROM": "Kickstart v1.3 r34.5 (1987)(Commodore)(A500-A1000-A2000-CDTV)[!].rom",
    "P6_GAME_ADF": "Speedball 2 - Brutal Deluxe.adf",
    "P6_KICKSTART_A1200_ROM": "Kickstart v3.1 r40.68 (1993)(Commodore)(A1200)[!].rom",
    "P6_AGA_ADF": "puae6-free/catabasis-cocoon/CocoonCatabasisD.adf",
}


def run_command(args):
    global BASELINES
    BASELINES = Path(args.root)
    if args.authorized_downloads:
        for variable, name in AUTHORIZED.items():
            candidate = Path.home() / "Downloads" / name
            if variable not in os.environ and candidate.is_file():
                os.environ[variable] = str(candidate)
    scenarios = [scenario_by_id(s) for s in args.scenario] if args.scenario else SCENARIOS
    matrix = plan_matrix(scenarios, args.quick)
    if args.runner:
        matrix = [m for m in matrix if m[1].id in args.runner]
    if not matrix:
        print("nothing to run (are the runners built? python3 tools/bench.py build)")
        return 2
    directory = BASELINES / args.id
    raw = directory / "raw"
    environment_path = directory / "environment.json"
    directory.mkdir(parents=True, exist_ok=True)
    if not environment_path.is_file():
        environment_path.write_text(json.dumps(capture_environment(), indent=2, ensure_ascii=False) + "\n")
    state_rounds = 5 if args.quick else args.state_rounds
    log = []
    for rep in range(1, args.reps + 1):
        for scenario, runner, units, warmup in matrix:               # interleaved: repetition 1 of everything, then 2: a drift of the machine hits every runner alike
            if rep > (3 if runner.id in SLOW_RUNNERS else args.reps):       # the runners that only contrast a configuration (Debug host, eviction on) run three times
                continue
            target = raw / f"{scenario.id}__{runner.id}__rep{rep}.json.gz"
            if target.is_file() and not args.force:
                continue
            waited = wait_for_quiet(args.max_load1, args.wait_quiet) if args.wait_quiet else os.getloadavg()[0]
            before = system_load()
            started = time.monotonic()
            attempts, crashes, result = 0, [], None
            while result is None and attempts < 3:
                attempts += 1
                try:
                    result = runner.run(scenario, rep, units, warmup, state_rounds)
                except (RuntimeError, TimeoutError) as error:
                    crashes.append(str(error)[-300:])
                    # T28a (closed): the macOS allocator can fault when a Waterbox host is created under memory pressure (SIGSEGV/SIGBUS in libsystem_malloc). It is the machine, not the benchmark: the signal is kept and the run is repeated.
                    if " exit -11" not in str(error) and " exit -10" not in str(error):
                        break
            if result is None:
                print(f"FAILED {scenario.id} {runner.id} rep{rep}: {crashes[-1][-200:]}")
                log.append({"scenario": scenario.id, "runner": runner.id, "rep": rep, "attempts": attempts, "errors": crashes})
                continue
            if crashes:
                print(f"  {scenario.id} {runner.id} rep{rep}: repeated after {len(crashes)} crash(es) of the host (T28a)")
            after = system_load()
            result.update({"scenario": scenario.id, "runner": runner.id, "layer": runner.layer, "rep": rep, "units": units, "warmup": warmup, "machine": scenario.machine, "standard": scenario.standard,
                           "load_before": before, "load_after": after, "load1_at_start": waited, "harness_seconds": round(time.monotonic() - started, 1), "attempts": attempts, "crashes_before_success": crashes})
            write_raw(target, result)
            wall = describe(result["unit_wall_ns"])
            print(f"{scenario.id:24s} {runner.id:34s} rep{rep} p50={ms(wall['p50']):7.3f} ms p95={ms(wall['p95']):7.3f} ms  load1={before['load'][0]:.1f}  [{result['harness_seconds']} s]", flush=True)
    if log:
        (directory / "failures.json").write_text(json.dumps(log, indent=2) + "\n")
    return 1 if log else 0


# --- summary --------------------------------------------------------------------------------------------------------------------------------------------------

def unit_ms_from_cadence(cadence):
    """The video unit in milliseconds from the engine's own cadence (num/den = units per second)."""
    num, den = cadence
    return 1000.0 * den / num if num else None


NOMINAL_UNIT_MS = {"pal": 20.0, "ntsc": 1000.0 / 60.0}


def run_summary(record):
    wall = describe(record["unit_wall_ns"])
    cpu = describe(record["unit_cpu_ns"])
    noisy = []
    if cpu.get("mean") and wall["mean"] / cpu["mean"] > NOISY_WAIT_RATIO:
        noisy.append(f"the thread waited ({wall['mean'] / cpu['mean']:.2f} x its CPU time)")
    summary = {"rep": record["rep"], "units": record["units"], "warmup": record["warmup"], "wall_ns": wall, "cpu_ns": cpu, "noisy": noisy, "peak_rss_bytes": record["peak_rss_bytes"],
               "master_ticks_at_window_end": record.get("master_ticks_at_window_end"), "unit_index_at_window_end": record.get("unit_index_at_window_end"),
               "load1_before": record["load_before"]["load"][0], "load1_after": record["load_after"]["load"][0], "attempts": record.get("attempts", 1)}
    if "parts" in record:
        summary["parts_p50_ns"] = {name: describe(values)["p50"] for name, values in record["parts"].items()}
    for key in ("state_save_ns", "state_load_ns", "first_unit_after_load_ns"):
        if record.get(key):
            summary[key] = describe(record[key])
    if record.get("cadence"):
        summary["cadence"] = record["cadence"]
    return summary


def aggregate(group):
    """The runs of one (scenario, runner): the median of the per-run medians, the dispersion between runs and the pool of all the samples for p95/p99."""
    medians = [run["wall_ns"]["p50"] for run in group]
    pooled = []
    for run in group:
        pooled.extend(run["_samples"])
    pooled_stats = describe(pooled)
    median_of_medians = percentile(sorted(medians), 50)
    mean = sum(medians) / len(medians)
    between = math.sqrt(sum((m - mean) ** 2 for m in medians) / len(medians)) / mean if mean else 0.0
    return {"runs": len(group), "noisy_runs": sum(1 for run in group if run["noisy"]), "median_of_run_medians_ns": median_of_medians, "run_median_range_ns": [min(medians), max(medians)],
            "between_run_cv": between, "host_crashes_retried": sum(run["attempts"] - 1 for run in group), "load1_range": [min(run["load1_before"] for run in group), max(run["load1_after"] for run in group)], "pooled": pooled_stats, "median_of_run_p95_ns": percentile(sorted(run["wall_ns"]["p95"] for run in group), 50),
            "cpu_median_of_run_medians_ns": percentile(sorted(run["cpu_ns"]["p50"] for run in group), 50)}


def median_field(group, key, field="p50"):
    values = [run[key][field] for run in group if key in run]
    return percentile(sorted(values), 50) if values else None


def summarize(identifier):
    directory = BASELINES / identifier
    records = [read_raw(path) for path in sorted((directory / "raw").glob("*.json.gz"))]
    if not records:
        raise SystemExit(f"{directory}/raw has no runs")
    groups = {}
    facts = {}
    for record in records:
        summary = run_summary(record)
        summary["_samples"] = record["unit_wall_ns"]
        groups.setdefault((record["scenario"], record["runner"]), []).append(summary)
        facts.setdefault((record["scenario"], record["runner"]), record)
    result = {"schema_version": 1, "baseline": identifier, "status": "ORIENTATIVE", "environment": json.loads((directory / "environment.json").read_text()), "scenarios": {}}
    for scenario in SCENARIOS:
        if not any(key[0] == scenario.id for key in groups):
            continue
        cadence = next((r["cadence"] for (s, rn), g in groups.items() if s == scenario.id for r in g if r.get("cadence")), None)
        unit_ms = unit_ms_from_cadence(cadence) if cadence else NOMINAL_UNIT_MS[scenario.standard]
        entry = {"summary": scenario.summary, "machine": scenario.machine, "standard": scenario.standard, "kind": scenario.kind, "unit_ms": unit_ms,
                 "unit_ms_nominal": NOMINAL_UNIT_MS[scenario.standard], "runners": {}, "ticks_equal_across_runners": None}
        ticks = {}
        for (s, runner_id), group in sorted(groups.items()):
            if s != scenario.id:
                continue
            stats = aggregate(group)
            record = facts[(s, runner_id)]
            p95 = stats["pooled"]["p95"]
            runner_summary = {"layer": record["layer"], "units": record["units"], "warmup": record["warmup"], "reps": len(group), **stats,
                              "p50_ms": ms(stats["median_of_run_medians_ns"]), "p95_ms": ms(p95), "p99_ms": ms(stats["pooled"]["p99"]),
                              "p50_unit_fraction": ms(stats["median_of_run_medians_ns"]) / unit_ms, "p95_unit_fraction": ms(p95) / unit_ms, "p95_headroom_x": unit_ms / ms(p95),
                              "meets_goal_p95_le_70_percent": ms(p95) <= GOAL_P95_FRACTION * unit_ms,
                              "parts_p50_ns": ({name: percentile(sorted(g["parts_p50_ns"][name] for g in group), 50) for name in group[0]["parts_p50_ns"]} if "parts_p50_ns" in group[0] else None),
                              "peak_rss_bytes_max": max(g["peak_rss_bytes"] for g in group)}
            for key in ("state_save_ns", "state_load_ns", "first_unit_after_load_ns"):
                if key in group[0]:
                    runner_summary[key.replace("_ns", "_p50_ms")] = ms(median_field(group, key))
                    runner_summary[key.replace("_ns", "_p95_ms")] = ms(median_field(group, key, "p95"))
            for run in group:
                run.pop("_samples", None)
            runner_summary["per_run"] = [{k: v for k, v in run.items() if k in ("rep", "wall_ns", "cpu_ns", "noisy", "load1_before", "load1_after", "peak_rss_bytes")} for run in group]
            ticks[runner_id] = {(g["master_ticks_at_window_end"], g["unit_index_at_window_end"]) for g in group}
            facts_in = record.get("facts", {})
            if record["layer"] == "native":
                runner_summary["state_bytes"] = facts_in.get("state", {}).get("bytes")
                runner_summary["state_bound_bytes"] = facts_in.get("state", {}).get("bound_bytes")
                runner_summary["alloc"] = facts_in.get("alloc")
                runner_summary["memory"] = facts_in.get("memory")
            elif record["layer"] == "libretro":
                runner_summary["serialize_size_bytes"] = facts_in.get("state_bytes")
            else:
                runner_summary["snapshot_bytes"] = facts_in.get("snapshot_bytes") or facts_in.get("state_bytes")
                for key in ("pages_after_seal", "pages_after_warmup", "pages_after_units", "heaps_after_units", "managed_allocated_bytes_in_window", "gc_collections_in_window", "managed_build",
                            "evict_blocks", "evict_blocks_forced_off", "core_create_ms", "layout"):
                    if key in facts_in:
                        runner_summary[key] = facts_in[key]
            entry["runners"][runner_id] = runner_summary
        by_index = {}
        for runner_id, pairs in ticks.items():
            for tick, index in pairs:
                by_index.setdefault(index, {}).setdefault(tick, []).append(runner_id)
        # The machine is the same whatever the layer: at a given unit index every runner of the scenario must report the same master tick.
        entry["window_end"] = {str(index): {str(tick): sorted(runners) for tick, runners in ticked.items()} for index, ticked in sorted(by_index.items(), key=lambda item: (item[0] is None, item[0]))}
        entry["ticks_equal_across_runners"] = bool(by_index) and all(index is not None and len(ticked) == 1 for index, ticked in by_index.items()) and all(None not in ticked for ticked in by_index.values())
        result["scenarios"][scenario.id] = entry
    result["cost_breakdown"] = breakdown(result)
    (directory / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    (directory / "summary.md").write_text(render_markdown(result))
    return result


def runner_p50(entry, runner_id):
    runner = entry["runners"].get(runner_id)
    return runner["p50_ms"] if runner else None


def breakdown(result):
    """Where a unit's time goes, per scenario, from the per-layer medians. Differences between layers are differences of medians of different processes: they are estimates, said so."""
    out = {}
    for scenario_id, entry in result["scenarios"].items():
        row = {"unit_ms": entry["unit_ms"]}
        engine, native, libretro = runner_p50(entry, "native-norender"), runner_p50(entry, "native"), runner_p50(entry, "libretro")
        row["engine_ms (native, render 0)"] = engine
        row["output_production_ms (native render 3 - render 0)"] = native - engine if native is not None and engine is not None else None
        row["libretro_adapter_ms (libretro - native)"] = libretro - native if libretro is not None and native is not None else None
        for rid in ("waterbox-guest-release-runtime", "waterbox-guest-debug-runtime"):
            guest = entry["runners"].get(rid)
            if guest:
                parts = {k: v / 1e6 for k, v in (guest.get("parts_p50_ns") or {}).items()}
                row[rid] = {"total_ms": guest["p50_ms"], "enter_ms": parts.get("unit_enter_ns"), "guest_call_ms": parts.get("unit_guest_ns"), "copy_ms": parts.get("unit_copy_ns"), "exit_ms": parts.get("unit_exit_ns"),
                            "guest_call_minus_native_ms": (parts["unit_guest_ns"] - native) if native is not None and parts.get("unit_guest_ns") is not None else None}
        core = runner_p50(entry, "waterbox-core-release")
        release = row.get("waterbox-guest-release-runtime")
        main_by_runner = {"native": "engine", "libretro": "engine"}
        if core is not None:
            row["waterbox_core_release_ms"] = core
            row["core_release_over_native_ms"] = core - native if native is not None else None
            # The wrapper is what the core does around the guest call: the input packet, Activate/Deactivate, the copy of the picture and of the PCM into BizHawk's arrays. The guest runner's own copy is
            # Debug-compiled C# (Smoke.csproj), so the core is compared with the guest CALL, not with the guest runner's total.
            if release and release.get("guest_call_ms") is not None:
                row["wrapper_ms (core release - guest call, release runtime)"] = core - release["guest_call_ms"]
            main_by_runner["waterbox-core-release"] = "engine (p6w_frame_advance inside the sandbox)"
        for rid, key in (("waterbox-guest-debug-runtime", "waterbox-guest-debug-runtime"), ("waterbox-guest-release-runtime", "waterbox-guest-release-runtime")):
            part = row.get(key)
            if part and part["guest_call_ms"] is not None:
                candidates = {"engine (p6w_frame_advance inside the sandbox)": part["guest_call_ms"], "entering and leaving the sandbox (runtime)": part["enter_ms"] + part["exit_ms"],
                              "copy of the picture (Debug-compiled harness)": part["copy_ms"]}
                main_by_runner[rid] = max(candidates, key=candidates.get)
        row["main_cost_by_runner"] = main_by_runner
        costs = {name: value for name, value in {
            "engine": row["engine_ms (native, render 0)"], "output production": row["output_production_ms (native render 3 - render 0)"], "libretro adapter": row["libretro_adapter_ms (libretro - native)"],
        }.items() if value is not None}
        row["main_cost"] = max(costs, key=costs.get) if costs else None
        out[scenario_id] = row
    return out


def render_markdown(result):
    lines = [f"# Baseline {result['baseline']}", "", f"Estado: **{result['status']}**. {result['environment']['measurement_status']}", ""]
    env = result["environment"]
    lines += [f"- Commit `{env['git']['commit'][:12]}`{' (con cambios sin commitear)' if env['git']['uncommitted_changes'] else ''}, {env['host']['cpu']} ({env['host']['performance_cores']}P+{env['host']['efficiency_cores']}E, "
              f"{env['host']['memory_bytes'] // 2**30} GiB), macOS {env['host']['os']}, {env['tools']['clang']}.",
              f"- Native: {env['tools']['native_flags'].get('CMAKE_BUILD_TYPE')} `{env['tools']['native_flags'].get('CMAKE_CXX_FLAGS_RELEASE')}`; guest Waterbox `-O2`; perfil `{env['profile']}`; "
              f"{env['engine']['patches']} parches, serie `{env['engine']['series_sha256'][:12]}`.", ""]
    for scenario_id, entry in result["scenarios"].items():
        lines += [f"## {scenario_id}", "", entry["summary"], "", f"Unidad de vídeo: {entry['unit_ms']:.3f} ms (cadencia del motor; nominal {entry['unit_ms_nominal']:.2f} ms). "
                  f"Mismo tick maestro al final de la ventana en todos los runners: **{entry['ticks_equal_across_runners']}**.", "",
                  "| Runner | reps | unidades | p50 ms | p95 ms | p99 ms | p95/unidad | margen p95 | dispersion entre runs | ruidosos |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for runner_id, r in entry["runners"].items():
            lines.append(f"| {runner_id} | {r['reps']} | {r['units']} | {r['p50_ms']:.3f} | {r['p95_ms']:.3f} | {r['p99_ms']:.3f} | {100 * r['p95_unit_fraction']:.1f} % | {r['p95_headroom_x']:.2f}x | "
                         f"{100 * r['between_run_cv']:.2f} % | {r['noisy_runs']}/{r['runs']} |")
        lines += ["", "Guardar/cargar (mediana de p50 de cada run):", "", "| Runner | bytes | save p50 ms | load p50 ms | 1.ª unidad tras load ms |", "| --- | --- | --- | --- | --- |"]
        for runner_id, r in entry["runners"].items():
            size = r.get("state_bytes") or r.get("snapshot_bytes") or r.get("serialize_size_bytes")
            if "state_save_p50_ms" in r:
                lines.append(f"| {runner_id} | {size} | {r['state_save_p50_ms']:.2f} | {r['state_load_p50_ms']:.2f} | {r['first_unit_after_load_p50_ms']:.3f} |")
        row = result["cost_breakdown"].get(scenario_id, {})
        lines += ["", "Desglose de una unidad (medianas de runs de procesos distintos: las diferencias son estimaciones):", "", "| Concepto | ms |", "| --- | --- |"]
        for name, value in row.items():
            if isinstance(value, (int, float)):
                lines.append(f"| {name} | {value:.3f} |")
            elif isinstance(value, dict) and name.startswith("waterbox-guest"):
                lines.append(f"| {name}: Activate / p6w_frame_advance / copia (harness Debug) / Deactivate | {value['enter_ms']:.3f} / {value['guest_call_ms']:.3f} / {value['copy_ms']:.3f} / {value['exit_ms']:.3f} |")
        lines += ["", "Coste principal por runner: " + "; ".join(f"{k}: {v}" for k, v in row.get("main_cost_by_runner", {}).items()) + ".", ""]
    return "\n".join(lines) + "\n"


def compare(first, second):
    """Reproducibility: two baselines taken at different times with the same tool, row by row. The relative difference of the medians of the run medians and of the pooled p95; a row is
    reproduced when both stay within the regression goal (5 %). Written to build/baselines/reproducibility-FIRST-vs-SECOND.json."""
    a = json.loads((BASELINES / first / "summary.json").read_text())
    b = json.loads((BASELINES / second / "summary.json").read_text())
    rows = {}
    for scenario_id, entry in a["scenarios"].items():
        other = b["scenarios"].get(scenario_id)
        if not other:
            continue
        for runner_id, r in entry["runners"].items():
            o = other["runners"].get(runner_id)
            if not o or o["units"] != r["units"]:
                continue
            p50 = (o["p50_ms"] - r["p50_ms"]) / r["p50_ms"]
            p95 = (o["p95_ms"] - r["p95_ms"]) / r["p95_ms"]
            row = {"first_p50_ms": r["p50_ms"], "second_p50_ms": o["p50_ms"], "p50_difference": p50, "first_p95_ms": r["p95_ms"], "second_p95_ms": o["p95_ms"], "p95_difference": p95,
                   "reproduced_within_goal": abs(p50) <= GOAL_TIME_REGRESSION and abs(p95) <= GOAL_TIME_REGRESSION}
            for key in ("state_save_p50_ms", "state_load_p50_ms"):
                if key in r and key in o:
                    row[key + "_difference"] = (o[key] - r[key]) / r[key]
            rows[f"{runner_id}/{scenario_id}"] = row
    document = {"schema_version": 1, "first": first, "second": second, "goal_time_regression": GOAL_TIME_REGRESSION, "rows": rows,
                "rows_reproduced": sum(1 for row in rows.values() if row["reproduced_within_goal"]), "rows_compared": len(rows),
                "rows_with_p50_within_goal": sum(1 for row in rows.values() if abs(row["p50_difference"]) <= GOAL_TIME_REGRESSION),
                "largest_p50_difference": max((abs(row["p50_difference"]) for row in rows.values()), default=0.0),
                "first_started": a["environment"]["date_utc"], "second_started": b["environment"]["date_utc"], "status": "ORIENTATIVE"}
    (BASELINES / f"reproducibility-{first}-vs-{second}.json").write_text(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return document


# --- budgets --------------------------------------------------------------------------------------------------------------------------------------------------

def write_budgets(identifier, compared_with=None):
    summary = json.loads((BASELINES / identifier / "summary.json").read_text())
    entries = {}
    for scenario_id, entry in summary["scenarios"].items():
        for runner_id, r in entry["runners"].items():
            if r["layer"] in ("native", "libretro", "waterbox-guest", "waterbox-core") and runner_id != "native-norender":
                key = f"{runner_id}/{scenario_id}"
                entries[key] = {
                    "runner": runner_id, "layer": r["layer"], "machine": entry["machine"], "standard": entry["standard"], "scenario": scenario_id, "kind": entry["kind"],
                    "unit_ms": round(entry["unit_ms"], 4),
                    "observed": {"p50_ms": round(r["p50_ms"], 3), "p95_ms": round(r["p95_ms"], 3), "p95_unit_fraction": round(r["p95_unit_fraction"], 4), "reps": r["reps"], "units_per_rep": r["units"],
                                 "between_run_cv": round(r["between_run_cv"], 4), "noisy_runs": r["noisy_runs"], "source": f"build/baselines/{identifier}/summary.json"},
                    "goal": {"p95_unit_fraction_max": GOAL_P95_FRACTION},
                    "goal_met_by_observation": r["meets_goal_p95_le_70_percent"],
                    "enforcement": "advisory",
                }
    document = {
        "schema_version": 1,
        "status": "ORIENTATIVE",
        "task": "T33",
        "baseline": f"build/baselines/{identifier}",
        "rule": "The goals below are the ones docs/04-validation.md proposed before any measurement. Ratifying a budget here means fixing the formula and the machine/profile it is compared on; it does NOT mean the "
                "numbers under `observed` are limits. They were taken on a Mac that someone was using (no dedicated runner, plan/runners.json), so no hard threshold derives from them: every entry is advisory "
                "until a quiet runner repeats the baseline with a small between-run dispersion.",
        "goals": {"p95_unit_fraction_max": GOAL_P95_FRACTION, "time_regression_investigate_above": GOAL_TIME_REGRESSION, "state_size_or_cost_regression_investigate_above": GOAL_STATE_REGRESSION,
                  "steady_state_allocation_per_unit": "none in the bridge and the adapters (measured: see `allocation`)", "note": "A regression sustained above these is investigated and approved by a person, with the run repeated to discard noise; a budget is never lowered in the change that breaks it."},
        "noise_policy": {"noisy_wait_ratio_above": NOISY_WAIT_RATIO, "load_average": "recorded with every run (build/baselines/*/raw), never used as a verdict by itself", "minimum_repetitions": 5,
                         "stable_means": "between_run_cv <= 0.02, no noisy run and at least five repetitions IN THIS BASELINE. It describes these repetitions only and does not promote an entry: enforcement stays advisory until a dedicated runner (plan/runners.json has none) repeats the baseline and a person approves the thresholds"},
        "entries": entries,
    }
    if compared_with:
        comparison = compare(identifier, compared_with)
        document["reproducibility"] = {"file": f"build/baselines/reproducibility-{identifier}-vs-{compared_with}.json", "second_baseline": f"build/baselines/{compared_with}",
                                       "rows_compared": comparison["rows_compared"], "rows_with_p50_within_goal": comparison["rows_with_p50_within_goal"], "rows_reproduced_p50_and_p95": comparison["rows_reproduced"],
                                       "largest_p50_difference": round(comparison["largest_p50_difference"], 4),
                                       "reading": "the medians of every compared row repeat within the 5 % goal between two sessions about 1.7 hours apart; the p95 of a row repeats unless a disturbance of the machine fell on one of the sessions"}
    for key, entry in entries.items():
        entry["stable"] = entry["observed"]["between_run_cv"] <= 0.02 and entry["observed"]["noisy_runs"] == 0 and entry["observed"]["reps"] >= 5
    BUDGETS.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return document


# --- building the runners -------------------------------------------------------------------------------------------------------------------------------------

def sh(command, **kwargs):
    print("+", " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def build_ffi_release():
    """The host of the FFI tests and the core, Release (the managed build BizHawk ships), built with the guest it belongs with (the SHA-256 of the receipt)."""
    import build_waterbox
    receipt = json.loads(GUEST_RECEIPT.read_text())["guest"]["sha256"]
    cache = Path.home() / ".nuget/packages"
    env = {**build_waterbox.managed_env(), "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget")}
    sh(["dotnet", "build", ROOT / "tests/ffi/Ffi.csproj", "-c", "Release", "--nologo", f"-p:Puae6GuestSha256={receipt}", f"-p:RestoreSources={cache}"], env=env, cwd=ROOT)


def build_command(args):
    jobs = str(min(args.jobs, 3))
    if not (NATIVE_TREE / "CMakeCache.txt").is_file():
        sh(["cmake", "-S", ROOT, "-B", NATIVE_TREE, "-G", "Ninja", "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_FLAGS_RELEASE=-O2",
            "-DCMAKE_CXX_FLAGS_RELEASE=-O2", "-DP6_SANITIZERS=OFF", "-DP6_UNIT_TRACE=OFF"])
    sh(["cmake", "--build", NATIVE_TREE, "--target", "p6_bench_probe", "puae6_libretro", "p6_libretro_host", "--", f"-j{jobs}"])
    import build_waterbox
    if not GUEST_RECEIPT.is_file():
        print("the Waterbox guest is not built: python3 tools/build_waterbox.py --puae6-run build/waterbox/boot.rom --units 60")
        return 1
    # The two lanes of the runtime (T34a): Release, the default (what BizHawk ships: no debug assertions, eviction switch honoured), and the Debug one (assertions, eviction forced).
    for profile in build_waterbox.RUNTIME_PROFILES:
        build_waterbox.ensure_runtime_lane(profile)
    WORK.mkdir(parents=True, exist_ok=True)
    build_waterbox.build_managed()
    build_waterbox.build_core()                                    # Debug host of the FFI tests (also compiles Bench.cs)
    build_ffi_release()
    print("built: native tree", NATIVE_TREE, "| release runtime", RELEASE_RUNTIME, "| debug lane", DEBUG_RUNTIME_RECEIPT, "| Ffi Release", FFI_RELEASE)
    return 0


def main():
    global BASELINES
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--jobs", type=int, default=3)
    run = sub.add_parser("run")
    run.add_argument("--id", required=True, help="baseline directory under build/baselines")
    run.add_argument("--root", default=str(BASELINES), help="where baselines live (a smoke run goes under build/)")
    run.add_argument("--reps", type=int, default=5)
    run.add_argument("--quick", action="store_true", help="400 units, 100 warmup, 5 state rounds: a smoke run, not a baseline")
    run.add_argument("--scenario", action="append", default=[])
    run.add_argument("--runner", action="append", default=[])
    run.add_argument("--state-rounds", type=int, default=20)
    run.add_argument("--wait-quiet", type=int, default=0, metavar="SECONDS", help="before every run wait up to this long for the 1-minute load average to drop")
    run.add_argument("--max-load1", type=float, default=NOISY_LOAD1)
    run.add_argument("--force", action="store_true")
    run.add_argument("--authorized-downloads", action="store_true", help="fill the opt-in variables (Kickstart 1.3 + Speedball 2, Kickstart 3.1 + an AGA demo) from ~/Downloads where the owner put them")
    summary = sub.add_parser("summarize")
    summary.add_argument("--id", required=True)
    summary.add_argument("--root", default=str(BASELINES))
    comparison = sub.add_parser("compare")
    comparison.add_argument("first")
    comparison.add_argument("second")
    comparison.add_argument("--root", default=str(BASELINES))
    budgets = sub.add_parser("budgets")
    budgets.add_argument("--id", required=True)
    budgets.add_argument("--root", default=str(BASELINES))
    budgets.add_argument("--compare-with", help="a second baseline of the same tool: the budgets record how well the two agree")
    args = parser.parse_args()
    if args.command == "build":
        return build_command(args)
    if args.command == "run":
        return run_command(args)
    BASELINES = Path(args.root)
    if args.command == "summarize":
        summarize(args.id)
        return 0
    if args.command == "compare":
        document = compare(args.first, args.second)
        print(f"{document['rows_reproduced']}/{document['rows_compared']} rows reproduced (p50 and p95) within {100 * GOAL_TIME_REGRESSION:.0f} %; p50 alone {document['rows_with_p50_within_goal']}/{document['rows_compared']}, largest p50 difference {100 * document['largest_p50_difference']:.2f} %")
        return 0
    write_budgets(args.id, args.compare_with)
    return 0


if __name__ == "__main__":
    sys.exit(main())
