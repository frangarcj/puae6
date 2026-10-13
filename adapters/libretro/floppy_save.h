// Saving the changes a guest made to its floppies (T27): the name of the file a disk is saved to and the write itself. Header-only, no engine.
//
// The core never touches the disk files it is given and never writes anything while it runs. When the content closes (retro_unload_game, never during
// a run, a save state, a rewind or a run-ahead) and the person asked for it (puae6_floppy_save), each disk the guest changed is exported with
// p6_media_export and written to the frontend's save directory under a name made from the identity of the disk as it was read from its file. The next
// time that content loads with floppy writes enabled, the file is the disk. A write replaces the old file in one step: the bytes go to a temporary file
// next to it, are flushed, and the temporary file is renamed over the old one, so the file is always either the old disk or the new one.
#ifndef P6_LIBRETRO_FLOPPY_SAVE_H
#define P6_LIBRETRO_FLOPPY_SAVE_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <filesystem>
#include <string>
#include <system_error>

#if !defined(_WIN32)
#include <unistd.h>
#endif

namespace p6_libretro {

// "puae6-" and the first 24 hex digits of the SHA-256 of the disk as its file gave it (so the saved copy follows the disk, not its name or place).
inline std::string saved_disk_name(const uint8_t origin[32])
{
    static const char digits[] = "0123456789abcdef";
    std::string name = "puae6-";
    for (size_t i = 0; i < 12; ++i) {
        name += digits[origin[i] >> 4];
        name += digits[origin[i] & 15];
    }
    return name + ".adf";
}

inline std::string saved_disk_path(const std::string &directory, const uint8_t origin[32])
{
    if (directory.empty()) return std::string();
    const char last = directory[directory.size() - 1];
    return directory + (last == '/' || last == '\\' ? "" : "/") + saved_disk_name(origin);
}

// The bridge refuses an image that starts like an extended ADF or an Amiga executable (it would take it for another format): such a disk, however a guest
// came to write its first bytes, cannot be saved as a plain ADF and loaded again, so it is not saved and a saved file like that is not used.
inline bool is_plain_adf_start(const uint8_t *data, size_t size)
{
    static const uint8_t ext1[8] = {'U', 'A', 'E', '-', '-', 'A', 'D', 'F'};
    static const uint8_t ext2[8] = {'U', 'A', 'E', '-', '1', 'A', 'D', 'F'};
    static const uint8_t hunk[8] = {0x00, 0x00, 0x03, 0xF3, 0x00, 0x00, 0x00, 0x00};
    return size >= 25 && memcmp(data, ext1, 8) && memcmp(data, ext2, 8) && memcmp(data, hunk, 8) && memcmp(data, "Formatted Disk Image file", 25);
}

// Writes `size` bytes to `path` so that a reader sees the old file or the new one and never half of it. On failure the temporary file is removed and the old file
// is untouched; `why` says what failed.
inline bool write_file_atomically(const std::string &path, const uint8_t *data, size_t size, std::string *why)
{
    const std::string temporary = path + ".tmp";
    FILE *file = fopen(temporary.c_str(), "wb");
    if (!file) {
        *why = "the temporary file " + temporary + " cannot be created";
        return false;
    }
    bool ok = fwrite(data, 1, size, file) == size && fflush(file) == 0;
#if !defined(_WIN32)
    ok = ok && fsync(fileno(file)) == 0;
#endif
    ok = (fclose(file) == 0) && ok;
    if (!ok) {
        remove(temporary.c_str());
        *why = "the temporary file " + temporary + " cannot be written";
        return false;
    }
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        remove(temporary.c_str());
        *why = "the file " + path + " cannot be replaced: " + error.message();
        return false;
    }
    return true;
}

}  // namespace p6_libretro

#endif
