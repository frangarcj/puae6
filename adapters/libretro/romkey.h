// The ROMs that Cloanto's Amiga Forever ships encrypted (T51). A file that starts with the 11 bytes "AMIROMTYPE1" is the Kickstart XORed, byte by byte, with the bytes of a key file (rom.key) repeated
// from its first byte; the key is the whole file, of any length. WinUAE (rommgr.cpp decode_cloanto_rom_do, which PUAE5 inherits) accepts the key if what comes out starts like a Kickstart: the word at
// offset 2 is $4EF9 (a JMP) or the image begins $1111 / $1114 (the 256 and 512 KiB headers). The same test is applied here, nothing more: whether the result is a Kickstart this machine runs is
// the bridge's decision from its bytes (size, header, checksum, CPU), so a key that passes this test by chance (about one in 20 000) is refused one step later with the bridge's reason.
//
// The decrypted image is the firmware: p6_load gets it and its SHA-256 is the identity of the states and of the movies. The key is read, used and dropped here; its bytes never reach the
// bridge, a state or a log (not even a hash or a length of it). Pure functions of their arguments (no file, no clock, no host state), so the same file and key always give the same ROM; the libretro
// core calls them after reading both from the system directory.
#ifndef P6_LIBRETRO_ROMKEY_H
#define P6_LIBRETRO_ROMKEY_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <vector>

namespace p6_libretro {

constexpr char kCloantoMagic[] = "AMIROMTYPE1";
constexpr size_t kCloantoMagicBytes = 11;
constexpr size_t kMaxRomKeyBytes = 64 * 1024;       // the key Amiga Forever ships is under 2 KiB; what the file claims never decides an allocation beyond this

enum class CloantoResult {
    NotEncrypted,   // the file does not start with AMIROMTYPE1: it is what it is
    Decrypted,      // the key made a Kickstart-looking image
    NeedsKey,       // encrypted and there is no key (empty)
    WrongKey,       // encrypted, a key, and what it gives does not start like a Kickstart (or there is no payload)
};

inline bool is_cloanto_rom(const uint8_t *data, size_t bytes)
{
    return data && bytes >= kCloantoMagicBytes && memcmp(data, kCloantoMagic, kCloantoMagicBytes) == 0;
}

// WinUAE's test of a decrypted image (rommgr.cpp): a JMP at offset 2, or the headers of the 256 and 512 KiB Kickstarts.
inline bool starts_like_a_kickstart(const uint8_t *m, size_t bytes)
{
    if (bytes < 4) return false;
    return (m[2] == 0x4e && m[3] == 0xf9) || (m[0] == 0x11 && (m[1] == 0x11 || m[1] == 0x14));
}

// Decrypts `file` with `key` into `out` (cleared unless the result is Decrypted). The input is not modified.
inline CloantoResult decrypt_cloanto_rom(const uint8_t *file, size_t file_bytes, const uint8_t *key, size_t key_bytes, std::vector<uint8_t> *out)
{
    out->clear();
    if (!is_cloanto_rom(file, file_bytes)) return CloantoResult::NotEncrypted;
    if (!key || key_bytes == 0) return CloantoResult::NeedsKey;
    const size_t payload = file_bytes - kCloantoMagicBytes;
    if (payload < 4) return CloantoResult::WrongKey;
    std::vector<uint8_t> rom(payload);
    size_t t = 0;
    for (size_t n = 0; n < payload; ++n) {
        rom[n] = uint8_t(file[kCloantoMagicBytes + n] ^ key[t]);
        if (++t == key_bytes) t = 0;
    }
    if (!starts_like_a_kickstart(rom.data(), rom.size())) return CloantoResult::WrongKey;
    out->swap(rom);
    return CloantoResult::Decrypted;
}

}  // namespace p6_libretro

#endif
