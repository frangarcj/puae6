"""Execute the T35a experiment in the five declared rows; containers use frozen local IDs, never pull."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.portability.rows import ROWS
from tests.simd import evaluate

BUILD = ROOT / "build/simd/matrix"
IMAGES = {"linux/arm64": "sha256:15413fec94f23773ff6ca066aff24e950a5aca006aeab30bf416188924818d8b",
          "linux/amd64": "sha256:8eda4b8c0792303c3d407747fdf7cd91b1c9739feb69cdecaa406c4407271921"}


def command(argv, timeout=180):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"T35a execution failed ({p.returncode}): {p.stdout[-1000:]}\n{p.stderr[-3000:]}")
    return p.stdout


def run(modes=("opt", "fallback", "san")):
    evidence = {"rows": {}, "modes": list(modes), "images": IMAGES, "timings_advisory": True}
    expected_digest = None
    for row, spec in ROWS.items():
        directory = BUILD / row
        directory.mkdir(parents=True, exist_ok=True)
        records = {}
        for mode in modes:
            if spec["kind"] == "native":
                record = evaluate.evaluate(directory, mode, row)
            else:
                image = IMAGES[spec["platform"]]
                info = json.loads(command(["docker", "image", "inspect", image]))[0]
                if info["Os"] != "linux" or info["Architecture"] != spec["platform"].split("/")[1]:
                    raise ValueError(f"wrong container architecture: {row}")
                target = f"{row}-{mode}.json"
                command(["docker", "run", "--rm", "--pull=never", "--network=none", "--platform", spec["platform"],
                         "--user", f"{os.getuid()}:{os.getgid()}", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "ASAN_OPTIONS=detect_leaks=0",
                         "-v", f"{ROOT}:/src:ro", "-v", f"{directory}:/out", "-w", "/src", image,
                         "python3", "tests/simd/evaluate.py", "--row", row, "--mode", mode, "--build-dir", "/out", "--output", f"/out/{target}"])
                record = json.loads((directory / target).read_text())
            isa = record["environment"]["isa"]
            if ("arm64" if isa == "aarch64" else isa) != spec["isa"]:
                raise ValueError(f"the executable did not run on the row's ISA: {row}: {isa}")
            if record["verified_rgb_values"] != 1 << 24 or record["forced_fallback"] != "scalar":
                raise ValueError(f"incomplete comparison: {row}/{mode}")
            backend = "neon" if spec["isa"] == "arm64" and mode != "fallback" else "scalar"
            if record["backend"] != backend:
                raise ValueError(f"wrong dispatch: {row}/{mode}")
            if expected_digest is None:
                expected_digest = record["rgb_digest"]
            if record["rgb_digest"] != expected_digest:
                raise ValueError(f"RGB differs between rows: {row}/{mode}")
            if any(evaluate.digest(ROOT / path) != sha for path, sha in record["sources"].items()):
                raise ValueError(f"stale sources: {row}/{mode}")
            records[mode] = record
            print(f"T35a {row}/{mode}: equal ({backend})", flush=True)
        evidence["rows"][row] = {"native_hardware": spec["native_hardware"], "execution": spec["execution"], "results": records}
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("opt", "fallback", "san"), action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(tuple(args.mode) if args.mode else ("opt", "fallback", "san"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
