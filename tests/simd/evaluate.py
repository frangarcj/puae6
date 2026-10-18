"""Build/run the T35 pixel experiment with a pinned compiler. No product flags change."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(directory, mode):
    pin = json.loads((ROOT / "plan/toolchains.json").read_text())["tools"]["clang"]
    compiler = Path(pin["path"]).with_name("clang++")
    if digest(compiler) != pin["sha256"]:
        raise ValueError("T35 compiler does not match the pinned clang binary")
    version = subprocess.check_output([str(compiler), "--version"], text=True).splitlines()[0]
    if version != pin["version"]:
        raise ValueError(f"T35 compiler version differs: {version}")
    flags = ["-std=c++17", "-O2", "-fno-fast-math", "-ffp-contract=off", "-Wall", "-Wextra", "-Werror"]
    if mode == "san":
        flags += ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    if mode == "fallback":
        flags += ["-DP6_SIMD_NO_NEON=1", "-fno-vectorize", "-fno-slp-vectorize"]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / f"rgb565-{mode}"
    includes = [ROOT / p for p in ("tests/simd", "adapters/libretro", "references/libretro-api/include", "bridge/include")]
    source = ROOT / "tests/simd/rgb565_probe.cpp"
    command = [str(compiler), *flags, *[f"-I{p}" for p in includes], str(source), "-o", str(exe)]
    compiled = subprocess.run(command, capture_output=True, text=True)
    if compiled.returncode:
        raise RuntimeError(f"T35 compile failed: {compiled.stderr[-4000:]}")
    return exe, {"compiler": version, "compiler_sha256": pin["sha256"], "flags": flags,
                 "sources": {str(p.relative_to(ROOT)): digest(p) for p in (source, ROOT / "tests/simd/rgb565_candidate.h", ROOT / "adapters/libretro/present.h", ROOT / "adapters/libretro/options_generated.h", ROOT / "bridge/include/p6.h", ROOT / "references/libretro-api/include/libretro.h", Path(__file__))}}


def run(exe, *args):
    return subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"})


def evaluate(directory, mode):
    exe, metadata = build(directory, mode)
    result = run(exe)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return {**metadata, "mode": mode, "environment": {"os": platform.platform(), "isa": platform.machine(), "load_average": os.getloadavg(), "dedicated_runner": False},
            **json.loads(result.stdout)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("opt", "san", "fallback"), default="opt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(ROOT / "build/simd", args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
