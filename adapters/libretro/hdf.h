// Hard files the libretro adapter reads (T63a): a plain .hdf, or an .hdz (gzip of a hard file, or a zip with one .hdf, decoded in memory with container.h's gunzip and unzip), and the name a changed one is
// saved to. Header-only, no engine and no ABI: the bytes go to p6_load as a P6_MEDIA_HDF, which mounts them on the UAE controller (uaehf.device and the UAEFS board) as DH0,
// DH1... in the order they were named (docs/02-contracts.md, "HDF").
//
// What the adapter decides, and what the engine does:
//   - The image is a multiple of 512 bytes, at least one block (the bridge refuses anything else; saying so here names the file and the size).
//   - A file that starts with "RDSK" brings its own Rigid Disk Block, its partitions and, when it has one, its file system; any other file is ONE partition, with the
//     classic UAE layout the engine gives it (32 sectors, 1 head, 2 reserved blocks, boot priority 0). The core does not look inside the RDB: the engine bounds-checks it
//     (T29a, patch 0163/0190). The adapter only says which of the two it is, in the log, so that a person can tell why a disk did not mount.
//   - Size. The bridge holds the file three times over (the caller's bytes, the registered base and the room for the overlay of the writes) and a native state is bound by
//     the size of the image (T29c), so a hard file is capped (kMaxHdfBytes, and kMaxHdfTotalBytes for all of them) the way a floppy file is, and nothing is sized from what
//     a file claims: a gzip stream stops at the cap.
//   - Saving. The guest can always write to a hard file (T29b); the file given to the core is never touched. The changes leave the machine only when the content closes and
//     puae6_hdf_save asks for it: the whole image is exported with p6_media_export and replaces puae6-<id>.hdf in the save directory in one step (floppy_save.h), and the next
//     session that loads the same file with puae6_hdf_save on uses that copy.
#ifndef P6_LIBRETRO_HDF_H
#define P6_LIBRETRO_HDF_H

#include "container.h"
#include "floppy_save.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kHdfBlock = 512;
constexpr size_t kMaxHdfBytes = 128 * 1024 * 1024;         // one hard file (or the largest HDZ once decoded); a file that claims or grows past it is refused
constexpr size_t kMaxHdfTotalBytes = 128 * 1024 * 1024;    // all the hard files of one content together

inline bool is_hard_file_extension(const std::string &path)
{
    const std::string ext = container_detail::lower_extension(path);
    return ext == "hdf" || ext == "hdz";
}

struct HardFileInfo {
    bool rdb = false;             // starts with "RDSK": the image brings its own Rigid Disk Block
    uint64_t blocks = 0;
};

// Turns the bytes of an .hdf or .hdz into the hard file they hold, in place. A .hdz must be gzip (its member decoded up to `max_bytes`); an .hdf is the image as it is, so a
// gzip file with that name is not decoded (it would be a different disk, and the identity of a medium is its bytes). False with the reason in `why`, a sentence about the
// file without its path.
inline bool decode_hard_file(std::vector<uint8_t> *bytes, const std::string &path, size_t max_bytes, std::string *why)
{
    why->clear();
    if (container_detail::lower_extension(path) == "hdz") {
        const std::vector<uint8_t> &file = *bytes;
        std::vector<uint8_t> decoded;
        if (file.size() >= 2 && file[0] == 0x1F && file[1] == 0x8B) {
            if (!container_detail::gunzip(file, &decoded, why, max_bytes)) return false;
        } else if (file.size() >= 4 && (!memcmp(file.data(), "PK\3\4", 4) || !memcmp(file.data(), "PK\5\6", 4))) {
            std::string member;
            if (!container_detail::unzip(file, &decoded, why, &member, max_bytes, true)) return false;
        } else {
            *why = "an .hdz must be a gzip file (or a zip file with one .hdf) of a hard file";
            return false;
        }
        bytes->swap(decoded);
    } else if (bytes->size() > max_bytes) {
        *why = "the hard file is larger than the " + std::to_string(max_bytes) + " bytes the core reads";
        return false;
    }
    if (bytes->size() < kHdfBlock || bytes->size() % kHdfBlock != 0) {
        *why = "a hard file must be a whole number of 512-byte blocks, at least one (this one is " + std::to_string(bytes->size()) + " bytes)";
        return false;
    }
    return true;
}

inline HardFileInfo inspect_hard_file(const uint8_t *data, size_t size)
{
    HardFileInfo info;
    info.rdb = size >= 4 && !memcmp(data, "RDSK", 4);
    info.blocks = size / kHdfBlock;
    return info;
}

// What the log says about a hard file: which layout the engine will give it.
inline std::string describe_hard_file(const HardFileInfo &info)
{
    if (info.rdb) return std::to_string(info.blocks) + " blocks with a Rigid Disk Block (its own partitions)";
    return std::to_string(info.blocks) + " blocks, one partition (32 sectors, 1 head, 2 reserved blocks, boot priority 0)";
}

// "puae6-" and the first 24 hex digits of the SHA-256 of the hard file as its file gave it, then ".hdf": the saved copy follows the disk, not its name or place.
inline std::string saved_hard_file_name(const uint8_t origin[32])
{
    std::string name = saved_disk_name(origin);
    name.resize(name.size() - 4);
    return name + ".hdf";
}

inline std::string saved_hard_file_path(const std::string &directory, const uint8_t origin[32])
{
    if (directory.empty()) return std::string();
    const char last = directory[directory.size() - 1];
    return directory + (last == '/' || last == '\\' ? "" : "/") + saved_hard_file_name(origin);
}

}  // namespace p6_libretro

#endif
