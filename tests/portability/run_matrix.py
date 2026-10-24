#!/usr/bin/env python3
"""Build and execute declared portability rows; compare current results under build/portability.

P6_MATRIX_SET=differential selects the public scenario corpus. Private firmware is optional
and its hashes are never stored in the golden fixtures. report --write only writes build/.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.portability import corpus as corpus_module  # noqa: E402
from tests.portability.rows import DATE, OPTS, REFERENCE, ROWS  # noqa: E402

PORT = ROOT / "tests/portability"
BUILD = ROOT / "build/portability"
SET = os.environ.get("P6_MATRIX_SET", "")
if SET not in corpus_module.CORPORA:
    raise SystemExit(f"P6_MATRIX_SET must be one of {sorted(corpus_module.CORPORA)}, not {SET!r}")
TAG = f"-{SET}" if SET else ""                       # appended to the name of every result file of a set other than the default one
EVIDENCE = BUILD / (f"comparison-{SET}.json" if SET else "comparison.json")
CORPUS_DIR = BUILD / (f"corpus-{SET}" if SET else "corpus")
TARGETS = ("p6_state_probe", "p6_headless")
FAKE_OFFSET = 800_000_000          # about 25 years ahead: 2051, past the 32-bit time_t of 2038
JOBS = int(os.environ.get("P6_PORTABILITY_JOBS", "4"))
WARNING = re.compile(r"^(\S+?):\d+:\d+: warning: .*\[(-W[\w=+-]+)\]", re.M)


class Row:
    """Where a row's files are, as the host sees them and as the row's own processes see them."""

    def __init__(self, name):
        self.name, self.spec = name, ROWS[name]
        self.docker = self.spec["kind"] == "docker"
        self.host_dir = BUILD / name
        self.src = "/src" if self.docker else str(ROOT)
        self.dir = "/src/build" if self.docker else str(self.host_dir)      # the row's build root, in the row's own paths
        self.corpus = "/corpus" if self.docker else str(CORPUS_DIR)
        self.states = "/states" if self.docker else str(BUILD / "states")

    def run(self, command, log, timeout, kickstart=None):
        """Runs a shell command in the row's environment; the output goes to `log` (a Path). Returns (exit code, seconds)."""
        self.host_dir.mkdir(parents=True, exist_ok=True)
        if self.docker:
            argv = ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform", self.spec["platform"], "--user", f"{os.getuid()}:{os.getgid()}",
                    "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{ROOT}:/src:ro", "-v", f"{self.host_dir}:/src/build",
                    "-v", f"{CORPUS_DIR}:/corpus:ro", "-v", f"{BUILD / 'states'}:/states", "-w", "/src"]
            if kickstart:
                argv += ["-v", f"{kickstart}:/kickstart/kick.rom:ro"]
            argv += [self.spec["image"], "bash", "-c", command]
        else:
            argv = ["bash", "-c", command]
        started = time.monotonic()
        with open(log, "w", encoding="utf-8") as stream:
            try:
                code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, cwd=ROOT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                stream.write(f"\ntimeout after {timeout} s\n")
                code = 124
        return code, round(time.monotonic() - started, 1)

    def tree(self, opt):
        return f"{self.dir}/{opt}"


def toolchain_args(row):
    return f"-DCMAKE_TOOLCHAIN_FILE={row.src}/{row.spec['toolchain']}"


def configure_command(row, opt):
    o = OPTS[opt]
    flags = ""
    if o["flags"]:
        # The flags of the variant go to the flags of its build type, never to CMAKE_<LANG>_FLAGS: that would replace the _INIT flags that a
        # toolchain file sets (the GCC file used to carry its section flags there; CMakeLists.txt owns them now). Debug's own default is "-g".
        key = o["build_type"].upper()
        text = ("-g " if o["build_type"] == "Debug" else "") + o["flags"]
        flags = f" -DCMAKE_C_FLAGS_{key}={shlex.quote(text)} -DCMAKE_CXX_FLAGS_{key}={shlex.quote(text)}"
    return (f"cmake -S {row.src} -B {row.tree(opt)} -G Ninja {toolchain_args(row)} -DCMAKE_BUILD_TYPE={o['build_type']}{flags} "
            f"-DP6_SANITIZERS={'ON' if o['sanitizers'] else 'OFF'}")


def build_row(row, opt):
    """Configures and builds the probe and the headless runner; returns the record of the build."""
    row.host_dir.mkdir(parents=True, exist_ok=True)
    log = row.host_dir / f"{opt}-build.log"
    command = f"{configure_command(row, opt)} && cmake --build {row.tree(opt)} --target {' '.join(TARGETS)} -j {JOBS}"
    code, seconds = row.run(command, log, 7200)
    text = log.read_text(encoding="utf-8", errors="replace")
    flags = collections.Counter(m[2] for m in WARNING.finditer(text))
    # The engine is upstream code (build/upstream, build/generated); everything else that is compiled is the project's own.
    origin = collections.Counter(("engine" if "/build/upstream/" in m[1] or "/build/generated/" in m[1] else "project") + " " + m[2]
                                 for m in WARNING.finditer(text))
    cache = row.host_dir / opt / "CMakeCache.txt"
    version_log = row.host_dir / f"{opt}-compiler.txt"
    row.run("$(sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p' %s/CMakeCache.txt) --version | head -1; "
            "$(sed -n 's/^CMAKE_C_COMPILER:[A-Z]*=//p' %s/CMakeCache.txt) --version | head -1; uname -srm" % (row.tree(opt), row.tree(opt)), version_log, 60)
    lines = version_log.read_text(encoding="utf-8", errors="replace").splitlines() if version_log.exists() else []
    return {"exit": code, "seconds": seconds, "warnings": sum(flags.values()), "warning_flags": dict(sorted(flags.items())),
            "warning_origin": dict(sorted(origin.items())),
            "cxx": lines[0] if lines else "", "cc": lines[1] if len(lines) > 1 else "", "kernel": lines[2] if len(lines) > 2 else "",
            "cache_present": cache.exists(), "compiled": "Building " in text}


def faketime(row):
    """Builds the clock-shifting library and the program that shows it works; returns the record of the check."""
    library = f"{row.dir}/faketime." + ("dylib" if sys.platform == "darwin" and not row.docker else "so")
    shared = "-dynamiclib" if library.endswith(".dylib") else "-shared -fPIC"
    check = f"{row.dir}/faketime_check"
    cc = "/usr/bin/clang" if not row.docker else "/usr/bin/gcc"
    preload = "DYLD_INSERT_LIBRARIES" if library.endswith(".dylib") else "LD_PRELOAD"
    log = row.host_dir / "faketime.log"
    command = (f"{cc} {shared} -o {library} {row.src}/tests/portability/faketime.c {'-ldl' if row.docker else ''} && "
               f"{cc} -DFAKETIME_CHECK -o {check} {row.src}/tests/portability/faketime.c && {check} && "
               f"P6_FAKE_TIME_OFFSET={FAKE_OFFSET} {preload}={library} {check}")
    code, _ = row.run(command, log, 300)
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-2:]
    shifted = False
    if code == 0 and len(lines) == 2:
        a, b = (int(x.split("=")[1]) for x in lines[0].split()[:1] + lines[1].split()[:1])
        shifted = abs((b - a) - FAKE_OFFSET) <= 5
    return library if shifted else None, {"exit": code, "shifted": shifted, "lines": lines}


def run_variants(row, opt, variants, kickstart):
    out = {}
    library, check = faketime(row) if "host" in variants else (None, None)
    for variant in variants:
        result = row.host_dir / f"{opt}-{variant}{TAG}.json"
        command = (f"python3 -I {row.src}/tests/portability/row_driver.py --probe {row.tree(opt)}/p6_state_probe --corpus {row.corpus} "
                   f"--out {row.dir if row.docker else row.host_dir}/{opt}-{variant}{TAG}.json --variant {variant} --jobs {JOBS}")
        if not SET:
            command += f" --headless {row.tree(opt)}/p6_headless"
        if variant == "host" and library:
            command += f" --preload {library} --fake-offset {FAKE_OFFSET}"
        if kickstart:
            command += " --kickstart " + shlex.quote("/kickstart/kick.rom" if row.docker else kickstart)
        code, seconds = row.run(command, row.host_dir / f"{opt}-{variant}.log", 14400, kickstart if row.docker else None)
        out[variant] = {"exit": code, "seconds": seconds, "result": str(result.relative_to(ROOT))}
    return out, check


def states_phase(pairs, mode):
    """`mode` save: every (row, opt) saves its states; load: every one tries the states of all the others. Returns the exit code."""
    (BUILD / "states").mkdir(parents=True, exist_ok=True)
    corpus_directory()
    status = 0
    for name, opt in pairs:
        row = Row(name)
        tag = f"{name}-{opt}"
        command = (f"python3 -I {row.src}/tests/portability/state_crossload.py {mode} --probe {row.tree(opt)}/p6_state_probe "
                   f"--corpus {row.corpus} --states {row.states} --tag {tag}")
        if mode == "load":
            command += f" --out {row.dir}/{opt}-crossload.json"
        code, seconds = row.run(command, row.host_dir / f"{opt}-states-{mode}.log", 3600)
        print(f"{tag}: states {mode} exit {code} in {seconds} s")
        status |= code
    return status


def corpus_directory():
    directory = CORPUS_DIR
    cases = corpus_module.CORPORA[SET](directory)
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return len(cases), digest.hexdigest()


def exec_row(name, opt, skip_build=False, skip_host=False, kickstart=None, drop_tree=False):
    row = Row(name)
    count, corpus_digest = corpus_directory()
    record = {"row": name, "opt": opt, "date": DATE, "corpus": {"cases": count, "sha256": corpus_digest}}
    if not skip_build:
        previous = load(row.host_dir / f"{opt}-record{TAG}.json").get("build") if (row.host_dir / f"{opt}-record{TAG}.json").exists() else None
        record["build"] = build_row(row, opt)
        if previous and previous.get("exit") == 0 and record["build"]["exit"] == 0 and not record["build"]["compiled"]:
            record["build"] = previous            # nothing was compiled this time: the record of the build that made these binaries stays
        print(f"{name} {opt}: build exit {record['build']['exit']} in {record['build']['seconds']} s, {record['build']['warnings']} warnings")
        if record["build"]["exit"]:
            (row.host_dir / f"{opt}-record{TAG}.json").write_text(json.dumps(record, indent=1) + "\n")
            return 1
    elif (row.host_dir / f"{opt}-record{TAG}.json").exists():          # the build of an earlier call is still the build of these binaries
        record["build"] = load(row.host_dir / f"{opt}-record{TAG}.json").get("build")
    variants = ["baseline"] + ([] if skip_host else ["host"])
    record["runs"], record["faketime"] = run_variants(row, opt, variants, kickstart)
    (row.host_dir / f"{opt}-record{TAG}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"{name} {opt}: " + ", ".join(f"{v} exit {r['exit']} in {r['seconds']} s" for v, r in record["runs"].items()))
    if drop_tree:       # the disk of the machine is shared: an audit tree is only needed while it runs; its results stay in build/portability/ROW
        tree = (row.host_dir / opt).resolve()
        if tree.parent == row.host_dir.resolve() and opt in OPTS and tree.is_dir():
            shutil.rmtree(tree)
    return 1 if any(r["exit"] for r in record["runs"].values()) else 0


# ---------------------------------------------------------------------------------------------------------------- comparison

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def first_difference(reference, other):
    """The first unit whose hardware or A/V hash differs, as ("hardware"|"av", unit), or None."""
    for unit in sorted(reference["per_unit"], key=int):
        mine = other["per_unit"].get(unit)
        if mine is None:
            return ("missing", unit)
        for index, kind in enumerate(("hardware", "av")):
            if mine[index] != reference["per_unit"][unit][index]:
                return (kind, unit)
    return None


def compare(reference, other, own_baseline=None):
    """Counts how many cases of `other` have the hardware / A/V hashes of `reference`. Cases the corpus has and `other` failed on are errors."""
    reference_cases = {k: v for k, v in reference["cases"].items() if not k.startswith("kickstart")}
    rows = {"cases": len(reference_cases), "hardware_equal": 0, "av_equal": 0, "errors": {}, "differences": []}
    for case_id, ref in reference_cases.items():
        mine = other["cases"].get(case_id)
        if mine is None or "error" in mine or "error" in ref:
            rows["errors"][case_id] = (mine or {}).get("error", "missing")
            continue
        rows["hardware_equal"] += mine["hardware"] == ref["hardware"]
        rows["av_equal"] += mine["av"] == ref["av"]
        if mine["hardware"] != ref["hardware"] or mine["av"] != ref["av"]:
            where = first_difference(ref, mine)
            rows["differences"].append({"case": case_id, "hardware_equal": mine["hardware"] == ref["hardware"],
                                        "av_equal": mine["av"] == ref["av"], "first": list(where) if where else None})
    return rows


def states_evidence():
    """What `states save` and `states load` measured: whether the bytes of a state are the same in every row and whether a row accepts
    the state of another. Nothing here promises anything: states are not portable between builds (docs/04-validation.md)."""
    directory = BUILD / "states"
    tags = sorted(p.name for p in directory.iterdir() if p.is_dir()) if directory.exists() else []
    if len(tags) < 2:
        return None
    same_bytes, sizes = {}, {}
    cases = sorted({p.stem for t in tags for p in (directory / t).glob("*.json")})
    for case in cases:
        hashes = {t: json.loads((directory / t / f"{case}.json").read_text())["sha256"] for t in tags if (directory / t / f"{case}.json").exists()}
        same_bytes[case] = len(set(hashes.values()))
        sizes[case] = sorted({json.loads((directory / t / f"{case}.json").read_text())["bytes"] for t in tags if (directory / t / f"{case}.json").exists()})
    accepted = refused = equal = wrong = 0
    messages = collections.Counter()
    as_loader, as_saver = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    for tag in tags:
        row, opt = tag.rsplit("-", 1)
        path = BUILD / row / f"{opt}-crossload.json"
        if not path.exists():
            continue
        for key, entry in load(path).items():
            saver = key.split("->")[0]
            ok = entry.get("code") == 0 and bool(entry.get("digests_equal_to_saver"))
            as_loader[tag][0] += ok
            as_loader[tag][1] += 1
            as_saver[saver][0] += ok
            as_saver[saver][1] += 1
            if entry.get("code") == 0:
                accepted += 1
                equal += bool(entry.get("digests_equal_to_saver"))
                wrong += not entry.get("digests_equal_to_saver")
            else:
                refused += 1
                messages[entry.get("message") or entry.get("error", "?")] += 1
    total = accepted + refused
    text = (f"Medido con {len(tags)} builds (O0) y {len(cases)} estados guardados con `p6_state_save` publico: "
            f"estados con bytes identicos entre todas las filas: {sum(1 for n in same_bytes.values() if n == 1)} de {len(cases)}. "
            f"Cargas cruzadas entre filas distintas: {total}; aceptadas {accepted} (con digests iguales a los del que guardo: {equal}); "
            f"rechazadas {refused}. Esto describe lo observado con estados nativos del ABI publico (bytes canonicos little-endian), no es una promesa: "
            f"los estados entre builds, ISA o compiladores quedan **no prometido**, y los snapshots de memoria de Waterbox no se trasladan "
            f"a ninguna otra fila ni se han probado en ella.")
    return {"promised": False, "builds": tags, "cases": cases, "distinct_state_hashes_per_case": same_bytes, "state_sizes": sizes,
            "cross_loads": {"total": total, "accepted": accepted, "accepted_digests_equal": equal, "accepted_digests_different": wrong,
                            "refused": refused, "refusal_messages": dict(messages)},
            "as_loader": {k: v for k, v in sorted(as_loader.items())}, "as_saver": {k: v for k, v in sorted(as_saver.items())}, "summary": text}


def report(write):
    reference_record = BUILD / REFERENCE[0] / f"{REFERENCE[1]}-baseline{TAG}.json"
    if not reference_record.exists():
        print("no reference result", reference_record, file=sys.stderr)
        return 1
    reference = load(reference_record)
    evidence = {"schema_version": 1, "date": DATE, "reference": {"row": REFERENCE[0], "opt": REFERENCE[1]},
                "headless": {k: v for k, v in sorted(reference.get("headless", {}).items())},
                "cases": {k: {"hardware": v["hardware"], "av": v["av"], "units": v["units"]} for k, v in sorted(reference["cases"].items())
                          if "error" not in v and not k.startswith("kickstart")},
                "executions": {}}
    corpus = None
    for name in ROWS:
        for record_path in sorted((BUILD / name).glob(f"*-record{TAG}.json")):
            record = load(record_path)
            opt = record["opt"]
            entry = {"row": name, "opt": opt, "isa": ROWS[name]["isa"], "native_hardware": ROWS[name]["native_hardware"]}
            build = record.get("build")
            if build:
                entry["build"] = {k: build[k] for k in ("exit", "seconds", "warnings", "warning_flags", "warning_origin", "cxx", "cc", "kernel")}
            corpus = corpus or record["corpus"]
            baseline_path = BUILD / name / f"{opt}-baseline{TAG}.json"
            if baseline_path.exists():
                baseline = load(baseline_path)
                entry["baseline"] = compare(reference, baseline)
                entry["baseline"]["seconds"] = round(sum(c.get("seconds", 0) for c in baseline["cases"].values()), 1)
                if "kickstart-a500-pal" in baseline["cases"]:
                    entry["kickstart_ran"] = "error" not in baseline["cases"]["kickstart-a500-pal"]
            if baseline_path.exists() and load(baseline_path).get("headless"):
                mine, ref = load(baseline_path)["headless"], reference.get("headless", {})
                entry["headless"] = {"cases": len(ref), "equal": sum(1 for k, v in ref.items() if mine.get(k) == v and not v.startswith("error"))}
            host_path = BUILD / name / f"{opt}-host{TAG}.json"
            if host_path.exists() and baseline_path.exists():
                host = load(host_path)
                own = compare(baseline, host)
                entry["det06"] = {"cases": own["cases"], "hardware_equal": own["hardware_equal"], "av_equal": own["av_equal"],
                                  "errors": own["errors"], "differences": own["differences"],
                                  "clock_shifted": bool(record.get("faketime", {}).get("shifted"))}
                if "kickstart-a500-pal" in host["cases"] and "kickstart-a500-pal" in baseline["cases"]:
                    a, b = baseline["cases"]["kickstart-a500-pal"], host["cases"]["kickstart-a500-pal"]
                    entry["det06"]["kickstart_equal"] = "error" not in a and "error" not in b and a["hardware"] == b["hardware"] and a["av"] == b["av"]
            evidence["executions"][f"{name}/{opt}"] = entry
    evidence["corpus"] = corpus
    states = None if SET else states_evidence()
    if states:
        evidence["states"] = states
    # Kickstart: the unit hashes of the reference row against every row, as booleans only
    kick = {}
    for name in ROWS:
        for opt in OPTS:
            path = BUILD / name / f"{opt}-baseline{TAG}.json"
            if path.exists():
                case = load(path)["cases"].get("kickstart-a500-pal")
                if case and "error" not in case:
                    kick[f"{name}/{opt}"] = (case["hardware"], case["av"])
    if kick:
        ref = kick.get(f"{REFERENCE[0]}/{REFERENCE[1]}")
        evidence["kickstart"] = {"rom": "authorized Kickstart 1.3 via P6_KICKSTART_ROM; not copied, not hashed, hashes not recorded",
                                 "reference_run": ref is not None,
                                 "rows": {k: {"hardware_equal": ref is not None and v[0] == ref[0], "av_equal": ref is not None and v[1] == ref[1]}
                                          for k, v in sorted(kick.items())}}
    text = json.dumps(evidence, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if write:
        old = load(EVIDENCE) if EVIDENCE.exists() else {}
        for key in ("states", "audit", "reruns"):          # sections recorded by other commands survive a new comparison
            if key in old and key not in evidence:
                evidence[key] = old[key]
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", EVIDENCE.relative_to(ROOT))
    for key, entry in evidence["executions"].items():
        b, d = entry.get("baseline"), entry.get("det06")
        print(f"{key:36s} build={entry.get('build', {}).get('exit', '-')!s:2} hw={b and b['hardware_equal']}/{b and b['cases']} "
              f"av={b and b['av_equal']}/{b and b['cases']} errors={b and len(b['errors'])} "
              f"det06 hw={d and d['hardware_equal']} av={d and d['av_equal']}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("exec")
    e.add_argument("row", choices=list(ROWS))
    e.add_argument("opt", choices=list(OPTS))
    e.add_argument("--skip-build", action="store_true")
    e.add_argument("--skip-host", action="store_true")
    e.add_argument("--drop-tree", action="store_true", help="delete the build tree of this row/opt after the run (results are kept)")
    e.add_argument("--kickstart", action="store_true", help="add the authorized Kickstart named by P6_KICKSTART_ROM as an extra case")
    t = sub.add_parser("states")
    t.add_argument("mode", choices=("save", "load"))
    t.add_argument("--opt", default="O0", choices=list(OPTS))
    r = sub.add_parser("report")
    r.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.command == "exec":
        kickstart = os.environ.get("P6_KICKSTART_ROM") if args.kickstart else None
        if args.kickstart and not (kickstart and Path(kickstart).is_file()):
            print("P6_KICKSTART_ROM does not name a file", file=sys.stderr)
            return 2
        return exec_row(args.row, args.opt, args.skip_build, args.skip_host, kickstart, args.drop_tree)
    if args.command == "states":
        return states_phase([(name, args.opt) for name in ROWS], args.mode)
    return report(args.write)


if __name__ == "__main__":
    sys.exit(main())
