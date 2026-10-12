#!/usr/bin/env python3
"""T38 rehearsal helper: assemble a project-shaped copy of the product that pins the CANDIDATE upstream.

  make_project.py DEST NEW_GIT_REPO NEW_SHA SERIES_DIR

DEST must be a new directory under build/.  It receives the tracked files of the product (minus references/, upstream/ and reports/),
the candidate snapshot (read from Git objects like tools/import_sources.py does) with its manifest, a lock that pins NEW_SHA, and the
rebased patch series of SERIES_DIR (patches/winuae/*.patch + series.json).  Nothing outside DEST is written; the product tree keeps
its pins, so returning to them is just not using DEST.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402

OLD = "857f48fcb09399d4d95527e29a2fdeaedb41f827"
SKIP = ("references/", "upstream/", "reports/", "build/")
REWRITE = ("manifests/source-manifest.json", "manifests/state-inventory.json", "manifests/codegen.json", "manifests/host-surface.json")


def main(dest, new_repo, new_sha, series_dir):
    dest, new_repo, series_dir = Path(dest).resolve(), Path(new_repo).resolve(), Path(series_dir).resolve()
    if (ROOT / "build") not in dest.parents or dest.exists():
        sys.exit("DEST must be a new directory under build/")
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
    for name in filter(None, names):
        if name.startswith(SKIP):
            continue
        source = ROOT / name
        if not source.exists():
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in REWRITE:
            target.write_text(source.read_text().replace(OLD, new_sha))
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    # lock: same file, winuae item pinned to the candidate commit (a bare commit: there is no tag after 6030)
    lock = json.loads((ROOT / "plan/upstream.lock.json").read_text())
    item = next(s for s in lock["sources"] if s["id"] == "winuae")
    for key in ("tag", "tag_object", "version"):
        item.pop(key, None)
    item["commit"] = new_sha
    item["verified"] = "T38 rehearsal: candidate snapshot read from Git objects of the fetched commit; not a product pin"
    (dest / "plan/upstream.lock.json").write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
    # candidate snapshot + manifest
    entries = []
    stage = dest / import_sources.DESTINATION
    for name, mode, content in import_sources.blobs(new_repo, {"id": "winuae", "commit": new_sha}):
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            path.symlink_to(import_sources.safe_link(name, content))
        else:
            path.write_bytes(content)
            path.chmod(0o755 if mode == "100755" else 0o644)
        entries.append({"path": name, "mode": mode, "sha256": hashlib.sha256(content).hexdigest()})
    item_for_meta = import_sources.source(dest)
    manifest = {**import_sources.metadata(item_for_meta), "files": sorted(entries, key=lambda e: e["path"])}
    (dest / import_sources.MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    # rebased series
    patches = dest / "patches/winuae"
    for old in patches.glob("*.patch"):
        old.unlink()
    for new in (series_dir / "patches/winuae").glob("*.patch"):
        shutil.copy2(new, patches / new.name)
    shutil.copy2(series_dir / "patches/winuae/series.json", patches / "series.json")
    import_sources.verify_source(dest)
    print(f"project at {dest}: {len(entries)} candidate files, SRC01 ok")


if __name__ == "__main__":
    main(*sys.argv[1:5])
