#!/usr/bin/env python3
"""Prepare and build the local Darwin ARM64 Waterbox smoke with pinned inputs.

This consumes only pinned snapshots and explicitly inventoried local tool/cache
inputs. It never modifies the MacHawk checkout and does not run the WBX guest.
"""
from __future__ import annotations

import argparse
import functools
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

# T34a: the local copy of libwaterboxhost is built Release by default (plan/toolchains.json runtime.profile): the same code BizHawk ships, ~0.0004 ms per
# sandbox entry against ~3.1 ms in Debug. The Debug runtime (runtime assertions, eviction of the sandbox forced on at every call) is a lane of its own,
# chosen with P6_WATERBOX_RUNTIME=debug: it builds into cargo/<target>/debug and writes build/waterbox/receipt-debug.json, so both can coexist.
RUNTIME_PROFILES = ("release", "debug")
RUNTIME_ENV = "P6_WATERBOX_RUNTIME"


def runtime_rustflags(root: Path, build: Path) -> str:
    """RUSTFLAGS of the runtime build. The library carries the paths of the tree it was compiled in (panic locations, dependency sources, and the install name of the dylib, which
    cargo makes the absolute path of target/<profile>/deps). Remapping the first and giving the dylib a fixed install name make its sha256 the same wherever the repository or its
    worktree lives (a later prefix wins, so the longest goes last). Its sha256 still depends on the pinned toolchain and sources, as it must."""
    return " ".join([f"--remap-path-prefix={Path.home()}=/p6/home", f"--remap-path-prefix={root}=/p6/src", f"--remap-path-prefix={build}=/p6/waterbox",
                     "-Clink-arg=-Wl,-install_name,@rpath/libwaterboxhost.dylib"])


def default_runtime_profile() -> str:
    profile = json.loads(LOCK.read_text())["runtime"]["profile"]
    if profile not in RUNTIME_PROFILES:
        raise BuildError(f"plan/toolchains.json runtime.profile debe ser uno de {RUNTIME_PROFILES}, no {profile!r}")
    return profile


def runtime_profile() -> str:
    """The profile the runtime of this process is built with: the one of plan/toolchains.json, or P6_WATERBOX_RUNTIME (the Debug lane)."""
    profile = os.environ.get(RUNTIME_ENV) or default_runtime_profile()
    if profile not in RUNTIME_PROFILES:
        raise BuildError(f"{RUNTIME_ENV} debe ser uno de {RUNTIME_PROFILES}, no {profile!r}")
    return profile


def runtime_receipt_path(profile: str | None = None) -> Path:
    """build/waterbox/receipt.json for the default profile, receipt-<profile>.json for the other lane."""
    profile = profile or runtime_profile()
    return BUILD / ("receipt.json" if profile == default_runtime_profile() else f"receipt-{profile}.json")


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


PT_LOAD, PT_TLS, PF_X = 1, 7, 1
SHF_TLS, STT_TLS, SHT_SYMTAB, SHT_DYNSYM = 0x400, 6, 2, 11
# MRS/MSR encodings; the low five bits carry Xt. XNU rewrites TPIDR_EL0 at every
# context switch, so guest code that reads or writes it cannot be correct.
THREAD_REGISTER_ACCESS = {
    0xD53BD040: "mrs TPIDR_EL0", 0xD51BD040: "msr TPIDR_EL0",
    0xD53BD060: "mrs TPIDRRO_EL0",
}


def check_no_compiler_tls(path: str | Path, limit: int = 4) -> dict:
    """WB16: the final guest ELF has no compiler-generated TLS (docs/adr/waterbox-compiler-tls.md).

    Rejects PT_TLS, SHF_TLS sections, STT_TLS symbols and any executable word that
    is an MRS/MSR of TPIDR_EL0 or MRS of TPIDRRO_EL0. Scanning executable words
    can only false-positive on data embedded in code, which fails safe.
    """
    elf_path = Path(path)
    raw = elf_path.read_bytes()
    if len(raw) < 64 or raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
        raise BuildError(f"{elf_path}: WB16 requiere un ELF64 little-endian")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", raw)
    phoff, shoff = header[5], header[6]
    phentsize, phnum, shentsize, shnum, shstrndx = header[9], header[10], header[11], header[12], header[13]
    if phentsize < 56 or phoff + phentsize * phnum > len(raw):
        raise BuildError(f"{elf_path}: tabla de program headers inválida")
    found: list[str] = []
    words = 0
    for index in range(phnum):
        p_type, flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", raw, phoff + index * phentsize)
        if p_type == PT_TLS:
            found.append(f"PT_TLS en el program header {index}")
        if p_type != PT_LOAD or not flags & PF_X:
            continue
        if offset + filesz > len(raw):
            raise BuildError(f"{elf_path}: PT_LOAD {index} excede el archivo")
        count = filesz // 4
        for position, word in enumerate(struct.unpack_from(f"<{count}I", raw, offset)):
            words += 1
            access = THREAD_REGISTER_ACCESS.get(word & ~0x1F)
            if access and len(found) < 64:
                found.append(f"{access} en {vaddr + position * 4:#x}")
    tls_sections = symbol_tables = 0
    if shoff and shnum and shentsize >= 64 and shoff + shentsize * shnum <= len(raw):
        sections = [struct.unpack_from("<IIQQQQIIQQ", raw, shoff + i * shentsize) for i in range(shnum)]
        names = sections[shstrndx] if shstrndx < shnum else None
        for name, sh_type, sh_flags, _addr, sh_offset, sh_size, sh_link, _info, _align, sh_entsize in sections:
            if sh_flags & SHF_TLS:
                label = ""
                if names:
                    start = names[4] + name
                    label = raw[start:raw.find(b"\0", start)].decode("ascii", "replace")
                found.append(f"seccion TLS {label or '?'}")
                tls_sections += 1
            if sh_type in (SHT_SYMTAB, SHT_DYNSYM) and sh_entsize == 24:
                symbol_tables += 1
                for at in range(sh_offset, sh_offset + sh_size - 23, 24):
                    if raw[at + 4] & 0xF == STT_TLS:
                        found.append(f"simbolo STT_TLS en la entrada {(at - sh_offset) // 24}")
    if found:
        shown = "; ".join(found[:limit]) + (f"; y {len(found) - limit} mas" if len(found) > limit else "")
        raise BuildError(f"WB16 {elf_path}: TLS del compilador en el guest: {shown}")
    return {"wb16": "PASS", "executable_words_scanned": words,
            "symbol_tables_checked": symbol_tables, "tls_sections": tls_sections}


# Assertions the managed harness must report for the whole T09 runtime gate, per check.
REQUIRED_ASSERTIONS = {
    "WB01": ["constructor_cpp", "musl_tls_key", "heap_limits", "frame_oracle", "buffer_bounds",
             "restore_same", "restore_new", "alternate_sandboxes", "invisible_poison"],
    "WB13": ["abi_callback_registers", "abi_callback_reentrant", "abi_across_restore", "abi_new_sandbox"],
    "WB14": ["page_first_write_group", "page_restore_clean_snapshot", "page_restore_rearm",
             "page_write_bounds", "ram_group_restore", "damaged_loads_classified"],
    "WB15": ["artifact_identity", "restore_fresh_process", "identity_mismatch_rejected"],
}
DAMAGED_IMAGES = ("wrong-machine", "bad-magic", "truncated", "mixed-rw-rx-group")
LAYOUT_PROBES = DAMAGED_IMAGES + ("unaligned-layout",)


def verify_smoke(result: dict, receipt: dict, damaged_dir: Path) -> dict:
    """Require the whole T09 runtime coverage; a missing or failing part is never a PASS."""
    if (result.get("status") != "PASS" or result.get("evidence") != "SMOKE_RUNTIME"
            or result.get("amiga_emulation") is not False):
        raise BuildError("el harness no informa un SMOKE_RUNTIME PASS sin emulacion Amiga")
    reported = result.get("checks", {})
    for check, names in REQUIRED_ASSERTIONS.items():
        missing = [name for name in names if name not in reported.get(check, [])]
        if missing:
            raise BuildError(f"{check}: el harness no cubre {', '.join(missing)}")
    runner = result.get("runner", {})
    if runner.get("architecture") != "Arm64" or not any(name in runner.get("os", "") for name in ("macOS", "Darwin")):
        raise BuildError(f"el harness no identifica un runner Darwin ARM64: {runner}")
    if (result.get("guest_sha256") != receipt["guest"]["sha256"]
            or result.get("runtime_sha256") != receipt["runtime_dylib"]["sha256"]):
        raise BuildError("el harness ejecuto un guest o runtime distinto del recibo de build")
    probes = {probe["kind"]: probe for probe in result.get("load_probes", [])}
    if set(probes) != set(LAYOUT_PROBES):
        raise BuildError(f"cargas dañadas sondeadas {sorted(probes)}, se esperaban {sorted(LAYOUT_PROBES)}")
    if probes["mixed-rw-rx-group"]["outcome"] == "accepted":
        raise BuildError("el runtime acepto un grupo de 16 KiB con RW dentro de RX")
    build_gate = {}
    for kind in DAMAGED_IMAGES:
        try:
            validate_elf(damaged_dir / f"{kind}.wbx")
        except BuildError as error:
            build_gate[kind] = str(error).split(": ", 1)[-1]
        else:
            raise BuildError(f"validate_elf acepta la imagen dañada {kind}")
    return {"checks": sorted(REQUIRED_ASSERTIONS),
            "runtime_outcomes": {kind: probe["outcome"] for kind, probe in probes.items()},
            "build_gate_rejections": build_gate}


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


def toolchain() -> dict:
    """The pinned toolchain, the verified sysroot and the musl wrappers: what every WBX guest of this project is built with."""
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
        "RUSTFLAGS": runtime_rustflags(ROOT, BUILD),
        "PATH": env_path,
        "RUSTC": str(paths["rustc"]),
        "RUSTC_BOOTSTRAP": lock["runtime"]["rustc_bootstrap"],
        "CARGO_HOME": str(BUILD / "cargo-home"),
        "CARGO_TARGET_DIR": str(BUILD / "cargo"),
    })
    return {"lock": lock, "paths": paths, "versions": versions, "machawk": machawk, "musl": musl,
            "machawk_manifest": machawk_manifest, "source_manifest_hashes": source_manifest_hashes,
            "sysroot_copy": sysroot_copy, "sysroot_inventory": sysroot_inventory, "musl_root": musl_root,
            "cc_wrapper": cc_wrapper, "ld_wrapper": ld_wrapper, "runtime_copy": runtime_copy,
            "runtime_inventory": runtime_inventory, "env": env}


def build(args: argparse.Namespace) -> dict:
    tc = toolchain()
    lock, paths, versions, machawk, musl = tc["lock"], tc["paths"], tc["versions"], tc["machawk"], tc["musl"]
    machawk_manifest, source_manifest_hashes = tc["machawk_manifest"], tc["source_manifest_hashes"]
    sysroot_copy, sysroot_inventory, musl_root = tc["sysroot_copy"], tc["sysroot_inventory"], tc["musl_root"]
    cc_wrapper, ld_wrapper, runtime_copy, runtime_inventory, env = (
        tc["cc_wrapper"], tc["ld_wrapper"], tc["runtime_copy"], tc["runtime_inventory"], tc["env"])
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
    profile = runtime_profile()
    dylib = cargo_build_runtime(tc, profile)
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
    guest_metadata["compiler_tls"] = check_no_compiler_tls(guest)

    # The direct thread_local case that failed inside Waterbox stays as a fixture:
    # it must build like any guest and be rejected by WB16, never executed.
    negative_dir = ROOT / "tests/waterbox-smoke/tls-negative"
    run_logged([
        str(paths["make"]), "-B", "-C", str(negative_dir), "release",
        f"SYSROOT={sysroot_copy}", f"CC={cc_wrapper}", f"CXX={cc_wrapper}",
        f"OUT_DIR={BUILD / 'tls-negative'}", "WBX_TARGET_ARCH=aarch64",
        f"WBX_HOST_PAGE_SIZE={PAGE_SIZE}",
    ], env, ROOT)
    negative = BUILD / "tls-negative/release/p6-tls-negative.wbx"
    validate_elf(negative, PAGE_SIZE)
    try:
        check_no_compiler_tls(negative)
    except BuildError as error:
        tls_negative = {"path": str(negative), "sha256": sha256(negative),
                        "rejected": True, "reason": str(error)}
    else:
        raise BuildError("WB16 no rechaza el caso tls-negative con thread_local")
    dylib_metadata = {"path": str(dylib), "size": dylib.stat().st_size,
                      "sha256": sha256(dylib)}
    input_hashes = {
        "toolchains_json": sha256(LOCK),
        "build_recipe": sha256(Path(__file__)),
        "machawk_manifest": source_manifest_hashes["machawk"],
        "musl_manifest": source_manifest_hashes["waterbox_musl_darwin"],
        "cargo_lock": sha256(cargo_lock),
        "common_mak": sha256(common_mak),
        "linkscript_aarch64": sha256(linkscript),
        "smoke_makefile": sha256(smoke_makefile),
        "guest_cpp": sha256(guest_source),
        "tls_negative_guest_cpp": sha256(negative_dir / "guest.cpp"),
        "tls_negative_makefile": sha256(negative_dir / "Makefile"),
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
        "source_commits": {"machawk": machawk["commit"], "waterbox_musl_darwin": musl["commit"]},
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
        "tls_negative": tls_negative,
        "runtime_dylib": dylib_metadata,
        "runtime_profile": profile,
    }
    receipt_path = runtime_receipt_path(profile)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"BUILD01 PASS: {receipt_path}")
    print("Tests internos del runtime ejecutados; guest aun no ejecutado dentro de Waterbox.")
    return receipt


def cargo_build_runtime(tc: dict, profile: str) -> Path:
    """cargo build of the runtime copy for `profile`, with the same environment as build(); returns the dylib."""
    run_logged([str(tc["paths"]["cargo"]), "build", "--offline", "--locked", *(["--release"] if profile == "release" else []), "--target",
                tc["lock"]["runtime"]["target"], "--manifest-path", str(tc["runtime_copy"] / "Cargo.toml")], tc["env"], ROOT)
    dylib = BUILD / "cargo" / tc["lock"]["runtime"]["target"] / profile / "libwaterboxhost.dylib"
    if not dylib.is_file():
        raise BuildError(f"cargo build no produjo dylib esperada: {dylib}")
    return dylib


def ensure_runtime_lane(profile: str) -> Path:
    """T34a: the receipt of the runtime built with `profile`, building it if it is missing or stale (the sources and the guest are the ones of build/waterbox/receipt.json).
    The default profile is what build() writes; the other lane is the same receipt with its own runtime_dylib, so both lanes run the same guest and the same managed harness."""
    if profile not in RUNTIME_PROFILES:
        raise BuildError(f"perfil de runtime desconocido: {profile!r}")
    path = runtime_receipt_path(profile)
    base_path = runtime_receipt_path(default_runtime_profile())
    if profile == default_runtime_profile():
        if not path.is_file():
            raise BuildError(f"falta {path}: ejecutar tools/build_waterbox.py")
        return path
    if not base_path.is_file():
        raise BuildError(f"falta {base_path}: ejecutar tools/build_waterbox.py")
    base = json.loads(base_path.read_text())
    tc = toolchain()
    dylib = cargo_build_runtime(tc, profile)
    receipt = {**base, "runtime_dylib": {"path": str(dylib), "size": dylib.stat().st_size, "sha256": sha256(dylib)}, "runtime_profile": profile}
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if not path.is_file() or path.read_text() != text:
        path.write_text(text)
    return path


IMPORTS = ROOT / "ports/waterbox/imports.json"


def allowed_imports() -> set[str]:
    inventory = json.loads(IMPORTS.read_text())
    allowed = {name for group in (inventory["libc"], inventory["cxx_runtime"], inventory["emulibc"]) for names in group.values() for name in names}
    overlap = allowed & set(inventory["forbidden"])
    if overlap:
        raise BuildError(f"imports.json autoriza nombres prohibidos: {sorted(overlap)}")
    return allowed


def classify_imports(undefined: set[str], defined: set[str], allowed: set[str]) -> dict:
    """What our objects ask the libc and the C++ runtime for: everything they reference and do not define themselves."""
    imports = undefined - defined
    return {"imports": sorted(imports), "unexpected": sorted(imports - allowed), "unused_allowances": sorted(allowed - imports)}


def audit_imports(tc: dict, objects: list[Path]) -> dict:
    """HOST01 for the guest: the libc names its objects import are exactly the authorized ones."""
    nm = tc["paths"]["clang"].parent / "llvm-nm"
    if not nm.is_file():
        raise BuildError(f"falta llvm-nm junto a clang: {nm}")
    def symbols(flag: str, columns: int) -> set[str]:
        result = subprocess.run([str(nm), flag, "--no-weak", *map(str, objects)], check=True, text=True, stdout=subprocess.PIPE)
        return {line.split()[-1] for line in result.stdout.splitlines() if len(line.split()) == columns and not line.endswith(":")}
    report = classify_imports(symbols("--undefined-only", 2), symbols("--defined-only", 3), allowed_imports())
    if report["unexpected"]:
        raise BuildError(f"HOST01: puae6.wbx importa nombres no autorizados por ports/waterbox/imports.json: {report['unexpected']}")
    return report


PUAE6_GUEST = "puae6.wbx"


def cmake_sources(target: str) -> list[str]:
    """The source files of a static library of CMakeLists.txt: the native build stays the single list of what is compiled."""
    text = (ROOT / "CMakeLists.txt").read_text()
    match = re.search(r"add_library\(%s\s+STATIC\s+(.*?)\)" % re.escape(target), text, re.S)
    if not match:
        raise BuildError(f"CMakeLists.txt no define la biblioteca {target}")
    return [token for token in match.group(1).split() if token.endswith((".c", ".cpp"))]


def puae6_sources() -> list[str]:
    """Everything that is linked into puae6.wbx, relative to the repository: engine, generated, portable host port, bridge, adapter."""
    selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
    sources = ([f"build/upstream/{unit}" for unit in selection["upstream_units"]]
               + [f"build/generated/{unit}" for unit in selection["generated_units"]]
               + cmake_sources("p6_portable") + cmake_sources("p6_bridge")
               + ["adapters/waterbox/abi_probe.cpp", "adapters/waterbox/puae6_guest.cpp"])
    if len(sources) != len(set(sources)):
        raise BuildError("fuentes duplicadas en la lista de puae6.wbx")
    missing = [source for source in sources if not (ROOT / source).is_file()]
    if missing:
        raise BuildError(f"faltan fuentes (materializar y generar primero): {missing[:5]}")
    return sources


STALE_TREES_HELP = ("mueva build/upstream y build/generated a build/stale/<nombre> (nunca se sobrescriben ni se borran aqui) y ejecute "
                    "python3 tools/materialize_upstream.py y python3 tools/generate_cpu.py")


def ensure_current_trees(root: Path = ROOT) -> dict:
    """T48f: the guest is only built from build/upstream and build/generated of the CURRENT patch series (patches/winuae/series.json).

    build/upstream goes through the official materializer: absent, it is materialized; present, its inventory must be the one the pristine snapshot plus
    the series gives, otherwise the materializer refuses and so does this. build/generated must carry the receipt of a codegen run over that same
    upstream commit and series (and the same recipe and source manifest), with the bytes the receipt lists. Nothing is rebuilt or overwritten here: a tree of another
    series is refused with the steps to renew it, instead of compiling a guest from it (a change that does not break the link would otherwise go unnoticed)."""
    import materialize_upstream
    import generate_cpu
    root = Path(root)
    try:
        receipt, upstream = materialize_upstream.materialize(root)
    except (ValueError, OSError) as error:
        raise BuildError(f"build/upstream no corresponde a la serie actual de patches/winuae/series.json: {error}\n  {STALE_TREES_HELP}") from error
    generated = root / "build/generated"
    if not (generated / "codegen-receipt.json").is_file():
        raise BuildError(f"falta {generated / 'codegen-receipt.json'}: ejecute python3 tools/generate_cpu.py")
    recipe_path, selection_path = root / "manifests/codegen.json", root / "manifests/source-manifest.json"
    products = [name for generator in json.loads(recipe_path.read_text())["generators"] for name in generator["outputs"]]
    try:
        cached = generate_cpu._cached_output(generated, products)
    except ValueError as error:
        raise BuildError(f"build/generated esta alterado o incompleto: {error}\n  {STALE_TREES_HELP}") from error
    identity = cached["identity"]
    expected = {"upstream_commit": receipt["upstream_commit"], "series": receipt["series_sha256"],
                "recipe": sha256(recipe_path), "source_manifest": sha256(selection_path)}
    stale = [f"{name}: {identity.get(name)} != {value}" for name, value in expected.items() if identity.get(name) != value]
    if stale:
        raise BuildError("build/generated se genero con otra serie o receta que las actuales (" + "; ".join(stale) + f")\n  {STALE_TREES_HELP}")
    return {"upstream": str(upstream), "generated": str(generated), **expected}


def build_puae6(tc: dict, jobs: int) -> dict:
    """Compile and link puae6.wbx with the pinned recipe and check the result; it does not run a machine."""
    ensure_current_trees()
    out = BUILD / "puae6"
    out.mkdir(parents=True, exist_ok=True)
    sources = puae6_sources()
    sources_mk = out / "sources.mk"
    sources_mk.write_text("SRCS := " + " \\\n  ".join(sources) + "\n")
    command = [str(tc["paths"]["make"]), "-C", str(ROOT), "-f", "ports/waterbox/Makefile", f"-j{jobs}", "release",
               f"REPO={ROOT}", f"ROOT_DIR={ROOT}", f"OUT_DIR={out}", f"SOURCES_MK={sources_mk}", f"SYSROOT={tc['sysroot_copy']}",
               f"CC={tc['cc_wrapper']}", f"CXX={tc['cc_wrapper']}", "WBX_TARGET_ARCH=aarch64", f"WBX_HOST_PAGE_SIZE={PAGE_SIZE}",
               "CCFLAGS_RELEASE=-O2", "CXXFLAGS_RELEASE=-O2"]
    log = out / "build.log"
    result = subprocess.run(command, cwd=ROOT, env=tc["env"], text=True, stdout=log.open("w"), stderr=subprocess.STDOUT)
    if result.returncode:
        raise BuildError(f"la receta de puae6.wbx fallo ({result.returncode}); ultimas lineas de {log}:\n" + "\n".join(log.read_text().splitlines()[-25:]))
    guest = out / "release" / PUAE6_GUEST
    metadata = validate_elf(guest, PAGE_SIZE)
    metadata["path"] = str(guest)
    metadata["compiler_tls"] = check_no_compiler_tls(guest)
    imports = audit_imports(tc, [out / "release" / f"{source}.o" for source in sources])
    receipt = {"schema_version": 1, "status": "BUILD01_PASS_GUEST_NOT_EXECUTED", "guest": metadata, "imports": imports,
               "sources": {source: sha256(ROOT / source) for source in sources},
               "recipe": {"makefile": sha256(ROOT / "ports/waterbox/Makefile"), "common_mak": sha256(ROOT / "references/machawk/waterbox/common.mak"),
                          "linkscript_aarch64": sha256(ROOT / "references/machawk/waterbox/linkscript_aarch64.T"), "build_recipe": sha256(Path(__file__))},
               "tool_versions": tc["versions"], "flags": {"optimization": "-O2", "target": "aarch64", "host_page_size": PAGE_SIZE}}
    (out / "puae6-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"PUAE6 BUILD PASS: {guest} ({metadata['size']} bytes, sha256 {metadata['sha256'][:16]}...)")
    return receipt


_TREES_CHECKED = False


def check_guest_matches_trees(guest_receipt: Path, root: Path = ROOT) -> None:
    """T48f: a guest is only measured if the engine trees are the current series' (checked once per process) and puae6.wbx was built from exactly those files:
    its receipt lists the sha256 of every source, and the ones under build/upstream and build/generated must still be the ones on disk."""
    global _TREES_CHECKED
    if not _TREES_CHECKED:
        ensure_current_trees(root)
        _TREES_CHECKED = True
    sources = json.loads(Path(guest_receipt).read_text())["sources"]
    stale = sorted(name for name, digest in sources.items()
                   if name.startswith(("build/upstream/", "build/generated/")) and (not (root / name).is_file() or sha256(root / name) != digest))
    if stale:
        raise BuildError(f"{guest_receipt} se construyo con otro build/upstream o build/generated ({len(stale)} fuentes distintas, p. ej. {stale[:3]}): "
                         "reconstruir el guest con tools/build_waterbox.py --puae6")


MEMORY_MANIFEST = ROOT / "manifests/waterbox-memory.json"
MANAGED = ROOT / "integration/bizhawk/smoke"


def managed_env() -> dict[str, str]:
    return {**os.environ, "DOTNET_CLI_HOME": str(BUILD / "dotnet-home"), "NUGET_PACKAGES": str(BUILD / "nuget"),
            "DOTNET_ROLL_FORWARD": "Major", "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}


@functools.cache
def build_managed() -> tuple[str, dict[str, str]]:
    """Builds the managed harness once per process, however many runs follow."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise BuildError("Falta dotnet para el harness managed")
    env = managed_env()
    run_logged([dotnet, "build", "Smoke.csproj", "--nologo"], env, MANAGED)
    return dotnet, env


FFI_PROJECT = ROOT / "tests/ffi/Ffi.csproj"
FFI_OUTPUT = ROOT / "build/ffi/bin/Debug/net8.0"


@functools.cache
def build_core() -> tuple[str, dict[str, str], Path]:
    """Builds the C# core (integration/bizhawk/core, compiled against the pinned MacHawk sources) and the host of its FFI tests (tests/ffi) once
    per process. The core is built together with the guest it belongs with: the SHA-256 in the receipt of the recipe is passed to the build and
    recorded in the assembly, and that is the only guest the wrapper will open. The runtime library is copied next to the test host, because
    BizHawk asks dlopen for the bare name libwaterboxhost.dylib and macOS looks for it in the working directory the host is run from.
    Packages are restored from the local package cache, never from the network."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise BuildError("Falta dotnet para el core de BizHawk")
    receipt, runtime_receipt = BUILD / "puae6/puae6-receipt.json", runtime_receipt_path()
    for needed in (receipt, runtime_receipt):
        if not needed.is_file():
            raise BuildError(f"falta {needed}: ejecutar tools/build_waterbox.py --puae6")
    guest_sha = json.loads(receipt.read_text())["guest"]["sha256"]
    runtime = json.loads(runtime_receipt.read_text())["runtime_dylib"]
    if sha256(Path(runtime["path"])) != runtime["sha256"]:
        raise BuildError("la biblioteca del runtime no es la del recibo")
    cache = Path.home() / ".nuget/packages"
    env = {**managed_env(), "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget")}
    run_logged([dotnet, "build", str(FFI_PROJECT), "--nologo", f"-p:Puae6GuestSha256={guest_sha}", f"-p:RestoreSources={cache}"], env, ROOT)
    output = FFI_OUTPUT
    if runtime_profile() != default_runtime_profile():          # the other lane gets its own copy of the host, so that two lanes never share one libwaterboxhost.dylib
        output = ROOT / f"build/ffi/lane-{runtime_profile()}"
        shutil.copytree(FFI_OUTPUT, output, dirs_exist_ok=True, copy_function=shutil.copy2)
    shutil.copy2(runtime["path"], output / "libwaterboxhost.dylib")
    return dotnet, env, output


def run_ffi(*args: str, timeout: int = 1800, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Runs the FFI test host (tests/ffi/Program.cs) with `args`; returns the finished process. By default it runs from the directory that holds the runtime library;
    `env` adds to the environment (tests of the host's locale, time zone and the like) and `cwd` runs it from elsewhere: the runtime library is then found through
    the arguments (`--runtime`), which tests/ffi loads by its absolute path first."""
    dotnet, benv, output = build_core()
    return subprocess.run([dotnet, str(output / "Ffi.dll"), *args], cwd=cwd or output, env={**benv, **(env or {})}, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


_RUNS = iter(range(1, 1 << 30))


def run_puae6(firmware: Path, units: int, media: tuple[Path, ...] = (), guest_receipt: Path | None = None, *, ports: tuple[str, ...] = (),
              render: str | None = None, digest_every: int = 0, bad_calls_at: int = 0, limits: bool = False, ntsc: bool = False, machine: str = "A500",
              abi_at: tuple[int, ...] = (), pages_at: int = 0, memory_at: int | None = None, save_at: int = 0, state: Path | None = None, restore: Path | None = None,
              restore_from: int = 0, timeout: int = 3600, chip_kib: int = 0, slow_kib: int | str = 0, fast_kib: int = 0, fpu_model: int = 0,
              hard_files: tuple[Path, ...] = (), export_at: tuple[int, ...] = (), export_dir: Path | None = None, rewind_to: int = 0, rewind_from: int = 0) -> tuple[list[str], dict]:
    """Runs puae6.wbx in a real Waterbox sandbox (init with the firmware and the ADF images, seal, `units` units) and returns the unit lines
    it printed in the format of p6_headless, plus the measurements. `ports` are "N:type:buttons:dx:dy" held on every unit, `render` a
    schedule "101-200:0,201-300:2" of render flags (1 video, 2 audio) by unit range, `digest_every` the period of the snapshot digests and
    `bad_calls_at` the unit before which the guest is handed calls it must refuse, `abi_at` the units before which a host callback crosses
    the guest with canaries in the registers (WB13), `pages_at` the unit after which the 16 KiB page groups of Chip RAM are written and
    restored (WB14, needs the chipcell fixture), `memory_at` the unit after which (0: right after the seal) the memory areas the guest reports are read, written,
    refused and restored through the guest (T20a; after a unit it needs the chipcell fixture), `save_at`/`state` where a snapshot is written and `restore`/`restore_from` a new process that
    initializes, seals, loads that snapshot and continues after unit `restore_from` (WB15), `machine` "A500", "A600", "A1200", "A500OG", "A500PLUS", "A1000", "A2000OG" or "A2000" the p6_config.machine the guest is given (T23e, T24f, T50), `chip_kib`, `slow_kib` ("none" or KiB) and `fast_kib` its memory (T49; 0 is the machine's own, and the
    mmap heap of the sandbox grows with the extra memory as manifests/waterbox-memory.json says), `fpu_model` the FPU the A1200 is given (T54: 0, 68881 or 68882), `hard_files` the hard files mounted
    on the UAE controller after the disks (T29d; the mmap heap grows with the heap of each, see the smoke's HardFileHeapKiB) and `export_at` the units after which they are exported through the guest
    (printed as "export" lines and returned in result["exports"]; `export_dir` also keeps the images); `rewind_to`/`rewind_from` load the snapshot of unit `rewind_to` back in the same sandbox after
    unit `rewind_from` and run the units after it again (WB10). Needs the runtime (build/waterbox/receipt.json, or receipt-debug.json in the Debug lane) and the
    guest (--puae6) to exist already; several runs may go on at once."""
    runtime_receipt, guest_receipt = runtime_receipt_path(), guest_receipt or BUILD / "puae6/puae6-receipt.json"
    for needed in (runtime_receipt, guest_receipt):
        if not needed.is_file():
            raise BuildError(f"falta {needed}: ejecutar tools/build_waterbox.py --puae6-run")
    check_guest_matches_trees(guest_receipt)
    dotnet, env = build_managed()
    tag = f"{os.getpid()}-{next(_RUNS)}"
    result_path, log_path = BUILD / f"puae6-result-{tag}.json", BUILD / f"puae6-run-{tag}.log"
    command = [dotnet, str(BUILD / "managed/bin/Debug/net8.0/Smoke.dll"), "--puae6", "--runtime", str(runtime_receipt), "--guest", str(guest_receipt),
               "--manifest", str(MEMORY_MANIFEST), "--firmware", str(Path(firmware).resolve()), "--units", str(units), "--result", str(result_path)]
    for disk in media:
        command += ["--media", str(Path(disk).resolve())]
    for hard_file in hard_files:
        command += ["--hdf", str(Path(hard_file).resolve())]
    if export_at:
        command += ["--export-at", ",".join(map(str, export_at))]
    if export_dir:
        command += ["--export-dir", str(Path(export_dir).resolve())]
    if rewind_from:
        command += ["--rewind-to", str(rewind_to), "--rewind-from", str(rewind_from)]
    for port in ports:
        command += ["--port", port]
    if render:
        command += ["--render", render]
    if digest_every:
        command += ["--digest-every", str(digest_every)]
    if bad_calls_at:
        command += ["--bad-calls-at", str(bad_calls_at)]
    if limits:
        command.append("--limits")
    if abi_at:
        command += ["--abi-at", ",".join(map(str, abi_at))]
    if pages_at:
        command += ["--pages-at", str(pages_at)]
    if memory_at is not None:
        command += ["--memory-at", str(memory_at)]
    if save_at:
        command += ["--save-at", str(save_at), "--state", str(Path(state).resolve())]
    if restore:
        command += ["--restore", str(Path(restore).resolve()), "--from", str(restore_from)]
    if ntsc:
        command.append("--ntsc")
    if machine.upper() != "A500":
        command += ["--machine", machine.lower()]
    for flag, value in (("--chip", chip_kib), ("--slow", slow_kib), ("--fast", fast_kib), ("--fpu", fpu_model)):
        if value:
            command += [flag, str(value)]
    process = subprocess.run(command, env=env, cwd=MANAGED, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    log_path.write_text(process.stdout + "\n--- stderr ---\n" + process.stderr)
    if process.returncode or not result_path.is_file():
        tail = "\n".join((process.stdout + process.stderr).splitlines()[-25:])
        raise BuildError(f"puae6.wbx no corrio en Waterbox ({process.returncode}); log completo: {log_path}\n{tail}")
    result = json.loads(result_path.read_text())
    result_path.unlink()
    log_path.unlink()
    lines = [line for line in process.stdout.splitlines() if line.startswith("unit ")]
    result["export_lines"] = [line for line in process.stdout.splitlines() if line.startswith("export ")]
    result["pcm"] = {int(fields[1]): fields[2] for fields in (line.split() for line in process.stdout.splitlines() if line.startswith("pcm "))}
    return lines, result


MEASURED_KEYS = {"A500": "measured", "A600": "measured_a600", "A1200": "measured_a1200", "A500OG": "measured_a500og", "A500PLUS": "measured_a500plus",
                 "A1000": "measured_a1000", "A2000OG": "measured_a2000og", "A2000": "measured_a2000"}


def measured_key(machine: str, chip_kib: int = 0, slow_kib: int | str = 0, fast_kib: int = 0, fpu_model: int = 0, hard_file_kib: int = 0) -> str:
    """The key of manifests/waterbox-memory.json a run is recorded under: the machine's, or (T49) the machine's and each size asked for: measured_a1200_fast8192. With hard files (T29d) the
    total of their sizes in KiB follows: measured_a500_hdf8192."""
    key = MEASURED_KEYS[machine.upper()]
    if machine.upper() == "A500" and (chip_kib or slow_kib or fast_kib or hard_file_kib):
        key += "_a500"
    for name, kib in (("chip", chip_kib), ("slow", slow_kib), ("fast", fast_kib)):
        if kib:
            key += f"_{name}{kib}"
    return key + (f"_fpu{fpu_model}" if fpu_model else "") + (f"_hdf{hard_file_kib}" if hard_file_kib else "")


def record_memory(firmware: Path, units: int, result: dict, machine: str = "A500", chip_kib: int = 0, slow_kib: int | str = 0, fast_kib: int = 0, fpu_model: int = 0,
                  hard_files: tuple[Path, ...] = ()) -> dict:
    """Stores what a run measured in the `measured` key (A500), `measured_a600` (A600) or `measured_a1200` (A1200) of manifests/waterbox-memory.json, or (T49) in the key of a machine
    with other memory (measured_key); the budgets stay as written: the base ones for the stock machines and the rule of `memory_budget` for the others."""
    manifest = json.loads(MEMORY_MANIFEST.read_text())
    memory = {name: kib for name, kib in (("chip_kib", chip_kib), ("slow_kib", slow_kib), ("fast_kib", fast_kib), ("fpu_model", fpu_model)) if kib}
    hard_bytes = [Path(path).stat().st_size for path in hard_files]
    manifest[measured_key(machine, chip_kib, slow_kib, fast_kib, fpu_model, sum(hard_bytes) // 1024)] = {
        "fixture": {"machine": machine.upper(), **memory, **({"hard_file_bytes": hard_bytes} if hard_bytes else {}), "firmware_sha256": sha256(firmware), "firmware_bytes": firmware.stat().st_size, "units": units,
                    "note": "micro-ROM, no Kickstart: the numbers of a real workload are T19c's and T34's"},
        "guest_sha256": result["guest_sha256"], "runtime_sha256": result["runtime_sha256"],
        "after_seal": result["after_seal"], "after_first_unit": result["after_first_unit"], "after_last_unit": result["after_last_unit"],
        "snapshot_bytes": result["snapshot_bytes"]}
    MEMORY_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true",
                        help="hacer cargo fetch --locked para el target ARM64 antes del build")
    parser.add_argument("--puae6", action="store_true",
                        help="compilar y enlazar puae6.wbx (T19a) con la receta fijada; no ejecuta ninguna maquina")
    parser.add_argument("--puae6-run", type=Path, metavar="FIRMWARE",
                        help="compilar puae6.wbx y ejecutarlo en Waterbox real con ese firmware (T19b): init, seal y --units unidades")
    parser.add_argument("--record-memory", action="store_true",
                        help="con --puae6-run: guardar las medidas de la ejecucion en manifests/waterbox-memory.json")
    parser.add_argument("--units", type=int, default=1, help="unidades que ejecuta --puae6-run")
    parser.add_argument("--machine", choices=tuple(MEASURED_KEYS), default="A500", help="maquina con la que --puae6-run configura el guest (T23e, T24f); con --record-memory decide la clave medida")
    parser.add_argument("--chip", type=int, default=0, metavar="KIB", help="memoria Chip con la que --puae6-run configura el guest (T49; 0: la de la maquina)")
    parser.add_argument("--slow", default="0", metavar="KIB|none", help="memoria Slow con la que --puae6-run configura el guest (T49; 0: la de la maquina)")
    parser.add_argument("--fast", type=int, default=0, metavar="KIB", help="memoria Fast Zorro II con la que --puae6-run configura el guest (T49)")
    parser.add_argument("--fpu", type=int, default=0, choices=(0, 68881, 68882), help="FPU con la que --puae6-run configura el guest (T54; solo el A1200)")
    parser.add_argument("--hdf", type=Path, action="append", default=[], metavar="IMAGE", help="disco duro que --puae6-run monta en el guest (T29d; repetible); el heap mmap crece con HardFileHeapKiB de cada uno")
    parser.add_argument("--runtime-lane", choices=RUNTIME_PROFILES, metavar="PROFILE",
                        help="T34a: construir solo el runtime del carril PROFILE (release o debug) y su recibo, a partir de build/waterbox/receipt.json (que escribe un build normal)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="trabajos paralelos de make para --puae6")
    parser.add_argument("--check-artifact", type=Path,
                        help="validar ELF WBX existente (incluye WB16) sin preparar ni compilar inputs")
    parser.add_argument("--smoke", "--snapshot-smoke", dest="smoke", action="store_true",
                        help="construir y ejecutar toda la puerta T09 en Waterbox real (WB01, WB13-WB15) "
                             "y exigir su cobertura completa; fallos no se convierten en PASS")
    args = parser.parse_args()
    try:
        if args.runtime_lane:
            print(f"RUNTIME LANE {args.runtime_lane}: {ensure_runtime_lane(args.runtime_lane)}")
        elif args.check_artifact:
            metadata = validate_elf(args.check_artifact)
            metadata["compiler_tls"] = check_no_compiler_tls(args.check_artifact)
            print(json.dumps(metadata, indent=2, sort_keys=True))
        elif args.puae6:
            build_puae6(toolchain(), args.jobs)
        elif args.puae6_run:
            build(args)
            build_puae6(toolchain(), args.jobs)
            slow = args.slow if args.slow == "none" else int(args.slow)
            lines, measured = run_puae6(args.puae6_run.resolve(), args.units, machine=args.machine, chip_kib=args.chip, slow_kib=slow, fast_kib=args.fast, fpu_model=args.fpu,
                                          hard_files=tuple(args.hdf))
            print("\n".join(lines))
            (BUILD / "puae6-result.json").write_text(json.dumps(measured, indent=2) + "\n")
            print(f"PUAE6 WBX RUNTIME PASS: {len(lines)} unidades; medidas en {BUILD / 'puae6-result.json'}")
            if args.record_memory:
                record_memory(args.puae6_run.resolve(), args.units, measured, args.machine, args.chip, slow, args.fast, args.fpu, tuple(args.hdf))
                print(f"medidas guardadas en {MEMORY_MANIFEST}")
        else:
            receipt = build(args)
            if args.smoke:
                dotnet, env = build_managed()
                result_path = BUILD / "smoke-result.json"
                run_logged([dotnet, str(BUILD / "managed/bin/Debug/net8.0/Smoke.dll"),
                            str(runtime_receipt_path()), str(result_path)], env, MANAGED)
                summary = verify_smoke(json.loads(result_path.read_text()), receipt, BUILD / "damaged")
                print("SMOKE_RUNTIME PASS: WB01 WB13 WB14 WB15 (WB16 en el build). "
                      "Runtime ante cargas dañadas: "
                      + ", ".join(f"{k}={v}" for k, v in summary["runtime_outcomes"].items()))
    except (BuildError, OSError, KeyError, subprocess.CalledProcessError,
            json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
