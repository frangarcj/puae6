#!/usr/bin/env python3
"""Materialize the REFERENCE tree of REF01: the same WinUAE 6.0.3 snapshot with only the patches the profile cannot run without.

The product is the pinned snapshot plus the whole patch queue (tools/materialize_upstream.py, a closed hash chain). The reference is the
same snapshot plus the queue MINUS the patches that tests/reference/patch-classes.json marks `ablate`: the ones that change what the
engine computes, or claim to rewrite a piece of code without changing its result. Everything else stays because the profile does not link
or run without it (host services excluded, portability, the return at a video unit) or because the probe that observes both builds
reads the machine through it.

Nothing here is an independent implementation: the reference shares the bridge, the portable port, the micro-ROMs and the probe with the
product, and so it can only show what the ablated patches change. It does not replace WinUAE on Windows, and equal output is fidelity to
the chosen upstream commit, never proof about a physical Amiga (docs/04-validation.md, "Referencia de exactitud").

    python3 tools/build_reference.py [--check] [--destination build/reference/upstream]

The tree is applied with `git apply` (offsets allowed, no fuzz, a context that no longer matches is an error that says which patch depends
on which ablated one), published atomically, and described by a receipt next to it. A tree whose files are not what the receipt says is
never reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402
import materialize_upstream as mu  # noqa: E402

CLASSES_FILE = "tests/reference/patch-classes.json"
CLASS_NAMES = ("required", "instrumentation", "behavior")
EFFECTS = ("neutral", "inert", "differs")


def load_classes(root: Path = ROOT) -> dict:
    return json.loads((root / CLASSES_FILE).read_text(encoding="utf-8"))


def validate(classes: dict, series: dict) -> list[str]:
    """Every patch of the series is classified exactly once; an ablated patch says what its absence is expected to change and which test backs it."""
    errors = []
    if classes.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    mapping = classes.get("patches")
    ablated = classes.get("ablate")
    if not isinstance(mapping, dict) or not isinstance(ablated, dict):
        return errors + ["'patches' and 'ablate' must be objects"]
    ids = [patch["id"] for patch in series["patches"]]
    for ident in ids:
        if ident not in mapping:
            errors.append(f"patch not classified: {ident}")
    for ident, name in mapping.items():
        if ident not in ids:
            errors.append(f"classified patch that is not in the series: {ident}")
        if name not in CLASS_NAMES:
            errors.append(f"{ident}: unknown class {name!r}")
    for ident, entry in ablated.items():
        if mapping.get(ident) not in ("behavior", "instrumentation"):
            errors.append(f"{ident}: only behavior and instrumentation patches can be ablated")
        if not isinstance(entry, dict) or set(entry) != {"effect", "why", "test", "scenarios", "subsystems"}:
            errors.append(f"{ident}: the ablation needs exactly effect, why, test, scenarios and subsystems")
            continue
        if entry["effect"] not in EFFECTS:
            errors.append(f"{ident}: effect must be one of {', '.join(EFFECTS)}")
        if not all(isinstance(entry[key], str) and entry[key] for key in ("why", "test")):
            errors.append(f"{ident}: why and test must be non-empty text")
        if not isinstance(entry["scenarios"], list) or not all(isinstance(s, str) for s in entry["scenarios"]):
            errors.append(f"{ident}: scenarios must be a list of case ids")
        if not isinstance(entry["subsystems"], list) or not all(isinstance(s, str) for s in entry["subsystems"]):
            errors.append(f"{ident}: subsystems must be a list of probe subsystems")
        if entry["effect"] == "differs" and not (entry["scenarios"] and entry["subsystems"]):
            errors.append(f"{ident}: a patch whose absence is expected to differ names the cases and the subsystems where")
        if entry["effect"] != "differs" and entry["subsystems"]:
            errors.append(f"{ident}: only a patch expected to differ names subsystems")
    kept = classes.get("keep", {})
    for ident, name in mapping.items():
        if name == "behavior" and ident not in ablated and ident not in kept:
            errors.append(f"{ident}: a behavior patch the reference keeps says why under 'keep'")
    for ident in kept:
        if mapping.get(ident) != "behavior" or ident in ablated:
            errors.append(f"{ident}: 'keep' lists behavior patches that are not ablated")
    return errors


def selection(classes: dict, series: dict) -> tuple[list[dict], list[dict]]:
    ablated = set(classes["ablate"])
    kept = [p for p in series["patches"] if p["id"] not in ablated]
    removed = [p for p in series["patches"] if p["id"] in ablated]
    return kept, removed


def _apply_allowing_offsets(data: bytes, tree: Path, ident: str) -> int:
    """git apply with offsets (an ablated patch moves the lines of the next ones) and without fuzz; returns the number of offset hunks."""
    env = dict(os.environ, LC_ALL="C", GIT_CEILING_DIRECTORIES=str(tree.parent))
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    result = subprocess.run(["git", "apply", "--verbose", "-"], cwd=tree, input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"{ident} does not apply to the reference tree (it depends on an ablated patch): {output.strip()}")
    return output.count("offset")


def _tree_digest(files: list[dict]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def materialize_reference(root: Path = ROOT, destination: str = "build/reference/upstream", *, force: bool = False,
                          classes: dict | None = None) -> tuple[dict, Path]:
    """`classes` replaces tests/reference/patch-classes.json (the tests use it to ablate a patch that others need)."""
    root = Path(root).absolute()
    pristine = import_sources.verify_source(root)
    commit = pristine["source"]["commit"]
    series, series_sha = mu._series(root, commit)
    classes = load_classes(root) if classes is None else classes
    errors = validate(classes, series)
    if errors:
        raise ValueError("patch classification is incoherent:\n  " + "\n  ".join(errors))
    classes_sha = hashlib.sha256(json.dumps(classes, sort_keys=True).encode()).hexdigest()
    kept, removed = selection(classes, series)
    target = mu._destination(root, destination)
    receipt_path = target.with_name(target.name + "-receipt.json")
    mu._no_symlinks(root, receipt_path)
    identity = dict(commit=commit, series=series_sha, classes=classes_sha, pristine=_tree_digest(pristine["files"]),
                    implementation=[mu._sha(Path(__file__)), mu._sha(Path(mu.__file__)), mu._sha(Path(import_sources.__file__))])
    identity_sha = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    try:
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        previous = None
    if not force and previous and previous.get("identity") == identity_sha and target.is_dir():
        if _tree_digest(import_sources.inventory(target)) != previous["tree"]:
            raise ValueError(f"the reference tree is not what its receipt says; not reused: {target}")
        return previous, target

    build = root / "build"
    mu._no_symlinks(root, build)
    build.mkdir(parents=True, exist_ok=True)
    source = root / pristine["destination"]
    with tempfile.TemporaryDirectory(prefix=".reference-", dir=build) as tmp:
        stage = Path(tmp) / "tree"
        shutil.copytree(source, stage, symlinks=True)
        if import_sources.inventory(stage) != pristine["files"]:
            raise ValueError("the staged copy does not match the verified snapshot")
        offsets = 0
        for patch in kept:
            path = root / patch["file"]
            mu._no_symlinks(root, path)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != patch["sha256"]:
                raise ValueError(f"wrong patch hash: {patch['id']}")
            offsets += _apply_allowing_offsets(data, stage, patch["id"])
        files = import_sources.inventory(stage)
        if target.exists():
            if _tree_digest(import_sources.inventory(target)) != _tree_digest(files):
                raise ValueError(f"an existing different tree is in the way; not overwritten: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, target)
    receipt = dict(identity=identity_sha, upstream_commit=commit, series_sha256=series_sha, classes_sha256=classes_sha,
                   applied=[p["id"] for p in kept], ablated=[p["id"] for p in removed], offset_hunks=offsets, tree=_tree_digest(files))
    mu._atomic_json(receipt_path, receipt)
    return receipt, target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--destination", default="build/reference/upstream")
    parser.add_argument("--check", action="store_true", help="only validate the classification against the series")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        series, _ = mu._series(ROOT, import_sources.verify_source(ROOT)["source"]["commit"])
        errors = validate(load_classes(ROOT), series)
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(series['patches'])} patches classified" if not errors else f"{len(errors)} problems")
        return 1 if errors else 0
    receipt, path = materialize_reference(ROOT, args.destination, force=args.force)
    print(f"reference tree {path}: {len(receipt['applied'])} patches applied, {len(receipt['ablated'])} ablated, "
          f"{receipt['offset_hunks']} hunks with an offset (WinUAE {receipt['upstream_commit']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
