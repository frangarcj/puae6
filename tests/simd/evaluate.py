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


def sources():
    return {name: digest(ROOT / name) for name in (
        "tests/simd/rgb565_probe.cpp", "tests/simd/rgb565_candidate.h", "ports/portable/simd/rgb565.h",
        "adapters/libretro/present.h", "adapters/libretro/options_generated.h", "bridge/include/p6.h",
        "references/libretro-api/include/libretro.h", "tests/simd/evaluate.py")}


def flags(mode, row=None):
    if mode not in ("opt", "fallback", "san"):
        raise ValueError("unknown SIMD mode")
    result = ["-std=c++17", "-O2", "-fno-fast-math", "-ffp-contract=off", "-Wall", "-Wextra", "-Werror"]
    if mode == "san":
        result += ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    if mode == "fallback":
        result += ["-DP6_SIMD_NO_NEON=1"] + (["-fno-tree-vectorize", "-fno-tree-slp-vectorize"]
                  if row and row.endswith("gcc") else ["-fno-vectorize", "-fno-slp-vectorize"])
    return result


def compiler_configuration(row=None):
    """Relocation selects authenticated LLVM bytes, never a different compiler."""
    toolchains = json.loads((ROOT / "plan/toolchains.json").read_text())
    pin = toolchains["portability_matrix"]["rows"][row] if row else toolchains["tools"]["clang"]
    compiler = Path("/usr/bin/g++" if row and row.endswith("gcc") else "/usr/bin/clang++") if row else Path(pin["path"]).with_name("clang++")
    driver, compile_env, version_env = [str(compiler)], None, None
    if row is None and "P6_WATERBOX_HOSTED_TOOLS" in os.environ:
        from tools.ci import hosted_environment as hosted
        manifest, _ = hosted.hosted.inputs()
        paths = {name: ROOT / "build/hosted-tools" / path for name, path in manifest["entrypoints"].items()}
        environment = hosted.select({**paths, "make": Path("/usr/bin/make")})
        compiler = paths["clang"]
        driver = [str(environment["launchers"]["clang++"])]
        compile_env, version_env = environment["tool_env"], environment["direct_env"]
    compiler_sha = digest(compiler)
    if not row and compiler_sha != pin["sha256"]:
        raise ValueError("T35 compiler does not match the pinned clang binary")
    version_command = [str(compiler), *(["--driver-mode=g++"] if version_env is not None else []), "--version"]
    version = subprocess.check_output(version_command, text=True, env=version_env).splitlines()[0]
    if version != pin["compiler_version" if row else "version"]:
        raise ValueError(f"T35 compiler version differs: {version}")
    effective = Path(subprocess.check_output(["/usr/bin/xcrun", "--find", compiler.name], text=True).strip()) if row and platform.system() == "Darwin" else compiler
    metadata = {"compiler": version, "compiler_sha256": compiler_sha, "compiler_path": str(compiler), "driver_mode": "g++",
                "compiler_implementation": {"path": str(effective.resolve()), "sha256": digest(effective)}}
    return driver, compile_env, metadata


def build(directory, mode, row=None):
    driver, compile_env, metadata = compiler_configuration(row)
    before = sources()
    selected_flags = flags(mode, row)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / f"rgb565-{mode}"
    includes = [ROOT / p for p in ("tests/simd", "adapters/libretro", "references/libretro-api/include", "bridge/include")]
    source = ROOT / "tests/simd/rgb565_probe.cpp"
    command = [*driver, *selected_flags, *[f"-I{p}" for p in includes], str(source), "-o", str(exe)]
    compiled = subprocess.run(command, capture_output=True, text=True, env=compile_env)
    if compiled.returncode:
        raise RuntimeError(f"T35 compile failed: {compiled.stderr[-4000:]}")
    if before != sources():
        raise ValueError("SIMD sources changed during compilation")
    return exe, {**metadata, "flags": selected_flags, "sources": before, "binary_sha256": digest(exe)}


def run(exe, *args, asan_options=None):
    return subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "ASAN_OPTIONS": asan_options or os.environ.get("ASAN_OPTIONS", "detect_leaks=1")})


def evaluate(directory, mode, row=None):
    exe, metadata = build(directory, mode, row)
    # T25's rows use ASan/UBSan, without LSan (unsupported by Apple Clang's
    # runtime and by translated x86_64). The LLVM experiment also tests LSan.
    asan_options = "detect_leaks=0" if row else os.environ.get("ASAN_OPTIONS", "detect_leaks=1")
    result = run(exe, asan_options=asan_options)
    if result.returncode:
        raise RuntimeError(result.stderr)
    if metadata["binary_sha256"] != digest(exe) or metadata["sources"] != sources() or metadata["compiler_sha256"] != digest(metadata["compiler_path"]):
        raise ValueError("SIMD binary/compiler/sources changed during execution")
    return {**metadata, "mode": mode, "row": row, "sanitizer_options": asan_options, "environment": {"os": platform.platform(), "isa": platform.machine(), "load_average": os.getloadavg(), "dedicated_runner": False},
            **json.loads(result.stdout)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("opt", "san", "fallback"), default="opt")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--row", choices=tuple(json.loads((ROOT / "plan/toolchains.json").read_text())["portability_matrix"]["rows"]))
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/simd")
    args = parser.parse_args()
    result = evaluate(args.build_dir, args.mode, args.row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
