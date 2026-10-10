#!/usr/bin/env python3
"""A patched copy of the pinned BizHawk (MacHawk) sources with the PUAE6 core in it, built, for T20.

Nothing is ever written in ../MacHawk: the sources come out of its git object store with `git archive` at the commit that plan/machawk.reference.json
pins, into build/bizhawk-tree/<key of the inputs>, where the patches of this directory are applied and the PUAE6 sources (integration/bizhawk/core/Puae6 and
integration/bizhawk/core/EmuHawk) are copied into BizHawk.Emulation.Cores. The projects that are built are the real ones, with BizHawk's own analyzers and
net48 reference assemblies, restored from the local NuGet cache (never from the network).

    python3 integration/bizhawk/patches/build_tree.py            # export, patch, install the sources and build; prints the assembly
    python3 integration/bizhawk/patches/build_tree.py --check    # only: does every patch apply to the pinned commit (nothing is written)
    python3 integration/bizhawk/patches/build_tree.py --emuhawk  # the same in a second tree (<key>-emuhawk) that also has EmuHawk, and builds EmuHawk.exe (WinForms, net48; it is
                                                                 # compiled, never run here): proves that what the patches change in MainForm.cs compiles against the core
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import hashlib

ROOT = Path(__file__).resolve().parents[3]
MACHAWK = (ROOT / "../MacHawk").resolve()
PATCHES = sorted((ROOT / "integration/bizhawk/patches").glob("*.patch"))
CORE_SOURCES = [ROOT / "integration/bizhawk/core/Puae6", ROOT / "integration/bizhawk/core/EmuHawk"]


def tree_key() -> str:
    """The copy is named after what goes into it (the pinned commit, the patches, the PUAE6 sources and this recipe), so the same inputs find the
    tree they already built and different inputs never overwrite it; nothing is deleted."""
    digest = hashlib.sha256(json.loads((ROOT / "plan/machawk.reference.json").read_text())["commit"].encode())
    for path in [*PATCHES, *(f for d in CORE_SOURCES for f in sorted(d.glob("*.cs"))), Path(__file__)]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


TREE = ROOT / "build/bizhawk-tree" / tree_key()
TREE_EMUHAWK = TREE.with_name(TREE.name + "-emuhawk")
INSTALL_AT = "src/BizHawk.Emulation.Cores/Computers/Amiga/PUAE6"
# What the projects that are built (and the patches) need: the solution props, analyzers and prebuilt generators the build references, and five projects. The single files of
# Client.Common and EmuHawk are only there to be patched; EmuHawk itself (WinForms) is built by --emuhawk, which adds the projects it references.
EXPORT = [".editorconfig", ".global.editorconfig.ini", ".stylecop.json", "Common.props", "Directory.Packages.props", "global.json", "References",
          "ExternalProjects", "src/.gitignore", "src/BannedSymbols.BannedApiAnalyzers.txt", "src/MainSlnCommon.props", "src/Menees.Analyzers.Settings.xml",
          "src/BizHawk.BizInvoke", "src/BizHawk.Common", "src/BizHawk.Emulation.Common", "src/BizHawk.Emulation.Cores", "src/BizHawk.Emulation.DiscSystem",
          "src/BizHawk.Client.Common/config/Config.cs", "src/BizHawk.Client.EmuHawk/MainForm.cs"]
EXPORT_EMUHAWK = [path for path in EXPORT if not path.startswith(("src/BizHawk.Client.Common/", "src/BizHawk.Client.EmuHawk/"))] + [
    "src/MainSlnExecutable.props", "src/BizHawk.Bizware.Audio", "src/BizHawk.Bizware.Graphics", "src/BizHawk.Bizware.Graphics.Controls", "src/BizHawk.Bizware.Input",
    "src/BizHawk.Client.Common", "src/BizHawk.Client.EmuHawk", "src/BizHawk.WinForms.Controls"]


class RecipeError(RuntimeError):
    pass


def git(*args: str, cwd: Path = MACHAWK, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def pinned_commit() -> str:
    return json.loads((ROOT / "plan/machawk.reference.json").read_text())["commit"]


def check_source() -> str:
    if not (MACHAWK / ".git").exists():
        raise RecipeError(f"no hay checkout de MacHawk en {MACHAWK}")
    head = git("rev-parse", "HEAD").stdout.decode().strip()
    if head != pinned_commit():
        raise RecipeError(f"MacHawk esta en {head}, el plan fija {pinned_commit()}: no se usa otro commit")
    return head


def check_patches() -> None:
    """Every patch applies to the pinned commit: `git apply --check` reads the files and writes nothing."""
    check_source()
    for patch in PATCHES:
        result = git("apply", "--check", str(patch))
        if result.returncode:
            raise RecipeError(f"{patch.name} no se aplica al commit fijado de MacHawk:\n{result.stderr.decode()}")


def export_tree(tree: Path = TREE, paths: list[str] = EXPORT) -> bool:
    """Exports the pinned sources into the tree of these inputs; returns False when that tree already exists (it is then left as it is)."""
    if tree.exists():
        return False
    tree.mkdir(parents=True)
    archive = git("archive", "--format=tar", pinned_commit(), "--", *paths)
    if archive.returncode:
        raise RecipeError(archive.stderr.decode())
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
    return True


def apply_patches(tree: Path = TREE) -> None:
    """GNU patch, not `git apply`: inside a subdirectory of another repository (build/ is one) `git apply` silently skips every file outside the
    directory it runs in. Each patch must then be reversible, which proves that it is there."""
    for patch in PATCHES:
        for command, what in ((["patch", "-p1", "--no-backup-if-mismatch", "-i", str(patch)], "se aplica"),
                              (["patch", "-p1", "-R", "--dry-run", "-i", str(patch)], "queda aplicado")):
            result = subprocess.run(command, cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                raise RecipeError(f"{patch.name} no {what} a la copia: {(result.stdout + result.stderr).decode()}")


def install_sources(tree: Path = TREE) -> None:
    target = tree / INSTALL_AT
    target.mkdir(parents=True, exist_ok=True)
    for directory in CORE_SOURCES:
        for source in sorted(directory.glob("*.cs")):
            shutil.copy2(source, target / source.name)


def build(guest_sha256: str, tree: Path = TREE, project: str = "src/BizHawk.Emulation.Cores/BizHawk.Emulation.Cores.csproj",
          output: str = "src/BizHawk.Emulation.Cores/bin/Debug/BizHawk.Emulation.Cores.dll") -> Path:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise RecipeError("falta dotnet")
    env = {**os.environ, "DOTNET_CLI_HOME": str(ROOT / "build/waterbox/dotnet-home"), "NUGET_PACKAGES": str(ROOT / "build/bizhawk-core/nuget"),
           "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    log = tree / "build.log"
    with log.open("w") as out:
        result = subprocess.run([dotnet, "build", str(tree / project), "-c", "Debug", "--nologo", f"-p:Puae6GuestSha256={guest_sha256}",
                                 f"-p:RestoreSources={Path.home() / '.nuget/packages'}", "-p:InstallGitHooks=false"],
                                cwd=tree, env=env, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RecipeError("la compilacion fallo; ultimas lineas de " + str(log) + ":\n" + "\n".join(log.read_text().splitlines()[-30:]))
    assembly = tree / output
    if not assembly.is_file():
        raise RecipeError(f"no se encuentra {assembly}")
    return assembly


def build_emuhawk(guest_sha256: str) -> Path:
    """EmuHawk.exe (net48, WinForms) of the patched tree: it is compiled, which is what proves that the lines the patches change in MainForm.cs are valid against the core; it
    is not run (it is a Windows program)."""
    if export_tree(TREE_EMUHAWK, EXPORT_EMUHAWK):
        apply_patches(TREE_EMUHAWK)
        install_sources(TREE_EMUHAWK)
    return build(guest_sha256, TREE_EMUHAWK, "src/BizHawk.Client.EmuHawk/BizHawk.Client.EmuHawk.csproj", "output/EmuHawk.exe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--emuhawk", action="store_true", help="build EmuHawk.exe in a second tree instead of the core's assembly")
    options = parser.parse_args()
    check_patches()
    if options.check:
        print(f"OK: {len(PATCHES)} parche(s) se aplican a MacHawk {pinned_commit()}")
        return 0
    receipt = ROOT / "build/waterbox/puae6/puae6-receipt.json"
    if not receipt.is_file():
        raise RecipeError(f"falta {receipt}: ejecutar tools/build_waterbox.py --puae6")
    guest = json.loads(receipt.read_text())["guest"]["sha256"]
    if options.emuhawk:
        print(build_emuhawk(guest))
        return 0
    if export_tree():
        apply_patches()
        install_sources()
    print(build(guest))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RecipeError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
