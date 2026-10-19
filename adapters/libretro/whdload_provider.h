// User-supplied provider only; never downloads or embeds third-party binaries.
#pragma once
#include "whdload_bundle.h"
#include "sha256.h"

namespace p6_libretro {
struct WhdLoadContent {
    std::vector<VolumeFile> files;
    std::vector<uint8_t> program, vfs;
    bool active() const { return !files.empty(); }
};

// SHA-1 is used only to recognize the public Kickstart names of the T65a recipe.
// Message length, padding and words follow FIPS 180-4; no OS crypto dependency.
inline std::string whdload_sha1(const std::vector<uint8_t> &data)
{
    auto rol = [](uint32_t x, unsigned n) { return (x << n) | (x >> (32 - n)); };
    uint32_t h[] = {0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0};
    const size_t padded = (data.size() + 9 + 63) & ~size_t(63);
    for (size_t at = 0; at < padded; at += 64) {
        uint32_t w[80] = {};
        for (size_t n = 0; n < 64; ++n) {
            const size_t p = at + n;
            const uint8_t value = p < data.size() ? data[p] : p == data.size() ? 0x80 :
                p >= padded - 8 ? uint8_t((uint64_t(data.size()) * 8) >> ((padded - 1 - p) * 8)) : 0;
            w[n / 4] |= uint32_t(value) << ((3 - n % 4) * 8);
        }
        for (unsigned n = 16; n < 80; ++n) w[n] = rol(w[n-3] ^ w[n-8] ^ w[n-14] ^ w[n-16], 1);
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (unsigned n = 0; n < 80; ++n) {
            const uint32_t f = n < 20 ? (b & c) | (~b & d) : n < 40 ? b ^ c ^ d : n < 60 ? (b & c) | (b & d) | (c & d) : b ^ c ^ d;
            const uint32_t k = n < 20 ? 0x5a827999 : n < 40 ? 0x6ed9eba1 : n < 60 ? 0x8f1bbcdc : 0xca62c1d6;
            const uint32_t next = rol(a, 5) + f + e + k + w[n];
            e = d; d = c; c = rol(b, 30); b = a; a = next;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
    }
    std::string hex;
    for (uint32_t word : h) for (int shift = 28; shift >= 0; shift -= 4) hex += "0123456789abcdef"[(word >> shift) & 15];
    return hex;
}
inline const char *whdload_kickstart_name(const std::vector<uint8_t> &rom)
{
    const auto sha1 = whdload_sha1(rom);
    for (const auto &entry : whdload_data::kKickstartNames) if (sha1 == entry.sha1) return entry.name;
    return nullptr;
}
inline bool read_whdload_provider(const FileReader &reader, const std::string &system, WhdLoadContent *pack, std::string *why)
{
    const auto path = join_path(system, "WHDLoad_usr.lha");
    std::vector<uint8_t> archive;
    const ReadResult read = reader.read(path, kMaxVolumeArchiveBytes, &archive);
    if (read != ReadResult::Ok) { *why = path + ": missing, unreadable or larger than the archive limit"; return false; }
    std::vector<VolumeFile> files;
    if (!volume_from_lha(archive, &files, why)) { *why = path + ": " + *why; return false; }
    std::vector<uint8_t> *outputs[] = {&pack->program, &pack->vfs};
    const char *names[] = {"WHDLoad/C/WHDLoad", "WHDLoad/C/WHDLoad.VFS"};
    const char *pins[] = {whdload_data::kProgramSha256, whdload_data::kVfsSha256};
    for (size_t n = 0; n < 2; ++n) {
        const auto found = std::find_if(files.begin(), files.end(), [&](const VolumeFile &f) { return !f.is_dir && f.path == names[n]; });
        if (found == files.end()) { *why = path + ": missing " + names[n]; return false; }
        uint8_t digest[32]; p6_sha256_hash(found->data.data(), found->data.size(), digest);
        std::string hex;
        for (uint8_t b : digest) { hex += "0123456789abcdef"[b >> 4]; hex += "0123456789abcdef"[b & 15]; }
        if (hex != pins[n]) { *why = path + ": " + names[n] + " differs from pinned WHDLoad 20.0"; return false; }
        *outputs[n] = found->data;
    }
    return true;
}
} // namespace p6_libretro
