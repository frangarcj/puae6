#!/usr/bin/env python3
"""T38 rehearsal helper: rebase the WinUAE patch queue onto a new upstream snapshot with Git's 3-way merge.

Only used inside the rehearsal work area (build/update/...). It never touches patches/, upstream/ or manifests/.

  init   REPO_DIR NEW_GIT_REPO NEW_SHA   build a sparse scratch repository: commit `old` (pristine files the series touches),
                                          one commit per old patch (branch `series`), commit `new` (same files at NEW_SHA,
                                          branch `new`, child of `old`).  Then:  git rebase --onto new old series
  export REPO_DIR OUT_DIR NEW_SHA        after the rebase: one patch per commit in the exact format of patches/winuae
                                          (no index lines), series.json with refreshed hashes (hash-closed chain verified)

The caller resolves conflicts by hand (edit, git add, git rebase --continue); nothing is resolved as ours/theirs here.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, LC_ALL="C", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
           GIT_AUTHOR_NAME="rehearsal", GIT_AUTHOR_EMAIL="rehearsal@invalid", GIT_COMMITTER_NAME="rehearsal",
           GIT_COMMITTER_EMAIL="rehearsal@invalid", GIT_AUTHOR_DATE="2000-01-01T00:00:00Z",
           GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")


def git(repo, *args, data=None, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=ENV, check=check)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_series():
    return json.loads((ROOT / "patches/winuae/series.json").read_text())


def init(repo, new_repo, new_sha):
    series = load_series()
    touched = sorted({f["path"] for p in series["patches"] for f in p["files"]})
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "old")
    git(repo, "config", "core.autocrlf", "false")
    for name in touched:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "upstream/winuae" / name).read_bytes())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "old: pristine files touched by the series")
    git(repo, "branch", "series")
    git(repo, "checkout", "-q", "series")
    for patch in series["patches"]:
        data = (ROOT / patch["file"]).read_bytes()
        assert sha(data) == patch["sha256"], patch["id"]
        git(repo, "apply", "-", data=data)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", patch["id"])
    git(repo, "checkout", "-q", "-b", "new", "old")
    missing = []
    for name in touched:
        blob = git(new_repo, "cat-file", "-p", f"{new_sha}:{name}", check=False)
        target = repo / name
        if blob.returncode:
            missing.append(name)
            target.unlink()
            continue
        target.write_bytes(blob.stdout)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"new: the same files at {new_sha}")
    git(repo, "checkout", "-q", "series")
    print(f"touched files: {len(touched)}; missing in new: {missing}")


def export(repo, out, new_sha):
    series = load_series()
    out.mkdir(parents=True, exist_ok=True)
    (out / "patches/winuae").mkdir(parents=True, exist_ok=True)
    commits = git(repo, "rev-list", "--reverse", "new..HEAD").stdout.decode().split()
    by_id = {git(repo, "log", "-1", "--format=%s", c).stdout.decode().strip(): c for c in commits}
    old_ids = [p["id"] for p in series["patches"]]
    # patches the update itself needs (upstream code that does not build in the profile): described in new-patches.json, applied after the queue
    new_file = Path(__file__).with_name("new-patches.json")
    added = json.loads(new_file.read_text()) if new_file.exists() else []
    added_ids = [p["id"] for p in added]
    unknown = sorted(set(by_id) - set(old_ids) - set(added_ids))
    if unknown:
        sys.exit(f"commits that are not patches of the series: {unknown}")
    dropped = [i for i in old_ids if i not in by_id]  # became empty: upstream already has the change
    last = {}
    candidate = json.loads(json.dumps(series))
    candidate["upstream_commit"] = new_sha
    candidate["patches"] = [p for p in candidate["patches"] if p["id"] in by_id]
    order = max(p["order"] for p in series["patches"])
    for extra in added:
        if extra["id"] in by_id:
            order += 1
            candidate["patches"].append({**extra, "file": f"patches/winuae/{extra['id']}.patch", "order": order, "files": [
                {"path": n, "before_sha256": "0" * 64, "after_sha256": "0" * 64}
                for n in git(repo, "diff", "--name-only", by_id[extra["id"]] + "^", by_id[extra["id"]]).stdout.decode().split()]})
    report = {"dropped": dropped, "added": [i for i in added_ids if i in by_id], "patches": {}}
    old_files = {p["id"]: p for p in series["patches"]}
    for patch in candidate["patches"]:
        commit = by_id[patch["id"]]
        names = git(repo, "diff", "--name-only", commit + "^", commit).stdout.decode().split()
        old_names = [f["path"] for f in patch["files"]]
        if patch["id"] in added_ids:
            pass
        elif sorted(names) != sorted(old_names):
            # a file of the patch no longer changes (upstream has it): keep only the files that still change
            patch["files"] = [f for f in patch["files"] if f["path"] in names]
            if not patch["files"] or set(names) - set(old_names):
                sys.exit(f"{patch['id']}: touches {names}, was {old_names}")
        text = b""
        for item in patch["files"]:
            name = item["path"]
            body = git(repo, "diff", "-U3", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", commit + "^", commit, "--", name).stdout
            lines = body.split(b"\n")
            keep = [lines[0]] + [l for l in lines[1:] if not l.startswith(b"index ")]
            text += b"\n".join(keep)
            before = git(repo, "show", f"{commit}^:{name}").stdout
            after = git(repo, "show", f"{commit}:{name}").stdout
            item["before_sha256"], item["after_sha256"] = sha(before), sha(after)
            if name in last and last[name] != item["before_sha256"]:
                sys.exit(f"{patch['id']}: hash chain broken at {name}")
            last[name] = item["after_sha256"]
        (out / patch["file"]).write_bytes(text)
        patch["sha256"] = sha(text)
        if patch["id"] in added_ids:
            report["patches"][patch["id"]] = {"new": True}
            continue
        old_text = (ROOT / old_files[patch["id"]]["file"]).read_bytes()

        def changed(data, mark):
            return sorted(l for l in data.split(b"\n") if l[:1] == mark and l[:3] not in (b"+++", b"---"))

        same_body = changed(old_text, b"+") == changed(text, b"+") and changed(old_text, b"-") == changed(text, b"-")
        report["patches"][patch["id"]] = {"body_unchanged": same_body, "files_dropped": sorted(set(old_names) - set(names)),
                                           "bytes_identical": old_text == text}
    (out / "patches/winuae/series.json").write_text(json.dumps(candidate, indent=2) + "\n")
    (out / "rebase-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"exported {len(candidate['patches'])} patches to {out}; dropped {dropped}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "init":
        init(Path(args[0]), Path(args[1]), args[2])
    elif cmd == "export":
        export(Path(args[0]), Path(args[1]), args[2])
    else:
        sys.exit(__doc__)
