"""Compare current T35c timings on stdout; no historical result files.

python3 -m tests.simd.frontend_statistics [--row ROW]
Positive CPU reduction means faster; positive state cost change means slower.
"""
import argparse
import json
import statistics

from tools.bench import describe
from tests.simd import frontend_matrix as matrix
from tests.simd.frontend_rgb565 import WORKLOADS


def compare(runs):
    """One workload, five paired repetitions; trim only the declared warmup."""
    variants = {}
    for variant in ("current", "candidate"):
        samples = sorted((r for r in runs if r["variant"] == variant), key=lambda r: r["repeat"])
        if [r["repeat"] for r in samples] != list(range(5)):
            raise ValueError(f"{variant}: exactly five distinct repetitions required")
        cpu, wall, medians = [], [], []
        for sample in samples:
            c = sample["raw"]["run_cpu_ns"][sample["warmup"]:]
            w = sample["raw"]["run_wall_ns"][sample["warmup"]:]
            if len(c) != sample["units"] or len(w) != sample["units"] or any(n <= 0 for n in c + w):
                raise ValueError("incomplete or invalid timing samples")
            medians.append(statistics.median(c))
            cpu.extend(c)
            wall.extend(w)
        variants[variant] = {"median_cpu_ns": statistics.median(medians), "cpu_medians_ns": medians,
                             "repetition_dispersion": describe(medians), "cpu": describe(cpu), "wall": describe(wall)}
        for field in ("state_save_ns", "state_load_ns"):
            values = [n for sample in samples for n in sample["raw"][field]]
            if not values or any(n <= 0 for n in values):
                raise ValueError(f"missing or invalid {field}")
            variants[variant][field] = describe(values)
    before, after = variants["current"], variants["candidate"]
    pairs = [100 * (1 - b / a) for a, b in zip(before["cpu_medians_ns"], after["cpu_medians_ns"])]
    return {"variants": variants, "cpu_reduction_percent": 100 * (1 - after["median_cpu_ns"] / before["median_cpu_ns"]),
            "paired_reductions_percent": pairs, "paired_wins": sum(p > 0 for p in pairs),
            "state_p95_cost_percent": {field: 100 * (after[field]["p95"] / before[field]["p95"] - 1)
                                       for field in ("state_save_ns", "state_load_ns")}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row", choices=matrix.MEASURED, action="append")
    args = parser.parse_args()
    for row in args.row or matrix.MEASURED:
        data = json.loads((matrix.directory(row) / "opt/measurements.json").read_text())
        print(row)
        for scenario in WORKLOADS:
            result = compare([r for r in data["runs"] if r["scenario"] == scenario.id])
            before, after = (result["variants"][v] for v in ("current", "candidate"))
            state = result["state_p95_cost_percent"]
            print(f"{scenario.id}: CPU {before['median_cpu_ns']/1e6:.3f}->{after['median_cpu_ns']/1e6:.3f} ms, "
                  f"reduction {result['cpu_reduction_percent']:+.2f}% ({result['paired_wins']}/5 pairs); "
                  f"wall p95 {before['wall']['p95']/1e6:.3f}->{after['wall']['p95']/1e6:.3f} ms; "
                  f"state p95 cost save/load {state['state_save_ns']:+.2f}%/{state['state_load_ns']:+.2f}%")
