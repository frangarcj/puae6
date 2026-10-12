#!/usr/bin/env python3
"""T38: size of the patch queue before and after the update (patches, files, +/- lines, hot files), from series.json and the patch files only.

    series_metrics.py OLD_ROOT NEW_SERIES_ROOT
OLD_ROOT is a project root (patches/winuae/ there); NEW_SERIES_ROOT holds patches/winuae/ of the candidate series. Prints JSON.
"""
import json
from pathlib import Path
import sys


def metrics(root):
    root = Path(root)
    series = json.loads((root / "patches/winuae/series.json").read_text())
    files, plus, minus, per_file = set(), 0, 0, {}
    for patch in series["patches"]:
        text = (root / patch["file"]).read_text(encoding="utf-8", errors="replace")
        current = None
        for line in text.splitlines():
            if line.startswith("diff --git "):
                current = line.split()[-1][2:]
                files.add(current)
                per_file.setdefault(current, [0, 0, 0])[2] += 1
            elif line.startswith("+") and not line.startswith("+++"):
                plus += 1
                per_file[current][0] += 1
            elif line.startswith("-") and not line.startswith("---"):
                minus += 1
                per_file[current][1] += 1
    hot = sorted(per_file.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]
    return {"patches": len(series["patches"]), "files": len(files), "added_lines": plus, "removed_lines": minus,
            "hot_files": [{"file": k, "added": v[0], "removed": v[1], "patches": v[2]} for k, v in hot]}


if __name__ == "__main__":
    print(json.dumps({"old": metrics(sys.argv[1]), "new": metrics(sys.argv[2])}, indent=2))
