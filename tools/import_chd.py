#!/usr/bin/env python3
"""Import/verify the standalone CHD dependency offline; never import a PUAE engine.

Initial import: --reference-root <PUAE6 checkout with the verified snapshot>.
Subsequent verification needs only the vendored selection manifest and files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "bizhawk_uae_reference"
PIN = "ccecb1ead642c1bbe391308b88a7ffa9478b918d"
DEST = ROOT / "third_party/libchdr"
PATCH = "patches/0001-zlib-standalone.patch"
PATCH_SHA = "858343d173089b336b282537579082eb560dd30dabc4032dfd94ce67701ac161"
PATCH_META = {"path": PATCH, "sha256": PATCH_SHA, "after": {
    "deps/zlib/adler32.c": "cc4cdf1611c5bb7c4d93f94113e49a49948c09c3b1c879588e026838c683e0e0",
    "deps/zlib/zutil.h": "8efb77720b79c73845015dd9a710b42922cf47e924b24911bbbef8bded9e1459",
    "deps/zlib/zutil.c": "b854a4b5fc643af109ed62dd14c597f8fd22563f5635ea806571070477676bee"}}
PATCHES = [PATCH_META, {"path": "patches/0002-readfn-header.patch",
    "sha256": "b98f643a3b87b356cc95ae7df2c48919302ab21d5d16de6057ee42e2f3cab0fd", "after": {
    "include/libchdr/coretypes.h": "ce189f4057f4e1fdaef674887624a8b977952cd496df14b207fa22e2f7ab21e4",
    "src/libchdr_chd.c": "ff7a30bedbe97c9e7c9068a42ea378111e6ff2206c40e26306d91d462cb10d9e",
    "src/libchdr_bitstream.c": "3610c14a487a103ea61b45630e08953e93d3ef4df8a8c5efdd7717fb201c8eef",
    "src/libchdr_huffman.c": "5e75d3527ef8fcb8a8cf1d8f9d2bf8347607f5759c88b6c57ec2548703d56783"}},
    {"path": "patches/0003-v5-map-metadata.patch",
    "sha256": "b6307ec26286d902e590f583e0602390455df4c89e3f9247eae9b57a1b4d97bf", "after": {
    "src/libchdr_bitstream.c": "cc58fe03235a8948e14cc94ba8209f28407fc732bc905beed33f82c6aecbd3b3",
    "include/libchdr/bitstream.h": "36382f8797459cc696424c09f285de56b414e7aeacb9fcb064e6869f3aa71927",
    "src/libchdr_huffman.c": "bd42f4334a9258879507b9623b338dbc522f981400252e4666152374cc93c432",
    "src/libchdr_chd.c": "920720563f0689bc348543aed88ce45f96e0f39f5edf724cfab37f8706d97c42"}},
    {"path": "patches/0004-cd-codec-bounds.patch",
    "sha256": "e0cb0e29d234a268b809d6f45add0ac1c8f4805804a7cb341107a471fb3810bf", "after": {
    "src/libchdr_chd.c": "f5b638d0564f585b6c91aa132609b5afe4c3a2f8d38d81495c6d68add1d54407",
    "src/libchdr_flac.c": "c054ba04984969467d7f889a1d0deac6d39599ef5c0cb8dfd86910f81c9fabc5",
    "include/libchdr/flac.h": "b9383a25672818652a82b119b1859df6ca5db447c27ff7050cd5ab3bcf88b0b1"}}]
CHDR = ["src/libchdr_" + n + ".c" for n in ("bitstream", "cdrom", "chd", "flac", "huffman")]
CHDR += ["include/libchdr/" + n + ".h" for n in ("bitstream", "cdrom", "chd", "chdconfig", "coretypes", "flac", "huffman")]
CHDR += ["include/dr_libs/dr_flac.h", "LICENSE.txt"]
LZMA = [n + ".c" for n in ("LzmaDec", "LzmaEnc", "LzFind")]
LZMA += [n + ".h" for n in ("7zTypes", "7zVersion", "Compiler", "Precomp", "LzFind", "LzHash", "LzmaDec", "LzmaEnc")]
ZLIB = [n + ".c" for n in ("adler32", "crc32", "inflate", "inffast", "inftrees", "zutil")]
ZLIB += [n + ".h" for n in ("zlib", "zconf", "zutil", "inflate", "inffast", "inftrees", "inffixed")]
NOTICE = """# Standalone CHD dependency (T66t1)

Imported byte for byte from TASEmulators/libretro-uae commit
ccecb1ead642c1bbe391308b88a7ffa9478b918d, verified against
references/machawk-uae.manifest.json. selection.json records every source/hash.
No PUAE engine or libretro-common source is included.

* libchdr: BSD-3-Clause; Romain Tisserand and Aaron Giles. Preserve LICENSE.txt
  and the notices in the selected sources/headers in source distributions;
  reproduce BSD notices in binary distribution materials.
* LZMA SDK 19.00: Igor Pavlov, public domain; notices in deps/lzma sources and
  headers, including 7zVersion.h. _7ZIP_ST excludes threads.
* zlib snapshot, API header 1.2.7 (some implementation notices 1.2.8):
  Jean-loup Gailly and Mark Adler; license in deps/zlib/zlib.h.
  Preserve that notice and identify altered sources if modifications are made.
* dr_flac 0.12.28: David Reid; public domain or MIT-0, chosen MIT-0 here.
  Both license texts are preserved at the end of include/dr_libs/dr_flac.h.

patches/0001 corrects Adler-32 ABI and standalone includes, without
changing the 32-bit calculation. Applied only to a derived build copy.
patches/0002 replaces host FILE/cursor with borrowed ReadFn offsets, validates
CDv5 geometry/resources, and makes construction/cleanup transactional.
patches/0003 validates bounded maps/metadata, resolves SELF iteratively and
checks bitstream/Huffman input. patches/0004 validates CD codec split lengths,
exact decoder completion, FLAC format/positions and failed codec cleanup.

The CDv5 reader uses bounded ReadFn offsets, validated maps/metadata and checked
codec payloads. Native libretro links this dependency statically through CdImage;
CD TOC, identity and normalized sector views belong to its CdChd owner. The C#
host backend and public typed Waterbox connection remain separate gates.
"""


def selection(reference=None):
    paths = {}
    for origin, dest, names in (("deps/libchdr/", "", CHDR),
                                ("deps/7zip/", "deps/lzma/", LZMA),
                                ("deps/libz/", "deps/zlib/", ZLIB)):
        paths.update({dest + n: origin + n for n in names})
    # Once imported, selection.json is the Git-owned authority; no references/
    # checkout or source manifest is required by consumers of source packages.
    if reference is None:
        result = json.loads((DEST / "selection.json").read_text())
        if (result["commit"] != PIN or result["source"] != SOURCE or
            result["patches"] != PATCHES or
            {f["path"]: f["source"] for f in result["files"]} != paths):
            raise ValueError("CHD selected provenance differs")
        return result
    manifest = json.loads((reference / "references/machawk-uae.manifest.json").read_text())
    source = manifest["source"]
    if source["commit"] != PIN:
        raise ValueError("CHD source pin differs")
    hashes = {f["path"]: f["sha256"] for f in manifest["files"]}
    files = [{"path": path, "source": origin, "sha256": hashes[origin]} for path, origin in paths.items()]
    return {"schema_version": 1, "source": SOURCE, "commit": PIN,
            "repository": source["repository"], "patches": PATCHES,
            "files": sorted(files, key=lambda f: f["path"])}


def verify(destination=DEST):
    expected = selection()
    if json.loads((destination / "selection.json").read_text()) != expected:
        raise ValueError("CHD selection differs from pinned manifest")
    allowed = {f["path"] for f in expected["files"] + PATCHES} | {"selection.json", "NOTICE.md"}
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if actual != allowed or any(p.is_symlink() for p in destination.rglob("*")):
        raise ValueError("CHD selection contains missing/extra files or symlinks")
    for f in expected["files"] + expected["patches"]:
        if hashlib.sha256((destination / f["path"]).read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError("CHD hash differs: " + f["path"])
    if (destination / "NOTICE.md").read_text() != NOTICE:
        raise ValueError("CHD notice differs")
    return expected


def derived_destination(destination):
    """Check nominal paths before following any existing build-tree links."""
    destination = Path(destination).absolute()
    build = ROOT / "build"
    relative = destination.relative_to(build)
    if not relative.parts or ".." in relative.parts:
        raise ValueError("CHD derived destination must be new and inside build/")
    for path in (build, *(build.joinpath(*relative.parts[:n]) for n in range(1, len(relative.parts) + 1))):
        if path.is_symlink():
            raise ValueError("CHD derived destination contains symlinks")
        if path.exists() and not path.is_dir():
            raise ValueError("CHD derived destination must be a directory")
    return destination


def verify_derived(destination, expected, outputs):
    paths = list(destination.rglob("*"))
    if any(p.is_symlink() or not (p.is_dir() or p.is_file()) for p in paths):
        raise ValueError("CHD derived receipt differs: linked/nonregular file")
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file() and p != destination / "receipt.json"}
    receipt = json.loads((destination / "receipt.json").read_text())
    if receipt != {"selection": expected, "files": outputs} or hashes != outputs:
        raise ValueError("CHD derived receipt differs")


def stage_derived(destination, expected, outputs):
    destination.mkdir()
    for f in expected["files"]:
        path = destination / f["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEST / f["path"], path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(destination.parent)
    for patch in PATCHES:
        subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply", "--verbose", str(DEST / patch["path"])], cwd=destination,
                       env=env, check=True, capture_output=True)
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob("*")) if p.is_file()}
    if hashes != outputs:
        raise ValueError("CHD patch output differs from pinned after hashes")
    (destination / "receipt.json").write_text(json.dumps({"selection": expected, "files": hashes}, indent=2) + "\n")


def publish_windows(destination, expected, outputs):
    """Publish a complete exclusive staging tree; a concurrent winner must verify.

    Windows rename never replaces an existing destination. No process can observe
    our tree there until every patch and receipt check succeeded.
    """
    destination = derived_destination(destination)
    if destination.exists():
        verify_derived(destination, expected, outputs)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="." + destination.name + "-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "tree"
        stage_derived(staged, expected, outputs)
        verify_derived(staged, expected, outputs)
        derived_destination(destination)
        try:
            staged.rename(destination)
        except OSError:
            derived_destination(destination)
            if not destination.exists():
                raise
            verify_derived(destination, expected, outputs)


def materialize(destination):
    expected = verify()
    outputs = {f["path"]: f["sha256"] for f in expected["files"]}
    for patch in PATCHES:
        outputs.update(patch["after"])
    destination = derived_destination(destination)
    if destination.exists():
        verify_derived(destination, expected, outputs)
        return  # An immutable cache hit needs neither a lock nor any writes.
    if os.name == "nt":
        return publish_windows(destination, expected, outputs)
    try:
        import fcntl
        if not hasattr(fcntl, "flock") or not hasattr(os, "O_NOFOLLOW"):
            raise ImportError("POSIX locking/NOFOLLOW unavailable")
    except ImportError as error:
        raise ValueError("CHD materialization requires POSIX file locking (Darwin/Linux)") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_name("." + destination.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "rb") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("CHD derivation lock must be a regular file with one link")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        derived_destination(destination)
        if destination.exists():
            verify_derived(destination, expected, outputs)
            return
        with tempfile.TemporaryDirectory(prefix="." + destination.name + "-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "tree"
            stage_derived(staged, expected, outputs)
            verify_derived(staged, expected, outputs)
            staged.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--materialize", type=Path)
    args = parser.parse_args()
    if args.reference_root:
        import import_sources
        import_sources.verify_source(args.reference_root.resolve(), SOURCE)
        expected = selection(args.reference_root)
        snapshot = args.reference_root / "references/machawk-uae"
        contents = [(f, (snapshot / f["source"]).read_bytes()) for f in expected["files"]]
        if any(hashlib.sha256(data).hexdigest() != f["sha256"] for f, data in contents):
            raise ValueError("Reference selection hash differs")
        for f, data in contents:
            path = DEST / f["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != data:
                raise ValueError("Refusing to overwrite altered source: " + f["path"])
            if not path.exists():
                path.write_bytes(data)
        (DEST / "selection.json").write_text(json.dumps(expected, indent=2) + "\n")
        (DEST / "NOTICE.md").write_text(NOTICE)
    print("CHD selection verified:", len(verify()["files"]), "files;", PIN)
    if args.materialize:
        materialize(args.materialize)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(error.stderr.decode() if isinstance(error, subprocess.CalledProcessError) else str(error))
