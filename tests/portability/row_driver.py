#!/usr/bin/env python3
"""Runs the corpus of the portability matrix with one p6_state_probe binary and writes the canonical hashes it printed (T25).

It uses only the standard library: it runs on the host for the native row and inside the container for the Linux rows.

    row_driver.py --probe PROBE --corpus DIR --out FILE [--variant baseline|host] [--jobs N] [--group G]... [--kickstart ROM]
                  [--preload LIBRARY --fake-offset SECONDS]

For every case it runs `PROBE ROM <args> --every 1` and reads the lines the probe prints (bridge/state_probe.cpp):
  hardware  `digest U SUBSYSTEM HEX BYTES` for cpu, scheduler, ram, rtc, cia, chipset, audio, input and bridge, and `chip U GROUP HEX`
  A/V       `digest U video HEX BYTES` (the rows of the video buffer), `pcm U HEX FRAMES` (the samples that unit handed over) and
            `picture U WxH pFIELD CADENCE HEX` (the picture that unit handed over)
and hashes each kind separately (SHA-256 of the lines, in the order printed). A case never gets a single hash that mixes the two: a row can
agree on the machine and differ in the picture, and the matrix has to say which.

Variants (DET06): `baseline` is a fixed environment (LC_ALL=C, TZ=UTC, working directory = the corpus, ROMs where the corpus is);
`host` changes everything the host decides: locale, time zone, the clock (a preloaded library that shifts time(), gettimeofday() and
clock_gettime() by --fake-offset seconds), the working directory and the path of every ROM (spaces, accents, an ideogram and a symbol).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

DIGEST = re.compile(r"^digest (\d+) (\w+) ([0-9a-f]{64}) (\d+)$")
CHIP = re.compile(r"^chip (\d+) (\w+) ([0-9a-f]{64})$")
PCM = re.compile(r"^pcm (\d+) ([0-9a-f]{64}) (\d+)$")
PICTURE = re.compile(r"^picture (\d+) (none|\d+x\d+ p\d+ \d+/\d+ [0-9a-f]{64})$")
HARDWARE = ("cpu", "scheduler", "ram", "rtc", "cia", "chipset", "audio", "input", "bridge")
# What the host variant sets; the locales may not exist on the machine, which the C library treats as "C" (that is part of the test: the
# result must not depend on whether a locale exists either).
HOST_ENV = {"LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8", "LC_NUMERIC": "de_DE.UTF-8", "LC_CTYPE": "ja_JP.UTF-8", "TZ": "Pacific/Kiritimati",
            "TMPDIR": "/tmp"}
BASE_ENV = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
# p6_headless runs: (name, corpus ROM, arguments). Its `unit N ticks=... hash=... audio=...` lines are what tests/waterbox-smoke (WB02) compares the
# Waterbox guest with, so equal lines in every row tie the guest of the Darwin row to the other rows.
HEADLESS_CASES = (("lace-a500-pal-200", "a500-hires-lace-fields", ["--units", "200"]),
                  ("vsync-a500-ntsc-300", "vsync", ["--units", "300", "--ntsc"]),
                  ("aga-bars24-a1200-pal-60", "aga-bars24-pal", ["--units", "60", "--machine", "a1200"]),
                  ("audio-tone_all-a600-44100-120", "audio-tone_all", ["--units", "120", "--machine", "a600"]))
ODD_DIRECTORY = "con espacios ñandú 日本語 ✓"


def classify(text):
    """-> {unit: {"hardware": [lines], "av": [lines]}}, by the unit the probe printed on each line, and the number of lines recognized."""
    units, recognized = {}, 0
    for line in text.splitlines():
        kind = unit = None
        if m := DIGEST.match(line):
            unit, kind = int(m[1]), ("hardware" if m[2] in HARDWARE else "av" if m[2] == "video" else None)
        elif m := CHIP.match(line):
            unit, kind = int(m[1]), "hardware"
        elif m := (PCM.match(line) or PICTURE.match(line)):
            unit, kind = int(m[1]), "av"
        if kind:
            units.setdefault(unit, {"hardware": [], "av": []})[kind].append(line)
            recognized += 1
    return units, recognized


def digest(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def run_case(probe, case, rom, env, cwd, timeout):
    arguments = [str(probe), str(rom), *case["args"], "--every", case.get("every", "1")]
    started = time.monotonic()
    result = subprocess.run(arguments, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout, errors="replace")
    if result.returncode:
        return {"error": f"exit {result.returncode}: {result.stderr[-300:]}"}
    units, recognized = classify(result.stdout)
    if not units or not recognized:
        return {"error": "the probe printed no digest: " + result.stdout[-200:]}
    hardware = [line for u in sorted(units) for line in units[u]["hardware"]]
    av = [line for u in sorted(units) for line in units[u]["av"]]
    return {"hardware": digest(hardware), "av": digest(av), "units": len(units), "lines": recognized,
            "per_unit": {str(u): [digest(units[u]["hardware"])[:16], digest(units[u]["av"])[:16]] for u in sorted(units)},
            "seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--variant", choices=("baseline", "host"), default="baseline")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--group", action="append", default=[], help="only the cases of this group (repeatable)")
    parser.add_argument("--headless", help="p6_headless of the same build: its `unit` lines for HEADLESS_CASES are hashed too (the lines WB02 compares)")
    parser.add_argument("--kickstart", help="an authorized Kickstart to add as a case; read where it is, never copied or hashed")
    parser.add_argument("--preload", help="library that shifts the clock (host variant)")
    parser.add_argument("--fake-offset", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    cases = json.loads((corpus / "cases.json").read_text(encoding="utf-8"))
    if args.group:
        cases = [c for c in cases if c["group"] in args.group]
    work = tempfile.TemporaryDirectory(prefix="p6-portability-")
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": work.name, "ASAN_OPTIONS": "detect_leaks=0"}
    if args.variant == "baseline":
        environment.update(BASE_ENV)
        rom_directory, cwd = corpus, corpus
    else:
        environment.update(HOST_ENV)
        if args.preload:
            key = "DYLD_INSERT_LIBRARIES" if sys.platform == "darwin" else "LD_PRELOAD"
            environment[key] = str(Path(args.preload).resolve())
            environment["P6_FAKE_TIME_OFFSET"] = str(args.fake_offset)
        rom_directory = Path(work.name) / ODD_DIRECTORY / "roms"
        rom_directory.mkdir(parents=True)
        cwd = Path(work.name) / "otro directorio"
        cwd.mkdir()
        for rom in corpus.glob("*.rom"):
            shutil.copyfile(rom, rom_directory / rom.name)
    roms = {c["id"]: rom_directory / (c["rom"] + ".rom") for c in cases}
    if args.kickstart:
        cases.append({"id": "kickstart-a500-pal", "group": "kickstart", "rom": "(authorized)", "args": ["--units", "150"], "every": "25"})
        roms["kickstart-a500-pal"] = Path(args.kickstart)
    probe = Path(args.probe).resolve()

    def one(case):
        try:
            return case["id"], run_case(probe, case, roms[case["id"]], environment, str(cwd), args.timeout)
        except subprocess.TimeoutExpired:
            return case["id"], {"error": "timeout"}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = dict(pool.map(one, cases))
    headless = {}
    if args.headless:
        for name, rom, extra in HEADLESS_CASES:
            result = subprocess.run([str(Path(args.headless).resolve()), "--run", str(rom_directory / (rom + ".rom")), *extra], capture_output=True,
                                    text=True, env=environment, cwd=str(cwd), timeout=args.timeout, errors="replace")
            lines = [line for line in result.stdout.splitlines() if line.startswith("unit ")]
            headless[name] = digest(lines) if result.returncode == 0 and lines else "error: " + result.stderr[-200:]
    document = {"variant": args.variant, "probe": probe.name, "cases": results, "headless": headless,
                "errors": {k: v["error"] for k, v in results.items() if "error" in v}}
    Path(args.out).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    work.cleanup()
    print(f"{args.variant}: {len(results) - len(document['errors'])}/{len(results)} cases ok -> {args.out}")
    return 1 if document["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
