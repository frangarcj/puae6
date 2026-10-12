#!/usr/bin/env python3
"""Compare two runs of the same scenario and say where they first part ways.

A run is what p6_state_probe printed (one `digest UNIT SUBSYSTEM HEX BYTES` per subsystem and unit, `chip UNIT GROUP HEX`, `pcm UNIT HEX FRAMES`,
`picture UNIT ...`) or a scenario result (tools/scenario_schema.py: per unit master_ticks, hashes ram/cpu/events/video/pcm, samples_total). The
comparison is per unit boundary and per key, never one final sum: it reports the first boundary at which any key differs, the keys and the
subsystems that differ there (ticks and events, CPU, chipset and its groups, RAM, audio and picture as the host receives them), the first
boundary of each other key that differs later, the last boundary that matched completely, and, when it is given the inputs, the inputs and
events applied up to the divergent boundary. A hash that differs says where to look; it says nothing about what is right.

    python3 tools/compare_runs.py A B [--format auto|probe|result] [--inputs SCENARIO.json | --probe-args "..."] [--last N] [--json]

Exit status: 0 the runs are equal, 1 they differ, 2 a run could not be read. Nothing here knows an expected difference: that policy belongs to the
caller (tests/reference), which can use `compare()` and `explain()` as a library.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import sys

HEX = r"[0-9a-f]{64}"
DIGEST = re.compile(rf"^digest (\d+) (\w+) ({HEX}) (\d+)$")
CHIP = re.compile(rf"^chip (\d+) (\w+) ({HEX})$")
PCM = re.compile(rf"^pcm (\d+) ({HEX}) (\d+)$")
PICTURE = re.compile(rf"^picture (\d+) (none|\d+x\d+ p\d+ \d+/\d+ {HEX})$")
LOAD_MARK = re.compile(r"^after-load (\d+)$")
PASS_MARK = re.compile(r"^pass (\d+)$")

# The order in which a difference between two runs is most likely to have started: what the CPU executes feeds the scheduler and the memory, those
# feed the chipset and the devices, and those feed what the host receives. Several keys differ in the same boundary once something has gone wrong,
# so the first of this list is where to look first, not a proof of cause.
CAUSAL = ("ticks", "cpu", "scheduler", "events", "ram", "rtc", "cia", "chipset", "audio", "input", "video", "bridge", "pcm", "picture", "samples")
SUBSYSTEM_OF_KEY = {"hash.ram": "ram", "hash.cpu": "cpu", "hash.events": "events", "hash.video": "video", "hash.pcm": "pcm"}


class RunFormatError(ValueError):
    pass


class Run:
    """{(boundary, key): value}. A key is `digest.cpu`, `chip.beam`, `pcm`, `picture`, `ticks`...;
    the digests that follow an `after-load` line are keyed `load<N>.<key>` (`pass<K>.` before it with --loads) so that a restore is compared as well, at the unit it was made in."""

    def __init__(self):
        self.values: dict[tuple[int, str], str] = {}
        self._units: set[int] = set()

    def put(self, unit: int, key: str, value: str) -> None:
        if (unit, key) in self.values:
            raise RunFormatError(f"{key} printed twice for boundary {unit}")
        self.values[(unit, key)] = value
        self._units.add(unit)

    @property
    def units(self) -> list[int]:
        return sorted(self._units)

    def keys_at(self, unit: int) -> set[str]:
        return {key for (u, key) in self.values if u == unit}


def parse_probe(text: str) -> Run:
    run = Run()
    base, load_index, prefix = "", 0, ""
    for line in text.splitlines():
        if LOAD_MARK.match(line):
            load_index += 1
            prefix = f"{base}load{load_index}."
        elif (m := PASS_MARK.match(line)):
            base, load_index, prefix = f"pass{m[1]}.", 0, f"pass{m[1]}."
        elif (m := DIGEST.match(line)):
            run.put(int(m[1]), f"{prefix}digest.{m[2]}", m[3])
        elif (m := CHIP.match(line)):
            run.put(int(m[1]), f"{prefix}chip.{m[2]}", m[3])
        elif (m := PCM.match(line)):
            run.put(int(m[1]), f"{prefix}pcm", m[2])
        elif (m := PICTURE.match(line)):
            run.put(int(m[1]), f"{prefix}picture", m[2])
    if not run.units:
        raise RunFormatError("no boundary line (digest, chip, pcm, picture) in the probe output")
    return run


def parse_result(document: dict) -> Run:
    run = Run()
    boundaries = document.get("boundaries") if isinstance(document, dict) else None
    if not isinstance(boundaries, list) or not boundaries:
        raise RunFormatError("a scenario result needs a non-empty 'boundaries' list")
    for record in boundaries:
        try:
            unit = int(record["unit"])
            run.put(unit, "ticks", str(record["master_ticks"]))
            run.put(unit, "samples", str(record["samples_total"]))
            for name, value in sorted(record["hashes"].items()):
                run.put(unit, f"hash.{name}", "none" if value is None else str(value))
        except (KeyError, TypeError, ValueError) as error:
            raise RunFormatError(f"malformed boundary record: {error}") from error
    return run


def load_run(path, fmt: str = "auto") -> Run:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    if fmt == "auto":
        fmt = "result" if raw.lstrip().startswith("{") else "probe"
    if fmt == "result":
        try:
            return parse_result(json.loads(raw))
        except json.JSONDecodeError as error:
            raise RunFormatError(f"not JSON: {error}") from error
    if fmt == "probe":
        return parse_probe(raw)
    raise RunFormatError(f"unknown format {fmt!r}")


def subsystem(key: str) -> str:
    """The part of the machine a key describes: `load0.1.digest.cpu` and `digest.cpu` are the CPU, every `chip.*` group is the chipset."""
    key = re.sub(r"^(pass\d+\.)?(load\d+\.)?", "", key)
    if key.startswith("digest."):
        return key.split(".", 1)[1]
    if key.startswith("chip."):
        return "chipset"
    return SUBSYSTEM_OF_KEY.get(key, key)


def _rank(name: str) -> int:
    return CAUSAL.index(name) if name in CAUSAL else len(CAUSAL)


def compare(a: Run, b: Run) -> dict:
    """The report of a comparison; equal runs have `equal` true and nothing else of interest."""
    units = sorted(set(a.units) | set(b.units))
    first = None
    last_equal = None
    per_key: dict[str, dict] = {}
    for unit in units:
        keys = a.keys_at(unit) | b.keys_at(unit)
        differing = []
        for key in sorted(keys):
            left, right = a.values.get((unit, key)), b.values.get((unit, key))
            if left != right:
                differing.append((key, left, right))
                entry = per_key.setdefault(key, {"first_unit": unit, "units": 0, "a": left, "b": right})
                entry["units"] += 1
        if differing and first is None:
            first = (unit, differing)
        if not differing and first is None:
            last_equal = unit
    report = {"equal": first is None, "boundaries": len(units), "compared_keys": len({k for (_, k) in a.values} | {k for (_, k) in b.values}),
              "last_equal_boundary": last_equal}
    if first is None:
        return report
    unit, differing = first
    subsystems = sorted({subsystem(key) for key, _, _ in differing}, key=lambda s: (_rank(s), s))
    report.update({
        "first_divergent_boundary": unit,
        "subsystems": subsystems,
        "look_first": subsystems[0],
        "keys": [{"key": key, "a": left, "b": right} for key, left, right in differing],
        "later": sorted(({"key": key, "first_unit": entry["first_unit"], "units": entry["units"]}
                         for key, entry in per_key.items() if entry["first_unit"] > unit), key=lambda e: (e["first_unit"], e["key"])),
        "differing_subsystems": sorted({subsystem(key) for key in per_key}, key=lambda s: (_rank(s), s)),
        "only_in_a": sorted(set(a.units) - set(b.units))[:1],
        "only_in_b": sorted(set(b.units) - set(a.units))[:1],
    })
    return report


def explain(report: dict, expected_subsystems: set[str]) -> list[str]:
    """Differences that a caller did not expect: the differing subsystems outside `expected_subsystems`, or any difference when none is expected."""
    if report["equal"]:
        return []
    return sorted(set(report["differing_subsystems"]) - set(expected_subsystems))


_UNIT_OPTIONS = {"--port-at": 1, "--key": 2, "--reset-at": 0, "--event-at": 3, "--append-at": 1, "--save-at": 1, "--load-at": 1, "--poke-at": 1,
                 "--digest-at": 0, "--public-save-at": 1, "--public-load-at": 1, "--public-at": 0}


def inputs_from_probe_args(args: list[str]) -> list[dict]:
    """The inputs and events a probe command line schedules, in unit order. The base `--port` packets act from the first unit."""
    items = []
    i = 0
    while i < len(args):
        option = args[i]
        if option == "--port" and i + 1 < len(args):
            items.append({"unit": 1, "kind": "port", "detail": args[i + 1]})
            i += 2
        elif option in _UNIT_OPTIONS and i + 1 < len(args):
            count = _UNIT_OPTIONS[option]
            detail = " ".join(args[i + 2:i + 2 + count])
            items.append({"unit": int(args[i + 1]), "kind": option[2:], "detail": detail})
            i += 2 + count
        else:
            i += 1
    return sorted(items, key=lambda item: item["unit"])


def inputs_from_scenario(document: dict) -> list[dict]:
    items = []
    for packet in document.get("inputs", []):
        ports = ",".join(f"{p['type']}:{p['buttons']}:{p['dx']}:{p['dy']}" for p in packet["ports"])
        items.append({"unit": packet["unit"], "kind": "input", "detail": f"keys={packet['keys']} ports={ports}"})
    for event in document.get("events", []):
        detail = " ".join(f"{k}={event[k]}" for k in ("drive", "media_index") if k in event)
        items.append({"unit": event["unit"], "kind": event["type"], "detail": detail})
    return sorted(items, key=lambda item: (item["unit"]))


def previous_inputs(items: list[dict], boundary: int, last: int) -> list[dict]:
    """The last `last` inputs applied up to the divergent boundary (an input of unit U is applied right before unit U runs, so U == boundary counts)."""
    return [item for item in items if item["unit"] <= boundary][-last:]


def render(report: dict, inputs: list[dict] | None = None) -> str:
    if report["equal"]:
        return f"EQUAL: {report['boundaries']} boundaries, {report['compared_keys']} keys"
    out = [f"DIFFERENT: first divergent boundary {report['first_divergent_boundary']} "
           f"(last boundary equal in every key: {report['last_equal_boundary']})",
           f"  look first at: {report['look_first']}; subsystems that differ there: {', '.join(report['subsystems'])}"]
    for item in report["keys"]:
        out.append(f"  {item['key']}: A={item['a']}  B={item['b']}")
    if report["later"]:
        out.append("  keys that part ways later: " + ", ".join(f"{e['key']}@{e['first_unit']}" for e in report["later"][:12]))
    if report["only_in_a"] or report["only_in_b"]:
        out.append(f"  one run ends early: first boundary only in A {report['only_in_a']}, only in B {report['only_in_b']}")
    if inputs is not None:
        out.append("  inputs and events applied up to that boundary (last first):")
        for item in reversed(inputs):
            out.append(f"    unit {item['unit']}: {item['kind']} {item['detail']}")
        if not inputs:
            out.append("    (none)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--format", choices=("auto", "probe", "result"), default="auto")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--inputs", help="scenario JSON whose inputs and events are listed up to the divergent boundary")
    source.add_argument("--probe-args", help="the probe command line (after the ROM) the runs were made with")
    parser.add_argument("--last", type=int, default=8, help="how many previous inputs to list")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = compare(load_run(args.a, args.format), load_run(args.b, args.format))
        items = None
        if args.inputs:
            items = inputs_from_scenario(json.loads(Path(args.inputs).read_text(encoding="utf-8")))
        elif args.probe_args is not None:
            items = inputs_from_probe_args(shlex.split(args.probe_args))
    except (OSError, ValueError) as error:
        print(f"compare_runs: {error}", file=sys.stderr)
        return 2
    shown = None if items is None or report["equal"] else previous_inputs(items, report["first_divergent_boundary"], args.last)
    if shown is not None:
        report["previous_inputs"] = shown
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report, shown))
    return 0 if report["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
