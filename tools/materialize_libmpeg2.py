#!/usr/bin/env python3
"""Materialize the pinned scalar libmpeg2 closure with hash-closed patches.

The MacHawk capture remains read-only.  Native and Waterbox builds consume the
derived tree in build/libmpeg2, or refuse an altered/stale existing tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402
import materialize_upstream  # noqa: E402

HASH = re.compile(r"^[0-9a-f]{64}$")
SERIES_PATH = Path("patches/libmpeg2/series.json")
OUTPUT_PATH = Path("build/libmpeg2")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _series(root: Path) -> tuple[dict, str]:
    path = root / SERIES_PATH
    materialize_upstream._no_symlinks(root, path)
    raw = path.read_bytes()
    series = json.loads(raw)
    if set(series) != {"schema_version", "source_manifest", "source_prefix", "patches"}:
        raise ValueError("campos inesperados en patches/libmpeg2/series.json")
    if (series["schema_version"] != 1
            or series["source_manifest"] != "references/machawk-uae.manifest.json"
            or series["source_prefix"] != "deps/libmpeg2/"
            or not isinstance(series["patches"], list)
            or not series["patches"]):
        raise ValueError("serie libmpeg2 incompatible")
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for patch in series["patches"]:
        if set(patch) != {"id", "file", "sha256", "files"}:
            raise ValueError("campos inesperados en parche libmpeg2")
        ident, filename = patch["id"], patch["file"]
        if (not isinstance(ident, str) or not ident
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", ident)
                or ident in seen_ids):
            raise ValueError(f"id de parche libmpeg2 invalido o duplicado: {ident!r}")
        relative = materialize_upstream._safe_rel(filename)
        if relative.parts[:2] != ("patches", "libmpeg2") or filename in seen_files:
            raise ValueError(f"ruta de parche libmpeg2 invalida o duplicada: {filename!r}")
        if not isinstance(patch["sha256"], str) or not HASH.fullmatch(patch["sha256"]):
            raise ValueError(f"hash de parche libmpeg2 invalido: {ident}")
        if not isinstance(patch["files"], list) or not patch["files"]:
            raise ValueError(f"lista de archivos vacia: {ident}")
        paths: set[str] = set()
        for item in patch["files"]:
            if set(item) != {"path", "before_sha256", "after_sha256"}:
                raise ValueError(f"campos inesperados en {ident}/files")
            name = item["path"]
            materialize_upstream._safe_rel(name)
            if name in paths:
                raise ValueError(f"ruta duplicada en {ident}: {name}")
            if any(not isinstance(item[key], str) or not HASH.fullmatch(item[key])
                   for key in ("before_sha256", "after_sha256")):
                raise ValueError(f"cadena de hashes invalida en {ident}/{name}")
            if item["before_sha256"] == item["after_sha256"]:
                raise ValueError(f"el parche no cambia {ident}/{name}")
            paths.add(name)
        seen_ids.add(ident)
        seen_files.add(filename)
    return series, hashlib.sha256(raw).hexdigest()


def _pristine(root: Path, series: dict) -> tuple[dict, Path, str]:
    manifest_path = root / series["source_manifest"]
    materialize_upstream._no_symlinks(root, manifest_path)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if (manifest.get("schema_version") != 1
            or manifest.get("destination") != "references/machawk-uae"
            or not isinstance(manifest.get("files"), list)):
        raise ValueError("manifest de la captura MacHawk incompatible")
    prefix = series["source_prefix"]
    files = []
    for item in manifest["files"]:
        name = item.get("path")
        if isinstance(name, str) and name.startswith(prefix):
            files.append({**item, "path": name[len(prefix):]})
    if not files or any(not item["path"] for item in files):
        raise ValueError("el manifest no contiene la clausura libmpeg2")
    files.sort(key=lambda item: item["path"])
    source = root / manifest["destination"] / prefix.removesuffix("/")
    materialize_upstream._no_symlinks(root, source)
    if import_sources.inventory(source) != files:
        raise ValueError("la captura libmpeg2 no coincide con su manifest fijado")
    return {"files": files, "source_commit": manifest["source"]["commit"]}, source, hashlib.sha256(raw).hexdigest()


def _expected(root: Path, pristine: dict, series: dict, series_sha: str,
              manifest_sha: str) -> dict:
    files = {item["path"]: dict(item) for item in pristine["files"]}
    for patch in series["patches"]:
        patch_path = materialize_upstream._regular_file(root, root, patch["file"])
        data = patch_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != patch["sha256"]:
            raise ValueError(f"hash de parche incorrecto: {patch['id']}")
        declared = {item["path"]: item for item in patch["files"]}
        if materialize_upstream._patch_paths(data) != set(declared):
            raise ValueError(f"archivos no declarados por {patch['id']}")
        for name, item in declared.items():
            old = files.get(name)
            if old is None or old["sha256"] != item["before_sha256"]:
                raise ValueError(f"hash previo incorrecto: {patch['id']}/{name}")
            old["sha256"] = item["after_sha256"]
    return {
        "files": sorted(files.values(), key=lambda item: item["path"]),
        "source_commit": pristine["source_commit"],
        "source_manifest_sha256": manifest_sha,
        "series_sha256": series_sha,
    }


def materialize(root: Path = ROOT) -> tuple[dict, Path]:
    """Return the verified receipt and derived path, without replacing a tree."""
    root = Path(root).absolute()
    series, series_sha = _series(root)
    pristine, source, manifest_sha = _pristine(root, series)
    receipt = _expected(root, pristine, series, series_sha, manifest_sha)
    target = root / OUTPUT_PATH
    proof_path = target.with_name(".libmpeg2-materialize.json")
    build = root / "build"
    materialize_upstream._no_symlinks(root, build)
    build.mkdir(parents=True, exist_ok=True)
    identity = {
        "receipt": receipt,
        "implementation": [_sha(Path(__file__)), _sha(Path(import_sources.__file__)),
                           _sha(Path(materialize_upstream.__file__))],
    }
    proof = {"identity": hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()}
    try:
        previous = json.loads(proof_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        previous = None
    if previous == proof and target.exists():
        if import_sources.inventory(target) != receipt["files"]:
            raise ValueError(f"destino libmpeg2 alterado; no se sobrescribe: {target}")
        return receipt, target

    with tempfile.TemporaryDirectory(prefix=".libmpeg2-", dir=build) as temporary:
        stage = Path(temporary) / "tree"
        shutil.copytree(source, stage, symlinks=True)
        if import_sources.inventory(stage) != pristine["files"]:
            raise ValueError("la copia staged de libmpeg2 no coincide con la captura")
        expected_inventory = {item["path"]: dict(item) for item in pristine["files"]}
        for patch in series["patches"]:
            patch_path = materialize_upstream._regular_file(root, root, patch["file"])
            data = patch_path.read_bytes()
            declared = {item["path"]: item for item in patch["files"]}
            for name, item in declared.items():
                path = materialize_upstream._regular_file(root, stage, name)
                if _sha(path) != item["before_sha256"]:
                    raise ValueError(f"hash previo incorrecto: {patch['id']}/{name}")
            materialize_upstream._apply(data, stage, check=True)
            materialize_upstream._apply(data, stage, check=False)
            for name, item in declared.items():
                path = materialize_upstream._regular_file(root, stage, name)
                if _sha(path) != item["after_sha256"]:
                    raise ValueError(f"hash posterior incorrecto: {patch['id']}/{name}")
                expected_inventory[name]["sha256"] = item["after_sha256"]
            if import_sources.inventory(stage) != sorted(expected_inventory.values(),
                                                          key=lambda item: item["path"]):
                raise ValueError(f"inventario posterior no declarado: {patch['id']}")
        if target.exists():
            if not target.is_dir() or import_sources.inventory(target) != receipt["files"]:
                raise ValueError(f"destino libmpeg2 distinto; no se sobrescribe: {target}")
        else:
            os.rename(stage, target)
    materialize_upstream._atomic_json(proof_path, proof)
    return receipt, target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    receipt, path = materialize(args.root)
    print(f"Materializados {len(receipt['files'])} archivos en {path} "
          f"(serie {receipt['series_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
