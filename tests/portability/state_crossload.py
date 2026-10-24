#!/usr/bin/env python3
"""Saves native states with one build and tries to load them with another (T25: states between rows are NOT promised, this measures what happens).

    state_crossload.py save --probe P --corpus DIR --states DIR --tag TAG
    state_crossload.py load --probe P --corpus DIR --states DIR --tag TAG --out FILE

`save` runs each case of STATE_CASES for its units, saves the state through the public ABI (`--public-save-at`) into DIR/TAG/CASE.state and
writes DIR/TAG/CASE.json with its size, SHA-256 and the canonical digests of the machine at that unit. `load` takes every other tag in DIR,
runs the same case for the same number of units on the loading build, loads the foreign state (`--public-load-at`) and records the code and
message of the load and, when it was accepted, whether the digests after the load are those of the machine that saved it.
Standard library only: it runs inside the containers of tests/portability/Dockerfile.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

# case id -> (rom, probe arguments, units); the ROMs are those of tests/portability/corpus.py
STATE_CASES = {
    "a500-vsync": ("vsync", [], 4),
    "a500-copper_irq-ntsc": ("copper_irq", ["--ntsc"], 4),
    "a1200-aga-bars24": ("aga-bars24-pal", ["--machine", "a1200"], 4),
    "audio-tone_all": ("audio-tone_all", [], 4),
}
DIGEST = re.compile(r"^digest (\d+) (\w+) ([0-9a-f]{64}) (\d+)$")
SAVE = re.compile(r"^save (\d+) (\d+) ([0-9a-f]{64}) bound=(\d+)$")
LOAD = re.compile(r"^load (\d+) (-?\d+) ?(.*)$")


def probe(executable, rom, arguments, units, extra):
    result = subprocess.run([executable, str(rom), "--units", str(units), "--every", str(units), *arguments, *extra],
                            capture_output=True, text=True, timeout=1800)
    return result.returncode, result.stdout, result.stderr


def final_digests(lines, unit, after_load):
    """The digests of `unit`: the ones printed outside a load, or the block after 'after-load'."""
    inside, out = False, {}
    for line in lines:
        if line.startswith("after-load "):
            inside = True
            out = {} if after_load else out
        elif m := DIGEST.match(line):
            if int(m[1]) == unit and inside == after_load:
                out[m[2]] = m[3]
    return out


def save(args):
    directory = Path(args.states) / args.tag
    directory.mkdir(parents=True, exist_ok=True)
    for case, (rom, arguments, units) in STATE_CASES.items():
        path = directory / f"{case}.state"
        code, out, err = probe(args.probe, Path(args.corpus) / f"{rom}.rom", arguments, units, ["--public-save-at", str(units), str(path)])
        lines = out.splitlines()
        saved = next((SAVE.match(line) for line in lines if SAVE.match(line)), None)
        if code or not saved:
            print(f"{args.tag} {case}: save failed ({code}) {err[-200:]}", file=sys.stderr)
            return 1
        (directory / f"{case}.json").write_text(json.dumps({"bytes": int(saved[2]), "sha256": saved[3], "bound": int(saved[4]),
                                                            "digests": final_digests(lines, units, False)}, indent=1) + "\n")
    print(f"{args.tag}: saved {len(STATE_CASES)} states")
    return 0


def load(args):
    root = Path(args.states)
    results = {}
    for other in sorted(p for p in root.iterdir() if p.is_dir() and p.name != args.tag):
        for case, (rom, arguments, units) in STATE_CASES.items():
            state, meta = other / f"{case}.state", other / f"{case}.json"
            if not state.is_file() or not meta.is_file():
                continue
            expected = json.loads(meta.read_text())
            code, out, err = probe(args.probe, Path(args.corpus) / f"{rom}.rom", arguments, units, ["--public-load-at", str(units), str(state)])
            lines = out.splitlines()
            loaded = next((LOAD.match(line) for line in lines if LOAD.match(line)), None)
            entry = {"exit": code}
            if loaded:
                entry.update(code=int(loaded[2]), message=loaded[3])
                if int(loaded[2]) == 0:
                    entry["digests_equal_to_saver"] = final_digests(lines, units, True) == expected["digests"]
            else:
                entry["error"] = err[-200:]
            results[f"{other.name}->{args.tag}/{case}"] = entry
    Path(args.out).write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    print(f"{args.tag}: {len(results)} cross loads")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("save", "load"))
    parser.add_argument("--probe", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--states", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    return save(args) if args.mode == "save" else load(args)


if __name__ == "__main__":
    sys.exit(main())
