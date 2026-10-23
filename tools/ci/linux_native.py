#!/usr/bin/env python3
"""Hosted native matrix; public micro-ROM only, no saved reports or uploads."""
import argparse
import importlib.util
import json
import platform
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.build_support import source_identity, digest  # noqa: E402
from tests.portability.row_driver import classify, HARDWARE, DIGEST, CHIP  # noqa: E402
from tools.ci.run import command, redact  # noqa: E402
from tests.portability.rows import ROWS, OPTS  # noqa: E402

TREE = ROOT / "build/native-linux"
TARGETS = ("p6_headless", "p6_state_probe", "p6_libretro_host", "puae6_libretro")
HOSTED_RUNNERS = {"macos-arm64-appleclang": "macos-15", "linux-arm64-clang": "ubuntu-24.04-arm",
                  "linux-arm64-gcc": "ubuntu-24.04-arm", "linux-x86_64-clang": "ubuntu-24.04",
                  "linux-x86_64-gcc": "ubuntu-24.04"}
VARIANTS = ("O0", "O2", "san")
DEFAULT_ROW = "linux-x86_64-clang"
# Same causal groups as the existing DET02 oracle. custom_storage (registers)
# records debugger write history, never read by emulation and never serialized.
CHIP_GROUPS = ("beam", "counters", "irq_dma", "clxdat", "lof", "timing", "aga")


def selection(row, opt):
    if row not in HOSTED_RUNNERS or opt not in VARIANTS:
        raise ValueError("unknown hosted row or optimization")
    return ROWS[row], OPTS[opt]


def compilers(row):
    return ("/usr/bin/gcc", "/usr/bin/g++") if row.endswith("-gcc") else ("/usr/bin/clang", "/usr/bin/clang++")


def core_name(row):
    return "puae6_libretro" + (".dylib" if row.startswith("macos-") else ".so")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def execute(argv, timeout=180):
    result = command(list(map(str, argv)), timeout=timeout,
                     env={"ASAN_OPTIONS": "detect_leaks=" + ("0" if platform.system() == "Darwin" else "1") + ":halt_on_error=1",
                          "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"})
    require(result.returncode == 0, f"command failed ({result.returncode}): {argv[0]}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    require("VIOLATION" not in result.stdout, "libretro host reported a protocol violation")
    return result.stdout


def preflight(row=DEFAULT_ROW, opt="O2"):
    spec, _ = selection(row, opt)
    expected = "Darwin" if row.startswith("macos-") else "Linux"
    machine = "arm64" if platform.machine() in ("aarch64", "arm64") else platform.machine()
    if (platform.system(), machine) != (expected, spec["isa"]):
        raise ValueError(f"requires the declared {expected} {spec['isa']} runner")
    for tool in ("git", "cmake", "ninja", "patch", *compilers(row)):
        if not shutil.which(tool):
            raise ValueError(f"missing preinstalled tool: {tool}")


def build_commands(row=DEFAULT_ROW, opt="O2", tree=TREE):
    spec, mode = selection(row, opt)
    flags = mode["flags"] or "-O0 -g"
    kind = mode["build_type"].upper()
    return (["cmake", "-S", str(ROOT), "-B", str(tree), "-G", "Ninja",
             "-DCMAKE_TOOLCHAIN_FILE=" + str(ROOT / spec["toolchain"]),
             "-DCMAKE_BUILD_TYPE=" + mode["build_type"], f"-DCMAKE_C_FLAGS_{kind}=" + flags,
             f"-DCMAKE_CXX_FLAGS_{kind}=" + flags, "-DP6_SANITIZERS=" + ("ON" if mode["sanitizers"] else "OFF")],
            ["cmake", "--build", str(tree), "--target", *TARGETS, "-j", "2"])


def rows(text, prefix, count):
    matches = re.findall(r"^" + prefix + r" (\d+) (.+)$", text, re.M)
    require([int(n) for n, _ in matches] == list(range(1, count + 1)), f"missing/duplicate {prefix} units")
    try:
        return [dict(field.split("=", 1) for field in body.split()) for _, body in matches]
    except ValueError as error:
        raise RuntimeError("malformed frontend output") from error


def native_future(text, first, count):
    units, _ = classify(text)
    result = []
    for n in range(first, first + count):
        lines = units.get(n, {})
        hardware, av = lines.get("hardware", []), lines.get("av", [])
        names = [m[2] for line in hardware + av if (m := DIGEST.match(line))]
        groups = [m[2] for line in hardware if (m := CHIP.match(line))]
        require(sorted(names) == sorted((*HARDWARE, "video")) and sorted(groups) == sorted((*CHIP_GROUPS, "registers"))
                and any(s.startswith("pcm ") for s in av) and any(s.startswith("picture ") for s in av), "incomplete native future")
        # The aggregate chipset digest also includes that noncausal shadow.
        # Keep every causal group exact rather than masking the aggregate hash.
        hardware = [line for line in hardware if not ((m := DIGEST.match(line)) and m[2] == "chipset")
                    and not ((m := CHIP.match(line)) and m[2] == "registers")]
        result.append([re.sub(r"^(\w+) \d+ ", r"\1 UNIT ", line) for line in hardware + av])
    return result


def check_native(work, rom, tree=TREE):
    state, again, final, restored = [work / name for name in ("cut.state", "again.state", "final.state", "restored.state")]
    probe = [tree / "p6_state_probe", rom, "--every", "1"]
    original = execute([*probe, "--units", "8", "--public-save-at", "4", state, "--public-save-at", "8", final])
    require(state.is_file() and state.stat().st_size > 0 and final.is_file() and final.stat().st_size > 0, "native save did not produce complete states")
    cold = execute([*probe, "--units", "4", "--public-load-at", "0", state, "--public-save-at", "0", again, "--public-save-at", "4", restored])
    require(re.search(r"^load 0 0 *$", cold, re.M), "native cold load was refused")
    require(again.is_file() and restored.is_file() and state.read_bytes() == again.read_bytes()
            and final.read_bytes() == restored.read_bytes(), "native cold fullstate mismatch")
    for unit, (expected, actual) in enumerate(zip(native_future(original, 5, 4), native_future(cold, 1, 4)), 1):
        require(expected == actual, f"native cold future differs at unit {unit}: "
                f"expected {[line for line in expected if line not in actual][:2]}, actual {[line for line in actual if line not in expected][:2]}")


def check_frontends(work, rom, tree=TREE, row=DEFAULT_ROW):
    headless = rows(execute([tree / "p6_headless", "--run", rom, "--units", "14"]), "unit", 14)
    system = work / "system"; system.mkdir(); shutil.copyfile(rom, system / "kick34005.A500")
    host = [tree / "p6_libretro_host", tree / core_name(row), "--system", system]
    state = work / "libretro.state"
    original = execute([*host, "--runs", "14", "--do", f"6:savefile:{state}", "--do", "14:save:final"])
    observed = rows(original, "run", 14)
    for expected, actual in zip(headless, observed):
        require((expected["ticks"], expected["audio"]) == (actual["ticks"], actual["audio"]), "headless/libretro ticks or audio differ")
        require((expected["active"], expected["hash"]) == (actual["video"], actual["hash"]) if expected["video"] == "1"
                else actual["dupe"] == "1", "headless/libretro picture differs")
    size = re.search(r"^state size=(\d+)$", original, re.M)
    require(size and state.is_file() and state.stat().st_size == int(size[1]), "libretro file differs from serialize_size")
    cold = execute([*host, "--runs", "8", "--do", f"0:loadfile:{state}:pos=6", "--do", "8:save:final"])
    require(re.search(r"^state load .* ok=1 size=\d+$", cold, re.M), "libretro cold load was refused")
    keys = ("unit", "ticks", "audio", "ahash", "hash", "px")
    require([[r[k] for k in keys] for r in observed[6:]] == [[r[k] for k in keys] for r in rows(cold, "run", 8)], "libretro cold AV/PCM future differs")
    finals = [re.search(r"^state final ok=1 size=(\d+) hash=([0-9a-f]+)$", t, re.M) for t in (original, cold)]
    require(all(finals) and finals[0][1] == size[1] and finals[0].groups() == finals[1].groups(), "libretro cold fullstate mismatch")


def run(row=DEFAULT_ROW, opt="O2"):
    preflight(row, opt); before = source_identity()
    _, mode = selection(row, opt)
    tree = ROOT / "build/hosted-native" / row / opt
    for argv in build_commands(row, opt, tree):
        execute(argv, 1800)
    tree.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scenario-", dir=tree) as directory:
        work = Path(directory)
        spec = importlib.util.spec_from_file_location("microrom", ROOT / "tests/fixtures/microrom/build_rom.py")
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        for name in ("input_all", "boot"):
            (work / (name + ".rom")).write_bytes(builder.build((ROOT / f"tests/fixtures/microrom/{name}.s").read_text()))
        # boot avoids frontend mouse/joystick defaults; input_all exercises native input restoration.
        check_native(work, work / "input_all.rom", tree); check_frontends(work, work / "boot.rom", tree, row)
    require(source_identity() == before, "sources changed during the gate")
    return dict(status="PASS", scope="native hosted row; not Waterbox or accumulated release", row=row, optimization=opt, revision=execute(["git", "rev-parse", "HEAD"]).strip(),
                source_sha256=before, runner=platform.platform(), patchset_sha256=digest(ROOT / "patches/winuae/series.json"),
                flags=(mode["flags"] or "-O0 -g") + "; assertions on", sanitizers=mode["sanitizers"],
                leak_detection=("unsupported on Darwin ARM64" if row.startswith("macos-") else "on") if mode["sanitizers"] else "not instrumented",
                toolchain_lock_sha256=digest(ROOT / "plan/toolchains.json"),
                compilers={name: execute([name, "--version"]).splitlines()[0] for name in compilers(row)},
                cmake_cache_sha256=digest(tree / "CMakeCache.txt"),
                codegen_sha256=digest(ROOT / "build/generated/codegen-receipt.json"),
                binaries={name: digest(tree / (core_name(row) if name == "puae6_libretro" else name)) for name in TARGETS})


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--row", choices=tuple(HOSTED_RUNNERS), default=DEFAULT_ROW)
        parser.add_argument("--opt", choices=VARIANTS, default="O2")
        args = parser.parse_args()
        result = run(args.row, args.opt)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        result = dict(status="BLOCKED" if isinstance(error, ValueError) else "FAIL", diagnostic=str(error))
    print(redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result["status"] == "PASS" else 1)
