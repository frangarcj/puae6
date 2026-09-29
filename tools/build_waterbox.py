#!/usr/bin/env python3
"""Prepare and build the local Darwin ARM64 Waterbox smoke with pinned inputs.

This consumes only pinned snapshots and explicitly inventoried local tool/cache
inputs. It never modifies the MacHawk checkout and does not run the WBX guest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import platform
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/waterbox"
LOCK = ROOT / "plan/toolchains.json"
PAGE_SIZE = 16384


class BuildError(ValueError):
    """A fixed input or build artifact violated the T09 contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def inventory(path: Path) -> list[dict]:
    """Use the source importer inventory format, including safe symlinks."""
    sys.path.insert(0, str(ROOT / "tools"))
    import import_sources

    try:
        return import_sources.inventory(path)
    except (OSError, ValueError) as error:
        raise BuildError(f"inventario inválido {path}: {error}") from error


def source_spec(source_id: str) -> tuple[dict, Path]:
    sys.path.insert(0, str(ROOT / "tools"))
    import import_sources

    try:
        item = import_sources.source(ROOT, source_id)
        if not item.get("local_path"):
            raise BuildError(f"la fuente {source_id} no es un checkout local fijado")
        checkout = (ROOT / item["local_path"]).resolve(strict=True)
        import_sources.check_pins(checkout, item)
        # blobs() also checks all selected worktree paths against their pinned
        # Git blobs or explicitly recorded overlays, without writing there.
        for _entry in import_sources.blobs(checkout, item):
            pass
        import_sources.verify_source(ROOT, source_id)
        return item, checkout
    except (OSError, KeyError, StopIteration, subprocess.CalledProcessError,
            ValueError) as error:
        raise BuildError(f"SRC01 {source_id}: {error}") from error


def verify_sysroot(lock: dict) -> tuple[Path, list[dict]]:
    spec = lock["sysroot"]
    source = (ROOT / spec["source_path"]).resolve(strict=True)
    entries = inventory(source)
    observed = canonical_hash(entries)
    if observed != spec["inventory_sha256"] or len(entries) != spec["file_count"]:
        raise BuildError(
            f"sysroot cambió: inventory sha256={observed}, archivos={len(entries)}"
        )
    return source, entries


def copy_verified(source: Path, destination: Path, expected: list[dict]) -> None:
    if destination.exists() or destination.is_symlink():
        actual = inventory(destination)
        if actual != expected:
            raise BuildError(f"copia cacheada modificada: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".waterbox-copy-", dir=destination.parent) as temp:
        staged = Path(temp) / destination.name
        shutil.copytree(source, staged, symlinks=True, copy_function=shutil.copy2)
        if inventory(staged) != expected:
            raise BuildError(f"la copia no coincide con su origen: {source}")
        os.replace(staged, destination)


def locked_tools(lock: dict) -> tuple[dict[str, Path], dict[str, str]]:
    tools = lock["tools"]
    required = ("clang", "lld", "cargo", "rustc", "make")
    paths: dict[str, Path] = {}
    versions: dict[str, str] = {}
    version_args = {
        "clang": ["--version"], "lld": ["--version"],
        "cargo": ["--version"], "rustc": ["--version"], "make": ["--version"],
    }
    for name in required:
        entry = tools[name]
        path = Path(entry["path"])
        observed_hash = sha256(path)
        if observed_hash != entry["sha256"]:
            raise BuildError(f"hash de {name} distinto: {observed_hash}")
        result = subprocess.run([str(path), *version_args[name]], check=True,
                                text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        version = result.stdout.splitlines()[0].strip()
        if version != entry["version"]:
            raise BuildError(f"versión de {name} distinta: {version!r}")
        paths[name], versions[name] = path, version

    path_dirs = [str(paths[name].parent) for name in required]
    env_path = os.pathsep.join(path_dirs + [os.environ.get("PATH", "")])
    linker_name = subprocess.run([str(paths["clang"]), "-print-prog-name=ld.lld"],
                                 check=True, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env={**os.environ, "PATH": env_path})
    linker = Path(linker_name.stdout.strip())
    if not linker.is_absolute():
        resolved = shutil.which(str(linker), path=env_path)
        if not resolved:
            raise BuildError("clang no puede resolver ld.lld en el PATH fijado")
        linker = Path(resolved)
    if linker.resolve(strict=True) != paths["lld"].resolve(strict=True):
        raise BuildError(f"clang resuelve otro linker: {linker}")
    return paths, versions


def validate_elf(path: str | Path, host_page_size: int = PAGE_SIZE) -> dict:
    """Validate an ELF64 little-endian AArch64 WBX and return load metadata."""
    elf_path = Path(path)
    if not elf_path.is_file():
        raise BuildError(f"guest ELF no existe: {elf_path}")
    raw = elf_path.read_bytes()
    if len(raw) < 64 or raw[:4] != b"\x7fELF":
        raise BuildError(f"{elf_path}: magic ELF inválido o archivo truncado")
    if raw[4] != 2:
        raise BuildError(f"{elf_path}: se requiere ELF64")
    if raw[5] != 1:
        raise BuildError(f"{elf_path}: se requiere ELF little-endian")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", raw)
    elf_type, machine = header[1], header[2]
    phoff, phentsize, phnum = header[5], header[9], header[10]
    if elf_type != 2:
        raise BuildError(f"{elf_path}: ELF debe ser ET_EXEC, no tipo {elf_type}")
    if machine != 183:
        raise BuildError(f"{elf_path}: se requiere EM_AARCH64 (183), machine={machine}")
    if phentsize < 56 or phoff + phentsize * phnum > len(raw):
        raise BuildError(f"{elf_path}: tabla de program headers inválida")

    loads = []
    page_flags: dict[int, int] = {}
    for index in range(phnum):
        p_type, flags, offset, vaddr, _paddr, filesz, memsz, align = struct.unpack_from(
            "<IIQQQQQQ", raw, phoff + index * phentsize
        )
        if p_type != 1:  # PT_LOAD
            continue
        if offset + filesz > len(raw):
            raise BuildError(f"{elf_path}: PT_LOAD {index} excede el archivo")
        if memsz < filesz:
            raise BuildError(f"{elf_path}: PT_LOAD {index} tiene memsz < filesz")
        if offset % host_page_size or vaddr % host_page_size:
            raise BuildError(f"{elf_path}: PT_LOAD {index} no empieza alineado a {host_page_size}")
        if align < host_page_size or align & (align - 1) or offset % align != vaddr % align:
            raise BuildError(f"{elf_path}: PT_LOAD {index} p_align menor a {host_page_size}")
        if memsz > 2**32 or vaddr + memsz > 2**64:
            raise BuildError(f"{elf_path}: PT_LOAD fuera del limite de una ventana Waterbox")
        if flags & 0x3 == 0x3:
            raise BuildError(f"{elf_path}: PT_LOAD {index} combina escritura y ejecución")
        if memsz:
            first = vaddr // host_page_size
            last = (vaddr + memsz - 1) // host_page_size
            for page in range(first, last + 1):
                previous = page_flags.setdefault(page, flags)
                if previous != flags:
                    raise BuildError(f"{elf_path}: protecciones incompatibles en página {page:#x}")
        loads.append({"index": index, "offset": offset, "vaddr": vaddr,
                      "filesz": filesz, "memsz": memsz, "flags": flags,
                      "align": align})
    if not loads:
        raise BuildError(f"{elf_path}: no contiene PT_LOAD")
    return {"path": str(elf_path), "size": len(raw), "sha256": sha256(elf_path),
            "class": 64, "endian": "little", "type": "ET_EXEC",
            "machine": "EM_AARCH64", "host_page_size": host_page_size,
            "load_segments": loads}


def install_wrappers(sysroot: Path, clang: Path, lld: Path, musl_root: Path) -> tuple[Path, Path]:
    wrapper_dir = BUILD / "toolchain/bin"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "@CC@": str(clang),
        "@PREFIX@": str(sysroot),
        "@INCDIR@": str(sysroot / "include"),
        "@LIBDIR@": str(sysroot / "lib"),
        "@LDSO@": "/lib/libc.so",
    }
    wrappers = []
    for template, output in (("musl-clang.in", "musl-clang"),
                             ("ld.musl-clang.in", "ld.musl-clang")):
        source = musl_root / "tools" / template
        text = source.read_text()
        for token, value in values.items():
            if any(c in value for c in '\\"$`\n') or (token == "@CC@" and ' ' in value):
                raise BuildError("Ruta no soportada por los wrappers shell de musl")
            text = text.replace(token, value)
        if re.search(r"@[A-Z]+@", text):
            raise BuildError(f"placeholder sin sustituir en {source}")
        target = wrapper_dir / output
        target.write_text(text)
        target.chmod(0o755)
        wrappers.append(target)
    return tuple(wrappers)  # type: ignore[return-value]


def copy_runtime_source(runtime_source: Path, reference_manifest: Path,
                        destination: Path) -> list[dict]:
    manifest = json.loads(reference_manifest.read_text())
    prefix = "waterbox/waterboxhost/"
    expected = []
    for entry in manifest["files"]:
        if entry["path"].startswith(prefix):
            expected.append({**entry, "path": entry["path"][len(prefix):]})
    expected.sort(key=lambda entry: entry["path"])
    if not expected:
        raise BuildError("el manifiesto machawk no incluye WaterboxHost")
    copy_verified(runtime_source, destination, expected)
    return expected


def run_logged(command: list[str], env: dict[str, str], cwd: Path,
               *, parse_tests: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, end="")
    if result.returncode:
        raise BuildError(f"comando falló ({result.returncode}): {command}")
    if parse_tests:
        rows = re.findall(
            r"test result: ok\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored;",
            result.stdout,
        )
        passed = sum(int(row[0]) for row in rows)
        failed = sum(int(row[1]) for row in rows)
        if passed != 8 or failed or any(int(row[2]) for row in rows):
            raise BuildError(f"cargo test sin suite no ignorada válida (passed={passed}, failed={failed})")
    return result.stdout


def build(args: argparse.Namespace) -> dict:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise BuildError("Este perfil requiere runner real Darwin ARM64")
    import import_sources
    import_sources.no_links(ROOT, BUILD / "toolchain/bin")
    import_sources.no_links(ROOT, BUILD / "runtime-src")
    import_sources.no_links(ROOT, BUILD / "sysroot")
    lock = json.loads(LOCK.read_text())
    if lock.get("schema_version") != 1:
        raise BuildError("schema_version de toolchains.json no es 1")
    paths, versions = locked_tools(lock)

    machawk, _ = source_spec("machawk")
    musl, _ = source_spec(lock["sysroot"]["associated_source"])
    emutls, _ = source_spec("compiler_rt_emutls")
    machawk_manifest = ROOT / machawk["snapshot"]["manifest"]
    source_manifest_hashes = {
        "machawk": sha256(machawk_manifest),
        "waterbox_musl_darwin": sha256(ROOT / musl["snapshot"]["manifest"]),
    }

    sysroot_source, sysroot_inventory = verify_sysroot(lock)
    sysroot_copy = BUILD / "sysroot"
    copy_verified(sysroot_source, sysroot_copy, sysroot_inventory)
    musl_root = ROOT / musl["snapshot"]["destination"]
    cc_wrapper, ld_wrapper = install_wrappers(
        sysroot_copy, paths["clang"], paths["lld"], musl_root
    )

    runtime_source = ROOT / machawk["snapshot"]["destination"] / "waterbox/waterboxhost"
    runtime_copy = BUILD / "runtime-src"
    runtime_inventory = copy_runtime_source(runtime_source, machawk_manifest, runtime_copy)

    env_path = os.pathsep.join(
        [str(paths[n].parent) for n in ("clang", "lld", "cargo", "rustc", "make")]
        + [str(cc_wrapper.parent), os.environ.get("PATH", "")]
    )
    env = os.environ.copy()
    for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "MAKEFLAGS", "CPATH", "CPLUS_INCLUDE_PATH", "C_INCLUDE_PATH", "LIBRARY_PATH"):
        env.pop(key, None)
    env.update({
        "PATH": env_path,
        "RUSTC": str(paths["rustc"]),
        "RUSTC_BOOTSTRAP": lock["runtime"]["rustc_bootstrap"],
        "CARGO_HOME": str(BUILD / "cargo-home"),
        "CARGO_TARGET_DIR": str(BUILD / "cargo"),
    })
    BUILD.mkdir(parents=True, exist_ok=True)

    if args.fetch:
        run_logged([str(paths["cargo"]), "fetch", "--locked", "--target",
                    lock["runtime"]["target"], "--manifest-path",
                    str(runtime_copy / "Cargo.toml")], env, ROOT)

    cargo_lock = runtime_copy / "Cargo.lock"
    if not cargo_lock.is_file():
        raise BuildError("Runtime-src no contiene Cargo.lock")
    cargo_test_output = run_logged(
        [str(paths["cargo"]), "test", "--offline", "--locked", "--target",
         lock["runtime"]["target"], "--manifest-path", str(runtime_copy / "Cargo.toml"),
         "--", "--test-threads=1"],
        env, ROOT, parse_tests=True,
    )
    run_logged(
        [str(paths["cargo"]), "build", "--offline", "--locked", "--target",
         lock["runtime"]["target"], "--manifest-path", str(runtime_copy / "Cargo.toml")],
        env, ROOT,
    )
    dylib = BUILD / "cargo" / lock["runtime"]["target"] / lock["runtime"]["profile"] / "libwaterboxhost.dylib"
    if not dylib.is_file():
        raise BuildError(f"cargo build no produjo dylib esperada: {dylib}")
    header = dylib.read_bytes()[:32]
    if (len(header) != 32 or struct.unpack_from("<II", header) != (0xfeedfacf, 0x100000c)
            or struct.unpack_from("<I", header, 12)[0] != 6):
        raise BuildError("El runtime producido no es Mach-O64 ARM64 MH_DYLIB")

    smoke_makefile = ROOT / "tests/waterbox-smoke/Makefile"
    guest_source = ROOT / "tests/waterbox-smoke/guest.cpp"
    common_mak = ROOT / "references/machawk/waterbox/common.mak"
    linkscript = ROOT / "references/machawk/waterbox/linkscript_aarch64.T"
    for input_path in (smoke_makefile, guest_source, common_mak, linkscript):
        if not input_path.is_file():
            raise BuildError(f"falta input de guest fijado en snapshot: {input_path}")
    run_logged([
        str(paths["make"]), "-B", "-C", str(smoke_makefile.parent), "release",
        f"SYSROOT={sysroot_copy}", f"CC={cc_wrapper}", f"CXX={cc_wrapper}",
        f"OUT_DIR={BUILD / 'guest'}", "WBX_TARGET_ARCH=aarch64",
        f"WBX_HOST_PAGE_SIZE={PAGE_SIZE}",
    ], env, ROOT)
    guest = BUILD / "guest/release/p6-smoke.wbx"
    guest_metadata = validate_elf(guest, PAGE_SIZE)
    dylib_metadata = {"path": str(dylib), "size": dylib.stat().st_size,
                      "sha256": sha256(dylib)}
    input_hashes = {
        "toolchains_json": sha256(LOCK),
        "build_recipe": sha256(Path(__file__)),
        "machawk_manifest": source_manifest_hashes["machawk"],
        "musl_manifest": source_manifest_hashes["waterbox_musl_darwin"],
        "emutls_manifest": sha256(ROOT / emutls["snapshot"]["manifest"]),
        "cargo_lock": sha256(cargo_lock),
        "common_mak": sha256(common_mak),
        "linkscript_aarch64": sha256(linkscript),
        "smoke_makefile": sha256(smoke_makefile),
        "guest_cpp": sha256(guest_source),
        "musl_clang_template": sha256(musl_root / "tools/musl-clang.in"),
        "musl_ld_template": sha256(musl_root / "tools/ld.musl-clang.in"),
        "guest_wrapper": sha256(cc_wrapper),
        "ld_wrapper": sha256(ld_wrapper),
        "emulibc_source": sha256(ROOT / "references/machawk/waterbox/emulibc/emulibc.c"),
    }
    receipt = {
        "schema_version": 1,
        "status": "BUILD01_PASS_GUEST_NOT_EXECUTED",
        "profile": lock["profile"],
        "source_commits": {"machawk": machawk["commit"], "waterbox_musl_darwin": musl["commit"], "compiler_rt_emutls": emutls["commit"]},
        "tool_versions": versions,
        "tool_hashes": {name: entry["sha256"] for name, entry in lock["tools"].items()},
        "sysroot": {"source_path": lock["sysroot"]["source_path"],
                    "inventory_sha256": canonical_hash(sysroot_inventory),
                    "file_count": len(sysroot_inventory)},
        "runtime_source_inventory_sha256": canonical_hash(runtime_inventory),
        "input_sha256": input_hashes,
        "cargo_test_nonignored_output_sha256": hashlib.sha256(cargo_test_output.encode()).hexdigest(),
        "cargo_tests": {"passed": 8, "failed": 0, "ignored": 0},
        "guest": guest_metadata,
        "runtime_dylib": dylib_metadata,
    }
    receipt_path = BUILD / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"BUILD01 PASS: {receipt_path}")
    print("Tests internos del runtime ejecutados; guest aun no ejecutado dentro de Waterbox.")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true",
                        help="hacer cargo fetch --locked para el target ARM64 antes del build")
    parser.add_argument("--check-artifact", type=Path,
                        help="validar ELF WBX existente sin preparar ni compilar inputs")
    parser.add_argument("--snapshot-smoke", action="store_true",
                        help="construir y ejecutar la puerta T09b real (fallos no se convierten en PASS)")
    args = parser.parse_args()
    try:
        if args.check_artifact:
            print(json.dumps(validate_elf(args.check_artifact), indent=2, sort_keys=True))
        else:
            build(args)
            if args.snapshot_smoke:
                managed = ROOT / "integration/bizhawk/smoke"
                env = {**os.environ, "DOTNET_CLI_HOME": str(BUILD / "dotnet-home"),
                       "NUGET_PACKAGES": str(BUILD / "nuget"), "DOTNET_ROLL_FORWARD": "Major",
                       "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
                dotnet = shutil.which("dotnet")
                if not dotnet:
                    raise BuildError("Falta dotnet para el harness managed")
                run_logged([dotnet, "build", "Smoke.csproj", "--nologo"], env, managed)
                run_logged([dotnet, str(BUILD / "managed/bin/Debug/net8.0/Smoke.dll"),
                            str(BUILD / "receipt.json"), str(BUILD / "smoke-result.json")], env, managed)
    except (BuildError, OSError, KeyError, subprocess.CalledProcessError,
            json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
