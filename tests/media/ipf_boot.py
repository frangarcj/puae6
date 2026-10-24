"""Original MIT boot disk and opt-in owner IPF for T58's frontend acceptance.

The boot block needs no DOS files: Kickstart reads/checks its first two sectors,
then executes our code. It marks Chip RAM and draws raster colours forever.
No commercial bytes, checksums or pictures are fixtures in the repository.
"""
import os
from pathlib import Path
import struct
import zipfile

from tests.media import ipf
from tests.scenarios.disk import disk

MARK = 0x6000
MAGIC = bytes.fromhex("50364950")  # P6IP, written only by the boot block
ROM = os.environ.get("P6_KICKSTART_ROM")
OWNER = os.environ.get("P6_IPF_FILE")


def boot_adf():
    block = bytearray(1024)
    block[:4] = b"DOS\0"
    struct.pack_into(">I", block, 8, 880)
    # Disable interrupts/DMA; MOVE.L #MAGIC,MARK;
    # loop: MOVE.W VHPOSR,D0; MOVE.W D0,COLOR00; BRA.S loop.
    # Kickstart calls boot code in user mode: never write SR here.
    code = bytes.fromhex("33fc7fff00dff09a 33fc7fff00dff096 23fc")
    code += MAGIC + struct.pack(">I", MARK)
    code += bytes.fromhex("303900dff006 33c000dff180 60f2")
    block[12:12 + len(code)] = code
    checksum = 0
    for word in struct.unpack(">256I", block):
        checksum += word
        checksum = (checksum & 0xffffffff) + (checksum >> 32)
    struct.pack_into(">I", block, 4, checksum ^ 0xffffffff)
    return bytes(block) + bytes(901120 - len(block))


def boot_image():
    adf = boot_adf()
    # Standard trackdisk boot layout: consecutive word-aligned sectors, one gap
    # at the end. dos_blocks deliberately scatters non-word-aligned gaps to
    # stress raw DMA; Kickstart's track decoder expects the standard layout.
    raw = b"".join(struct.pack(">H", w) for sector in disk.encode_track(adf, 0) for w in sector)
    tracks = [ipf.Track(0, 0, [ipf.Block([("sync", raw)], gap_cells=ipf.PAL_CELLS - len(raw) * 8)])]
    tracks += [ipf.Track(t // 2, t % 2, [], type=1) for t in range(1, 168)]
    return ipf.build(tracks, key_base=1)


def owner_image(directory):
    """Accept one raw IPF or a ZIP with exactly one IPF; materialize only in a temporary test directory."""
    source = Path(OWNER).resolve(strict=True)
    target = Path(directory) / "owner.ipf"
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            members = [m for m in archive.infolist() if not m.is_dir() and m.filename.lower().endswith(".ipf")]
            if len(members) != 1 or not 12 <= members[0].file_size <= 3 << 20:
                raise ValueError("P6_IPF_FILE must contain exactly one bounded IPF")
            target.write_bytes(archive.read(members[0]))
    else:
        target.symlink_to(source)
    return target
