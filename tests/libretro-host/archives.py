"""Synthetic 7z and RP9 files for the tests of T59: a 7z writer in pure Python (the container of 7zFormat.txt around the raw LZMA / LZMA2 streams of the standard library's
liblzma), so that the decoder of adapters/libretro/sevenzip.h is checked against an encoder that is not its own, and an RP9 package (a ZIP with rp9-manifest.xml).

The writer makes what 7-Zip makes: a signature header, the packed streams, then a header that is either plain or itself LZMA-compressed (an "encoded header"); solid (one
folder for all the files) or not; empty files and directories as empty streams. `tests/media/containers.py` has the ZIP side."""
import lzma
import struct
import zlib

from tests.media import containers as c


def number(value):
    """The variable-length number of 7z: the leading one bits of the first byte say how many bytes follow."""
    for extra in range(9):
        if extra == 8:
            return b"\xff" + value.to_bytes(8, "little")
        if value < (1 << (7 * (extra + 1))):
            first = ((0xFF00 >> extra) & 0xFF) | (value >> (8 * extra))
            return bytes([first]) + (value & ((1 << (8 * extra)) - 1)).to_bytes(extra, "little")


def bits(flags):
    out = bytearray((len(flags) + 7) // 8)
    for i, flag in enumerate(flags):
        if flag:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def digests(values):
    """AllAreDefined, then the CRC-32 of each."""
    return b"\x01" + b"".join(struct.pack("<I", v) for v in values)


def crc(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def compress(data, method, dict_size=1 << 20, lc=3, lp=0, pb=2, preset=6, chunk=None):
    """(coder id, properties, packed bytes) for one folder."""
    if method == "copy":
        return b"\x00", b"", data
    if method == "lzma2":
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": dict_size, "preset": preset, "lc": lc, "lp": lp, "pb": pb}])
        return b"\x21", bytes([2 * (dict_size.bit_length() - 1 - 12)]), packed
    if method == "lzma":
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "preset": preset, "lc": lc, "lp": lp, "pb": pb}])
        return b"\x03\x01\x01", bytes([(pb * 5 + lp) * 9 + lc]) + struct.pack("<I", dict_size), packed
    raise ValueError(method)


def coder(method_id, props):
    flags = len(method_id) | (0x20 if props else 0)
    return bytes([flags]) + method_id + (number(len(props)) + props if props else b"")


def streams_info(pack_pos, folders, substreams=None):
    """folders: list of dicts with coder id, props, packed size, unpack size and optional crc; substreams: list per folder of (size, crc) or None."""
    out = bytearray()
    out += b"\x06" + number(pack_pos) + number(len(folders)) + b"\x09" + b"".join(number(f["packed"]) for f in folders) + b"\x00"
    out += b"\x07\x0b" + number(len(folders)) + b"\x00"
    for f in folders:
        out += number(1) + coder(f["id"], f["props"])
    out += b"\x0c" + b"".join(number(f["unpack"]) for f in folders)
    crcs = [f.get("crc") for f in folders]
    if any(v is not None for v in crcs):
        out += b"\x0a" + (b"\x01" + b"".join(struct.pack("<I", v) for v in crcs) if all(v is not None for v in crcs) else
                          b"\x00" + bits([v is not None for v in crcs]) + b"".join(struct.pack("<I", v) for v in crcs if v is not None))
    out += b"\x00"
    if substreams is not None:
        out += b"\x08" + b"\x0d" + b"".join(number(len(s)) for s in substreams)
        sizes = b"".join(number(size) for s in substreams for size, _ in s[:-1])
        if sizes:
            out += b"\x09" + sizes
        wanted = [crc_value for s, f in zip(substreams, folders) if not (len(s) == 1 and f.get("crc") is not None) for _, crc_value in s]
        if wanted:
            out += b"\x0a" + digests(wanted)
        out += b"\x00"
    out += b"\x00"
    return bytes(out)


def names_property(names):
    raw = b"".join(n.encode("utf-16-le") + b"\x00\x00" for n in names)
    return b"\x11" + number(len(raw) + 1) + b"\x00" + raw


def sevenzip_file(files, method="lzma2", solid=True, encode_header=True, header_method="lzma", **options):
    """files: a list of (name, bytes); a name that ends in '/' is a directory, empty bytes an empty file. Returns the bytes of the archive."""
    streams = [(n, d) for n, d in files if not n.endswith("/") and d]
    groups = [streams] if solid and streams else [[s] for s in streams]
    folders, packed_all, substreams = [], b"", []
    for group in groups:
        data = b"".join(d for _, d in group)
        method_id, props, packed = compress(data, method, **options)
        folders.append({"id": method_id, "props": props, "packed": len(packed), "unpack": len(data)})
        substreams.append([(len(d), crc(d)) for _, d in group])
        packed_all += packed
    header = bytearray(b"\x01")
    if folders:
        header += b"\x04" + streams_info(0, folders, substreams)
    if files:
        empty_stream = [n.endswith("/") or not d for n, d in files]
        empty_file = [not n.endswith("/") for (n, d), e in zip(files, empty_stream) if e]
        header += b"\x05" + number(len(files))
        if any(empty_stream):
            body = bits(empty_stream)
            header += b"\x0e" + number(len(body)) + body
            if any(empty_file):
                body = bits(empty_file)
                header += b"\x0f" + number(len(body)) + body
        header += names_property([n.rstrip("/") for n, _ in files]) + b"\x00"
    header += b"\x00"
    header = bytes(header)
    body = packed_all
    if encode_header:
        method_id, props, packed = compress(header, header_method)
        info = streams_info(len(packed_all), [{"id": method_id, "props": props, "packed": len(packed), "unpack": len(header), "crc": crc(header)}])
        body += packed
        header = b"\x17" + info
    start = struct.pack("<QQI", len(body), len(header), crc(header))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", crc(start)) + start + body + header


def rp9_manifest(floppies, other="", configuration="<system>a500</system><ram type=\"chip\">512</ram>"):
    """floppies: list of (path, priority or None, root or None)."""
    items = ""
    for path, priority, root in floppies:
        attributes = ' type="adf"' + (f' priority="{priority}"' if priority is not None else "") + (f' root="{root}"' if root else "")
        items += f"<floppy{attributes}>{path}</floppy>"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- a package -->\n<rp9 xmlns="http://www.cloanto.com/rp9" version="1.0"><requirements><host>RetroPlatform</host>'
            '<playerversion>2.2.0.0</playerversion></requirements><application><description><title>A &amp; B</title></description>'
            f"<configuration>{configuration}</configuration><media>{items}{other}</media></application></rp9>").encode()


def rp9_file(floppies, members, **options):
    """A package: rp9-manifest.xml listing `floppies` ((path, priority, root) tuples) and the members (name, bytes) of the package."""
    return c.zip_file([("rp9-manifest.xml", rp9_manifest(floppies, **options))] + list(members))
