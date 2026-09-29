#!/usr/bin/env python3
"""Fetch pinned sources explicitly; import selected Git blobs; verify offline.

Only `fetch` accesses the network. `import` never repairs an existing snapshot
or rewrites a manifest. Local references are read-only, with hashed overlays.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = "upstream/winuae"
MANIFEST = "manifests/upstream-files.json"


def git(cache: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(cache), *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True).stdout


def source(root: Path, source_id: str = "winuae") -> dict:
    lock = json.loads((root / "plan/upstream.lock.json").read_text())
    item = next(s for s in lock["sources"] if s["id"] == source_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
        raise ValueError("ID de fuente invalido")
    for key in ("commit", *(('tag_object',) if 'tag' in item else ())):
        if not re.fullmatch(r"[0-9a-f]{40}", item[key] or ""):
            raise ValueError(f"Pin invalido: {key}")
    if 'tag' in item and not re.fullmatch(r"[A-Za-z0-9._-]+", item["tag"]):
        raise ValueError("Tag invalido")
    if not item.get("local_path") and (not item["repository"] or item["repository"].startswith("-")):
        raise ValueError("Repositorio invalido")
    selected = {key: item[key] for key in ("id", "repository", "commit", "tag", "tag_object",
                                          "local_path", "snapshot") if key in item}
    layout(selected)
    return selected


def cache_path(root: Path, source_id: str = "winuae") -> Path:
    return root / "build/source-cache" / (source_id + ".git")


def layout(item: dict) -> tuple[str, str]:
    if item["id"] == "winuae":
        return DESTINATION, MANIFEST
    spec = item["snapshot"]
    for key in ("destination", "manifest"):
        safe_name(spec[key])
        if not spec[key].startswith("references/"):
            raise ValueError("Una referencia debe quedar bajo references/")
    if spec["manifest"].startswith(spec["destination"] + "/"):
        raise ValueError("El manifiesto no puede estar dentro del snapshot")
    for name in spec.get("include", []) + spec.get("exclude", []) + list(spec.get("overlay_sha256", {})):
        safe_name(name)
    return spec["destination"], spec["manifest"]


def selected_path(name: str, item: dict) -> bool:
    spec = item.get("snapshot", {})
    matches = lambda prefix: name == prefix or name.startswith(prefix + "/")
    return ((not spec.get("include") or any(map(matches, spec["include"])))
            and not any(map(matches, spec.get("exclude", []))))


def no_links(root: Path, target: Path) -> None:
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"Ruta con symlink: {part}")


def check_pins(cache: Path, item: dict) -> None:
    checks = [("rev-parse", item["commit"] + "^{commit}", item["commit"])]
    if item.get("local_path"):
        checks.append(("rev-parse", "HEAD", item["commit"]))
    if "tag" in item:
        tag = "refs/tags/" + item["tag"]
        checks += [("rev-parse", tag, item["tag_object"]),
              ("rev-parse", tag + "^{commit}", item["commit"]),
              ("cat-file", "-t", tag, "tag")]
    for *args, expected in checks:
        if git(cache, *args).decode().strip() != expected:
            raise ValueError(f"Pin/tag no coincide: {args[-1]}")


def fetch_source(root: Path, source_id: str = "winuae") -> None:
    item = source(root, source_id)
    if item.get("local_path"):
        check_pins((root / item["local_path"]).resolve(), item)
        return  # No fetch, checkout or write in a sibling repository.
    cache = cache_path(root, source_id)
    no_links(root, cache)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", str(cache)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    refs = []
    if "tag" in item:
        tag = "refs/tags/" + item["tag"]
        refs.append(tag + ":" + tag)
    git(cache, "fetch", "--no-tags", "--depth=1", item["repository"],
        item["commit"], *refs)
    check_pins(cache, item)


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name
            or ".." in path.parts or "\\" in name
            or any(part.lower() == ".git" for part in path.parts)):
        raise ValueError(f"Ruta de fuente insegura: {name!r}")


def safe_link(name: str, content: bytes) -> str:
    target = content.decode("utf-8")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    if (not target or "\\" in target or target.startswith("/")
            or resolved == ".." or resolved.startswith("../")):
        raise ValueError(f"Symlink fuera del snapshot: {name}")
    return target


def blobs(cache: Path, item: dict):
    tree = git(cache, "ls-tree", "-rz", item["commit"])
    entries = []
    for record in tree.split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = raw_name.decode("utf-8")
        if not selected_path(name, item):
            continue
        safe_name(name)
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise ValueError(f"Entrada no soportada (no omitir): {name}: {mode} {kind}")
        entries.append((name, mode, oid))
    names = [name for name, _, _ in entries]
    for prefix in item.get("snapshot", {}).get("include", []):
        if not any(name == prefix or name.startswith(prefix + "/") for name in names):
            raise ValueError(f"Seleccion vacia o inexistente: {prefix}")
    overlays = item.get("snapshot", {}).get("overlay_sha256", {})
    if not entries or not set(overlays).issubset(names):
        raise ValueError("Fuente vacia u overlay fuera de la seleccion")
    # Read object bytes, not git archive: export-ignore/subst must not change pristine.
    raw = git(cache, "cat-file", "--batch",
              data="".join(oid + "\n" for _, _, oid in entries).encode())
    offset = 0
    for name, mode, oid in entries:
        end = raw.index(b"\n", offset)
        actual, kind, size = raw[offset:end].decode().split()
        if actual != oid or kind != "blob":
            raise ValueError(f"Objeto Git inesperado: {name}")
        offset, size = end + 1, int(size)
        content = raw[offset:offset + size]
        offset += size + 1
        if len(content) != size or raw[offset - 1:offset] != b"\n":
            raise ValueError(f"Objeto Git truncado: {name}")
        if item.get("local_path"):
            local = cache / name
            no_links(cache, local.parent if mode == "120000" else local)
            observed = os.fsencode(os.readlink(local)) if mode == "120000" else local.read_bytes()
            observed_mode = "120000" if local.is_symlink() else (
                "100755" if local.stat().st_mode & 0o111 else "100644")
            expected_hash = overlays.get(name, hashlib.sha256(content).hexdigest())
            if observed_mode != mode or hashlib.sha256(observed).hexdigest() != expected_hash:
                raise ValueError(f"Cambio local no fijado: {name}")
            content = observed
        yield name, mode, content


def inventory(directory: Path) -> list[dict]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Snapshot inexistente o symlink: {directory}")
    entries = []
    for parent, dirs, files in os.walk(directory, followlinks=False):
        links = [name for name in dirs if (Path(parent) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in files + links:
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            safe_name(relative)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                content, gitmode = os.fsencode(os.readlink(path)), "120000"
                safe_link(relative, content)
            elif stat.S_ISREG(mode):
                content = path.read_bytes()
                gitmode = "100755" if mode & 0o111 else "100644"
            else:
                raise ValueError(f"Tipo de archivo no soportado: {relative}")
            entries.append({"path": relative, "mode": gitmode,
                            "sha256": hashlib.sha256(content).hexdigest()})
    return sorted(entries, key=lambda entry: entry["path"])


def metadata(item: dict) -> dict:
    return {"schema_version": 1, "source": item, "destination": layout(item)[0]}


def verify_source(root: Path, source_id: str = "winuae") -> dict:
    item = source(root, source_id)
    destination, manifest = layout(item)
    no_links(root, root / destination)
    no_links(root, root / manifest)
    expected = json.loads((root / manifest).read_text())
    actual = {**metadata(item), "files": inventory(root / destination)}
    if expected != actual:
        raise ValueError("SRC01: snapshot/manifiesto/lock difieren; no se modifica pristine")
    return actual


def import_source(root: Path, cache: Path | None = None, source_id: str = "winuae") -> dict:
    item = source(root, source_id)
    if cache is None:
        cache = ((root / item["local_path"]).resolve() if item.get("local_path")
                 else cache_path(root, source_id))
    check_pins(cache, item)
    destination, manifest = (root / path for path in layout(item))
    for target in (destination, manifest, root / "build"):
        no_links(root, target)
    existing_tree = destination.exists() and (
        not destination.is_dir() or any(destination.iterdir()))
    if manifest.exists():
        verify_source(root, source_id)
    work = root / "build"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-winuae-", dir=work) as temporary:
        stage = Path(temporary) / "snapshot"
        stage.mkdir()
        files = []
        for name, mode, content in blobs(cache, item):
            path = stage / name
            no_links(stage, path.parent)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                raise ValueError(f"Colision de rutas: {name}")
            if mode == "120000":
                path.symlink_to(safe_link(name, content))
            else:
                path.write_bytes(content)
                path.chmod(0o755 if mode == "100755" else 0o644)
            files.append({"path": name, "mode": mode,
                          "sha256": hashlib.sha256(content).hexdigest()})
        result = {**metadata(item), "files": sorted(files, key=lambda entry: entry["path"])}
        if inventory(stage) != result["files"]:
            raise ValueError("El filesystem no conserva las rutas/modos/bytes Git exactos")
        if existing_tree and inventory(destination) != result["files"]:
            raise ValueError("Snapshot existente difiere; no se sobrescribe trabajo local")
        if manifest.exists():
            if json.loads(manifest.read_text()) != result:
                raise ValueError("Manifiesto existente no corresponde a los blobs fijados")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
            # Recover a prior interrupted import only after matching every blob/mode.
            if not existing_tree:
                stage.rename(destination)
            with manifest.open("x", encoding="utf-8") as output:
                output.write(encoded)
    return verify_source(root, source_id)


def verify_licenses(root: Path, source_ids: list[str]) -> None:
    catalog = json.loads((root / "manifests/licenses.json").read_text())
    if catalog.get("schema_version") != 1:
        raise ValueError("Catalogo de licencias invalido")
    notices_by_source = {}
    for source_id in source_ids:
        destination, manifest = layout(source(root, source_id))
        files = json.loads((root / manifest).read_text())["files"]
        notices_by_source[source_id] = {destination + "/" + f["path"]: f["sha256"] for f in files}
    covered, seen = set(), set()
    for component in catalog["components"]:
        source_id = component["source_id"]
        if component["id"] in seen or source_id not in notices_by_source:
            raise ValueError("Componente duplicado o fuente desconocida en licencias")
        seen.add(component["id"])
        for notice in component["notice_files"]:
            if notices_by_source[source_id].get(notice["path"]) != notice["sha256"]:
                raise ValueError(f"Aviso fuera del snapshot o hash incorrecto: {notice['path']}")
            covered.add(source_id)
    if covered != set(source_ids):
        raise ValueError("Faltan avisos de licencia para fuentes seleccionadas")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "import", "verify"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--source", default="winuae")
    selection.add_argument("--all", action="store_true", help="Todas las fuentes con snapshot seleccionado")
    args = parser.parse_args(argv)
    try:
        ids = [args.source]
        if args.all:
            lock = json.loads((ROOT / "plan/upstream.lock.json").read_text())
            ids = [s["id"] for s in lock["sources"] if s["id"] == "winuae" or "snapshot" in s]
        for source_id in ids:
            if args.command == "fetch":
                fetch_source(ROOT, source_id)
                print(f"{source_id}: pin disponible (cache o referencia local de solo lectura).")
            else:
                result = (import_source(ROOT, source_id=source_id) if args.command == "import"
                          else verify_source(ROOT, source_id))
                print(f"SRC01 {source_id}: {len(result['files'])} archivos verificados offline; no prueba emulacion.")
        if args.all and args.command != "fetch":
            verify_licenses(ROOT, ids)
            print("Avisos de licencia: cobertura de fuentes y hashes verificados.")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        detail = error.stderr.decode(errors="replace") if isinstance(error, subprocess.CalledProcessError) else str(error)
        print("ERROR: " + detail.strip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
