"""T35c full-frontend rows, with frozen offline containers and isolated codegen."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from tests.portability.rows import ROWS
from tests.simd import matrix

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/simd/frontend"
MEASURED = ("macos-arm64-appleclang", "linux-arm64-clang", "linux-arm64-gcc")


def directory(row):
    return BUILD / row / ("experiment" if ROWS[row]["kind"] == "docker" else "")


def execute(row, action, sanitizers=False):
    spec = ROWS[row]
    target = BUILD / row
    target.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", "-m", "tests.simd.frontend_rgb565", "--row", row, "--work", str(directory(row)), "--action", action]
    if sanitizers:
        command += ["--sanitizers"]
    if spec["kind"] == "docker":
        image = matrix.IMAGES[spec["platform"]]
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
        if info["Os"] != "linux" or info["Architecture"] != spec["platform"].split("/")[1]:
            raise ValueError("wrong container architecture")
        command[0], command[command.index("--work") + 1] = "python3", "/src/build/experiment"
        command = ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform", spec["platform"], "--user", f"{os.getuid()}:{os.getgid()}",
                   "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{ROOT}:/src:ro", "-v", f"{target}:/src/build", "-w", "/src", image, *command]
    result = subprocess.run(command, cwd=ROOT, timeout=14400)
    if result.returncode:
        raise RuntimeError(f"frontend {row}/{action}: exit {result.returncode}")


def verify(sanitizers=False):
    results = {}
    for row in ROWS:
        execute(row, "verify", sanitizers)
        results[row] = json.loads((directory(row) / ("san" if sanitizers else "opt") / "verification.json").read_text())
    reference = results["macos-arm64-appleclang"]["cases"]
    for row, result in results.items():
        if set(result["cases"]) != set(reference):
            raise AssertionError(f"incomplete frontend corpus: {row}")
        for name, case in result["cases"].items():
            if case["runs"] != reference[name]["runs"]:
                raise AssertionError(f"frontend pixels/PCM/ticks differ across rows: {row} {name}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "measure"))
    parser.add_argument("--row", choices=tuple(ROWS), action="append")
    parser.add_argument("--sanitizers", action="store_true")
    args = parser.parse_args()
    if args.action == "verify" and not args.row:
        verify(args.sanitizers)
    else:
        for row in args.row or (MEASURED if args.action == "measure" else tuple(ROWS)):
            for action in (("build", "verify") if args.action == "prepare" else (args.action,)):
                execute(row, action, args.sanitizers)
