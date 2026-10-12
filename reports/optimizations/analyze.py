#!/usr/bin/env python3
"""T34: before/after of Puae6Core.FrameAdvance from the raw files tools/bench.py wrote (waterbox-core-release, Release managed code, Release runtime with eviction off).

    python3 reports/optimizations/analyze.py T34f [T34]      prints the table of reports/optimizations/T34.md for each prefix (T34f-before / T34f-after ...)

Per scenario: the median of the five medians of a unit, the median of the five p95, the CPU time of the thread, the managed bytes allocated and the GC collections per run, the load
average each run started under, and in how many of the five interleaved pairs the "after" median is lower than the "before" one.
"""
from pathlib import Path
import glob
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402

SCENARIOS = ("a500-ocs-busy-pal", "a600-ecs-copper-ntsc", "a1200-aga-planes8-pal")


def records(prefix, variant, scenario):
    files = sorted(glob.glob(str(ROOT / f"reports/optimizations/{prefix}-{variant}/raw/{scenario}__waterbox-core-release__rep*.json.gz")))
    return [bench.read_raw(Path(f)) for f in files]


def summary(runs):
    medians = [bench.describe(r["unit_wall_ns"])["p50"] / 1e6 for r in runs]
    p95s = [bench.describe(r["unit_wall_ns"])["p95"] / 1e6 for r in runs]
    return {"medians": medians, "p50": statistics.median(medians), "p95": statistics.median(p95s),        # median of the five p95: one run disturbed by the machine does not move it
            "cpu_p50": statistics.median(bench.describe(r["unit_cpu_ns"])["p50"] / 1e6 for r in runs),
            "alloc_per_unit": [r["facts"]["managed_allocated_bytes_in_window"] / r["facts"]["units"] for r in runs],
            "gc": [sum(r["facts"]["gc_collections_in_window"]) for r in runs],
            "ticks": sorted({r["master_ticks_at_window_end"] for r in runs}), "units": sorted({r["unit_index_at_window_end"] for r in runs})}


def main(prefixes):
    for prefix in prefixes:
        print(f"## {prefix}")
        print("| scenario | before p50 | after p50 | delta p50 | before p95 (median of 5) | after p95 (median of 5) | after < before | alloc B/unit before -> after | GC collections in the 5 runs, before -> after | same end tick |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for scenario in SCENARIOS:
            before, after = summary(records(prefix, "before", scenario)), summary(records(prefix, "after", scenario))
            pairs = sum(a < b for a, b in zip(after["medians"], before["medians"]))
            delta = after["p50"] - before["p50"]
            print(f"| {scenario} | {before['p50']:.3f} ms | {after['p50']:.3f} ms | {delta:+.3f} ms ({100 * delta / before['p50']:+.1f} %) | {before['p95']:.3f} | {after['p95']:.3f} | {pairs}/5 | "
                  f"{statistics.median(before['alloc_per_unit']):.0f} -> {statistics.median(after['alloc_per_unit']):.0f} | {sum(before['gc'])} -> {sum(after['gc'])} | "
                  f"{'yes' if before['ticks'] == after['ticks'] and before['units'] == after['units'] else 'NO'} |")
        loads = []
        for variant in ("before", "after"):
            for scenario in SCENARIOS:
                for path in sorted(glob.glob(str(ROOT / f"reports/optimizations/{prefix}-{variant}/raw/{scenario}__waterbox-core-release__rep*.json.gz"))):
                    sample = bench.read_raw(Path(path))
                    loads.append(sample.get("load1_at_start"))
        shown = [x for x in loads if x is not None]
        if shown:
            print(f"\nload average (1 min) at the start of the {len(shown)} runs: min {min(shown):.1f}, median {statistics.median(shown):.1f}, max {max(shown):.1f}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:] or ["T34f"])
