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
import subprocess
import sys

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

This dependency is not linked to a product. The imported reader still requires
bounded ReadFn I/O and hardening before accepting arbitrary files. CD TOC,
identity, subchannels, provider restore and public loading are later gates.
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
            result["patches"] != [PATCH_META] or
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
            "repository": source["repository"], "patches": [PATCH_META],
            "files": sorted(files, key=lambda f: f["path"])}


def verify(destination=DEST):
    expected = selection()
    if json.loads((destination / "selection.json").read_text()) != expected:
        raise ValueError("CHD selection differs from pinned manifest")
    allowed = {f["path"] for f in expected["files"]} | {"selection.json", "NOTICE.md", PATCH}
    actual = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
    if actual != allowed or any(p.is_symlink() for p in destination.rglob("*")):
        raise ValueError("CHD selection contains missing/extra files or symlinks")
    for f in expected["files"] + expected["patches"]:
        if hashlib.sha256((destination / f["path"]).read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError("CHD hash differs: " + f["path"])
    if (destination / "NOTICE.md").read_text() != NOTICE:
        raise ValueError("CHD notice differs")
    return expected


def materialize(destination):
    expected = verify()
    outputs = {f["path"]: f["sha256"] for f in expected["files"]}
    outputs.update(PATCH_META["after"])
    destination = destination.resolve()
    destination.relative_to((ROOT / "build").resolve())
    if destination == (ROOT / "build").resolve():
        raise ValueError("CHD derived destination must be new and inside build/")
    if destination.exists():
        receipt = json.loads((destination / "receipt.json").read_text())
        hashes = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in destination.rglob("*") if p.is_file() and p != destination / "receipt.json"}
        if (receipt != {"selection": expected, "files": outputs} or hashes != outputs or
            any(p.is_symlink() for p in destination.rglob("*"))):
            raise ValueError("CHD derived receipt differs")
        return
    destination.mkdir(parents=True)
    for f in expected["files"]:
        path = destination / f["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEST / f["path"], path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(destination.parent)
    subprocess.run(["git", "apply", "--verbose", str(DEST / PATCH)], cwd=destination,
                   env=env, check=True, capture_output=True)
    hashes = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob("*")) if p.is_file()}
    if hashes != outputs:
        raise ValueError("CHD patch output differs from pinned after hashes")
    (destination / "receipt.json").write_text(json.dumps({"selection": expected, "files": hashes}, indent=2) + "\n")


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
