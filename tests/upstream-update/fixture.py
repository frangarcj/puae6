"""A mini "upstream" with two versions and a project that patches and generates from it.

The mini upstream is a real Git repository (so the tool reads NEW_SHA the way it reads WinUAE) with a
selected unit that two patches change (core.cpp), a second patched unit (util.cpp), an unpatched selected unit
(extra.cpp), a licence notice (COPYING) and a C generator (gen.cpp) whose output is the generated unit tab.cpp.
`Mini.commit(...)` makes the next upstream version; the project (lock, pristine snapshot, series, recipe,
manifests) is always the one that pins the first commit.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_sources  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def functions() -> list[str]:
    body = ["// core.cpp - mini engine unit", ""]
    for name in "abcdefghijklmnopqrstuvwxy":
        value = {"a": "x + 1", "f": "x * 6"}.get(name, f"x - {ord(name) - 96}")
        body += [f"int core_{name}(int x)", "{", f"    return {value};", "}", ""]
    body += ["int core_z(void)", "{", "    return 0;", "}", ""]
    return body


BASE = {
    "core.cpp": "\n".join(functions()),
    "util.cpp": "int util_one(void) { return 1; }\nint util_two(void) { return 2; }\nint util_three(void) { return 3; }\n",
    "extra.cpp": "int extra(void) { return 5; }\n",
    "COPYING": "GNU General Public License fixture, version 2\n",
    "gen.cpp": '#include <stdio.h>\nint main(void)\n{\n    puts("const int table[] = {1, 2, 3};");\n    return 0;\n}\n',
    "README": "mini upstream\n",
    "gayle_like/ide.cpp": "int ide(void) { return 0; }\n",
}

RECIPE = {
    "schema_version": 1, "upstream_commit": None,
    "flags": {"c": ["-std=c11", "-O2"], "c++": ["-std=c++17", "-O2"]},
    "environment": {"LC_ALL": "C", "TZ": "UTC"}, "absent_inputs": [],
    "generators": [{"name": "mini", "language": "c", "sources": ["upstream/gen.cpp"], "outputs": ["tab.cpp"],
                   "runs": [{"args": [], "stdout": "generated/tab.cpp", "cwd": "generated"}]}],
}
SOURCE_MANIFEST = {
    "schema_version": 1,
    "audit_sources": ["gayle_like/ide.cpp"],
    "selection": {"upstream_units": ["core.cpp", "util.cpp", "extra.cpp"], "textual_inputs": [],
                  "generated_units": ["tab.cpp"], "excluded_units": {"skip.cpp": "fixture"}},
}


def make_patch(path: str, old: str, new: str) -> bytes:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=3))
    return (f"diff --git a/{path} b/{path}\n" + "\n".join(lines) + "\n").encode()


def run(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="f@invalid", GIT_COMMITTER_NAME="Fixture",
               GIT_COMMITTER_EMAIL="f@invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


class Mini:
    """Mini upstream repository plus the project that pins its first commit."""

    def __init__(self, base: Path):
        self.repo = base / "upstream.git"
        self.repo.mkdir()
        run(self.repo, "init", "-q")
        self.files = dict(BASE)
        self.first = self._commit("v1")
        self.root = base / "project"
        self.root.mkdir()
        self._project()

    def _commit(self, message: str) -> str:
        for path in list(self.repo.rglob("*")):
            if path.is_file() and ".git" not in path.relative_to(self.repo).parts:
                path.unlink()
        for name, text in self.files.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode())
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return run(self.repo, "rev-parse", "HEAD")

    def commit(self, message: str, changes: dict[str, str | None]) -> str:
        """The next upstream version: text replaces a file, None removes it."""
        files = dict(self.files)
        for name, text in changes.items():
            if text is None:
                files.pop(name, None)
            else:
                files[name] = text
        saved, self.files = self.files, files
        try:
            return self._commit(message)
        finally:
            self.files = saved

    def _project(self) -> None:
        root = self.root
        (root / "plan").mkdir()
        (root / "plan/upstream.lock.json").write_text(json.dumps({"schema_version": 1, "sources": [
            {"id": "winuae", "repository": "https://example.invalid/mini.git", "commit": self.first}]}))
        import_sources.import_source(root, cache=self.repo)
        recipe = dict(RECIPE, upstream_commit=self.first)
        (root / "manifests/codegen.json").write_text(json.dumps(recipe, indent=2))
        (root / "manifests/source-manifest.json").write_text(json.dumps(SOURCE_MANIFEST, indent=2))
        (root / "manifests/licenses.json").write_text(json.dumps({"schema_version": 1, "components": [{
            "id": "mini", "source_id": "winuae",
            "notice_files": [{"path": "upstream/winuae/COPYING", "sha256": sha(BASE["COPYING"].encode())}]}]}))
        (root / "tools/codegen_host").mkdir(parents=True)
        (root / "tools/codegen_host/stub.h").write_text("/* host stub */\n")
        (root / "tests/codegen").mkdir(parents=True)
        (root / "tests/codegen/blitter_truth.c").write_text("int main(void) { return 0; }\n")
        (root / "patches/winuae").mkdir(parents=True)
        self.write_series(self.default_patches())

    def default_patches(self) -> list[dict]:
        core = BASE["core.cpp"]
        step1 = core.replace("return x + 1;", "return x + 2;")
        step2 = step1.replace("return 0;", "return 7;")
        util = BASE["util.cpp"]
        return [dict(id="0001-core-a", path="core.cpp", old=core, new=step1),
                dict(id="0002-core-z", path="core.cpp", old=step1, new=step2),
                dict(id="0003-util-two", path="util.cpp", old=util, new=util.replace("return 2;", "return 22;"))]

    def write_series(self, patches: list[dict], *, patch_bytes: dict[str, bytes] | None = None) -> None:
        series = {"schema_version": 1, "upstream_commit": self.first, "patches": []}
        for order, spec in enumerate(patches, 1):
            data = (patch_bytes or {}).get(spec["id"]) or make_patch(spec["path"], spec["old"], spec["new"])
            name = f"patches/winuae/{spec['id']}.patch"
            (self.root / name).write_bytes(data)
            series["patches"].append({
                "id": spec["id"], "file": name, "sha256": sha(data), "title": spec["id"], "order": order,
                "symbols": [spec["id"]], "test": "tests/upstream-update",
                "files": [{"path": spec["path"], "before_sha256": sha(spec["old"].encode()),
                           "after_sha256": sha(spec["new"].encode())}]})
        (self.root / "patches/winuae/series.json").write_text(json.dumps(series, indent=2) + "\n")

    def listing(self, skip: str) -> dict[str, str]:
        """Every file of the project outside `skip` (a workdir), for the "nothing else changed" checks."""
        result = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            if path.is_file() and not path.is_symlink() and not relative.startswith(skip):
                result[relative] = sha(path.read_bytes())
        return result
