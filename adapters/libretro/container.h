// Disk files the libretro adapter reads (T17d): the plain ADF, an ADZ (gzip of an ADF), a ZIP that holds one disk, and a DMS archive. Each is decoded in
// memory, with no temporary file and no write, to the 901120-byte ADF it contains; the SHA-256 of THAT image is the identity of the medium
// (docs/adr/0008-media-identity.md), so a disk is the same medium, with the same states and movies, whichever container it came in. The native ABI
// (p6_load) still sees nothing but ADF images.
//
// Files are recognised by their bytes, not by their name: a plain ADF is exactly 901120 bytes, and anything else must start with the magic of a
// container this file decodes. The name is only used to say something useful about a format the core does not read. Nothing is sized from a number
// the file claims: a file is read with a cap (kMaxDiskFileBytes), a deflate stream stops at the size of an ADF, a zip member is limited by the same
// cap and a DMS track by the 32000 bytes the format allows (inflate.h, dms.h). A hostile file is therefore a message, never a large allocation.
//
// Decisions (docs/02-contracts.md, "Medios"):
//   - ZIP: exactly one disk image (.adf, .adz or .dms) among its entries; other entries (readmes, directories, macOS resource forks) are ignored. A zip
//     with several disks is refused and asks for an .m3u playlist, the one way to name a set (labels, order): the core does not guess an order from names.
//     Encrypted entries, ZIP64 and compression methods other than stored and deflate are refused. A zip does not contain another zip.
//   - IPF (and CT RAW): refused. It needs the SPS decoder library (capsimg), whose licence (references/capsimg/LICENCE.txt) forbids commercial use and
//     redistribution inside a commercial product or as a "freebie"; a core that ships it would be tied to that condition, and linking a binary
//     library host-side is not an option for the deterministic profile. The way in is an IPF reader with a compatible licence, a task of its own
//     (docs/07-machawk.md).
//   - FDI, SCP and other flux or track-level formats, 7z, RAR: refused with a message; convert to ADF. An LHA, and a ZIP that holds files and no disk image, are not disks:
//     they are volumes (volume.h, T64a) and become a hard file.
#ifndef P6_LIBRETRO_CONTAINER_H
#define P6_LIBRETRO_CONTAINER_H

#include "dms.h"
#include "inflate.h"

#include <ctype.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kDiskBytes = 901120;                          // 80 cylinders x 2 sides x 11 sectors x 512 bytes: what the bridge accepts as an ADF
constexpr size_t kMaxDiskFileBytes = 2 * 1024 * 1024;           // the largest file read as a disk: the biggest real ADZ, DMS or ZIP of one disk is a little over an ADF

namespace container_detail {

inline std::string lower_extension(const std::string &path)
{
    const size_t slash = path.find_last_of("/\\");
    const std::string name = slash == std::string::npos ? path : path.substr(slash + 1);
    const size_t dot = name.rfind('.');
    if (dot == std::string::npos || dot == 0) return std::string();
    std::string ext = name.substr(dot + 1);
    for (char &c : ext) c = char(tolower(static_cast<unsigned char>(c)));
    return ext;
}

inline const char *ipf_reason()
{
    return "IPF images are not supported: they need the SPS decoder library (capsimg), whose licence forbids commercial use and redistribution; "
           "use an ADF, ADZ, DMS or ZIP of the disk";
}

}  // namespace container_detail

// Extensions the core loads as a disk, in lower case.
inline bool is_disk_extension(const std::string &path)
{
    const std::string ext = container_detail::lower_extension(path);
    return ext == "adf" || ext == "adz" || ext == "dms" || ext == "zip";
}

// What to say about a file whose extension names a format the core does not read; nullptr for any other extension.
inline const char *unsupported_format_reason(const std::string &path)
{
    const std::string ext = container_detail::lower_extension(path);
    if (ext == "ipf" || ext == "ctr" || ext == "raw") return container_detail::ipf_reason();
    if (ext == "fdi") return "FDI images are not supported (a track-level format the disk loader here does not read); convert the disk to ADF";
    if (ext == "scp" || ext == "dsk" || ext == "img" || ext == "ima" || ext == "st") return "this disk format is not supported; convert the disk to ADF";
    if (ext == "7z" || ext == "rar" || ext == "lzx") return "only ZIP archives are read as disks; put the ADF in a ZIP or use an ADZ (an LHA or a ZIP of files is a volume, a different thing)";
    return nullptr;
}

namespace container_detail {

inline const char *describe(InflateResult result)
{
    switch (result) {
    case InflateResult::Truncated: return "is truncated";
    case InflateResult::Corrupt: return "is corrupt";
    case InflateResult::TooLarge: return "decompresses to more than it declares or than the core reads";
    default: return "";
    }
}

inline uint16_t le16(const uint8_t *p) { return uint16_t(p[0] | (p[1] << 8)); }
inline uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }

// gzip (RFC 1952): one member, which must end the file. `out` receives the data, at most `limit` bytes of it (an ADF by default; a hard file asks for its own cap, T63a).
inline bool gunzip(const std::vector<uint8_t> &file, std::vector<uint8_t> *out, std::string *why, size_t limit = kDiskBytes)
{
    const uint8_t *in = file.data();
    const size_t size = file.size();
    if (size < 18) { *why = "the gzip file is truncated"; return false; }
    if (in[2] != 8) { *why = "the gzip file uses a compression method other than deflate"; return false; }
    const unsigned flags = in[3];
    if (flags & 0xE0) { *why = "the gzip file has reserved flags set"; return false; }
    size_t pos = 10;
    auto have = [&](size_t n) { return size - pos >= n; };
    if (flags & 4) {
        if (!have(2)) { *why = "the gzip file is truncated"; return false; }
        const size_t extra = le16(in + pos);
        pos += 2;
        if (!have(extra)) { *why = "the gzip file is truncated"; return false; }
        pos += extra;
    }
    for (unsigned bit : {8u, 16u}) {                      // the name and the comment: zero-terminated text
        if (!(flags & bit)) continue;
        while (pos < size && in[pos]) ++pos;
        if (pos >= size) { *why = "the gzip file is truncated"; return false; }
        ++pos;
    }
    if (flags & 2) {
        if (!have(2)) { *why = "the gzip file is truncated"; return false; }
        pos += 2;
    }
    size_t consumed = 0;
    out->clear();
    const InflateResult result = inflate_raw(in + pos, size - pos, limit, out, &consumed);
    if (result != InflateResult::Ok) {
        *why = std::string("the gzip stream ") + describe(result) + (result == InflateResult::TooLarge ? (limit == kDiskBytes ? " (an ADF is 901120 bytes)" : " (more than the core reads)") : "");
        return false;
    }
    pos += consumed;
    if (!have(8)) { *why = "the gzip file is truncated (no trailer)"; return false; }
    if (le32(in + pos) != crc32_update(0, out->data(), out->size()) || le32(in + pos + 4) != uint32_t(out->size())) {
        *why = "the gzip file is corrupt (CRC or size mismatch)";
        return false;
    }
    if (size - pos != 8) { *why = "the gzip file has data after its stream"; return false; }
    return true;
}

struct ZipEntry {
    std::string name;
    unsigned method, flags;
    uint32_t crc, compressed, uncompressed, offset;
};

struct ZipDirectory {
    std::vector<ZipEntry> entries;
    size_t directory_offset = 0;           // where the central directory starts: no entry's data reaches past it
};

// The central directory of a ZIP file, which is the authority (sizes in a local header may be zero when a data descriptor follows). Every entry is listed; what to do with them is for the caller.
inline bool read_zip_directory(const std::vector<uint8_t> &file, ZipDirectory *zip, std::string *why)
{
    const uint8_t *in = file.data();
    const size_t size = file.size();
    if (size < 22) { *why = "the ZIP file is truncated"; return false; }
    // The end-of-central-directory record is within 22 + 65535 bytes of the end (its comment is the only variable part).
    size_t eocd = size;
    for (size_t at = size - 22 + 1, floor = size > 22 + 65535 ? size - 22 - 65535 : 0; at-- > floor;)
        if (!memcmp(in + at, "PK\5\6", 4) && at + 22 + le16(in + at + 20) <= size) { eocd = at; break; }
    if (eocd == size) { *why = "the ZIP file is truncated or damaged (no central directory)"; return false; }
    const uint8_t *end = in + eocd;
    if (le16(end + 4) != 0 || le16(end + 6) != 0) { *why = "multi-volume ZIP files are not supported"; return false; }
    const unsigned count = le16(end + 10);
    const uint32_t directory_size = le32(end + 12), directory_offset = le32(end + 16);
    if (count == 0xFFFF || directory_size == 0xFFFFFFFFu || directory_offset == 0xFFFFFFFFu) { *why = "ZIP64 files are not supported"; return false; }
    if (directory_offset > eocd || directory_size > eocd - directory_offset) { *why = "the ZIP file is damaged (central directory out of range)"; return false; }
    if (count == 0) { *why = "the ZIP file is empty"; return false; }
    zip->entries.clear();
    zip->directory_offset = directory_offset;
    size_t pos = directory_offset;
    const size_t directory_end = size_t(directory_offset) + directory_size;
    for (unsigned i = 0; i < count; ++i) {
        if (directory_end - pos < 46 || memcmp(in + pos, "PK\1\2", 4) != 0) { *why = "the ZIP file is damaged (bad central directory entry)"; return false; }
        const uint8_t *e = in + pos;
        const size_t name_length = le16(e + 28), extra_length = le16(e + 30), comment_length = le16(e + 32);
        if (directory_end - pos - 46 < name_length + extra_length + comment_length) { *why = "the ZIP file is damaged (entry out of range)"; return false; }
        ZipEntry entry;
        entry.name.assign(reinterpret_cast<const char *>(e + 46), name_length);
        entry.flags = le16(e + 8);
        entry.method = le16(e + 10);
        entry.crc = le32(e + 16);
        entry.compressed = le32(e + 20);
        entry.uncompressed = le32(e + 24);
        entry.offset = le32(e + 42);
        pos += 46 + name_length + extra_length + comment_length;
        zip->entries.push_back(std::move(entry));
    }
    return true;
}

// A directory entry, a macOS resource fork or the folder macOS adds: not a file of the archive.
inline bool zip_entry_ignored(const ZipEntry &entry)
{
    const size_t slash = entry.name.find_last_of('/');
    const std::string base = slash == std::string::npos ? entry.name : entry.name.substr(slash + 1);
    return base.empty() || entry.name.compare(0, 9, "__MACOSX/") == 0 || base.compare(0, 2, "._") == 0;
}

// The bytes of one entry, checked (encryption, method, size against `limit`, CRC). `what` names what the limit is the largest of ("disk file", "hard file").
inline bool extract_zip_entry(const std::vector<uint8_t> &file, const ZipDirectory &zip, const ZipEntry &entry, size_t limit, const char *what, std::vector<uint8_t> *out, std::string *why)
{
    const uint8_t *in = file.data();
    const size_t size = file.size();
    if (entry.flags & 1) { *why = "the ZIP entry is encrypted"; return false; }
    if (entry.method != 0 && entry.method != 8) { *why = "the ZIP entry uses compression method " + std::to_string(entry.method) + " (only stored and deflate are read)"; return false; }
    if (entry.uncompressed > limit) { *why = std::string("the ZIP entry is larger than any ") + what + " the core reads"; return false; }
    if (entry.offset > size || size - entry.offset < 30 || memcmp(in + entry.offset, "PK\3\4", 4) != 0) { *why = "the ZIP file is damaged (bad local header)"; return false; }
    const size_t data = size_t(entry.offset) + 30 + le16(in + entry.offset + 26) + le16(in + entry.offset + 28);
    if (data > zip.directory_offset || entry.compressed > zip.directory_offset - data) { *why = "the ZIP file is truncated (entry data out of range)"; return false; }
    out->clear();
    if (entry.method == 0) {
        if (entry.compressed != entry.uncompressed) { *why = "the ZIP entry is damaged (stored sizes differ)"; return false; }
        out->assign(in + data, in + data + entry.compressed);
    } else {
        size_t consumed = 0;
        const InflateResult result = inflate_raw(in + data, entry.compressed, entry.uncompressed, out, &consumed);
        if (result != InflateResult::Ok) { *why = std::string("the ZIP entry ") + describe(result); return false; }
        if (out->size() != entry.uncompressed) { *why = "the ZIP entry is damaged (it decompresses to less than it declares)"; return false; }
    }
    if (crc32_update(0, out->data(), out->size()) != entry.crc) { *why = "the ZIP entry is corrupt (CRC mismatch)"; return false; }
    return true;
}

// ZIP: picks the one disk entry, or with `hard_files` (T63a, an .hdz that is a zip) the one .hdf entry, whose size may be up to `limit`.
inline bool unzip(const std::vector<uint8_t> &file, std::vector<uint8_t> *out, std::string *why, std::string *member_name, size_t limit = kMaxDiskFileBytes, bool hard_files = false)
{
    ZipDirectory zip;
    if (!read_zip_directory(file, &zip, why)) return false;
    std::vector<const ZipEntry *> disks;
    unsigned unsupported = 0;
    for (const ZipEntry &entry : zip.entries) {
        if (zip_entry_ignored(entry)) continue;
        const size_t slash = entry.name.find_last_of('/');
        const std::string base = slash == std::string::npos ? entry.name : entry.name.substr(slash + 1);
        const std::string ext = lower_extension(base);
        if (hard_files ? ext == "hdf" : (ext == "adf" || ext == "adz" || ext == "dms")) disks.push_back(&entry);
        else if (!hard_files && unsupported_format_reason(base)) ++unsupported;
    }
    if (disks.empty()) {
        *why = hard_files ? "the ZIP file holds no hard file (.hdf)"
              : unsupported ? "the ZIP file holds a disk in a format the core does not read (IPF, FDI or similar); only ADF, ADZ and DMS disks are"
                            : "the ZIP file holds no disk image (.adf, .adz or .dms)";
        return false;
    }
    if (disks.size() > 1) {
        *why = hard_files ? "the ZIP file holds " + std::to_string(disks.size()) + " hard files; the core loads one per file, so list them in an .m3u playlist"
                          : "the ZIP file holds " + std::to_string(disks.size()) + " disk images; the core loads one disk per file, so list them in an .m3u playlist";
        return false;
    }
    *member_name = disks[0]->name;
    return extract_zip_entry(file, zip, *disks[0], limit, hard_files ? "hard file" : "disk file", out, why);
}

// `bytes` holds a file or a member of a zip; `depth` is 0 for a file. On success `bytes` is the ADF.
inline bool decode_image(std::vector<uint8_t> *bytes, const std::string &path, unsigned depth, std::string *why)
{
    if (bytes->size() == kDiskBytes) return true;            // a plain ADF is recognised by its size alone, never by what it starts with
    const uint8_t *data = bytes->data();
    const size_t size = bytes->size();
    std::vector<uint8_t> decoded;
    if (size >= 2 && data[0] == 0x1F && data[1] == 0x8B) {
        if (!gunzip(*bytes, &decoded, why)) return false;
        if (decoded.size() != kDiskBytes) {
            *why = "the gzip stream holds " + std::to_string(decoded.size()) + " bytes, not an ADF of " + std::to_string(kDiskBytes);
            return false;
        }
    } else if (size >= 4 && (!memcmp(data, "PK\3\4", 4) || !memcmp(data, "PK\5\6", 4))) {
        if (depth) { *why = "a ZIP inside a ZIP is not read"; return false; }
        std::string member;
        if (!unzip(*bytes, &decoded, why, &member)) return false;
        std::string inner;
        if (!decode_image(&decoded, member, 1, &inner)) { *why = "the ZIP entry " + member + ": " + inner; return false; }
    } else if (looks_like_dms(data, size)) {
        if (!decode_dms(data, size, &decoded, why)) return false;
    } else if (size >= 4 && !memcmp(data, "CAPS", 4)) {
        *why = ipf_reason();
        return false;
    } else if (size >= 25 && !memcmp(data, "Formatted Disk Image file", 25)) {
        *why = unsupported_format_reason("x.fdi");
        return false;
    } else if (size >= 8 && (!memcmp(data, "UAE-1ADF", 8) || !memcmp(data, "UAE--ADF", 8))) {
        *why = "extended ADF images are not supported";
        return false;
    } else if (const char *reason = unsupported_format_reason(path)) {
        *why = reason;
        return false;
    } else {
        *why = "the file is " + std::to_string(size) + " bytes, not an ADF of " + std::to_string(kDiskBytes) + " or a supported container (ADZ, ZIP, DMS)";
        return false;
    }
    bytes->swap(decoded);
    return true;
}

}  // namespace container_detail

// Turns the bytes of a disk file into the ADF they hold, in place. False with the reason in `why` (a sentence about the file, without its path).
// `path` is only used to name the format of a file the core cannot read.
inline bool decode_disk(std::vector<uint8_t> *bytes, const std::string &path, std::string *why)
{
    why->clear();
    if (bytes->size() > kMaxDiskFileBytes) {
        *why = "the file is larger than any disk file the core reads";
        return false;
    }
    if (!container_detail::decode_image(bytes, path, 0, why)) return false;
    return bytes->size() == kDiskBytes;
}

}  // namespace p6_libretro

#endif
