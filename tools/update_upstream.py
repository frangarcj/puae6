#!/usr/bin/env python3
"""UPD01: stage an upstream update (OLD_SHA -> NEW_SHA) in a work area of its own and report.

What it does, in order, and what it never does:

* OLD_SHA must be the commit pinned by plan/upstream.lock.json; the pristine snapshot is verified against
  the lock and manifests/upstream-files.json first (SRC01). NEW_SHA is a full commit given explicitly: from
  `--new-source` (a local Git repository that already has it, or a snapshot directory plus its manifest) or from
  the cache that tools/import_sources.py keeps. Nothing is ever downloaded, and a branch name is not a SHA.
* It reports what the new snapshot adds, removes and changes in the manifests, the licence notices, the
  generator inputs and the sources the profile selects, and flags new sources nobody has classified yet.
* It applies the patch series one patch at a time on a sparse copy of the new files, exactly (no fuzz and no
  offset: the same `git apply` as tools/materialize_upstream.py). A hunk that does not apply is a conflict;
  nothing is resolved as "ours" or "theirs". Each conflict says whether upstream rewrote the patch zone
  (semantic), the zone moved only (shifted: a mechanical re-export) or the series itself is broken
  (port-failure), and the patches that depend on its files are blocked, not guessed.
* With no conflict it regenerates the CPU/blitter/line tables with tools/generate_cpu.py on the candidate tree
  (in a project-shaped copy inside the work area) and compares the outputs with those of the old snapshot.
* It prepares, and does not run, the checks and the hash comparison of docs/05-maintenance.md, and it never
  touches goldens, the pristine snapshot, build/upstream, build/generated or any other directory of the
  project: everything it writes is under --workdir (report.json, report.md, candidate-series.json and, with
  --keep-workspace, the trees).

Exit status: 0 no changes or a clean candidate (review and the suite still pending), 1 conflicts, failed
generation or files that need triage, 2 error (nothing was verified or nothing could be decided).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402
import materialize_upstream as mat  # noqa: E402

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SOURCE_SUFFIXES = (".c", ".cpp", ".cc", ".h", ".hpp", ".def", ".s", ".asm", ".inc", ".y", ".l")
SOURCE_NAMES = ("table68k",)
LICENSE_NAME = re.compile(r"(^|/)(license|licence|copying|notice|copyright|authors?)[^/]*$", re.I)
MARKERS = [("GPL", re.compile(rb"General Public License|\bGPL\b", re.I)),
           ("LGPL", re.compile(rb"Lesser General Public|Library General Public|\bLGPL\b", re.I)),
           ("MIT", re.compile(rb"\bMIT License\b|Permission is hereby granted", re.I)),
           ("BSD", re.compile(rb"\bBSD\b|Redistribution and use in source", re.I)),
           ("Apache", re.compile(rb"Apache License", re.I)),
           ("MPL", re.compile(rb"Mozilla Public", re.I)),
           ("SPDX", re.compile(rb"SPDX-License-Identifier")),
           ("Copyright", re.compile(rb"\bcopyright\b", re.I))]
RELEVANT = ("patched", "generator-input", "selected", "license-notice", "audited", "excluded", "include", "header")
LISTING_CAP = 200
HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIAG_LIMIT = 3000
EXIT = {"no-changes": 0, "candidate": 0, "conflicts": 1, "generator-failed": 1, "triage-required": 1, "error": 2}


class UpdateError(ValueError):
    """The update cannot even be evaluated (unverified snapshot, unknown NEW_SHA, broken inputs)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------------------------- snapshots

class Tree:
    """A snapshot as the importer sees it: (path, mode, sha256) per entry, with readable bytes."""

    def __init__(self, files: list[dict]):
        self.files = sorted(files, key=lambda item: item["path"])
        self.by_path = {item["path"]: item for item in self.files}
        self.tree_sha256 = sha256(canonical(self.files))

    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def export(self, destination: Path) -> None:
        raise NotImplementedError

    def sha(self, path: str):
        entry = self.by_path.get(path)
        return entry["sha256"] if entry else None


class DirTree(Tree):
    def __init__(self, directory: Path, files: list[dict]):
        super().__init__(files)
        self.directory = directory

    def read(self, path: str) -> bytes:
        target = self.directory.joinpath(*path.split("/"))
        return os.fsencode(os.readlink(target)) if target.is_symlink() else target.read_bytes()

    def export(self, destination: Path) -> None:
        def link_or_copy(source, target, *, follow_symlinks=True):
            try:  # Hard links cost no disk; the tree is only ever read through them.
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target, follow_symlinks=follow_symlinks)
        shutil.copytree(self.directory, destination, symlinks=True, copy_function=link_or_copy)
        if import_sources.inventory(destination) != self.files:
            raise UpdateError("La copia de trabajo no conserva rutas/modos/bytes del snapshot")


class MemoryTree(Tree):
    """Blobs read from a Git object database (never from a checkout, like the importer)."""

    def __init__(self, blobs: list[tuple[str, str, bytes]]):
        super().__init__([{"path": n, "mode": m, "sha256": sha256(c)} for n, m, c in blobs])
        self.blobs = {n: (m, c) for n, m, c in blobs}

    def read(self, path: str) -> bytes:
        return self.blobs[path][1]

    def export(self, destination: Path) -> None:
        for name, (mode, content) in sorted(self.blobs.items()):
            path = destination.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                path.symlink_to(import_sources.safe_link(name, content))
            else:
                path.write_bytes(content)
                path.chmod(0o755 if mode == "100755" else 0o644)
        if import_sources.inventory(destination) != self.files:
            raise UpdateError("El filesystem no conserva las rutas/modos/bytes Git exactos")


def _is_git_repository(path: Path) -> bool:
    try:
        import_sources.git(path, "rev-parse", "--git-dir")
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def load_old(root: Path, old_sha: str) -> tuple[Tree, dict]:
    if not SHA40.fullmatch(old_sha or ""):
        raise UpdateError("OLD_SHA debe ser un commit completo de 40 hex")
    try:
        manifest = import_sources.verify_source(root)  # SRC01: lock + manifest + bytes, offline
    except (OSError, ValueError, KeyError, StopIteration) as error:
        raise UpdateError(f"SRC01: el snapshot fijado no se verifica; no se actualiza sobre un origen dudoso: {error}") from error
    if manifest["source"]["commit"] != old_sha:
        raise UpdateError(f"OLD_SHA {old_sha} no es el commit fijado en el lock ({manifest['source']['commit']})")
    return DirTree(root / manifest["destination"], manifest["files"]), manifest


def load_new(root: Path, new_sha: str, new_source, new_manifest) -> tuple[Tree, dict]:
    """Resolve NEW_SHA to a verified tree. Never fetches; `master`/`latest` are not SHAs."""
    if not SHA40.fullmatch(new_sha or ""):
        raise UpdateError("NEW_SHA debe ser un commit completo de 40 hex (ni ramas ni tags)")
    if new_source is None and new_manifest is None:
        cache = import_sources.cache_path(root)
        if not cache.exists():
            raise UpdateError("NEW_SHA no esta importado: pase --new-source (repositorio Git local con el commit o "
                              "directorio + --new-manifest). No se descarga nada.")
        new_source = cache
    new_source = Path(new_source).resolve()
    if new_manifest is not None:
        return _load_new_directory(new_sha, new_source, Path(new_manifest))
    if not new_source.is_dir() or not _is_git_repository(new_source):
        raise UpdateError(f"--new-source no es un repositorio Git ni se dio --new-manifest: {new_source.name}")
    try:
        found = import_sources.git(new_source, "rev-parse", "--verify", "--quiet", new_sha + "^{commit}").decode().strip()
        tree_object = import_sources.git(new_source, "rev-parse", new_sha + "^{tree}").decode().strip()
    except subprocess.CalledProcessError as error:
        raise UpdateError(f"NEW_SHA {new_sha} no esta en el repositorio local {new_source.name}; "
                          "esta herramienta no descarga (use un fetch explicito aparte)") from error
    if found != new_sha:
        raise UpdateError("NEW_SHA no resuelve a si mismo como commit")
    try:
        tree = MemoryTree(list(import_sources.blobs(new_source, {"id": "winuae", "commit": new_sha})))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise UpdateError(f"No se pudo leer el commit {new_sha}: {error}") from error
    return tree, {"kind": "git", "commit": new_sha, "git_tree": tree_object}


def _load_new_directory(new_sha: str, directory: Path, manifest_path: Path) -> tuple[Tree, dict]:
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        files = manifest["files"]
        if manifest.get("schema_version") != 1 or manifest["source"]["commit"] != new_sha:
            raise ValueError("el manifiesto no es del commit NEW_SHA")
        actual = import_sources.inventory(directory)
        if actual != files:
            raise ValueError("el directorio no coincide con su manifiesto")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise UpdateError(f"Snapshot NEW no verificado contra su manifiesto: {error}") from error
    return DirTree(directory, actual), {"kind": "directory", "commit": new_sha, "manifest_sha256": sha256(raw)}


# --------------------------------------------------------------------------------------- classification

class Known:
    """What the project already knows about upstream paths: the lists a glob must never replace."""

    def __init__(self, root: Path, series: dict):
        manifest = json.loads((root / "manifests/source-manifest.json").read_text())
        selection = manifest.get("selection", {})
        excluded = selection.get("excluded_units", {})
        self.patched = {item["path"] for patch in series["patches"] for item in patch["files"]}
        self.selected = set(selection.get("upstream_units", [])) | set(selection.get("textual_inputs", []))
        self.audited = set(manifest.get("audit_sources", []))
        self.excluded = set(excluded if isinstance(excluded, dict) else [e[0] if isinstance(e, list) else e for e in excluded])
        self.generator_sources = set()
        self.generators = {}
        recipe = json.loads((root / "manifests/codegen.json").read_text())
        for generator in recipe.get("generators", []):
            inputs = [s.split("/", 1)[1] for s in generator.get("sources", []) if s.startswith("upstream/")]
            for run in generator.get("runs", []):
                if str(run.get("stdin", "")).startswith("upstream/"):
                    inputs.append(run["stdin"].split("/", 1)[1])
            self.generators[generator["name"]] = sorted(inputs)
            self.generator_sources.update(inputs)
        prefix = "upstream/winuae/"
        licenses = json.loads((root / "manifests/licenses.json").read_text()) if (root / "manifests/licenses.json").exists() else {}
        self.notices = {n["path"][len(prefix):] for c in licenses.get("components", []) if c.get("source_id") == "winuae"
                        for n in c.get("notice_files", []) if n["path"].startswith(prefix)}
        known = self.patched | self.selected | self.audited | self.excluded | self.generator_sources | self.notices
        self.directories = {os.path.dirname(path) for path in known}

    def classify(self, path: str) -> str:
        if path in self.patched:
            return "patched"
        if path in self.generator_sources:
            return "generator-input"
        if path in self.selected:
            return "selected"
        if path in self.notices:
            return "license-notice"
        if path in self.excluded:
            return "excluded"
        if path in self.audited:
            return "audited"
        if path.startswith("include/"):
            return "include"
        source_like = path.endswith(SOURCE_SUFFIXES) or os.path.basename(path) in SOURCE_NAMES
        if os.path.dirname(path) in self.directories:
            if path.endswith((".h", ".hpp")):
                return "header"
            if source_like:
                return "unlisted-source"
        elif source_like:
            return "unselected"
        return "other"


def markers(data: bytes) -> list[str]:
    head = data[:8192]
    return [name for name, pattern in MARKERS if pattern.search(head)]


def diff_inventory(old: Tree, new: Tree) -> dict:
    added = sorted(set(new.by_path) - set(old.by_path))
    removed = sorted(set(old.by_path) - set(new.by_path))
    changed = sorted(p for p in set(old.by_path) & set(new.by_path)
                     if old.by_path[p] != new.by_path[p])
    return {"added": added, "removed": removed, "changed": changed}


def diffstat(work: Path, old: bytes, new: bytes) -> dict | None:
    """Hunks and lines changed between two versions of a file, by Git's own diff (no repository needed)."""
    if b"\0" in old or b"\0" in new:
        return None
    work.mkdir(parents=True, exist_ok=True)
    (work / "a").write_bytes(old)
    (work / "b").write_bytes(new)
    env = dict(os.environ, LC_ALL="C")
    out = subprocess.run(["git", "diff", "--no-index", "--no-color", "-U0", "a", "b"], cwd=work, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    hunks, ranges, plus, minus = 0, [], 0, 0
    for line in out.split(b"\n"):
        match = HUNK_HEADER.match(line)
        if match:
            hunks += 1
            if len(ranges) < 10:
                ranges.append(f"{match[1].decode()},{(match[2] or b'1').decode()}")
        elif line.startswith(b"+") and not line.startswith(b"+++"):
            plus += 1
        elif line.startswith(b"-") and not line.startswith(b"---"):
            minus += 1
    return {"hunks": hunks, "added": plus, "removed": minus, "old_ranges": ranges}


def snapshot_report(old: Tree, new: Tree, known: Known, work: Path) -> tuple[dict, dict, list[dict]]:
    """Inventory, manifest and licence differences, plus the findings a person has to decide."""
    delta = diff_inventory(old, new)
    triage = []
    by_relevance: dict[str, dict[str, list]] = {}
    relevant = {"added": [], "removed": [], "changed": []}
    marker_changes = []
    for kind in ("added", "removed", "changed"):
        for path in delta[kind]:
            relevance = known.classify(path)
            bucket = by_relevance.setdefault(relevance, {"added": [], "removed": [], "changed": []})
            bucket[kind].append(path)
            if relevance not in RELEVANT and not (kind == "added" and relevance == "unlisted-source"):
                continue
            entry = {"path": path, "relevance": relevance}
            if kind == "added":
                entry["sha256"] = new.sha(path)
                entry["license_markers"] = markers(new.read(path))
            elif kind == "removed":
                entry["sha256"] = old.sha(path)
            else:
                entry["old_sha256"], entry["new_sha256"] = old.sha(path), new.sha(path)
                entry["diffstat"] = diffstat(work / "cmp", old.read(path), new.read(path))
                before, after = markers(old.read(path)), markers(new.read(path))
                if before != after:
                    marker_changes.append({"path": path, "old": before, "new": after})
            relevant[kind].append(entry)
            if kind == "added" and (relevance == "unlisted-source" or LICENSE_NAME.search(path)):
                triage.append({"kind": "added-source" if relevance == "unlisted-source" else "added-license",
                               "path": path,
                               "action": "decidir si entra en manifests/source-manifest.json (selection) o en "
                                         "excluded_units con su motivo; ningun glob lo compila" if relevance == "unlisted-source"
                                         else "revisar el texto y registrarlo en manifests/licenses.json"})
            if kind == "removed" and relevance not in ("excluded", "license-notice"):
                triage.append({"kind": "removed-relevant", "path": path, "relevance": relevance,
                               "action": "quitar o sustituir la referencia en los manifiestos y revisar los parches que lo tocan"})
    for path in delta["added"]:  # a licence file that sits where nothing is classified is still a licence file
        if LICENSE_NAME.search(path) and known.classify(path) not in RELEVANT and not any(
                t["path"] == path for t in triage):
            relevant["added"].append({"path": path, "relevance": known.classify(path),
                                      "sha256": new.sha(path), "license_markers": markers(new.read(path))})
            triage.append({"kind": "added-license", "path": path,
                           "action": "revisar el texto y registrarlo en manifests/licenses.json"})
    notice_status = []
    for path in sorted(known.notices):
        status = ("removed" if path not in new.by_path else
                  "unchanged" if old.sha(path) == new.sha(path) else "changed")
        notice_status.append({"path": "upstream/winuae/" + path, "status": status})
        if status != "unchanged":
            triage.append({"kind": f"license-notice-{status}", "path": path,
                           "action": "actualizar manifests/licenses.json (hash) tras leer el aviso"})
    unselected = {name: {k: len(v) for k, v in bucket.items()} for name, bucket in sorted(by_relevance.items())
                  if name not in RELEVANT and name != "unlisted-source"}
    missing_refs = sorted(
        {p for p in (known.selected | known.audited | known.generator_sources | known.patched | known.excluded)
         if p in old.by_path and p not in new.by_path})
    report = {
        "counts": {k: len(v) for k, v in delta.items()},
        "relevant": {k: v[:LISTING_CAP] for k, v in relevant.items()},
        "relevant_truncated": {k: max(0, len(v) - LISTING_CAP) for k, v in relevant.items()},
        "other_paths": unselected,
        "manifest_references_missing_in_new": missing_refs,
        "licenses": {"notice_files": notice_status, "marker_changes": marker_changes,
                     "added_with_markers": [e["path"] for e in relevant["added"] if e.get("license_markers")]},
    }
    return report, delta, sorted(triage, key=lambda t: (t["kind"], t["path"]))


# ------------------------------------------------------------------------------------------ patch series

def split_chunks(data: bytes) -> dict[str, bytes]:
    """One self-contained patch per file, in the original bytes."""
    starts = [m.start() for m in re.finditer(rb"(?m)^diff --git ", data)]
    chunks = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        header = data[start:data.index(b"\n", start)].decode("utf-8")
        chunks[mat.DIFF_HEADER.fullmatch(header)[1]] = data[start:end]
    return chunks


def parse_hunks(chunk: bytes) -> list[tuple[int, list[bytes]]]:
    lines, i, hunks = chunk.split(b"\n"), 0, []
    while i < len(lines):
        match = HUNK_HEADER.match(lines[i])
        i += 1
        if not match:
            continue
        old_count = int(match[2]) if match[2] is not None else 1
        new_count = int(match[4]) if match[4] is not None else 1
        block, seen_old, seen_new = [], 0, 0
        while i < len(lines) and (seen_old < old_count or seen_new < new_count):
            line = lines[i]
            if line.startswith(b"\\"):
                i += 1
                continue
            tag = line[:1]
            if tag in (b" ", b""):
                block.append(line[1:])
                seen_old += 1
                seen_new += 1
            elif tag == b"-":
                block.append(line[1:])
                seen_old += 1
            elif tag == b"+":
                seen_new += 1
            else:
                break
            i += 1
        hunks.append((int(match[1]), block))
    return hunks


def find_block(lines: list[bytes], block: list[bytes]) -> list[int]:
    if not block:
        return [0]
    positions = []
    first, size = block[0], len(block)
    for index in range(len(lines) - size + 1):
        if lines[index] == first and lines[index:index + size] == block:
            positions.append(index)
            if len(positions) >= 50:
                break
    return positions


def analyse_hunks(chunk: bytes, current: bytes, reference: bytes | None) -> list[dict]:
    current_lines = current.split(b"\n")
    reference_lines = reference.split(b"\n") if reference is not None else None
    result = []
    for index, (start, block) in enumerate(parse_hunks(chunk), 1):
        wanted = max(start - 1, 0)
        here = find_block(current_lines, block)
        entry = {"index": index, "old_start": start, "old_lines": len(block)}
        if wanted in here:
            entry["state"] = "in-place"
        elif here:
            entry["state"] = "shifted"
            entry["delta"] = min(here, key=lambda p: abs(p - wanted)) - wanted
        elif reference_lines is None:
            entry["state"] = "absent"
        elif find_block(reference_lines, block):
            entry["state"] = "upstream-changed"
        else:
            entry["state"] = "absent-from-baseline"
        result.append(entry)
    return result


def classify_failure(hunks: list[dict], rejection: mat.PatchRejected, has_reference: bool) -> tuple[str, str]:
    states = {h["state"] for h in hunks}
    if "upstream-changed" in states:
        return "semantic-conflict", "upstream cambio el codigo que el parche espera (zona del parche)"
    if "absent-from-baseline" in states or ("absent" in states and not has_reference):
        return "port-failure", "el contexto del parche no existe ni en el snapshot viejo: serie rota, no es culpa de upstream"
    if "shifted" in states:
        return "shifted-conflict", "el contexto esta intacto pero en otra linea (offset): re-exportar el parche, sin cambio semantico"
    return "port-failure", f"git apply lo rechazo ({rejection.kind}) sin causa localizable en los hunks"


def scrub(text: str, *paths: Path) -> str:
    spellings = {spelling for path in paths for spelling in (str(path), str(path.resolve()))}
    for spelling in sorted(spellings, key=len, reverse=True):  # /private/var/... before /var/...
        text = text.replace(spelling, "<workspace>")
    text = re.sub(r"\.(codegen|materialize|import-winuae)-[A-Za-z0-9_]+", r".\1-<tmp>", text)
    return text.strip()[:DIAG_LIMIT]


def read_patches(root: Path, series: dict) -> dict[str, bytes]:
    result = {}
    for patch in series["patches"]:
        path = mat._regular_file(root, root, patch["file"])
        result[patch["id"]] = path.read_bytes()
    return result


def run_series(series: dict, patch_bytes: dict[str, bytes], tree: Tree, directory: Path, *,
               reference: dict | None = None, baseline_failed: dict | None = None,
               original: Tree | None = None) -> tuple[list[dict], dict]:
    """Apply the series patch by patch on a sparse copy; return per-patch results and each patch's inputs."""
    touched = sorted({item["path"] for patch in series["patches"] for item in patch["files"]})
    for name in touched:
        if name in tree.by_path:
            target = directory.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if tree.by_path[name]["mode"] == "120000":
                raise UpdateError(f"El parche toca un symlink: {name}")
            target.write_bytes(tree.read(name))
    blocked: dict[str, str] = {}
    results, befores = [], {}
    for patch in series["patches"]:
        pid = patch["id"]
        entry = {"id": pid, "order": patch.get("order"), "title": patch.get("title"),
                 "test": patch.get("test"), "symbols": patch.get("symbols", []), "files": []}
        names = [item["path"] for item in patch["files"]]
        data = patch_bytes[pid]

        def finish(status: str, cause: str | None = None):
            entry["status"] = status
            if cause:
                entry["cause"] = cause
            if status in ("port-failure", "semantic-conflict", "shifted-conflict", "blocked"):
                for name in names:
                    blocked.setdefault(name, cause or pid)
            results.append(entry)
            return entry

        if sha256(data) != patch["sha256"]:
            entry["files"] = [{"path": n, "status": "port-failure", "detail": "el hash del archivo .patch no coincide con series.json"} for n in names]
            finish("port-failure")
            continue
        if baseline_failed and pid in baseline_failed:
            entry["files"] = [{"path": n, "status": "port-failure", "detail": "no aplicaba ya sobre el snapshot viejo"} for n in names]
            finish("port-failure")
            continue
        causes = sorted({blocked[n] for n in names if n in blocked})
        if causes:
            entry["files"] = [{"path": n, "status": "blocked", "detail": "depende de un archivo con conflicto"} for n in names]
            finish("blocked", causes[0])
            entry["blocked_by"] = causes
            continue
        chunks = split_chunks(data)
        befores[pid] = {}
        checked, failures = {}, False
        for item in patch["files"]:
            name = item["path"]
            file_result = {"path": name, "upstream_changed": bool(original and original.sha(name) != tree.sha(name))}
            current_path = directory.joinpath(*name.split("/"))
            if not current_path.is_file():
                file_result.update(status="semantic-conflict" if reference is not None else "port-failure",
                                   detail="el archivo ya no existe en el snapshot nuevo")
                entry["files"].append(file_result)
                failures = True
                continue
            current = current_path.read_bytes()
            befores[pid][name] = current
            file_result["before_sha256"] = sha256(current)
            try:
                mat._apply(chunks[name], directory, check=True)
                file_result["status"] = "checked"
            except mat.PatchRejected as rejection:
                ref = reference.get(pid, {}).get(name) if reference is not None else None
                hunks = analyse_hunks(chunks[name], current, ref)
                status, detail = classify_failure(hunks, rejection, reference is not None and ref is not None)
                file_result.update(status=status, detail=detail, hunks=hunks,
                                   git_output=scrub(rejection.output, directory)[:600])
                failures = True
            entry["files"].append(file_result)
        if failures:
            worst = ("port-failure", "semantic-conflict", "shifted-conflict")
            statuses = {f["status"] for f in entry["files"]}
            finish(next(s for s in worst if s in statuses) if statuses & set(worst) else "port-failure")
            for f in entry["files"]:  # untouched files of a failed patch are not applied either
                if f["status"] == "checked":
                    f["status"] = "not-applied"
            continue
        status = "clean"
        for item, file_result in zip(patch["files"], entry["files"]):
            name = item["path"]
            mat._apply(chunks[name], directory, check=False)
            after = sha256(directory.joinpath(*name.split("/")).read_bytes())
            file_result["after_sha256"] = after
            file_result["declared_before_sha256"] = item["before_sha256"]
            file_result["declared_after_sha256"] = item["after_sha256"]
            if file_result["before_sha256"] == item["before_sha256"]:
                if after == item["after_sha256"]:
                    file_result["status"] = "clean"
                else:
                    file_result.update(status="port-failure", detail="el parche produce un hash distinto del registrado")
                    status = "port-failure"
            elif reference is None:
                file_result.update(status="port-failure", detail="el archivo no es el que la serie registra como base")
                status = "port-failure"
            else:
                file_result["status"] = "rebased"
                file_result["detail"] = "el archivo difiere del registrado; el parche aplica exacto, falta re-registrar hashes"
                if status == "clean":
                    status = "rebased"
        finish(status)
    return results, befores


def last_after(results: list[dict]) -> dict[str, str]:
    final = {}
    for result in results:
        for f in result["files"]:
            if "after_sha256" in f:
                final[f["path"]] = f["after_sha256"]
    return final


def candidate_series(series: dict, results: list[dict], commit: str) -> dict:
    candidate = copy.deepcopy(series)
    candidate["upstream_commit"] = commit
    by_patch = {r["id"]: r for r in results}
    for patch in candidate["patches"]:
        for item in patch["files"]:
            applied = next(f for f in by_patch[patch["id"]]["files"] if f["path"] == item["path"])
            item["before_sha256"], item["after_sha256"] = applied["before_sha256"], applied["after_sha256"]
    return candidate


# ---------------------------------------------------------------------------------------------- codegen

def build_project_root(real_root: Path, destination: Path, tree: Tree, commit: str, series: dict,
                       patch_bytes: dict[str, bytes]) -> Path:
    """A project-shaped copy in the work area, so tools/generate_cpu.py runs on it unmodified."""
    lock = json.loads((real_root / "plan/upstream.lock.json").read_text())
    item = next(s for s in lock["sources"] if s["id"] == "winuae")
    pinned = {"id": "winuae", "repository": item["repository"], "commit": commit}
    (destination / "plan").mkdir(parents=True)
    (destination / "plan/upstream.lock.json").write_text(json.dumps({"schema_version": 1, "sources": [pinned]}, indent=2) + "\n")
    tree.export(destination / import_sources.DESTINATION)
    manifest = {**import_sources.metadata(import_sources.source(destination)), "files": tree.files}
    (destination / "manifests").mkdir()
    (destination / import_sources.MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    recipe = json.loads((real_root / "manifests/codegen.json").read_text())
    recipe["upstream_commit"] = commit
    (destination / "manifests/codegen.json").write_text(json.dumps(recipe, indent=2) + "\n")
    shutil.copy2(real_root / "manifests/source-manifest.json", destination / "manifests/source-manifest.json")
    shutil.copytree(real_root / "tools/codegen_host", destination / "tools/codegen_host")
    (destination / "tests/codegen").mkdir(parents=True)
    shutil.copy2(real_root / "tests/codegen/blitter_truth.c", destination / "tests/codegen/blitter_truth.c")
    (destination / "patches/winuae").mkdir(parents=True)
    for patch in series["patches"]:
        (destination / patch["file"]).write_bytes(patch_bytes[patch["id"]])
    (destination / "patches/winuae/series.json").write_text(json.dumps(series, indent=2) + "\n")
    return destination


def regenerate(real_root: Path, stage: Path, label: str, tree: Tree, commit: str, series: dict,
               patch_bytes: dict[str, bytes], cc: str, cxx: str) -> dict:
    import generate_cpu  # deferred: imports pull the whole codegen runner
    project = build_project_root(real_root, stage / f"root-{label}", tree, commit, series, patch_bytes)
    try:
        receipt = generate_cpu.generate(project, "build/generated", cc=cc, cxx=cxx)
    except Exception as error:  # noqa: BLE001 - every failure is the report, never a pass
        return {"status": "failed", "error_type": type(error).__name__, "diagnostic": scrub(str(error), stage),
                "project": project}
    return {"status": "ok", "files": receipt["files"], "project": project,
            "tool_inputs": {name: tool["inputs"] for name, tool in sorted(receipt["tools"].items())}}


def compare_generation(old: dict | None, new: dict, known: Known) -> dict:
    if old is None:
        base = new
    else:
        base = old
    before = {f["path"]: f["sha256"] for f in base["files"]}
    after = {f["path"]: f["sha256"] for f in new["files"]}
    outputs = {"unchanged": sum(1 for p in after if before.get(p) == after[p]),
               "changed": sorted(p for p in after if p in before and before[p] != after[p]),
               "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after))}
    generators = {}
    for name in sorted(new["tool_inputs"]):
        old_inputs = base["tool_inputs"].get(name, {})
        new_inputs = new["tool_inputs"][name]
        delta = sorted(k for k in set(old_inputs) | set(new_inputs) if old_inputs.get(k) != new_inputs.get(k))
        generators[name] = {"inputs_changed": delta[:20], "inputs_changed_count": len(delta)}
    return {"outputs": outputs, "generators": generators}


# ------------------------------------------------------------------------------------------------ driver

def validate_workdir(root: Path, workdir: str) -> Path:
    return mat._destination(root, workdir)


def proposed_tasks(new_sha: str, results: list[dict], triage: list[dict], generation: dict) -> list[dict]:
    tasks = []
    short = new_sha[:12]
    advice = {
        "semantic-conflict": "leer ambos lados (el parche y el cambio de upstream), reescribir el parche con su test y "
                             "registrar el motivo; no elegir ours/theirs",
        "shifted-conflict": "re-exportar el parche contra NEW sin cambiar su semantica (el cuerpo del diff debe ser el mismo)",
        "port-failure": "reparar la serie o el parche antes del update; el fallo no depende de upstream",
        "blocked": "se resuelve despues del parche que lo bloquea",
    }
    for result in results:
        if result["status"] in advice:
            tasks.append({"id": f"UPD-{short}-{result['id']}", "kind": result["status"],
                          "title": f"{result['status']}: {result['id']}",
                          "files": [f["path"] for f in result["files"]], "test": result.get("test"),
                          "action": advice[result["status"]], **({"blocked_by": result["blocked_by"]} if "blocked_by" in result else {})})
    for item in triage:
        tasks.append({"id": f"UPD-{short}-{item['kind']}-{item['path'].replace('/', '_')}", "kind": item["kind"],
                      "title": f"{item['kind']}: {item['path']}", "files": [item["path"]], "action": item["action"]})
    if generation.get("status") == "failed":
        tasks.append({"id": f"UPD-{short}-generators", "kind": "generator-failed", "title": "generador o receta no regeneran",
                      "files": generation.get("suspect_inputs", []),
                      "action": "leer el diagnostico del reporte; la receta manifests/codegen.json o el generador cambiaron: "
                                "adaptar la receta o el parche del generador, nunca editar las salidas"})
    return sorted(tasks, key=lambda t: t["id"])


def verification_plan(root: Path) -> list[dict]:
    plan = [("SRC01", "python3 tools/import_sources.py verify", "snapshot y manifiesto del nuevo pin (tras T38 mueve el lock)"),
            ("GEN01", "python3 tools/generate_cpu.py --check", "dos regeneraciones limpias iguales con el pin nuevo")]
    for check in ("REF01", "DET01", "DET02", "DET03", "DET04", "DET06", "SAFE01", "FUZZ01"):
        plan.append((check, f"python3 tools/check.py --check {check} --task T32", "corpus diferencial y matriz con el pin nuevo"))
    items = [{"id": i, "command": c, "covers": w, "status": "pending"} for i, c, w in plan]
    bench = (root / "tools/bench.py").exists()
    items.append({"id": "PERF01", "command": "python3 tools/bench.py --scenario ID --backend native --output PATH" if bench else None,
                  "covers": "rendimiento por perfil frente a los baselines registrados",
                  "status": "pending" if bench else "unavailable"})
    return items


def update(root: Path, old_sha: str, new_sha: str, workdir: str, *, new_source=None, new_manifest=None,
           skip_generate: bool = False, keep_workspace: bool = False, cc: str = "clang", cxx: str = "clang++") -> dict:
    root = Path(root).absolute()
    out = validate_workdir(root, workdir)
    mat._no_symlinks(root, out)
    pristine = import_sources.inventory(root / import_sources.DESTINATION)  # untouched-pristine witness
    old_tree, old_manifest = load_old(root, old_sha)
    if new_sha == old_sha and new_source is None and new_manifest is None:
        new_tree, provenance = old_tree, {"kind": "pinned-pristine", "commit": new_sha}
    else:
        new_tree, provenance = load_new(root, new_sha, new_source, new_manifest)
    series, series_sha = mat._series(root, old_sha)
    patch_bytes = read_patches(root, series)
    known = Known(root, series)
    out.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        if keep_workspace:
            stage = out / "workspace"
            if stage.exists():
                raise UpdateError(f"{stage.relative_to(root)} ya existe; no se sobrescribe (otro --workdir)")
            stage.mkdir()
        else:
            stage = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".stage-", dir=out))).absolute()
        report = _evaluate(root, stage, old_sha, new_sha, old_tree, new_tree, provenance, old_manifest, series,
                           series_sha, patch_bytes, known, skip_generate, cc, cxx)
    if import_sources.inventory(root / import_sources.DESTINATION) != pristine:
        raise UpdateError("SRC01: el snapshot pristine cambio durante la actualizacion")  # a bug of this tool
    write_outputs(out, report)
    return report


def _evaluate(root, stage, old_sha, new_sha, old_tree, new_tree, provenance, old_manifest, series, series_sha,
              patch_bytes, known, skip_generate, cc, cxx) -> dict:
    snapshot, delta, triage = snapshot_report(old_tree, new_tree, known, stage)
    unchanged_tree = not any(delta.values())
    baseline, old_before = run_series(series, patch_bytes, old_tree, stage / "patched-old")
    baseline_failed = {r["id"]: r for r in baseline if r["status"] == "port-failure"}
    reference = old_before
    results, _ = run_series(series, patch_bytes, new_tree, stage / "patched-new", reference=reference,
                            baseline_failed=baseline_failed, original=old_tree)
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    conflicts = [r for r in results if r["status"] in ("semantic-conflict", "shifted-conflict", "port-failure", "blocked")]
    old_after, new_after = last_after(baseline), last_after(results)
    patched_files = [{"path": path, "old_sha256": old_after.get(path, old_tree.sha(path)), "new_sha256": sha,
                      "changed": old_after.get(path, old_tree.sha(path)) != sha} for path, sha in sorted(new_after.items())]
    candidate = None if conflicts else candidate_series(series, results, new_sha)
    suspects = sorted({p for name, inputs in known.generators.items() for p in inputs
                       if p in delta["changed"] or p in delta["removed"]})
    generation: dict = {"status": "skipped", "reason": "--skip-generate"}
    materialized = {"predicted_equal": None, "build_upstream": "not-compared", "build_generated": "not-compared"}
    if conflicts:
        generation = {"status": "skipped", "reason": "hay conflictos en la serie: no se regenera sobre un arbol dudoso"}
    elif not skip_generate:
        old_gen = None
        if unchanged_tree:
            generation_base = "same-inputs"
        else:
            generation_base = "old-snapshot"
            old_gen = regenerate(root, stage, "old", old_tree, old_sha, series, patch_bytes, cc, cxx)
            if old_gen["status"] != "ok":
                raise UpdateError("El generador no funciona ni con el snapshot viejo (entorno, no upstream): "
                                  + old_gen["diagnostic"])
        new_gen = regenerate(root, stage, "new", new_tree, new_sha, candidate, patch_bytes, cc, cxx)
        if new_gen["status"] != "ok":
            generation = {"status": "failed", "error_type": new_gen["error_type"], "diagnostic": new_gen["diagnostic"],
                          "suspect_inputs": suspects, "baseline": generation_base}
        else:
            generation = {"status": "ok", "baseline": generation_base, **compare_generation(old_gen, new_gen, known),
                          "output_count": len(new_gen["files"])}
            built = import_sources.inventory(new_gen["project"] / "build/upstream")
            predicted = {item["path"]: dict(item) for item in new_tree.files}
            for path, sha in new_after.items():
                predicted[path]["sha256"] = sha
            materialized["predicted_equal"] = built == sorted(predicted.values(), key=lambda i: i["path"])
            if unchanged_tree and (root / "build/upstream").is_dir() and not (root / "build/upstream").is_symlink():
                materialized["build_upstream"] = ("identical" if import_sources.inventory(root / "build/upstream") == built
                                                  else "different")
            elif unchanged_tree:
                materialized["build_upstream"] = "absent"
            if unchanged_tree:  # read-only comparison with what the project built before; never written
                current = root / "build/generated"
                if current.is_dir() and not current.is_symlink():
                    present = [f for f in import_sources.inventory(current) if f["path"] != "codegen-receipt.json"]
                    materialized["build_generated"] = "identical" if present == new_gen["files"] else "different"
                else:
                    materialized["build_generated"] = "absent"
    if generation["status"] == "ok" and materialized["predicted_equal"] is False:
        generation = {**generation, "status": "failed", "error_type": "UpdateError",
                      "diagnostic": "el arbol materializado no coincide con la cadena de hashes prevista", "suspect_inputs": []}
    verdict = ("conflicts" if conflicts else "generator-failed" if generation["status"] == "failed" else
               "triage-required" if triage else "no-changes" if unchanged_tree else "candidate")
    tasks = proposed_tasks(new_sha, results, triage, generation)
    report = {
        "schema_version": 1,
        "tool": "tools/update_upstream.py",
        "verdict": verdict,
        "exit_code": EXIT[verdict],
        "old": {"commit": old_sha, "files": len(old_tree.files), "tree_sha256": old_tree.tree_sha256,
                "series_sha256": series_sha, "patches": len(series["patches"]), "snapshot_verified": "SRC01 (lock + manifest + bytes)"},
        "new": {"commit": new_sha, "files": len(new_tree.files), "tree_sha256": new_tree.tree_sha256,
                "source": provenance,
                "snapshot_verified": {"git": "blobs leidos del objeto Git del commit",
                                      "directory": "inventario igual al manifiesto dado",
                                      "pinned-pristine": "SRC01 (lock + manifest + bytes)"}[provenance["kind"]]},
        "snapshot": snapshot,
        "patches": {"counts": dict(sorted(counts.items())), "total": len(results),
                    "baseline_ok": not baseline_failed, "policy": "git apply exacto: sin fuzz ni offset; un hunk que no aplica limpio es conflicto",
                    "details": [r for r in results if r["status"] != "clean"] if results else [],
                    "clean": [r["id"] for r in results if r["status"] == "clean"]},
        "hashes": {"patched_files": patched_files, "series_changes": sum(1 for p in patched_files if p["changed"]),
                   "candidate_series": "candidate-series.json" if candidate else None},
        "materialized": materialized,
        "generation": generation,
        "triage": triage,
        "proposed_tasks": tasks,
        "verification_plan": {"note": "Preparado, no ejecutado: esta herramienta no corre la suite, no toca goldens y no publica. "
                                      "Un hash distinto se clasifica como bug upstream documentado, regresion del port o desconocido (bloquea).",
                              "steps": verification_plan(root)},
        "_candidate_series": candidate,
    }
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# Update de upstream {report['old']['commit'][:12]} -> {report['new']['commit'][:12]}", "",
             f"Veredicto: **{report['verdict']}** (salida {report['exit_code']})", ""]
    counts = report["snapshot"]["counts"]
    lines += [f"- Archivos: {counts['added']} añadidos, {counts['removed']} quitados, {counts['changed']} cambiados",
              f"- Parches: {report['patches']['total']} ({', '.join(f'{k} {v}' for k, v in report['patches']['counts'].items())})",
              f"- Regeneracion: {report['generation']['status']}"]
    if report["generation"].get("reason"):
        lines.append(f"  - {report['generation']['reason']}")
    lines.append("")
    if report["patches"]["details"]:
        lines += ["## Parches que no son limpios", ""]
        for patch in report["patches"]["details"]:
            lines.append(f"- {patch['id']}: {patch['status']}" + (f" (bloqueado por {', '.join(patch['blocked_by'])})" if "blocked_by" in patch else ""))
            for f in patch["files"]:
                if f["status"] not in ("clean",):
                    lines.append(f"  - {f['path']}: {f['status']}" + (f" - {f['detail']}" if f.get("detail") else ""))
        lines.append("")
    if report["triage"]:
        lines += ["## A decidir", ""] + [f"- {t['kind']}: {t['path']} - {t['action']}" for t in report["triage"]] + [""]
    if report["generation"]["status"] == "failed":
        lines += ["## Generador", "", "```", report["generation"]["diagnostic"], "```", ""]
        if report["generation"].get("suspect_inputs"):
            lines += ["Fuentes de generador cambiadas upstream: " + ", ".join(report["generation"]["suspect_inputs"]), ""]
    if report["proposed_tasks"]:
        lines += ["## Tareas propuestas", ""] + [f"- {t['id']}: {t['action']}" for t in report["proposed_tasks"]] + [""]
    lines += ["## Verificacion pendiente", ""] + [
        f"- {s['id']}: `{s['command']}` ({s['status']})" if s["command"] else f"- {s['id']}: {s['status']}"
        for s in report["verification_plan"]["steps"]] + [""]
    return "\n".join(lines)


def write_outputs(out: Path, report: dict) -> None:
    candidate = report.pop("_candidate_series")
    files = {"report.json": json.dumps(report, indent=2, ensure_ascii=False) + "\n",
             "report.md": render_markdown(report)}
    if candidate is not None:
        files["candidate-series.json"] = json.dumps(candidate, indent=2) + "\n"
    else:
        (out / "candidate-series.json").unlink(missing_ok=True)  # never leave a stale candidate next to a failing report
    for name, text in files.items():
        _atomic_text(out / name, text)


def _atomic_text(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".out-", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="OLD_SHA: el commit fijado en plan/upstream.lock.json")
    parser.add_argument("--new", required=True, help="NEW_SHA: commit completo (nunca master/latest)")
    parser.add_argument("--workdir", required=True, help="Area de trabajo propia, bajo build/ del proyecto")
    parser.add_argument("--new-source", help="Repositorio Git local con NEW_SHA, o directorio snapshot con --new-manifest; "
                                             "por defecto la cache de tools/import_sources.py")
    parser.add_argument("--new-manifest", help="Manifiesto (formato manifests/upstream-files.json) del directorio --new-source")
    parser.add_argument("--skip-generate", action="store_true", help="No regenerar (cpu/blitter/linetoscr)")
    parser.add_argument("--keep-workspace", action="store_true", help="Conservar los arboles en WORKDIR/workspace")
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = update(Path(args.root), args.old, args.new, args.workdir, new_source=args.new_source,
                        new_manifest=args.new_manifest, skip_generate=args.skip_generate,
                        keep_workspace=args.keep_workspace, cc=args.cc, cxx=args.cxx)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error).strip(), file=sys.stderr)
        return EXIT["error"]
    print(f"UPD01 {report['verdict']}: {report['patches']['total']} parches "
          f"({', '.join(f'{k} {v}' for k, v in report['patches']['counts'].items())}), "
          f"generacion {report['generation']['status']}; reporte en {args.workdir}/report.json")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
