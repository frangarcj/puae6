"""T65a: three deterministic HDF roles; no AmigaOS files or live host mounts.

WHDLoad's own VFS reads Game.zip; SavePath redirects resload writes to WHDSaves.
The original P6Assign program binds S:/DEVS: to RASystem using AmigaDOS v36.
Library ABI: https://raw.githubusercontent.com/fpc/FPCSource/master/rtl/amiga/m68k/doslibf.inc
"""
import hashlib
import io
import os
from pathlib import Path
import struct
import zipfile

from whdload import ffs

KICKSTARTS = {
    "11f9e62cf299f72184835b7b2a70a16333fc0d88": "kick33180.A500",
    "891e9a547772fe0c6c19b610baf8bc4ea7fcb785": "kick34005.A500",
    "3b7f1493b27e212830f989f26ca76c02049f09ca": "kick40063.A600",
    "e21545723fe8374e91342617604f1b3d703094f1": "kick40068.A1200",
}
VFS_SHA256 = "4b9c51581c3b581ae9c72f01635c9f05a1e9ac744518a1915202a4a1f071ec96"


def assign_program():
    """One PC-relative HUNK_CODE, generated from original instructions below.

    Preserve D2/D3/A6; OpenLibrary dos v36, Lock each directory, AssignLock it,
    CloseLibrary and return 0, or RETURN_FAIL (20). Failed assigns release locks.
    No relocation, embedded OS code, host toolchain or wall clock is required.
    """
    code, labels, fixups = bytearray(), {}, []

    def words(*values):
        code.extend(struct.pack(">" + "H" * len(values), *(v & 0xffff for v in values)))

    def relative(opcode, label):
        words(opcode)
        fixups.append((len(code), label))
        words(0)

    words(0x48e7, 0x3002, 0x7614, 0x2c78, 4)   # movem d2-d3/a6,-(sp); moveq #20,d3; movea 4.w,a6
    relative(0x43fa, "dos")                     # lea dos(pc),a1
    words(0x7024, 0x4eae, -552, 0x4a80)         # OpenLibrary v36; tst.l d0
    relative(0x6700, "end")
    words(0x2c40)                               # movea.l d0,a6
    for alias in ("S", "DEVS"):
        relative(0x41fa, alias + "path")        # lea path(pc),a0
        words(0x2208, 0x74fe, 0x4eae, -84, 0x2400)  # Lock(path,ACCESS_READ); move.l d0,d2
        relative(0x6700, "close")
        relative(0x41fa, alias)
        words(0x2208, 0x4eae, -612, 0x4a80)     # AssignLock(alias,d2); tst.l d0
        relative(0x6600, alias + "ok")
        words(0x2202, 0x4eae, -90)              # failed: UnLock(d2)
        relative(0x6000, "close")
        labels[alias + "ok"] = len(code)
    words(0x7600)                               # moveq #0,d3
    labels["close"] = len(code)
    words(0x224e, 0x2c78, 4, 0x4eae, -414)      # movea a6,a1; execbase,a6; CloseLibrary
    labels["end"] = len(code)
    words(0x2003, 0x4cdf, 0x400c, 0x4e75)       # d3,d0; restore d2-d3/a6; rts
    for name, value in (("dos", "dos.library"), ("S", "S"), ("Spath", "RASystem:S"), ("DEVS", "DEVS"), ("DEVSpath", "RASystem:Devs")):
        labels[name] = len(code)
        code.extend(value.encode("ascii") + b"\0")
        code.extend(b"\0" * (len(code) % 2))
    for at, label in fixups:
        struct.pack_into(">h", code, at, labels[label] - at)
    code.extend(b"\0" * (-len(code) % 4))
    size = len(code) // 4
    return struct.pack(">8I", 1011, 0, 1, 0, 0, size, 1001, size) + code + struct.pack(">I", 1010)


def build(files, vfs, *, buttonwait=False, kickstarts=None):
    """The three images, given the verified T30 file tree and the user's VFS.

    Only one slave is selected; ambiguity fails instead of choosing by directory
    order. All configured game-data beside it becomes a stored ZIP at fixed time.
    """
    slaves = sorted(p for p in files if p.lower().endswith(".slave"))
    if len(slaves) != 1:
        raise ValueError("the bundle needs exactly one .slave, found %d" % len(slaves))
    slave = slaves[0]
    parent = slave.rpartition("/")[0]
    prefix = parent + "/" if parent else ""
    data = {p[len(prefix):]: b for p, b in files.items() if p.startswith(prefix) and p not in ("C/WHDLoad", "S/Startup-Sequence", "S/WHDLoad.prefs")}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        for path in sorted(data):
            entry = zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0))
            entry.create_system, entry.external_attr = 0, 0
            output.writestr(entry, data[path])
    prefs = b"SplashDelay=0\nConfigDelay=0\nNoWriteCache\nSavePath=WHDSaves:\n" + (b"ButtonWait\n" if buttonwait else b"")
    boot = {"C/WHDLoad": files["C/WHDLoad"], "C/WHDLoad.VFS": vfs, "C/P6Assign": assign_program(), "Game.slave": files[slave],
            "S/Startup-Sequence": b"C:P6Assign\nIF WARN\n  QUIT 20\nENDIF\nC:WHDLoad SLAVE=SYS:Game.slave DATA=RASystem:Game.zip\nEcho ended >WHDSaves:exit.log\n"}
    system = {"S/WHDLoad.prefs": prefs, "Devs/Kickstarts": None, "Game.zip": archive.getvalue()}
    system.update({p: b for p, b in files.items() if p.startswith("Devs/Kickstarts/")})
    for name, contents in sorted((kickstarts or {}).items()):
        if not name.startswith("kick") or "/" in name or ":" in name or len(name) > 30:
            raise ValueError("invalid Kickstart member name")
        system["Devs/Kickstarts/" + name] = contents

    def image(tree, label, free):
        root = ffs.build_tree(tree)
        return ffs.write_volume(root, label, blocks=ffs.volume_blocks(root, free_blocks=free))

    return {"WHDLoad.hdf": image(boot, "WHDLoad", 2048), "RASystem.hdf": image(system, "RASystem", 0),
            "WHDSaves.hdf": image({}, "WHDSaves", 8192)}


def prepare(config, inputs, assets, *, buttonwait=False, kickstarts=()):
    import prepare_whdload as prep
    image, _, _ = prep.prepare(config, inputs, assets)
    tool = next(entry for entry in config["files"] if entry["to"] == "C/WHDLoad")
    name, _, member = tool["from"].partition(":")
    spec = config["inputs"][name]
    contents, _ = prep.read_input(name, spec, inputs.get(name) or os.environ.get(spec.get("env", "")))
    vfs = contents.get(member.rpartition("/")[0] + "/WHDLoad.VFS")
    if vfs is None or hashlib.sha256(vfs).hexdigest() != VFS_SHA256:
        raise prep.PrepareError("WHDLoad.VFS is missing or is not the pinned WHDLoad 20.0 plugin")
    roms = {}
    for path in kickstarts:
        data = Path(path).read_bytes()
        name = KICKSTARTS.get(hashlib.sha1(data).hexdigest())
        if not name:
            raise prep.PrepareError("unrecognized Kickstart for RASystem; use an unencrypted supported image")
        roms[name] = data                       # the digest is only an in-memory name lookup
    return build(ffs.read_volume(image).files, vfs, buttonwait=buttonwait, kickstarts=roms)
