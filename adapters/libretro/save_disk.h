// #SAVEDISK: an empty OFS DD floppy, built in memory. No clock or file access.
// PUAE5 libretro-dc.c supplies the directive/volume conventions; disk.c's
// floppy_get_rootblock describes the AmigaDOS root and bitmap layout.
#ifndef P6_LIBRETRO_SAVE_DISK_H
#define P6_LIBRETRO_SAVE_DISK_H

#include "content.h"
#include <stdint.h>

namespace p6_libretro {

inline std::string save_disk_volume(std::string name, unsigned index)
{
    const size_t first = name.find_first_not_of(" \t");
    name = first == std::string::npos ? std::string() : name.substr(first);
    bool empty = name.size() >= 5;
    for (size_t i = 0; empty && i < 5; ++i) empty = tolower(static_cast<unsigned char>(name[i])) == "empty"[i];
    if (name.empty() || empty) name = "Save Disk " + std::to_string(index);
    if (name.size() > 30) name.resize(30);
    for (char &c : name) if (c == '/' || c == ':') c = '_';
    return name;
}

inline std::string save_disk_filename(const std::string &playlist, unsigned index)
{
    // A basename, never the directive's text: the save directory owns this file.
    return without_extension(file_name(playlist)) + ".save" + std::to_string(index) + ".adf";
}

inline std::vector<uint8_t> make_save_disk(const std::string &label, unsigned index)
{
    std::vector<uint8_t> out(901120, 0);
    auto put = [&](size_t at, uint32_t value) {
        for (unsigned i = 0; i < 4; ++i) out[at + i] = uint8_t(value >> (24 - 8 * i));
    };
    auto seal = [&](size_t block, unsigned checksum) {
        uint32_t sum = 0;
        for (unsigned i = 0; i < 512; i += 4)
            sum += uint32_t(out[block + i]) << 24 | uint32_t(out[block + i + 1]) << 16 | uint32_t(out[block + i + 2]) << 8 | out[block + i + 3];
        put(block + 4 * checksum, 0u - sum);
    };
    memcpy(out.data(), "DOS", 3);              // OFS, non-bootable
    const size_t root = 880 * 512, bitmap = 881 * 512;
    put(root, 2);
    put(root + 12, 72);
    put(root + 312, UINT32_MAX);
    put(root + 316, 881);
    const std::string volume = save_disk_volume(label, index);
    out[root + 432] = uint8_t(volume.size());
    memcpy(out.data() + root + 433, volume.data(), volume.size());
    // Fixed Amiga epoch plus ordinal: identical labels still yield independent
    // media in one playlist. This is content, never the host's current time.
    put(root + 428, index);
    put(root + 492, index);
    put(root + 508, 1);
    seal(root, 5);
    for (unsigned block = 2; block < 1760; ++block) {
        if (block == 880 || block == 881) continue;
        const unsigned bit = block - 2;
        const size_t word = bitmap + 4 + (bit / 32) * 4;
        out[word + 3 - (bit % 32) / 8] |= uint8_t(1u << (bit % 8));
    }
    seal(bitmap, 0);
    return out;
}

}  // namespace p6_libretro
#endif
