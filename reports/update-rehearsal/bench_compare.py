#!/usr/bin/env python3
"""T38: pin against candidate from two logs of `tools/bench.py run` (one line per repetition: scenario, runner, p50, p95, load1).

    bench_compare.py PIN.log CANDIDATE.log > bench-comparison.md

Orientative only (docs/04-validation.md): the machine was in use, the load average of every sample is printed, no threshold is derived and nothing
here approves or rejects a performance budget.
"""
import re
import statistics
import sys

LINE = re.compile(r"^(\S+)\s+(native|native-norender|libretro)\s+rep(\d+) p50=\s*([\d.]+) ms p95=\s*([\d.]+) ms\s+load1=([\d.]+)")


def read(path):
    data = {}
    for line in open(path, encoding="utf-8"):
        match = LINE.match(line)
        if match:
            scenario, runner, rep, p50, p95, load = match.groups()
            data.setdefault((scenario, runner), []).append((float(p50), float(p95), float(load)))
    return data


def main():
    pin, candidate = read(sys.argv[1]), read(sys.argv[2])
    print("| scenario | runner | pin p50 ms | candidate p50 ms | change | pin p95 | candidate p95 | load1 pin/cand (max) |")
    print("|---|---|---|---|---|---|---|---|")
    ratios = []
    for key in sorted(pin):
        if key not in candidate:
            continue
        a, b = pin[key], candidate[key]
        a50, b50 = statistics.median(x[0] for x in a), statistics.median(x[0] for x in b)
        a95, b95 = statistics.median(x[1] for x in a), statistics.median(x[1] for x in b)
        change = (b50 / a50 - 1) * 100
        if key[1] == "native":
            ratios.append(change)
        print(f"| {key[0]} | {key[1]} | {a50:.3f} | {b50:.3f} | {change:+.1f} % | {a95:.3f} | {b95:.3f} | {max(x[2] for x in a):.1f} / {max(x[2] for x in b):.1f} |")
    if ratios:
        print()
        print(f"native runner: median change of p50 over the scenarios {statistics.median(ratios):+.1f} %, range {min(ratios):+.1f} % .. {max(ratios):+.1f} % ({len(ratios)} scenarios, 3 repetitions each)")


if __name__ == "__main__":
    main()
