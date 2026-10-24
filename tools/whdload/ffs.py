"""Deterministic FFS volume writer for the WHDLoad preparation (T30a).

It writes ONE plain partition, the shape of a hard file without an RDB (T29a: 32 sectors, 1 head, 2 reserved blocks, the DosType is the first long of block 0): block 0-1 the boot
block, a root block in the middle of the volume with the bitmap blocks after it, and every other block handed out in order, directories and files sorted by name as bytes. Nothing
depends on the host: no clock, no file system order, no uid. The same tree, label, DosType, time and size give the same bytes.

Layout of the blocks (longs of 512 bytes, AmigaDOS "filing system" documentation):
  root       T_HEADER(2), ht_size 72 at long 3, checksum at 5, hash table 6..77, bitmap valid flag 78, bitmap pages 79..103, bitmap extension 104, date 105..107, name 108..115,
             last disk change 118..120, creation 121..123, secondary type ST_ROOT(1) at 127
  directory  T_HEADER, own key, hash table, protection 80, comment 82..101, date 105..107, name 108..115, hash chain 124, parent 125, ST_USERDIR(2)
  file       T_HEADER, own key, high_seq (data blocks listed here), first data block, checksum, data block table 77 down to 6, protection 80, size 81, comment, date, name, hash chain,
             parent, extension block 126, ST_FILE(-3)
  extension  T_LIST(16), own key, high_seq, checksum, the next 72 data blocks, parent 125 (the file header), next extension 126, ST_FILE
  data       FFS: the 512 bytes of the file, no header
"""
from dataclasses import dataclass, field
import datetime
import struct

BLOCK = 512
RESERVED = 2
HASH_SIZE = 72
TABLE = 72                      # data block pointers in a file header or an extension block
BITMAP_LONGS = 127              # a bitmap page maps 127 * 32 blocks
BITMAP_PAGES = 25               # in the root block; the extension chain is not written
MAX_NAME = 30
EPOCH = datetime.datetime(1978, 1, 1, tzinfo=datetime.timezone.utc)
T_HEADER, T_LIST, ST_ROOT, ST_USERDIR, ST_FILE = 2, 16, 1, 2, 0xFFFFFFFD
DEFAULT_TIME = datetime.datetime(1990, 1, 1, tzinfo=datetime.timezone.utc)


class FfsError(ValueError):
    """The tree does not fit the volume, or a name cannot be written."""


@dataclass
class Node:
    name: str
    data: bytes = b""
    is_dir: bool = False
    protection: int = 0                       # AmigaDOS bits: a set bit in the low nibble DENIES (r, w, e, d); 0x40 = script
    children: dict = field(default_factory=dict)
    key: int = 0
    chain: int = 0
    data_blocks: list = field(default_factory=list)
    ext_blocks: list = field(default_factory=list)


def amiga_date(moment):
    delta = moment - EPOCH
    if delta.total_seconds() < 0:
        raise FfsError("time before 1978-01-01")
    seconds = delta.days * 86400 + delta.seconds
    return delta.days, (seconds % 86400) // 60, (seconds % 60) * 50


def upper(value, intl):
    if 0x61 <= value <= 0x7A or (intl and 0xE0 <= value <= 0xFE and value != 0xF7):
        return value - 0x20
    return value


def name_hash(raw, intl):
    h = len(raw)
    for byte in raw:
        h = (h * 13 + upper(byte, intl)) & 0x7FF
    return h % HASH_SIZE


def checksum_block(longs, index):
    longs[index] = 0
    longs[index] = (-sum(longs)) & 0xFFFFFFFF
    return struct.pack(">128L", *longs)


def _bstr(raw, size):
    return (bytes([len(raw)]) + raw).ljust(size, b"\0")


def build_tree(files):
    """Node tree from {path: bytes | None} (None = an empty directory); parents are created, a path may not be both. Children are kept sorted by the name's bytes."""
    root = Node("", is_dir=True)
    for path in sorted(files, key=lambda p: [part.encode("latin-1") for part in p.split("/")]):
        parts = path.split("/")
        node = root
        for depth, part in enumerate(parts):
            raw = part.encode("latin-1")
            if not raw or len(raw) > MAX_NAME or b":" in raw or b"/" in raw:
                raise FfsError("name %r cannot be an AmigaDOS name (1 to %d bytes, no ':' or '/')" % (part, MAX_NAME))
            folded = bytes(upper(b, True) for b in raw)
            last = depth == len(parts) - 1
            twin = [n for n in node.children.values() if bytes(upper(b, True) for b in n.name.encode("latin-1")) == folded and n.name != part]
            if twin:
                raise FfsError("%r and %r differ only in case" % (twin[0].name, part))
            child = node.children.get(part)
            if child is None:
                child = node.children[part] = Node(part, is_dir=not last or files[path] is None)
            if last:
                if files[path] is None:
                    if not child.is_dir:
                        raise FfsError("%s is both a file and a directory" % path)
                else:
                    if child.is_dir:
                        raise FfsError("%s is both a file and a directory" % path)
                    child.data = bytes(files[path])
            elif not child.is_dir:
                raise FfsError("%s is both a file and a directory" % "/".join(parts[:depth + 1]))
            node = child
    return root


def _walk(node):
    for name in sorted(node.children, key=lambda n: n.encode("latin-1")):
        child = node.children[name]
        yield child
        if child.is_dir:
            yield from _walk(child)


def blocks_needed(root):
    """Blocks the tree takes besides the 2 reserved ones, the root block and the bitmap pages."""
    total = 0
    for node in _walk(root):
        total += 1
        if not node.is_dir:
            data = -(-len(node.data) // BLOCK)
            total += data + (-(-(data - TABLE) // TABLE) if data > TABLE else 0)
    return total


def volume_blocks(root, min_blocks=0, free_blocks=0):
    """Smallest multiple of 32 blocks that holds the tree, the bitmap and `free_blocks` more, and is at least `min_blocks`."""
    need = RESERVED + 1 + blocks_needed(root) + free_blocks
    pages = 1
    while True:
        blocks = max(min_blocks, need + pages, 64)
        blocks = -(-blocks // 32) * 32
        wanted = -(-(blocks - RESERVED) // (BITMAP_LONGS * 32))
        if wanted <= pages:
            break
        pages = wanted
    if wanted > BITMAP_PAGES:
        raise FfsError("volume of %d blocks needs more than %d bitmap pages" % (blocks, BITMAP_PAGES))
    return blocks


def write_volume(root, label, dostype=b"DOS\x01", blocks=None, when=DEFAULT_TIME):
    """The bytes of the volume. `root` comes from build_tree; `blocks` defaults to the smallest size that holds it."""
    if len(dostype) != 4 or dostype[:3] != b"DOS" or dostype[3] > 5:
        raise FfsError("unsupported DosType %r" % (dostype,))
    intl = bool(dostype[3] & 2)
    if intl is False and any(ord(ch) > 127 for node in _walk(root) for ch in node.name):
        raise FfsError("a name with a character above 127 needs an international DosType (DOS\\3)")
    raw_label = label.encode("latin-1")
    if not raw_label or len(raw_label) > MAX_NAME or b":" in raw_label or b"/" in raw_label:
        raise FfsError("volume name %r is not an AmigaDOS name" % label)
    blocks = blocks or volume_blocks(root)
    if blocks % 32 or blocks < 64:
        raise FfsError("a volume is a multiple of 32 blocks, at least 64")
    pages = -(-(blocks - RESERVED) // (BITMAP_LONGS * 32))
    if pages > BITMAP_PAGES:
        raise FfsError("too large: more than %d bitmap pages" % BITMAP_PAGES)
    root_block = (blocks - 1 + RESERVED) // 2
    taken, cursor = set(range(root_block, root_block + 1 + pages)), RESERVED

    def allocate():
        nonlocal cursor
        while cursor in taken:
            cursor += 1
        if cursor >= blocks:
            raise FfsError("the tree does not fit in %d blocks" % blocks)
        taken.add(cursor)
        return cursor

    for node in _walk(root):
        node.key = allocate()
        if not node.is_dir:
            count = -(-len(node.data) // BLOCK)
            node.data_blocks = [allocate() for _ in range(count)]
            node.ext_blocks = [allocate() for _ in range(-(-(count - TABLE) // TABLE) if count > TABLE else 0)]
    days, mins, ticks = amiga_date(when)
    image = bytearray(blocks * BLOCK)
    image[0:4] = dostype

    def put(block, packed):
        image[block * BLOCK:(block + 1) * BLOCK] = packed

    def hash_table(directory, parent_key):
        table = [0] * HASH_SIZE
        for name in sorted(directory.children, key=lambda n: n.encode("latin-1")):
            child = directory.children[name]
            h = name_hash(name.encode("latin-1"), intl)
            child.chain, table[h] = table[h], child.key
            write_node(child, parent_key)
        return table

    def header_common(longs, node, parent_key, kind):
        longs[0], longs[1] = T_HEADER, node.key
        longs[80] = node.protection
        longs[105:108] = (days, mins, ticks)
        longs[108:116] = struct.unpack(">8L", _bstr(node.name.encode("latin-1"), 32))
        longs[124], longs[125], longs[127] = node.chain, parent_key, kind

    def write_node(node, parent_key):
        longs = [0] * 128
        if node.is_dir:
            longs[6:78] = hash_table(node, node.key)
            header_common(longs, node, parent_key, ST_USERDIR)
            put(node.key, checksum_block(longs, 5))
            return
        listed = node.data_blocks[:TABLE]
        longs[2], longs[4] = len(listed), node.data_blocks[0] if node.data_blocks else 0
        for i, block in enumerate(listed):
            longs[77 - i] = block
        longs[81], longs[126] = len(node.data), node.ext_blocks[0] if node.ext_blocks else 0
        header_common(longs, node, parent_key, ST_FILE)
        put(node.key, checksum_block(longs, 5))
        for n, block in enumerate(node.ext_blocks):
            part = node.data_blocks[TABLE * (n + 1):TABLE * (n + 2)]
            ext = [0] * 128
            ext[0], ext[1], ext[2] = T_LIST, block, len(part)
            for i, data_block in enumerate(part):
                ext[77 - i] = data_block
            ext[125], ext[126] = node.key, node.ext_blocks[n + 1] if n + 1 < len(node.ext_blocks) else 0
            ext[127] = ST_FILE
            put(block, checksum_block(ext, 5))
        for n, block in enumerate(node.data_blocks):
            image[block * BLOCK:block * BLOCK + len(node.data[n * BLOCK:(n + 1) * BLOCK])] = node.data[n * BLOCK:(n + 1) * BLOCK]

    root.chain = 0
    longs = [0] * 128
    longs[0], longs[3] = T_HEADER, HASH_SIZE
    longs[6:78] = hash_table(root, root_block)
    longs[78] = 0xFFFFFFFF
    for page in range(pages):
        longs[79 + page] = root_block + 1 + page
    longs[105:108], longs[118:121], longs[121:124] = (days, mins, ticks), (days, mins, ticks), (days, mins, ticks)
    longs[108:116] = struct.unpack(">8L", _bstr(raw_label, 32))
    longs[127] = ST_ROOT
    put(root_block, checksum_block(longs, 5))
    free = [(b not in taken) for b in range(blocks)]
    for page in range(pages):
        words = [0] * BITMAP_LONGS
        for w in range(BITMAP_LONGS):
            for bit in range(32):
                block = RESERVED + (page * BITMAP_LONGS + w) * 32 + bit
                if block < blocks and free[block]:
                    words[w] |= 1 << bit
        longs = [0] + words
        put(root_block + 1 + page, checksum_block(longs, 0))
    return bytes(image)


@dataclass(frozen=True)
class Volume:
    label: str
    dostype: bytes
    blocks: int
    files: dict            # path -> bytes
    dirs: tuple            # every directory path, sorted
    protection: dict       # path -> protection bits of files and directories


def read_volume(image):
    """Check an image written by write_volume (or any plain FFS partition of the same shape) and return what it holds. Raises FfsError at the first inconsistency:
    block checksums, types, a block reached twice, a data length that the blocks do not hold, and a bitmap that differs from the blocks actually in use."""
    if len(image) % BLOCK or len(image) < 64 * BLOCK:
        raise FfsError("the image is not a whole number of blocks")
    blocks, dostype = len(image) // BLOCK, bytes(image[:4])
    if dostype[:3] != b"DOS" or dostype[3] not in (1, 3, 5):
        raise FfsError("not an FFS volume (DosType %r)" % (dostype,))
    intl = bool(dostype[3] & 2)

    def long_block(number):
        if not RESERVED <= number < blocks:
            raise FfsError("block %d is outside the volume" % number)
        return struct.unpack(">128L", image[number * BLOCK:(number + 1) * BLOCK])

    def checked(number, kind, secondary):
        longs = long_block(number)
        if sum(longs) & 0xFFFFFFFF:
            raise FfsError("checksum of block %d" % number)
        if longs[0] != kind or longs[127] != secondary:
            raise FfsError("block %d is not of the expected type" % number)
        return longs

    def name_of(longs):
        length = longs[108] >> 24
        if length > MAX_NAME:
            raise FfsError("name too long")
        return struct.pack(">8L", *longs[108:116])[1:1 + length].decode("latin-1")

    root_block = (blocks - 1 + RESERVED) // 2
    root = checked(root_block, T_HEADER, ST_ROOT)
    used, files, dirs, protection = {root_block}, {}, [], {}

    def claim(number):
        if number in used:
            raise FfsError("block %d is used twice" % number)
        used.add(number)

    def read_file(number):
        longs = checked(number, T_HEADER, ST_FILE)
        size, listed, header = longs[81], [], longs
        while True:
            count = header[2]
            if count > TABLE:
                raise FfsError("too many data blocks in a header")
            listed += [header[77 - i] for i in range(count)]
            following = header[126]
            if not following:
                break
            claim(following)
            header = checked(following, T_LIST, ST_FILE)
            if header[125] != number:
                raise FfsError("extension block of another file")
        if len(listed) != -(-size // BLOCK) or (listed and longs[4] != listed[0]):
            raise FfsError("the data blocks do not hold the size of the file")
        data = bytearray()
        for block in listed:
            claim(block)
            data += image[block * BLOCK:(block + 1) * BLOCK]
        return bytes(data[:size])

    def walk(table, prefix, parent):
        for slot, head in enumerate(table):
            chain, seen = head, 0
            while chain:
                seen += 1
                if seen > blocks:
                    raise FfsError("hash chain loop")
                claim(chain)
                longs = long_block(chain)
                name = name_of(longs)
                if longs[125] != parent or longs[1] != chain or name_hash(name.encode("latin-1"), intl) != slot:
                    raise FfsError("directory entry %r is misplaced" % name)
                path = prefix + name
                protection[path] = longs[80]
                if longs[127] == ST_USERDIR:
                    checked(chain, T_HEADER, ST_USERDIR)
                    dirs.append(path)
                    walk(longs[6:78], path + "/", chain)
                else:
                    files[path] = read_file(chain)
                chain = longs[124]

    pages = [p for p in root[79:104] if p]
    for page in pages:
        claim(page)
    walk(root[6:78], "", root_block)
    free = set()
    for number, page in enumerate(pages):
        longs = long_block(page)
        if sum(longs) & 0xFFFFFFFF:
            raise FfsError("checksum of bitmap block %d" % page)
        for w in range(BITMAP_LONGS):
            for bit in range(32):
                if longs[1 + w] >> bit & 1:
                    free.add(RESERVED + (number * BITMAP_LONGS + w) * 32 + bit)
    if free != set(range(RESERVED, blocks)) - used:
        raise FfsError("the bitmap does not match the blocks in use")
    return Volume(name_of(root), dostype, blocks, files, tuple(sorted(dirs)), protection)
