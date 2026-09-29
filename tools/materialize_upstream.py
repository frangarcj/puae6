#!/usr/bin/env python3
"""Materialize verified WinUAE sources with a hash-closed patch series."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402

HASH = re.compile(r"^[0-9a-f]{64}$")
PATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
FILE_HEADER = re.compile(r"^(---|\+\+\+) ([ab]/)(\S+)(?:\t.*)?$")
OFFSET = re.compile(r"\(offset -?\d+ lines?\)")


def _safe_rel(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or not path.parts or path.is_absolute() or path.as_posix() != name
            or any(part in ("", ".", "..") for part in path.parts)
            or "\\" in name or any(part.lower() == ".git" for part in path.parts)):
        raise ValueError(f"Ruta relativa insegura: {name!r}")
    return path


def _no_symlinks(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Ruta fuera del proyecto: {path}") from error
    current = root
    if current.is_symlink():
        raise ValueError(f"Raiz con symlink: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Ruta con symlink: {current}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_file(root: Path, tree: Path, name: str) -> Path:
    relative = _safe_rel(name)
    path = tree.joinpath(*relative.parts)
    _no_symlinks(root, path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"No es archivo regular: {name}")
    return path


def _patch_paths(data: bytes) -> set[str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("Parche no UTF-8; rutas fuera del formato admitido") from error
    paths = set()
    current = None
    file_headers = []
    for line in lines:
        if line.startswith(("new file mode ", "deleted file mode ", "old mode ",
                            "new mode ", "rename from ", "rename to ", "copy from ", "copy to ")):
            raise ValueError("La serie solo admite cambios de contenido en archivos existentes")
        if line.startswith("diff --git "):
            match = DIFF_HEADER.fullmatch(line)
            if not match or match[1] != match[2]:
                raise ValueError(f"Cabecera diff no admitida: {line}")
            name = match[1]
            _safe_rel(name)
            paths.add(name)
            current = name
            file_headers = []
        elif line.startswith(("--- ", "+++ ")):
            match = FILE_HEADER.fullmatch(line)
            if not match or match[3] != current:
                raise ValueError(f"Cabecera de archivo no coincide con diff: {line}")
            file_headers.append(match[1])
            if len(file_headers) > 2:
                raise ValueError(f"Cabeceras de archivo duplicadas: {line}")
            if len(file_headers) == 2:
                file_headers = []
    if not paths:
        raise ValueError("Parche sin archivos diff --git")
    return paths


def _series(root: Path, commit: str) -> tuple[dict, str]:
    path = root / "patches/winuae/series.json"
    _no_symlinks(root, path)
    raw = path.read_bytes()
    series = json.loads(raw)
    if (series.get("schema_version") != 1 or series.get("upstream_commit") != commit
            or not isinstance(series.get("patches"), list)):
        raise ValueError("Serie incompatible con el snapshot fijado")
    seen_ids, seen_files = set(), set()
    for patch in series["patches"]:
        ident, file = patch.get("id"), patch.get("file")
        if not isinstance(ident, str) or not PATCH_ID.fullmatch(ident) or ident in seen_ids:
            raise ValueError(f"ID de parche invalido o duplicado: {ident!r}")
        if not isinstance(file, str) or file in seen_files:
            raise ValueError(f"Archivo de parche invalido o duplicado: {file!r}")
        rel = _safe_rel(file)
        if rel.parts[0] != "patches" or rel.parts[:2] != ("patches", "winuae"):
            raise ValueError(f"Parche fuera de patches/winuae: {file}")
        if not isinstance(patch.get("sha256"), str) or not HASH.fullmatch(patch["sha256"]):
            raise ValueError(f"Hash de parche invalido: {ident}")
        files = patch.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"Lista files vacia o invalida: {ident}")
        file_names = set()
        for item in files:
            name = item.get("path")
            _safe_rel(name)
            if name in file_names:
                raise ValueError(f"Ruta duplicada en {ident}: {name}")
            for key in ("before_sha256", "after_sha256"):
                if not isinstance(item.get(key), str) or not HASH.fullmatch(item[key]):
                    raise ValueError(f"{key} invalido en {ident}: {name}")
            if item["before_sha256"] == item["after_sha256"]:
                raise ValueError(f"El parche no cambia contenido: {ident}/{name}")
            file_names.add(name)
        seen_ids.add(ident)
        seen_files.add(file)
    return series, hashlib.sha256(raw).hexdigest()


def _apply(data: bytes, tree: Path, check: bool) -> None:
    command = ["git", "apply", "--verbose"]
    if check:
        command.append("--check")
    command.append("-")
    env = dict(os.environ, LC_ALL="C", GIT_CEILING_DIRECTORIES=str(tree.parent))
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    result = subprocess.run(command, cwd=tree, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"git apply fallo: {output.strip()}")
    if OFFSET.search(output):
        raise ValueError(f"git apply encontro un offset; parche rechazado: {output.strip()}")


def _destination(root: Path, value: str) -> Path:
    relative = _safe_rel(value)
    if len(relative.parts) < 2 or relative.parts[0] != "build":
        raise ValueError("El destino debe estar dentro de build/ (ruta relativa insegura)")
    path = root.joinpath(*relative.parts)
    _no_symlinks(root, path)
    return path


def materialize(root: Path = ROOT, destination: str = "build/upstream") -> tuple[dict, Path]:
    """Return (receipt, output_path); never modify the verified pristine snapshot."""
    root = Path(root).absolute()
    pristine_manifest = import_sources.verify_source(root)
    upstream_commit = pristine_manifest["source"]["commit"]
    series, series_sha = _series(root, upstream_commit)
    target = _destination(root, destination)
    build = root / "build"
    _no_symlinks(root, build)
    build.mkdir(parents=True, exist_ok=True)
    source = root / pristine_manifest["destination"]
    _no_symlinks(root, source)

    with tempfile.TemporaryDirectory(prefix=".materialize-", dir=build) as tmp:
        stage = Path(tmp) / "tree"
        shutil.copytree(source, stage, symlinks=True)
        if import_sources.inventory(stage) != pristine_manifest["files"]:
            raise ValueError("La copia staged no coincide con el snapshot verificado")
        expected_inventory = {item["path"]: dict(item) for item in pristine_manifest["files"]}
        for patch in series["patches"]:
            patch_path = root / patch["file"]
            _no_symlinks(root, patch_path)
            data = patch_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != patch["sha256"]:
                raise ValueError(f"Hash de parche incorrecto: {patch['id']}")
            expected = {item["path"]: item for item in patch["files"]}
            if _patch_paths(data) != set(expected):
                raise ValueError(f"Archivos tocados no coinciden con el manifiesto: {patch['id']}")
            for name, item in expected.items():
                path = _regular_file(root, stage, name)
                if _sha(path) != item["before_sha256"]:
                    raise ValueError(f"Hash previo incorrecto: {patch['id']}/{name}")
            _apply(data, stage, check=True)
            _apply(data, stage, check=False)
            for name, item in expected.items():
                path = _regular_file(root, stage, name)
                if _sha(path) != item["after_sha256"]:
                    raise ValueError(f"Hash posterior incorrecto: {patch['id']}/{name}")
                expected_inventory[name]["sha256"] = item["after_sha256"]
            if import_sources.inventory(stage) != sorted(expected_inventory.values(),
                                                          key=lambda item: item["path"]):
                raise ValueError(f"Inventario posterior no declarado: {patch['id']}")

        files = import_sources.inventory(stage)
        receipt = {"files": files, "upstream_commit": upstream_commit,
                   "series_sha256": series_sha}
        if target.exists():
            if not target.is_dir() or import_sources.inventory(target) != files:
                raise ValueError(f"Destino existente distinto; no se sobrescribe: {target}")
        else:
            _no_symlinks(root, target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, target)
    return receipt, target


if __name__ == "__main__":
    receipt, path = materialize()
    print(f"Materializado {len(receipt['files'])} archivos en {path} "
          f"(WinUAE {receipt['upstream_commit']}, serie {receipt['series_sha256']})")
