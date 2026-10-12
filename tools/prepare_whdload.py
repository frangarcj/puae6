#!/usr/bin/env python3
"""Offline, deterministic WHDLoad hard-file preparation (T30a).

    prepare_whdload.py prepare CONFIG --input NAME=PATH ... [--asset NAME=PATH ...] --out DIR [--allow-missing-assets]
    prepare_whdload.py identify IMAGE --sha256 HEX
    prepare_whdload.py inspect IMAGE

`prepare` builds ONE FFS volume (an HDF without an RDB, the shape T29a mounts as DH0) and its manifest from

  * a config (config/whdload/*.json): the inputs it needs, every file with the SHA-256 it must have, the boot script and WHDLoad.prefs as text, the volume's name, DosType, time and
    size, and the assets that only the user can supply;
  * the inputs, each a directory or an LHA archive on the host (the WHDLoad package, the game's install); they are read here and NEVER again: the image is the only thing the emulator
    is given, no host folder is mounted at replay;
  * the user's assets (a Kickstart image a slave asks for, a licensed data file), by `--asset` or the environment variable the config names.

Same config + same inputs = the same image and the same manifest, byte for byte: members sorted by name, fixed time stamps, no host path, clock, user or file order in anything that is
written. Nothing from an input is ever run (the LHA decoder is in tools/whdload/lha.py: bounded, pure Python), a file whose hash is not the pinned one stops the preparation, an input or an
asset that is missing is named (with where to get it) and the preparation fails, or with --allow-missing-assets writes the manifest with `"complete": false` and exits 3. The image of a
preparation that includes a user asset is for that user: the manifest says so (`"redistributable": false`) and the repository never holds one.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whdload import ffs, lha                 # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "manifests/licenses.json"
MAX_DIR_FILES, MAX_DIR_BYTES, MAX_CONFIG_BYTES = 4096, 128 * 1024 * 1024, 1024 * 1024
ROLES = ("tool", "game-data", "generated")
CONFIG_KEYS = {"schema_version", "id", "title", "volume", "inputs", "files", "user_assets", "run", "image_sha256"}
VOLUME_KEYS = {"name", "dostype", "min_kib", "free_kib", "timestamp"}
INPUT_KEYS = {"kind", "env", "license", "source_url", "description"}
FILE_KEYS = {"to", "from", "text", "sha256", "role", "license", "protection"}
ASSET_KEYS = {"to", "env", "size", "why", "license", "source_url"}
RUN_KEYS = {"machine", "kickstart", "units", "notes", "slow_kib"}


class PrepareError(Exception):
    """A legal-input problem the user can fix; the message says which one."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_licenses():
    catalog = json.loads(LICENSES.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in catalog.get("user_supplied_inputs", [])}


def _exact(mapping, allowed, where, required=()):
    if not isinstance(mapping, dict):
        raise PrepareError("%s must be an object" % where)
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise PrepareError("%s: unknown key %s" % (where, ", ".join(extra)))
    missing = [key for key in required if key not in mapping]
    if missing:
        raise PrepareError("%s: missing %s" % (where, ", ".join(missing)))


def load_config(path):
    raw = Path(path).read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise PrepareError("config too large")
    try:
        config = json.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise PrepareError("%s is not JSON: %s" % (path, error))
    _exact(config, CONFIG_KEYS, "config", ("schema_version", "id", "volume", "inputs", "files"))
    if config["schema_version"] != 1:
        raise PrepareError("config schema_version must be 1")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", config["id"]):
        raise PrepareError("config id must be lower-case letters, digits and '-'")
    _exact(config["volume"], VOLUME_KEYS, "volume", ("name", "dostype", "timestamp"))
    for name, spec in config["inputs"].items():
        _exact(spec, INPUT_KEYS, "input %s" % name, ("kind", "license"))
        if spec["kind"] not in ("lha", "dir"):
            raise PrepareError("input %s: kind must be lha or dir" % name)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise PrepareError("input name %r is not a plain word" % name)
    if not isinstance(config["files"], list) or not config["files"]:
        raise PrepareError("config needs a list of files")
    for entry in config["files"]:
        _exact(entry, FILE_KEYS, "file", ("to", "role", "license"))
        if entry["role"] not in ROLES:
            raise PrepareError("file %s: role must be one of %s" % (entry["to"], ", ".join(ROLES)))
        if entry["role"] == "generated":
            if "text" not in entry or "from" in entry:
                raise PrepareError("file %s: a generated file has `text` and no `from`" % entry["to"])
        else:
            if "from" not in entry or "sha256" not in entry or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise PrepareError("file %s: `from` and a 64-digit sha256 are required" % entry["to"])
            if entry["from"].split(":", 1)[0] not in config["inputs"]:
                raise PrepareError("file %s: its input is not declared" % entry["to"])
    for asset in config.get("user_assets", []):
        _exact(asset, ASSET_KEYS, "user asset", ("to", "env", "why", "license"))
    _exact(config.get("run", {}), RUN_KEYS, "run")
    return config


def read_directory(base):
    base = Path(base)
    if base.is_symlink() or not base.is_dir():
        raise PrepareError("%s is not a directory" % base)
    members, total = {}, 0
    for current, dirs, names in os.walk(base, followlinks=False):
        dirs.sort()
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise PrepareError("%s is a link or not a regular file: it is not read" % path)
            data = path.read_bytes()
            total += len(data)
            if len(members) >= MAX_DIR_FILES or total > MAX_DIR_BYTES:
                raise PrepareError("%s is larger than the limits (%d files, %d bytes)" % (base, MAX_DIR_FILES, MAX_DIR_BYTES))
            members[path.relative_to(base).as_posix()] = data
    return members


def read_input(name, spec, path):
    """{member path: bytes} of an input and, for an archive, the SHA-256 of the file that was given."""
    path = Path(path)
    if spec["kind"] == "dir":
        return read_directory(path), None
    if path.is_symlink() or not path.is_file():
        raise PrepareError("input %s: %s is not a file" % (name, path))
    blob = path.read_bytes()
    try:
        entries = lha.read_archive(blob)
    except lha.LhaError as error:
        raise PrepareError("input %s: %s is not a usable LHA archive: %s" % (name, path.name, error))
    members = {}
    for entry in entries:
        if not entry.is_dir:
            if entry.name in members:
                raise PrepareError("input %s: %s appears twice in the archive" % (name, entry.name))
            members[entry.name] = entry.data
    return members, sha256(blob)


def _script(lines):
    text = "\n".join(lines) if isinstance(lines, list) else lines
    data = text.encode("latin-1") + b"\n"
    if b"\r" in data or b"\0" in data:
        raise PrepareError("a generated script holds a CR or a NUL")
    return data


def prepare(config, inputs, assets, allow_missing=False, licenses=None):
    """(image bytes, manifest dict, missing list). `inputs` maps a name to a path, `assets` maps an asset name (the id of its file) to a path."""
    licenses = load_licenses() if licenses is None else licenses
    problems, missing = [], []
    wanted = {config["inputs"][n]["license"] for n in config["inputs"]} | {f["license"] for f in config["files"]}
    wanted |= {a["license"] for a in config.get("user_assets", [])}
    for license_id in sorted(wanted):
        if license_id not in licenses:
            problems.append("license %r has no entry in manifests/licenses.json (user_supplied_inputs): add its evidence before preparing" % license_id)
    members, input_records = {}, []
    for name in sorted(config["inputs"]):
        spec = config["inputs"][name]
        path = inputs.get(name) or (os.environ.get(spec["env"]) if spec.get("env") else None)
        if not path:
            how = ("set %s or " % spec["env"] if spec.get("env") else "") + "pass --input %s=PATH" % name
            missing.append("input %s (%s; %s)%s" % (name, spec.get("description", spec["kind"]), how, "; get it from " + spec["source_url"] if spec.get("source_url") else ""))
            continue
        try:
            members[name], digest = read_input(name, spec, path)
        except PrepareError as error:
            problems.append(str(error))
            continue
        input_records.append({"name": name, "kind": spec["kind"], "license": spec["license"], "sha256": digest})
    files, records = {}, []

    def place(to, data, record):
        folded = to.lower()
        if folded in {k.lower() for k in files}:
            problems.append("%s is placed twice" % to)
            return
        files[to] = data
        records.append(dict(record, path=to, size=len(data), sha256=sha256(data)))

    for entry in config["files"]:
        record = {"role": entry["role"], "license": entry["license"]}
        if entry["role"] == "generated":
            try:
                place(entry["to"], _script(entry["text"]), record)
            except (PrepareError, UnicodeEncodeError) as error:
                problems.append("%s: %s" % (entry["to"], error))
            continue
        name, _, member = entry["from"].partition(":")
        if name not in members:
            continue                                   # the input is missing: already reported
        data = members[name].get(member)
        if data is None:
            problems.append("%s: %s is not in input %s" % (entry["to"], member, name))
        elif sha256(data) != entry["sha256"]:
            problems.append("%s: %s of input %s has sha256 %s, the config pins %s (another version of the install than the one the config was written for)"
                            % (entry["to"], member, name, sha256(data), entry["sha256"]))
        else:
            version = re.search(rb"\$VER: ([ -~]{1,80})", data) if entry["role"] == "tool" else None
            place(entry["to"], data, dict(record, **({"version": version.group(1).decode("ascii").strip()} if version else {})))
    asset_records = []
    for asset in config.get("user_assets", []):
        path = assets.get(asset["to"]) or os.environ.get(asset["env"])
        if not path:
            missing.append("asset %s (%s): set %s or pass --asset %s=PATH" % (asset["to"], asset["why"], asset["env"], asset["to"]))
            continue
        try:
            data = Path(path).read_bytes()
        except OSError as error:
            problems.append("asset %s: %s" % (asset["to"], error))
            continue
        if "size" in asset and len(data) != asset["size"]:
            problems.append("asset %s: %d bytes, expected %d (the file is not the one the install asks for)" % (asset["to"], len(data), asset["size"]))
            continue
        if asset["to"].lower() in {k.lower() for k in files}:
            problems.append("%s is placed twice" % asset["to"])
            continue
        # The user's file goes into the image; its hash is deliberately not recorded anywhere (a Kickstart is never hashed or copied by this project).
        files[asset["to"]] = data
        asset_records.append({"path": asset["to"], "size": len(data), "license": asset["license"]})
    if problems:
        raise PrepareError("\n".join(problems))
    if missing and not allow_missing:
        raise PrepareError("missing, nothing written:\n  " + "\n  ".join(missing))
    volume = config["volume"]
    try:
        stamp = datetime.datetime.fromisoformat(volume["timestamp"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("the time has no time zone")
        if not re.fullmatch(r"DOS[135]", volume["dostype"]):
            raise ValueError("dostype is DOS1, DOS3 or DOS5 (FFS, international, directory cache)")
        tree = ffs.build_tree(files)
        for entry in config["files"]:
            if "protection" in entry:
                node = tree
                for part in entry["to"].split("/"):
                    node = node.children[part]
                node.protection = int(entry["protection"])
        dostype = b"DOS" + bytes([int(volume["dostype"][3])])
        blocks = ffs.volume_blocks(tree, volume.get("min_kib", 0) * 2, volume.get("free_kib", 0) * 2)
        image = ffs.write_volume(tree, volume["name"], dostype, blocks, stamp)
    except (ffs.FfsError, ValueError) as error:
        raise PrepareError("image: %s" % error)
    if config.get("image_sha256") and not missing and not asset_records and sha256(image) != config["image_sha256"]:
        raise PrepareError("the image has sha256 %s but the config identifies %s: an input differs from the one the identification was made with" % (sha256(image), config["image_sha256"]))
    redistributable = not asset_records and all(licenses[r["license"]].get("redistribution") == "yes" for r in records)
    manifest = {
        "schema_version": 1,
        "id": config["id"],
        "config_sha256": sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        "complete": not missing,
        "missing": sorted(missing),
        "image": {"file": config["id"] + ".hdf", "size": len(image), "sha256": sha256(image), "blocks": blocks, "dostype": volume["dostype"], "volume": volume["name"],
                  "timestamp": volume["timestamp"]},
        "inputs": sorted(input_records, key=lambda r: r["name"]),
        "files": sorted(records, key=lambda r: r["path"]),
        "user_assets": sorted(asset_records, key=lambda r: r["path"]),
        "licenses": sorted({r["license"] for r in records} | {r["license"] for r in asset_records}),
        "redistributable": redistributable,
    }
    return image, manifest, sorted(missing)


def encode_manifest(manifest):
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_atomic(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(data)
    os.replace(temp, path)


def parse_pairs(values, what):
    pairs = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise PrepareError("%s expects NAME=PATH, got %r" % (what, value))
        pairs[name] = path
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("config")
    prep.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    prep.add_argument("--asset", action="append", default=[], metavar="PATH_IN_VOLUME=PATH")
    prep.add_argument("--out", required=True)
    prep.add_argument("--allow-missing-assets", action="store_true")
    ident = sub.add_parser("identify")
    ident.add_argument("image")
    ident.add_argument("--sha256", required=True)
    insp = sub.add_parser("inspect")
    insp.add_argument("image")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            config = load_config(args.config)
            image, manifest, missing = prepare(config, parse_pairs(args.input, "--input"), parse_pairs(args.asset, "--asset"), args.allow_missing_assets)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            write_atomic(out / manifest["image"]["file"], image)
            write_atomic(out / (config["id"] + ".manifest.json"), encode_manifest(manifest).encode("utf-8"))
            print("%s: %d bytes, sha256 %s%s" % (manifest["image"]["file"], len(image), manifest["image"]["sha256"], "" if manifest["redistributable"] else " (NOT redistributable: for the user who supplied the inputs)"))
            if missing:
                print("INCOMPLETE image, missing:\n  " + "\n  ".join(missing), file=sys.stderr)
                return 3
            return 0
        image = Path(args.image).read_bytes()
        if args.command == "identify":
            if sha256(image) != args.sha256:
                raise PrepareError("the image has sha256 %s, not the identified %s" % (sha256(image), args.sha256))
            volume = ffs.read_volume(image)
            print("identified: %s, %d files, %d directories, volume %s" % (args.sha256, len(volume.files), len(volume.dirs), volume.label))
            return 0
        volume = ffs.read_volume(image)
        print("volume %s, %s, %d blocks" % (volume.label, volume.dostype.decode("latin-1").replace("\x01", "\\1").replace("\x03", "\\3"), volume.blocks))
        for path in sorted(volume.files):
            print("%9d %s" % (len(volume.files[path]), path))
        return 0
    except (PrepareError, ffs.FfsError, OSError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
