"""Synthetic hard files for the T29a tests (generated, never committed): one with a Rigid Disk Block and an empty FFS partition, and one plain partition.

Every block is built from the layouts of AmigaDOS alone (RDSK/PART blocks of devices/hardblocks.h, the FFS root and bitmap blocks of the filing system), with the
checksum rule "the longs of the block add up to zero". Block size is 512; sizes stay small (hundreds of KiB) so a test can compare a whole image.
"""
import struct

BLOCK = 512
DOS_FFS = 0x444F5301          # "DOS\1"
END = 0xFFFFFFFF


def _fix(longs, checksum_index):
    longs[checksum_index] = 0
    longs[checksum_index] = (-sum(longs)) & 0xFFFFFFFF
    return struct.pack(">%dL" % len(longs), *longs)


def _bstr(text, size):
    data = bytes([len(text)]) + text.encode("latin-1")
    return data.ljust(size, b"\0")


def ffs_partition(blocks, name="Empty"):
    """An empty FFS volume of `blocks` blocks (including the 2 of the boot block), as one bytes object."""
    reserved, root = 2, (blocks - 1 + 2) // 2
    bitmap = root + 1
    image = bytearray(blocks * BLOCK)
    image[0:4] = struct.pack(">L", DOS_FFS)
    longs = [0] * 128
    longs[0], longs[3] = 2, 72                      # T_HEADER, hash table size
    longs[128 - 50], longs[128 - 49] = END, bitmap  # bitmap valid, first bitmap page
    longs[128 - 20:128 - 12] = struct.unpack(">8L", _bstr(name, 32))
    longs[127] = 1                                  # ST_ROOT
    image[root * BLOCK:(root + 1) * BLOCK] = _fix(longs, 5)
    bits = [0] * 127 * 32
    for block in range(reserved, blocks):
        if block not in (root, bitmap):
            index = block - reserved
            if index < len(bits):
                bits[index] = 1
    words = [sum(bit << n for n, bit in enumerate(bits[w * 32:(w + 1) * 32])) for w in range(127)]
    image[bitmap * BLOCK:(bitmap + 1) * BLOCK] = _fix([0] + words, 0)
    return bytes(image)


def rdb_image(cylinders=16, heads=2, sectors=32, name="DH0", rdb_block_bytes=BLOCK, fshd_loop=False):
    """A disk with an RDB (block 0), one bootable partition `name` (block 1, cylinders 1 to the last) and an empty FFS volume in it.

    Hostile variants: rdb_block_bytes is the block size the RDB declares (a field the engine used to take as the size of a buffer), and fshd_loop makes the list of
    file system headers a single header whose successor is itself, with a DosType that is nobody's (the loop a RDB loader has to leave on its own)."""
    cylinder_blocks = heads * sectors
    total = cylinders * cylinder_blocks
    rdsk = [0] * 64
    rdsk[0:2], rdsk[3:6] = (0x5244534B, 64), (7, rdb_block_bytes, 0)
    rdsk[6:9] = (END, 1, 2 if fshd_loop else END)   # bad blocks, partitions at block 1, file system headers
    rdsk[9:16] = (END,) * 7                         # drive init and the reserved longs
    rdsk[16:20] = (cylinders, sectors, heads, 0)    # physical geometry
    rdsk[32:39] = (0, cylinder_blocks - 1, 1, cylinders - 1, cylinder_blocks, 0, 1)   # RDB blocks, cylinders, highest RDSK block
    rdsk[40:47] = struct.unpack(">7L", b"UAE     HardDrive       0.1 ")
    part = [0] * 64
    part[0:2], part[3:5] = (0x50415254, 64), (7, END)
    part[5] = 1                                     # PBFF_BOOTABLE
    part[9:17] = struct.unpack(">8L", _bstr(name, 32))
    env = [16, BLOCK // 4, 0, heads, 1, sectors, 2, 0, 0, 1, cylinders - 1, 30, 0, 0x7FFFFFFF, 0xFFFFFFFE, 0, DOS_FFS]
    part[32:32 + len(env)] = env
    image = bytearray(total * BLOCK)
    if fshd_loop:
        fshd = [0] * 64
        fshd[0:2], fshd[3:5] = (0x46534844, 64), (7, 2)       # "FSHD", next = itself
        fshd[8] = 0x58585858                                   # a DosType no partition has
        image[2 * BLOCK:3 * BLOCK] = _fix(fshd, 2).ljust(BLOCK, b"\0")
    image[0:BLOCK] = _fix(rdsk, 2).ljust(BLOCK, b"\0")
    image[BLOCK:2 * BLOCK] = _fix(part, 2).ljust(BLOCK, b"\0")
    start, blocks = cylinder_blocks, (cylinders - 1) * cylinder_blocks
    image[start * BLOCK:(start + blocks) * BLOCK] = ffs_partition(blocks, "Empty")
    return bytes(image)


def plain_partition(blocks=2048):
    """One partition without an RDB, as a `hardfile` is: it starts at the boot block of the file system."""
    return ffs_partition(blocks, "Plain")
