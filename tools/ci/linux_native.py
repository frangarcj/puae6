#!/usr/bin/env python3
"""Manual hosted Linux gate; public micro-ROM only, no saved reports or uploads."""
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
from tests.portability.row_driver import classify, HARDWARE, DIGEST  # noqa: E402
from tools.ci.run import command, redact  # noqa: E402

TREE = ROOT / "build/native-linux"
TARGETS = ("p6_headless", "p6_state_probe", "p6_libretro_host", "puae6_libretro")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def execute(argv, timeout=180):
    result = command(list(map(str, argv)), timeout=timeout)
    require(result.returncode == 0, f"command failed ({result.returncode}): {argv[0]}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    require("VIOLATION" not in result.stdout, "libretro host reported a protocol violation")
    return result.stdout


def preflight():
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise ValueError("requires the declared Linux x86_64 runner")
    for tool in ("git", "cmake", "ninja", "clang", "clang++", "patch"):
        if not shutil.which(tool):
            raise ValueError(f"missing preinstalled tool: {tool}")


def build_commands():
    return (["cmake", "-S", str(ROOT), "-B", str(TREE), "-G", "Ninja",
             "-DCMAKE_TOOLCHAIN_FILE=" + str(ROOT / "cmake/toolchains/linux-clang.cmake"),
             "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -g",
             "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2 -g", "-DP6_SANITIZERS=OFF"],
            ["cmake", "--build", str(TREE), "--target", *TARGETS, "-j", "2"])


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
        require(sorted(names) == sorted((*HARDWARE, "video")) and any(s.startswith("chip ") for s in hardware)
                and any(s.startswith("pcm ") for s in av) and any(s.startswith("picture ") for s in av), "incomplete native future")
        result.append([re.sub(r"^(\w+) \d+ ", r"\1 UNIT ", line) for line in hardware + av])
    return result


def check_native(work, rom):
    state, again, final, restored = [work / name for name in ("cut.state", "again.state", "final.state", "restored.state")]
    probe = [TREE / "p6_state_probe", rom, "--every", "1"]
    original = execute([*probe, "--units", "8", "--public-save-at", "4", state, "--public-save-at", "8", final])
    require(state.is_file() and state.stat().st_size > 0 and final.is_file() and final.stat().st_size > 0, "native save did not produce complete states")
    cold = execute([*probe, "--units", "4", "--public-load-at", "0", state, "--public-save-at", "0", again, "--public-save-at", "4", restored])
    require(re.search(r"^load 0 0 *$", cold, re.M), "native cold load was refused")
    require(again.is_file() and restored.is_file() and state.read_bytes() == again.read_bytes()
            and final.read_bytes() == restored.read_bytes(), "native cold fullstate mismatch")
    require(native_future(original, 5, 4) == native_future(cold, 1, 4), "native cold hardware/AV/PCM future differs")


def check_frontends(work, rom):
    headless = rows(execute([TREE / "p6_headless", "--run", rom, "--units", "14"]), "unit", 14)
    system = work / "system"; system.mkdir(); shutil.copyfile(rom, system / "kick34005.A500")
    host = [TREE / "p6_libretro_host", TREE / "puae6_libretro.so", "--system", system]
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


def run():
    preflight(); before = source_identity()
    for argv in build_commands():
        execute(argv, 1800)
    TREE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scenario-", dir=TREE) as directory:
        work = Path(directory)
        spec = importlib.util.spec_from_file_location("microrom", ROOT / "tests/fixtures/microrom/build_rom.py")
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        for name in ("input_all", "boot"):
            (work / (name + ".rom")).write_bytes(builder.build((ROOT / f"tests/fixtures/microrom/{name}.s").read_text()))
        # boot avoids frontend mouse/joystick defaults; input_all exercises native input restoration.
        check_native(work, work / "input_all.rom"); check_frontends(work, work / "boot.rom")
    require(source_identity() == before, "sources changed during the gate")
    return dict(status="PASS", scope="native Linux O2; not Waterbox or accumulated release", revision=execute(["git", "rev-parse", "HEAD"]).strip(),
                source_sha256=before, runner=platform.platform(), patchset_sha256=digest(ROOT / "patches/winuae/series.json"),
                flags="-O2 -g; assertions on; sanitizers off", toolchain_lock_sha256=digest(ROOT / "plan/toolchains.json"),
                compilers={name: execute([name, "--version"]).splitlines()[0] for name in ("/usr/bin/clang", "/usr/bin/clang++")},
                cmake_cache_sha256=digest(TREE / "CMakeCache.txt"),
                codegen_sha256=digest(ROOT / "build/generated/codegen-receipt.json"),
                binaries={name: digest(TREE / (name + (".so" if name == "puae6_libretro" else ""))) for name in TARGETS})


if __name__ == "__main__":
    try:
        result = run()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        result = dict(status="BLOCKED" if isinstance(error, ValueError) else "FAIL", diagnostic=str(error))
    print(redact(json.dumps(result, sort_keys=True)))
    sys.exit(0 if result["status"] == "PASS" else 1)
