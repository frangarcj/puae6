"""What the tests of the volumes (T64) share: the oracle image of a tree (tools/whdload/ffs.py with the options of the adapter), trees, and the three ways to hand the same content to a
core: a directory, an LHA and a ZIP.

The trees are dicts {path: bytes | None} (None is a directory), the paths are str whose characters are the ISO 8859-1 bytes of the AmigaDOS names, as ffs.py takes them.
"""
from pathlib import Path
import os
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from whdload import ffs, lha   # noqa: E402,F401
from tests.whdload import lha_fixture as fx   # noqa: E402

FREE_BLOCKS = 2048          # kVolumeFreeBlocks of adapters/libretro/volume.h
WHDLOAD, GAME = os.environ.get("P6_WHDLOAD_LHA"), os.environ.get("P6_FATALMISSION_LHA")      # the free packages of config/whdload/fatal-mission.json (opt-in)

# Content with the shapes the volume has to carry: a boot script, nested directories, an empty file, an empty directory and a file that is several blocks long.
CONTENT = {"S/Startup-Sequence": b"echo \"hello\"\n" * 5, "Game/game.slave": bytes(range(256)) * 9, "Game/data/level1.dat": b"A" * 5000 + bytes(range(200)),
           "Game/data/empty": b"", "Game/readme": b"read me\n", "Devs": None}


def oracle(files):
    """The image tools/whdload/ffs.py gives for {path (str of latin-1): bytes | None}, with the options of the adapter."""
    tree = ffs.build_tree(files)
    high = any(ord(ch) > 127 for node in ffs._walk(tree) for ch in node.name)
    blocks = ffs.volume_blocks(tree, 0, FREE_BLOCKS)
    return ffs.write_volume(tree, "PUAE6", b"DOS\x03" if high else b"DOS\x01", blocks, ffs.DEFAULT_TIME)


def write_directory(base, files, order=None, times=None):
    """The tree on disk under `base` (created), in the given order; `times` sets every modification time."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    for path in order or list(files):
        full = base / path
        if files[path] is None:
            full.mkdir(parents=True, exist_ok=True)
        else:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(files[path])
    if times is not None:
        for path in base.rglob("*"):
            os.utime(path, (times, times))
    return base


def lha_archive(content, level=1, method=b"-lh5-", bits=13):
    """The LHA of a tree. Header level 0 has no directory entries (this writer's limit), so the directories of a tree are only written for levels 1 and 2."""
    members = []
    for path, data in content.items():
        if data is None:
            members.append(fx.member(b"", method=b"-lhd-", level=max(level, 1), directory=path.encode("latin-1").replace(b"/", b"\xff") + b"\xff"))
            continue
        head, _, tail = path.rpartition("/")
        if level == 0:
            members.append(fx.member(path.encode("latin-1"), data, method, 0, method_bits=bits))
        else:
            members.append(fx.member(tail.encode("latin-1"), data, method, level, head.encode("latin-1").replace(b"/", b"\xff") + (b"\xff" if head else b""), bits))
    return fx.archive(*members)


def zip_file(path, content, compression=zipfile.ZIP_DEFLATED):
    """A ZIP of a tree (a directory entry ends with '/'); the stamp of every member is fixed so that only the content could matter."""
    with zipfile.ZipFile(path, "w", compression) as handle:
        for name, data in content.items():
            info = zipfile.ZipInfo(name + "/" if data is None else name, (2001, 2, 3, 4, 5, 6))
            handle.writestr(info, b"" if data is None else data)
    return Path(path)


def whdload_tree():
    """The files of config/whdload/fatal-mission.json as a tree: the WHDLoad tool and the game from the free packages, and the boot script (which writes boot.log and exit.log)."""
    import json
    config = json.loads((ROOT / "config/whdload/fatal-mission.json").read_text())
    members = {}
    for name, path in (("whdload", WHDLOAD), ("game", GAME)):
        members[name] = {e.name: e.data for e in lha.read_archive(Path(path).read_bytes()) if not e.is_dir}
    tree = {}
    for entry in config["files"]:
        if entry["role"] == "generated":
            tree[entry["to"]] = ("\n".join(entry["text"]) + "\n").encode("latin-1")
        else:
            name, _, member = entry["from"].partition(":")
            tree[entry["to"]] = members[name][member]
    return tree
