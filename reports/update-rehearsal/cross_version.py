#!/usr/bin/env python3
"""T38: the corpus of T32 (28 scenarios on three machines) run on the PINNED build and on the CANDIDATE build, compared boundary by boundary.

    cross_version.py OLD_ROOT NEW_ROOT OUT.json [--units N]

OLD_ROOT and NEW_ROOT are project trees that already hold build/state-opt/p6_state_probe (tests.reference.builds.product_probe()).  The same ROMs,
media and inputs go through both; tools/compare_runs.py says where each scenario first differs (boundary, keys, subsystems).  A difference between two upstream
versions is NOT a failure: it is the list of things a person has to classify (upstream bug fixed or behaviour changed on purpose, regression of the port, unknown).
Nothing is written but OUT.json; the probes are run with an empty environment.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import compare_runs as cr  # noqa: E402
from tests.reference import corpus_run  # noqa: E402
from tests.scenarios.corpus import corpus  # noqa: E402


def run(probe, rom, args):
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}
    result = subprocess.run([str(probe), str(rom), *map(str, args), "--every", "1"], capture_output=True, text=True, env=env, timeout=3600)
    return result.returncode, result.stdout, result.stderr[-300:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("out")
    options = parser.parse_args()
    probes = {"old": Path(options.old) / "build/state-opt/p6_state_probe", "new": Path(options.new) / "build/state-opt/p6_state_probe"}
    for path in probes.values():
        if not path.is_file():
            sys.exit(f"missing {path}")
    results = {}
    with tempfile.TemporaryDirectory(prefix="p6-crossver-") as tmp:
        directory = Path(tmp)
        assets = corpus_run.Assets(directory)
        roms = {}
        for name, text in corpus.programs().items():
            roms[name] = directory / f"{name}.rom"
            roms[name].write_bytes(corpus.build_rom(text))
        for entry in corpus.ENTRIES:
            arguments = corpus_run.probe_arguments(entry, assets)
            runs, status = {}, {}
            for side, probe in probes.items():
                code, out, err = run(probe, roms[entry.program], arguments)
                status[side] = code
                if code == 0:
                    runs[side] = cr.parse_probe(out)
                else:
                    status[side + "_stderr"] = err
            if len(runs) == 2:
                report = cr.compare(runs["old"], runs["new"])
                first = {}
                for (unit, key), value in sorted(runs["old"].values.items()):
                    if runs["new"].values.get((unit, key)) != value and key not in first:
                        first[key] = unit
                results[entry.id] = {"machine": entry.machine, "units": entry.units, "equal": report["equal"], "first_unit_by_key": first,
                                     "first_divergent_boundary": report.get("first_divergent_boundary"),
                                     "differing_subsystems": report.get("differing_subsystems"),
                                     "differing_keys": sorted(report.get("differing_keys", []))[:12] if "differing_keys" in report else None}
            else:
                results[entry.id] = {"machine": entry.machine, "error": status}
            print(entry.id, results[entry.id].get("equal"), results[entry.id].get("first_divergent_boundary"), results[entry.id].get("differing_subsystems"), flush=True)
    Path(options.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
