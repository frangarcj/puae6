// Floppy images as the bridge accepts them (T57): a plain ADF and the track-level formats of WinUAE that the engine reads in memory.
//
// The engine has no error path for these formats and a hostile header overruns its tables (docs/adr/0008-media-identity.md, the T12 finding): a track count beyond the
// 166 tracks of the drive, a track that claims more bits than the engine's MFM buffer holds, or one that lies outside the file. So this file is the only gatekeeper, as
// the size check is for the plain ADF: an image reaches the engine only after classify_floppy accepted it, and every number the engine takes from a header is checked
// here against the bytes it will read. Pure and allocation free (it is also the target of the parser fuzzer), no engine include.
//
//   plain ADF   exactly 901120 bytes (80 cylinders x 2 heads x 11 sectors x 512), no magic.
//   UAE-1ADF    "extended ADF" version 2 (disk.cpp ADF_EXT2): 8 bytes of magic, u16 reserved, u16 number of tracks, then per track 12 bytes
//               {u16 reserved, u8 revolutions - 1, u8 type (0 AmigaDOS sectors, 1 raw MFM), u32 length in bytes, u32 length in bits}, then the track data in order.
//   UAE--ADF    "extended ADF" version 1 (ADF_EXT1): 8 bytes of magic, 160 times {u16 sync, u16 length in bytes}, then the track data in order. A track with a sync
//               word of zero is 11 AmigaDOS sectors, any other is raw MFM that starts after that sync word.
//
// What is accepted is what a double-density 3.5" drive can read: a track is at most kMaxTrackBytes (a PAL revolution is 12668 bytes, a long protected track a
// little more; beyond 20000 bytes upstream switches the drive to high density, which this core does not have). Everything is big-endian, as in the files.
#ifndef P6_MEDIA_TRACKDISK_H
#define P6_MEDIA_TRACKDISK_H

#include <stdint.h>
#include <string.h>

namespace p6_floppy {

constexpr uint64_t kPlainAdfBytes = 1760u * 512u;
constexpr uint64_t kMaxTrackDiskBytes = 3u * 1024u * 1024u;     // the largest file read as a track-level disk (a full UAE-1ADF of 166 tracks of 13 KB is 2.2 MB)
constexpr uint64_t kMaxFloppyBytes = 16u * kPlainAdfBytes;       // all floppies together: the 16 plain disks the sealed heap of the Waterbox guest was sized for
constexpr uint32_t kMaxTracks = 2 * 83;                          // disk.cpp MAX_TRACKS: the table of tracks of a drive
constexpr uint32_t kMaxTrackBytes = 20000;
constexpr uint32_t kAmigaDosTrackBytes = 11 * 512;

enum Format : uint8_t { kAdf = 0, kExt2 = 1, kExt1 = 2, kFdi = 3 };

struct Verdict {
    bool ok;
    uint8_t format;          // a Format, when ok
    const char *why;         // static text, when not ok
};

namespace detail {

inline uint32_t be16(const uint8_t *p) { return (uint32_t(p[0]) << 8) | p[1]; }
inline uint32_t be24(const uint8_t *p) { return (uint32_t(p[0]) << 16) | (uint32_t(p[1]) << 8) | p[2]; }

inline Verdict bad(const char *why) { return Verdict{false, 0, why}; }
inline Verdict good(Format format) { return Verdict{true, uint8_t(format), nullptr}; }

inline bool starts_with(const uint8_t *data, uint64_t bytes, const char *magic)
{
    const size_t n = strlen(magic);
    return bytes >= n && !memcmp(data, magic, n);
}

// UAE-1ADF. Mirrors read_header_ext2 and drive_fill_bigbuf (raw tracks: (bitlen + 7) / 8 bytes read at the offset of the track into a buffer of 0x8000 words).
inline Verdict ext2(const uint8_t *data, uint64_t bytes)
{
    if (bytes < 12) return bad("an extended ADF is cut short in its header");
    const uint32_t tracks = be16(data + 10);
    if (data[8] || data[9]) return bad("an extended ADF has reserved header bytes set");
    if (tracks == 0 || tracks > kMaxTracks) return bad("an extended ADF has no tracks or more than a drive's 166");
    uint64_t offset = 12 + uint64_t(tracks) * 12;
    if (offset > bytes) return bad("an extended ADF is cut short in its table of tracks");
    for (uint32_t i = 0; i < tracks; ++i) {
        const uint8_t *t = data + 12 + 12 * i;
        const uint32_t type = t[3];
        if (t[0] || t[1] || t[4] || t[8]) return bad("an extended ADF track has reserved bytes set");
        const uint32_t length = be24(t + 5), bits = be24(t + 9);
        if (type > 1) return bad("an extended ADF track is neither AmigaDOS nor raw MFM");
        if (length > kMaxTrackBytes) return bad("an extended ADF track is longer than a double-density track (high density is not supported)");
        if (offset + length > bytes) return bad("an extended ADF track lies beyond the end of the file");
        if (type == 0) {
            if (length < kAmigaDosTrackBytes) return bad("an extended ADF AmigaDOS track is shorter than eleven sectors");
        } else {
            if (bits < 16 || (bits + 7) / 8 > length) return bad("an extended ADF raw track has no bits or more bits than its space holds");
        }
        offset += length;
    }
    return good(kExt2);
}

// UAE--ADF. Mirrors drive_insert (ADF_EXT1): 160 tracks, the track data from byte 648.
inline Verdict ext1(const uint8_t *data, uint64_t bytes)
{
    constexpr uint32_t tracks = 160;
    uint64_t offset = 8 + uint64_t(tracks) * 4;
    if (bytes < offset) return bad("an extended ADF is cut short in its table of tracks");
    for (uint32_t i = 0; i < tracks; ++i) {
        const uint32_t sync = be16(data + 8 + 4 * i), length = be16(data + 10 + 4 * i);
        if (length > kMaxTrackBytes) return bad("an extended ADF track is longer than a double-density track (high density is not supported)");
        if (offset + length > bytes) return bad("an extended ADF track lies beyond the end of the file");
        if (sync == 0) {
            if (length < kAmigaDosTrackBytes) return bad("an extended ADF AmigaDOS track is shorter than eleven sectors");
        } else if (length < 2) {
            return bad("an extended ADF raw track has no bits");
        }
        offset += length;
    }
    return good(kExt1);
}

}  // namespace detail

// Which floppy format the bytes are, or why they are none. Only the cheap, structural checks of the engine's own readers: the content id (the SHA-256 of the bytes)
// is the caller's.
inline Verdict classify_floppy(const uint8_t *data, uint64_t bytes)
{
    using namespace detail;
    if (!data) return bad("a disk image has no data");
    // The header of an Amiga executable: upstream would convert the file into a dated ADF.
    static const uint8_t hunk[8] = {0x00, 0x00, 0x03, 0xF3, 0x00, 0x00, 0x00, 0x00};
    if (bytes >= 8 && !memcmp(data, hunk, 8)) return bad("an Amiga executable is not a disk image");
    if (starts_with(data, bytes, "UAE-1ADF")) {
        if (bytes > kMaxTrackDiskBytes) return bad("an extended ADF is larger than the core reads (3 MiB)");
        return ext2(data, bytes);
    }
    if (starts_with(data, bytes, "UAE--ADF")) {
        if (bytes > kMaxTrackDiskBytes) return bad("an extended ADF is larger than the core reads (3 MiB)");
        return ext1(data, bytes);
    }
    if (bytes != kPlainAdfBytes) return bad("a disk image must be exactly 901120 bytes, or an extended ADF");
    return good(kAdf);
}

inline const char *format_name(uint8_t format)
{
    switch (format) {
    case kAdf: return "ADF";
    case kExt2: return "UAE-1ADF";
    case kExt1: return "UAE--ADF";
    case kFdi: return "FDI";
    default: return "unknown";
    }
}

}  // namespace p6_floppy

#endif
