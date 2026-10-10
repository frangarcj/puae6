#!/usr/bin/env python3
"""DET04 for the FPU (T54): the FPU corpus and the FPU programs of the state tests run on every row of the platform matrix and must print the same hashes.

    fpu_matrix.py corpus                        write the ROMs and cases.json into build/portability/corpus-fpu
    fpu_matrix.py exec ROW OPT [--skip-build]   build ROW at OPT (as tests/portability/run_matrix.py does) and run that corpus with its p6_state_probe
    fpu_matrix.py report [--write]              compare every result under build/portability with the reference row and write tests/host/fpu_matrix_evidence.json

It is the machinery of tests/portability (rows, builds, containers, the canonical per-unit hashes of tests/portability/row_driver.py) pointed at another corpus: the
A1200 with a 68881 or 68882 running tests/host/fpu_corpus.py (about 2300 coprocessor instructions: the result of each is left in Chip RAM, and the digest of RAM, of the
FP registers and FPCR/FPSR/FPIAR in the digest of the CPU are hashed unit by unit) and the programs of tests/state/test_det02_fpu.py. What is compared is the hardware
hash of every case (the machine) and its A/V hash (the picture and the samples), separately, against the reference row; the FPU is SoftFloat, so every row must give the
same bytes whatever the ISA, the compiler or the optimization level. A row whose binary uses the host FPU for the emulated one differs here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests.host import fpu_corpus  # noqa: E402
from tests.portability import run_matrix as rm  # noqa: E402
from tests.portability.rows import DATE, REFERENCE, ROWS  # noqa: E402

CORPUS = rm.BUILD / "corpus-fpu"
EVIDENCE = ROOT / "tests/host/fpu_matrix_evidence.json"
UNITS = 8
CORPUS_UNITS = 6


def programs():
    from tests.state import test_det02_fpu as state
    source, cases = fpu_corpus.build()
    rom = {"fpu-corpus": source}
    rom.update({name: text for name, (text, _, _) in state.ROMS.items()})
    return rom


def listing():
    cases = []
    for model in ("68881", "68882"):
        cases.append({"id": f"fpu-corpus-{model}", "group": "fpu-corpus", "rom": "fpu-corpus", "args": ["--machine", "a1200", "--fpu", model, "--units", str(CORPUS_UNITS)]})
    for name in ("fpu-arith", "fpu-modes", "fpu-rounding", "fpu-trap", "fpu-frames"):
        cases.append({"id": f"{name}-68882", "group": "fpu-programs", "rom": name, "args": ["--machine", "a1200", "--fpu", "68882", "--units", str(UNITS)]})
    cases.append({"id": "fpu-trap-68881", "group": "fpu-programs", "rom": "fpu-trap", "args": ["--machine", "a1200", "--fpu", "68881", "--units", str(UNITS)]})
    cases.append({"id": "fpu-arith-no-fpu", "group": "fpu-off", "rom": "fpu-arith", "args": ["--machine", "a1200", "--units", "4"]})
    return cases


def write_corpus(directory=CORPUS):
    import importlib.util
    spec = importlib.util.spec_from_file_location("microrom_builder", ROOT / "tests/fixtures/microrom/build_rom.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in programs().items():
        (directory / f"{name}.rom").write_bytes(builder.build(text))
    cases = listing()
    (directory / "cases.json").write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return cases, digest.hexdigest()


class FpuRow(rm.Row):
    """A row of the matrix whose corpus is the FPU one (mounted at the place the container expects the corpus)."""

    def __init__(self, name):
        super().__init__(name)
        if not self.docker:
            self.corpus = str(CORPUS)

    def run(self, command, log, timeout, kickstart=None):
        if not self.docker:
            return super().run(command, log, timeout, kickstart)
        self.host_dir.mkdir(parents=True, exist_ok=True)
        import os
        argv = ["docker", "run", "--rm", "--platform", self.spec["platform"], "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{ROOT}:/src:ro", "-v", f"{self.host_dir}:/src/build", "-v", f"{CORPUS}:/corpus:ro", "-w", "/src", self.spec["image"], "bash", "-c", command]
        started = time.monotonic()
        with open(log, "w", encoding="utf-8") as stream:
            try:
                code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, cwd=ROOT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                stream.write(f"\ntimeout after {timeout} s\n")
                code = 124
        return code, round(time.monotonic() - started, 1)


def exec_row(name, opt, skip_build=False):
    row = FpuRow(name)
    cases, corpus_digest = write_corpus()
    record = {"row": name, "opt": opt, "date": DATE, "corpus": {"cases": len(cases), "sha256": corpus_digest}}
    if not skip_build:
        record["build"] = rm.build_row(row, opt)
        print(f"{name} {opt}: build exit {record['build']['exit']} in {record['build']['seconds']} s")
        if record["build"]["exit"]:
            return 1
    else:
        earlier = row.host_dir / f"{opt}-fpu-record.json"          # the build of these binaries is the one an earlier call recorded
        if earlier.exists() and rm.load(earlier).get("build"):
            record["build"] = rm.load(earlier)["build"]
    output = f"{row.dir if row.docker else row.host_dir}/{opt}-fpu-baseline.json"
    command = (f"python3 -I {row.src}/tests/portability/row_driver.py --probe {row.tree(opt)}/p6_state_probe --corpus {row.corpus} --out {output} "
               f"--variant baseline --jobs {rm.JOBS}")
    code, seconds = row.run(command, row.host_dir / f"{opt}-fpu-baseline.log", 14400)
    record["run"] = {"exit": code, "seconds": seconds}
    (row.host_dir / f"{opt}-fpu-record.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"{name} {opt}: fpu corpus exit {code} in {seconds} s")
    return code


def report(write):
    reference_path = rm.BUILD / REFERENCE[0] / f"{REFERENCE[1]}-fpu-baseline.json"
    if not reference_path.exists():
        print("no reference result", reference_path, file=sys.stderr)
        return 1
    reference = rm.load(reference_path)
    document = {"schema_version": 1, "date": DATE, "reference": {"row": REFERENCE[0], "opt": REFERENCE[1]},
                "cases": {k: {"hardware": v["hardware"], "av": v["av"], "units": v["units"]} for k, v in sorted(reference["cases"].items()) if "error" not in v},
                "executions": []}
    corpus = None
    for name in ROWS:
        for record_path in sorted((rm.BUILD / name).glob("*-fpu-record.json")):
            record = rm.load(record_path)
            opt = record["opt"]
            result_path = rm.BUILD / name / f"{opt}-fpu-baseline.json"
            if not result_path.exists():
                continue
            corpus = corpus or record["corpus"]
            entry = {"row": name, "opt": opt, "isa": ROWS[name]["isa"], "native_hardware": ROWS[name]["native_hardware"], "compiler": ROWS[name]["compiler"]}
            if record.get("build"):
                entry["build"] = {k: record["build"][k] for k in ("exit", "seconds", "warnings", "cxx", "cc", "kernel")}
            entry["baseline"] = rm.compare(reference, rm.load(result_path))
            document["executions"].append(entry)
    document["corpus"] = corpus
    equal = all(e["baseline"]["hardware_equal"] == e["baseline"]["cases"] == e["baseline"]["av_equal"] and not e["baseline"]["errors"] for e in document["executions"])
    document["all_rows_equal_to_the_reference"] = equal
    for entry in document["executions"]:
        base = entry["baseline"]
        print(f"{entry['row']:26s} {entry['opt']:4s} cases {base['cases']}  hardware equal {base['hardware_equal']}  A/V equal {base['av_equal']}  errors {len(base['errors'])}")
    if write:
        EVIDENCE.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", EVIDENCE)
    return 0 if equal else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("corpus")
    run = sub.add_parser("exec")
    run.add_argument("row", choices=sorted(ROWS))
    run.add_argument("opt")
    run.add_argument("--skip-build", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.command == "corpus":
        cases, digest = write_corpus()
        print(f"{len(cases)} cases in {CORPUS} ({digest})")
        return 0
    if args.command == "exec":
        return exec_row(args.row, args.opt, args.skip_build)
    return report(args.write)


if __name__ == "__main__":
    sys.exit(main())
