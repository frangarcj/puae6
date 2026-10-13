"""T28a: reproducer of the intermittent SIGSEGV / SIGBUS / hang when a Waterbox sandbox is created (tests/ffi session host, real libwaterboxhost).

In T28 a movie test (core R with FloppyWrites, then Q without, then W and P) died once with SIGSEGV on the second sandbox; the same test alone passed 3 of 3. This creates, runs, alternates
and frees sandboxes of different heaps in processes of the session host, many times, and counts the processes that die or hang. Nothing is retried: a process that does not exit with 0 is
reported with its signal and the tail of its output; one that is still alive after --timeout is a hang, it is sampled (`sample`, into --evidence-dir) and killed. The report is
reports/tasks/T28a.md: it shows that the second sandbox, the heaps and FloppyWrites do not matter, the crash is in libsystem_malloc when the host is created (variant `boot`).

Variants (--variant, several allowed, "all" is every one):
  heaps-keep       A500 with FloppyWrites and A500 without, then two more of each (the shape of the test that failed); all alive until the end of the cycle
  heaps-before     the same two, A is disposed BEFORE B is created
  heaps-after      the same two, A is disposed AFTER B is created (and ran)
  machine-*        the same three orders with A500 and A1200 without FloppyWrites (test_wrapper_machine's shape; the heaps are the same: it predates T28)
  memory-*         the same three orders with an A500 and an A500 with fast=MB8 (heaps of other sizes without FloppyWrites: it predates T28 too)
  single           one sandbox per cycle, always the same heaps (control: no second sandbox at all), 3 units
  same             two sandboxes with the SAME heaps (control: the second is not the issue, the heap sizes are)
  boot             one sandbox, created and disposed, no unit run

    python3 tests/waterbox-media/repro_heaps.py --variant all --processes 20 --cycles 10          # 200 cycles per variant, 10 per process
    python3 tests/waterbox-media/repro_heaps.py --variant boot --processes 150 --cycles 1 --keep-going --timeout 90 --evidence-dir /tmp/p6-hangs
    --units 30 --states        longer runs, with a save and a load in each sandbox (the movie test ran 30 units in each)
    --no-evict                 P6_EVICT=0: the host keeps a sandbox mapped between calls, as BizHawk's Release build sets it (the default of the host since T34a; with the Debug lane the runtime ignores it and always evicts)
    --env KEY=VALUE            for every process (MallocScribble=1, DOTNET_TieredCompilation=0, ...);  --ffi-dir DIR  another build of libwaterboxhost.dylib in a copy of the host's directory
    --audit mmap_audit.dylib   clang -dynamiclib -o mmap_audit.dylib tests/waterbox-media/mmap_audit.c  (and tests/waterbox-media/boot_driver.c is the same creation without .NET)
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import GUEST, RECEIPT, build_waterbox, builder, disk, med02  # noqa: E402
import json  # noqa: E402


def make_fixtures(directory):
    (directory / "med02.rom").write_bytes(builder.build(med02.rom_source(embedded=True)))
    for name, image in (("A", disk.make_adf(med02.LABEL_A)), ("B", disk.make_adf(med02.LABEL_B))):
        (directory / f"{name}.adf").write_bytes(image)


def core(name, directory, machine="a500", writes=False, extra=""):
    words = [f"core {name}", f"adf={directory}/A.adf,{directory}/B.adf", f"machine={machine}"]
    if writes:
        words.append("writes")
    if extra:
        words.append(extra)
    return " ".join(words)


# (machine, writes, extra settings) of the first sandbox and of the second one
SPEC = {
    "heaps": (("a500", True, ""), ("a500", False, "")),
    "machine": (("a500", False, ""), ("a1200", False, "")),
    "memory": (("a500", False, ""), ("a500", False, "fast=MB8")),
}


def run_session(arguments, env, timeout, evidence=None, ffi_dir=None):
    """The session host (tests/ffi) in a process of its own. With an interposer the real dotnet executable is run (Homebrew's `dotnet` is a /bin/bash script and SIP strips DYLD_* at its
    exec). A process that is still alive after `timeout` seconds is a HANG: it is sampled (`sample`, a stack of every thread) into `evidence` and then killed, never waited for again."""
    import shutil
    import subprocess
    dotnet, benv, output = build_waterbox.build_core()
    if ffi_dir:      # a copy of the host's output directory (build/ffi/bin/Debug/net8.0) whose libwaterboxhost.dylib is another build of the runtime
        output = Path(ffi_dir)
    env = env or {}
    if "DYLD_INSERT_LIBRARIES" in env:
        root = Path(shutil.which("dotnet")).resolve().parent.parent / "libexec"
        command, full = [str(root / "dotnet"), str(output / "Ffi.dll"), *arguments], {**benv, **env, "DOTNET_ROOT": str(root)}
    else:
        command, full = [dotnet, str(output / "Ffi.dll"), *arguments], {**benv, **env}
    process = subprocess.Popen(command, cwd=output, env=full, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if evidence is not None:
            subprocess.run(["sample", str(process.pid), "2", "-file", str(evidence)], capture_output=True)
        process.kill()
        out, err = process.communicate()
        return subprocess.CompletedProcess(command, "HANG", out, err)
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def cycle(variant, directory, units=3, states=False):
    """The program lines of one create/run/alternate/free cycle."""
    kind, _, order = variant.partition("-")
    lines = cycle_lines(variant, directory, kind, order)
    # --units scales every "run NAME n" (the movie test of T28 ran 30 units in each sandbox: the page faults the host takes to snapshot dirty pages grow with them) and --states
    # adds a save and a load of the state after the first run of each sandbox
    out, seen = [], set()
    for line in lines:
        words = line.split()
        if words[0] == "run":
            out.append(f"run {words[1]} {int(words[2]) * units // 3}")
            if states and words[1] not in seen:
                seen.add(words[1])
                out += [f"save {words[1]} s{words[1]}", f"run {words[1]} 2", f"load {words[1]} s{words[1]}"]
        else:
            out.append(line)
    return out


def cycle_lines(variant, directory, kind, order):
    if variant == "boot":
        return [core("A", directory, "a500", False), "dispose A"]
    if variant == "single":
        return [core("A", directory, "a500", True), "run A 3", "dispose A"]
    if variant == "same":
        return [core("A", directory, "a500", True), "run A 3", core("B", directory, "a500", True), "run B 3", "run A 2", "dispose A", "run B 2", "dispose B"]
    (ma, wa, xa), (mb, wb, xb) = SPEC[kind]
    a, b = core("A", directory, ma, wa, xa), core("B", directory, mb, wb, xb)
    if order == "keep":
        # four sandboxes alive at once, as in the movie test: A, B, then one more of each, all freed at the end
        return [a, "run A 3", b, "run B 3", "run A 2", core("C", directory, ma, wa, xa), "run C 2", core("D", directory, mb, wb, xb), "run D 2", "run B 2", "run A 1",
                "dispose A", "dispose B", "dispose C", "dispose D"]
    if order == "before":
        return [a, "run A 3", "dispose A", b, "run B 3", core("C", directory, ma, wa, xa), "run C 3", "dispose B", "dispose C"]
    if order == "after":
        return [a, "run A 3", b, "run B 3", "dispose A", "run B 2", core("C", directory, ma, wa, xa), "run C 3", "dispose B", "dispose C"]
    raise SystemExit(f"unknown variant {variant}")


VARIANTS = [f"{k}-{o}" for k in SPEC for o in ("keep", "before", "after")] + ["single", "same", "boot"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", nargs="+", default=["all"])
    parser.add_argument("--processes", type=int, default=10)
    parser.add_argument("--cycles", type=int, default=10, help="cycles of the variant in each process")
    parser.add_argument("--units", type=int, default=3, help="units run by the first run of each sandbox (the others scale with it); T28's movie test used 30")
    parser.add_argument("--states", action="store_true", help="save a state after the first run of each sandbox and load it after two more units")
    parser.add_argument("--timeout", type=int, default=600, help="seconds after which a process counts as hung (it is sampled, then killed)")
    parser.add_argument("--evidence-dir", default=".", help="where the samples of hung processes go")
    parser.add_argument("--audit", metavar="DYLIB", help="a DYLD interposer for mmap/munmap/mprotect (tests/waterbox-media/mmap_audit.c) to load into every process; its log goes to --audit-log")
    parser.add_argument("--audit-log", default="mmap_audit.log")
    parser.add_argument("--no-evict", action="store_true", help="P6_EVICT=0: the host keeps a sandbox mapped between calls, as BizHawk's Release build sets it (since T34a it is the host's default; the Debug lane P6_WATERBOX_RUNTIME=debug still unmaps it at every EnterExit)")
    parser.add_argument("--ffi-dir", help="run the session host from this copy of its output directory (to swap the runtime library for another build)")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="an environment variable for every process (MallocScribble=1, MallocGuardEdges=1, ...)")
    parser.add_argument("--keep-going", action="store_true", help="go on after a failure (the default stops that variant, to keep the evidence)")
    options = parser.parse_args()
    variants = VARIANTS if options.variant == ["all"] else options.variant
    guest_sha = json.loads(RECEIPT.read_text())["guest"]["sha256"]
    failures = 0
    env = {"DYLD_INSERT_LIBRARIES": str(Path(options.audit).resolve()), "MMAP_AUDIT_LOG": str(Path(options.audit_log).resolve())} if options.audit else {}
    if options.no_evict:
        env["P6_EVICT"] = "0"
    env.update(item.split("=", 1) for item in options.env)
    with tempfile.TemporaryDirectory(prefix="p6-repro-heaps-") as tmp:
        directory = Path(tmp)
        make_fixtures(directory)
        for variant in variants:
            program = directory / f"{variant}.txt"
            lines = []
            for _ in range(options.cycles):
                lines += cycle(variant, directory, options.units, options.states)
            program.write_text("\n".join(lines) + "\n")
            done, started = 0, time.time()
            for number in range(options.processes):
                evidence = Path(options.evidence_dir) / f"hang-{variant}-{os.getpid()}-{number + 1}.sample.txt"
                result = run_session(["session", "--runtime", "libwaterboxhost.dylib", "--guest", str(GUEST), "--sha", guest_sha, "--firmware", str(directory / "med02.rom"),
                                      "--program", str(program)], env, options.timeout, evidence, options.ffi_dir)
                if result.returncode != 0:
                    failures += 1
                    print(f"FAIL {variant} process {number + 1}/{options.processes} (cycle count so far {done * options.cycles}) returncode={result.returncode}", flush=True)
                    print("  stdout tail:", result.stdout[-600:].replace("\n", "\n    "))
                    print("  stderr tail:", result.stderr[-900:].replace("\n", "\n    "), flush=True)
                    if not options.keep_going:
                        break
                else:
                    done += 1
            print(f"{variant}: {done}/{options.processes} processes ok x {options.cycles} cycles = {done * options.cycles} cycles, {time.time() - started:.0f} s", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
