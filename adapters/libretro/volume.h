// Volumes (T64a): a directory, an LHA or a ZIP of files that the libretro adapter turns, at load time, into ONE hard file in memory (ffs_volume.h) that p6_load takes as a P6_MEDIA_HDF.
// The host is read once, here, and never again: what enters the machine is the image, an object in memory whose identity is the SHA-256 of its bytes (docs/adr/0011-volumes-from-the-host.md).
// There is no mount of the host file system, no live view of a folder, nothing for a state to point at: a state, a rewind and a run-ahead carry the guest's writes as the overlay of
// the hard file (docs/02-contracts.md, "HDF"), and the source is never written.
//
// What decides the bytes of the image is the CONTENT of the tree and nothing else: the names (ISO 8859-1 bytes) and the bytes of the files, and the empty directories. Not the path the
// directory, the archive or the zip was opened from, not the name of the file, not a modification time, not the order the host lists a directory or the archive stores its members, not
// the permissions, not the user. Every date of the volume is the one stamp of ffs_volume.h, the volume is always called PUAE6, and every file has the same protection bits. The same
// content from a directory, an LHA and a ZIP gives the same image (tests/libretro-host/test_volume.py).
//
// Sources and what they take:
//   - A directory: read through the frontend's VFS (version 3: stat and the directory calls) or, when the frontend offers less, through the C library. Hidden files are included except
//     .DS_Store, AppleDouble forks (._*) and __MACOSX, which are macOS litter. A symbolic link is the file or the directory it points to (the VFS cannot tell a link, so the C library
//     path does not either); a loop is stopped by the nesting cap and the entry cap. A name is the UTF-8 of the host converted to ISO 8859-1; a character outside it (macOS stores é as e
//     and a combining accent) is an error that names the file.
//   - An LHA (-lh0- -lh5- -lh6- -lh7-, header levels 0, 1 and 2): lha.h. Its names are already AmigaDOS's.
//   - A ZIP (stored and deflate): the members that are not directories, with the names as the zip gives them (UTF-8 when its flag says so, ISO 8859-1 bytes otherwise). A ZIP that holds a
//     disk image (.adf .adz .dms, or a format the core refuses) is a disk, as before; a ZIP of files without one is a volume.
// Bounds, always applied before anything is allocated: 4096 entries, 48 MiB of file bytes (a volume has 25 bitmap pages, ffs_volume.h), 64 MiB for the archive file, 64 levels of nesting.
#ifndef P6_LIBRETRO_VOLUME_H
#define P6_LIBRETRO_VOLUME_H

#include "container.h"
#include "content.h"
#include "ffs_volume.h"
#include "lha.h"

#include <algorithm>
#include <stdint.h>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kMaxVolumeEntries = 4096;                 // files and directories of one volume
constexpr size_t kMaxVolumeBytes = 48u * 1024 * 1024;      // the bytes of all its files
constexpr size_t kMaxVolumeArchiveBytes = 64u * 1024 * 1024;   // an LHA or a ZIP as a file
constexpr uint32_t kVolumeFreeBlocks = 2048;               // 1 MiB left free in the volume for the guest's writes

struct VolumeSummary {
    size_t files = 0, directories = 0, bytes = 0;
};

inline bool is_volume_extension(const std::string &path)
{
    const std::string ext = container_detail::lower_extension(path);
    return ext == "lha" || ext == "lzh";
}

namespace volume_detail {

// macOS litter that a folder or a zip gets and no Amiga volume wants.
inline bool ignored_name(const std::string &name)
{
    return name == ".DS_Store" || name == "__MACOSX" || name.compare(0, 2, "._") == 0;
}

// The bytes of an ISO 8859-1 name from the UTF-8 of a host. False for bytes that are not UTF-8 and for any character above U+00FF.
inline bool latin1_from_utf8(const std::string &in, std::string *out)
{
    out->clear();
    for (size_t i = 0; i < in.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(in[i]);
        if (c < 0x80) {
            out->push_back(char(c));
        } else if ((c & 0xE0) == 0xC0 && i + 1 < in.size() && (static_cast<unsigned char>(in[i + 1]) & 0xC0) == 0x80) {
            const unsigned code = ((c & 0x1Fu) << 6) | (static_cast<unsigned char>(in[i + 1]) & 0x3Fu);
            if (code < 0x80 || code > 0xFF) return false;
            out->push_back(char(code));
            ++i;
        } else {
            return false;
        }
    }
    return true;
}

inline bool control_character(const std::string &name)
{
    for (unsigned char c : name) if (c < 32 || c == 127) return true;
    return false;
}

inline std::string megabytes(size_t bytes) { return std::to_string(bytes >> 20) + " MiB"; }

}  // namespace volume_detail

// --- a directory --------------------------------------------------------------------------------------------------------------------------------

// Reads the tree under `root` through `reader`. `root` is only the place to start: no part of it is kept.
inline bool volume_from_directory(const FileReader &reader, const std::string &root, std::vector<VolumeFile> *files, std::string *why)
{
    using namespace volume_detail;
    files->clear();
    struct Pending {
        std::string host, amiga;
        size_t depth;
    };
    std::vector<Pending> pending;
    pending.push_back({root, std::string(), 0});
    size_t bytes = 0;
    while (!pending.empty()) {
        const Pending here = pending.back();
        pending.pop_back();
        if (here.depth >= ffs::kMaxDepth) {
            *why = "the directory is nested more than " + std::to_string(ffs::kMaxDepth) + " levels deep";
            return false;
        }
        std::vector<DirEntry> entries;
        const ListResult listed = reader.list(here.host, kMaxVolumeEntries, &entries);
        if (listed == ListResult::TooMany) {
            *why = "a directory has more than " + std::to_string(kMaxVolumeEntries) + " entries";
            return false;
        }
        if (listed != ListResult::Ok) {
            *why = "the directory cannot be listed";
            return false;
        }
        std::sort(entries.begin(), entries.end(), [](const DirEntry &a, const DirEntry &b) { return a.name < b.name; });
        for (const DirEntry &entry : entries) {
            if (ignored_name(entry.name)) continue;
            std::string name;
            if (!latin1_from_utf8(entry.name, &name) || control_character(name)) {
                *why = "the name \"" + entry.name + "\" cannot be an AmigaDOS name: it has a character outside ISO 8859-1 (or a control character)";
                return false;
            }
            if (files->size() >= kMaxVolumeEntries) {
                *why = "the directory holds more than " + std::to_string(kMaxVolumeEntries) + " files and directories";
                return false;
            }
            const std::string amiga = here.amiga.empty() ? name : here.amiga + "/" + name;
            const std::string host = join_path(here.host, entry.name);
            VolumeFile file;
            file.path = amiga;
            if (entry.is_dir) {
                file.is_dir = true;
                files->push_back(std::move(file));
                pending.push_back({host, amiga, here.depth + 1});
                continue;
            }
            const ReadResult read = reader.read(host, kMaxVolumeBytes - bytes, &file.data);
            if (read == ReadResult::TooLarge) {
                *why = "the files together are larger than the " + megabytes(kMaxVolumeBytes) + " a volume takes";
                return false;
            }
            if (read != ReadResult::Ok) {
                *why = "\"" + entry.name + "\" cannot be read";
                return false;
            }
            bytes += file.data.size();
            files->push_back(std::move(file));
        }
    }
    if (files->empty()) {
        *why = "the directory is empty";
        return false;
    }
    return true;
}

// --- an LHA ---------------------------------------------------------------------------------------------------------------------------------------

inline bool volume_from_lha(const std::vector<uint8_t> &bytes, std::vector<VolumeFile> *files, std::string *why)
{
    files->clear();
    LhaLimits limits;
    limits.max_entries = kMaxVolumeEntries;
    limits.max_file = limits.max_total = kMaxVolumeBytes;
    std::vector<LhaEntry> entries;
    if (!read_lha(bytes.data(), bytes.size(), limits, &entries, why)) return false;
    for (LhaEntry &entry : entries) {
        VolumeFile file;
        file.path = std::move(entry.name);
        file.is_dir = entry.is_dir;
        file.data = std::move(entry.data);
        files->push_back(std::move(file));
    }
    if (files->empty()) {
        *why = "the LHA archive holds no files";
        return false;
    }
    return true;
}

// --- a ZIP ------------------------------------------------------------------------------------------------------------------------------------------

// Does this ZIP hold files and no disk? A zip with an ADF, an ADZ or a DMS (or a disk in a format the core refuses) is a disk: the old rules and messages apply to it. A zip that cannot be
// read as a zip is not a volume either (the disk path says what is wrong with it).
inline bool zip_is_volume(const std::vector<uint8_t> &bytes)
{
    using namespace container_detail;
    ZipDirectory zip;
    std::string why;
    if (!read_zip_directory(bytes, &zip, &why)) return false;
    bool files = false;
    for (const ZipEntry &entry : zip.entries) {
        if (zip_entry_ignored(entry)) continue;
        const size_t slash = entry.name.find_last_of('/');
        const std::string base = slash == std::string::npos ? entry.name : entry.name.substr(slash + 1);
        const std::string ext = container_detail::lower_extension(base);
        if (ext == "adf" || ext == "adz" || ext == "dms" || ext == "fdi" || ext == "7z" || unsupported_format_reason(base)) return false;
        files = true;
    }
    return files;
}

inline bool volume_from_zip(const std::vector<uint8_t> &bytes, std::vector<VolumeFile> *files, std::string *why)
{
    using namespace volume_detail;
    using namespace container_detail;
    files->clear();
    ZipDirectory zip;
    if (!read_zip_directory(bytes, &zip, why)) return false;
    size_t declared = 0;
    struct Item {
        const ZipEntry *entry;
        std::string path;
        bool is_dir;
    };
    std::vector<Item> items;
    for (const ZipEntry &entry : zip.entries) {
        std::string name = entry.name;
        const bool is_dir = !name.empty() && name.back() == '/';
        if (is_dir) name.pop_back();
        if ((entry.flags & 0x800) != 0) {
            std::string latin1;
            if (!latin1_from_utf8(name, &latin1)) {
                *why = "the name \"" + name + "\" in the ZIP file cannot be an AmigaDOS name: it has a character outside ISO 8859-1";
                return false;
            }
            name = latin1;
        }
        bool skip = false;
        size_t at = 0;
        for (;;) {                                      // every component must be a plain name
            const size_t slash = name.find('/', at);
            const std::string part = name.substr(at, slash == std::string::npos ? std::string::npos : slash - at);
            if (part.empty() || part == "." || part == ".." || control_character(part)) {
                *why = "the ZIP file has an unsafe name (\"" + name + "\"): empty, \".\", \"..\" or control characters in a component";
                return false;
            }
            skip = skip || ignored_name(part);
            if (slash == std::string::npos) break;
            at = slash + 1;
        }
        if (skip) continue;
        if (items.size() >= kMaxVolumeEntries) {
            *why = "the ZIP file holds more than " + std::to_string(kMaxVolumeEntries) + " files and directories";
            return false;
        }
        if (!is_dir) {
            declared += entry.uncompressed;
            if (declared > kMaxVolumeBytes) {
                *why = "the files of the ZIP file together are larger than the " + megabytes(kMaxVolumeBytes) + " a volume takes";
                return false;
            }
        }
        items.push_back({&entry, name, is_dir});
    }
    for (const Item &item : items) {
        VolumeFile file;
        file.path = item.path;
        file.is_dir = item.is_dir;
        if (!item.is_dir && !extract_zip_entry(bytes, zip, *item.entry, kMaxVolumeBytes, "file a volume takes", &file.data, why)) {
            *why = "\"" + item.entry->name + "\": " + *why;
            return false;
        }
        files->push_back(std::move(file));
    }
    if (files->empty()) {
        *why = "the ZIP file holds no files";
        return false;
    }
    return true;
}

// --- the image --------------------------------------------------------------------------------------------------------------------------------------

// The hard file of a tree. `files` is consumed.
inline bool build_volume_image(std::vector<VolumeFile> *files, std::vector<uint8_t> *image, VolumeSummary *summary, std::string *why)
{
    *summary = VolumeSummary();
    for (const VolumeFile &file : *files) {
        if (file.is_dir) ++summary->directories;
        else ++summary->files, summary->bytes += file.data.size();
    }
    VolumeOptions options;
    options.free_blocks = kVolumeFreeBlocks;
    return build_ffs_volume(files, options, image, why);
}

}  // namespace p6_libretro

#endif
