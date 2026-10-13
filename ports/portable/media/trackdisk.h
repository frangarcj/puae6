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
//   FDI         "Formatted Disk Image file" (T57b, read by upstream's fdi2raw.cpp): a header of 512 bytes (version at 140, from 2.1 the CRC-32 of the first 508 bytes at 508, the last
//               cylinder at 142 and head at 144, a {type, size} pair per track from 152) and the tracks. Of the track types of the format the core accepts three: 0x00 empty, 0x01 an
//               Amiga double-density track (eleven sectors of data, the reader builds the MFM) and 0x80..0xBF a low-level track (the flux transitions of a revolution, with the weak
//               bits). The other types (the PC and Atari forms, the sector-described and the raw tracks) are refused here: fdi2raw reads them without looking at where the data ends.
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

inline uint32_t be32(const uint8_t *p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }

// CRC-32 (IEEE, reflected, as zlib and crc32.cpp), bit by bit: it runs once over 508 bytes.
inline uint32_t crc32_ieee(const uint8_t *p, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; ++i) {
        crc ^= p[i];
        for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

constexpr uint32_t kMaxFdiPulses = 262144;        // fdi2raw.cpp FDI_MAX_PULSES: a double-density revolution has 100000 to 200000

// The data of a low-level FDI track, `length` bytes at `t`: u32 pulses, four u24 descriptors {average, minimum, maximum, index} of (mode << 22) | length, then the streams in that order
// from byte 16. Mirrors decode_lowlevel_track (patch 0332): minimum and maximum come together or not at all; a stream is in the plain form (mode 0: `pulses` big-endian words, read in place and so
// aligned) or the Huffman form (mode 1: its content is checked by the decoder, with bounds, when the track is read).
inline const char *fdi_lowlevel(const uint8_t *t, uint64_t length)
{
    if (length < 16) return "an FDI low-level track is shorter than its own header";
    const uint32_t pulses = be32(t);
    if (pulses < 2 || pulses > kMaxFdiPulses) return "an FDI low-level track has no pulses or more than the core reads (262144)";
    uint64_t pos = 16;
    for (int n = 0; n < 4; ++n) {
        const uint32_t desc = be24(t + 4 + 3 * n), mode = desc >> 22, size = desc & 0x3FFFFF;
        if ((n == 1 || n == 2) && !(be24(t + 7) && be24(t + 10))) continue;
        if (pos > length || mode > 1 || size > length - pos || (n == 0 && !size)) return "an FDI low-level track has a stream outside the track or of a form the core does not read";
        if (mode == 0 && uint64_t(pulses) * 2 <= size && (size < uint64_t(pulses) * 4 || (pos & 3))) return "an FDI low-level track has a plain stream that is short or not aligned";
        pos += size;
    }
    return nullptr;
}

inline Verdict fdi(const uint8_t *d, uint64_t bytes)
{
    if (bytes < 512) return bad("an FDI is cut short in its header");
    if (d[140] != 1 && d[140] != 2) return bad("an FDI of a version the core does not read (1 and 2 are)");
    if (d[140] * 256 + d[141] >= 2 * 256 + 1 && crc32_ieee(d, 508) != be32(d + 508)) return bad("an FDI header whose checksum is wrong");
    const uint64_t tracks = (uint64_t(be16(d + 142)) + 1) * (uint64_t(d[144]) + 1);
    if (tracks > kMaxTracks) return bad("an FDI has more tracks than a drive's 166");
    uint64_t offset = 512;
    for (uint64_t i = 0; i < tracks; ++i) {
        const uint32_t type = d[152 + 2 * i], size = d[153 + 2 * i];
        uint64_t length;
        if (type == 1) length = uint64_t(size & 15) * 512;
        else if ((type & 0xC0) == 0x80) length = ((uint64_t(type & 0x3F) << 8) | size) * 256;
        else length = uint64_t(size) * 256;
        if (type == 1 && ((size & 15) != 11 || (size >> 4) > 10)) return bad("an FDI Amiga track that is not eleven sectors starting at a sector of the track");
        if (type != 0 && type != 1 && (type & 0xC0) != 0x80) return bad("an FDI track of a type the core does not read (only empty, Amiga and low-level tracks are)");
        if (offset + length > bytes) return bad("an FDI track lies beyond the end of the file");
        if ((type & 0xC0) == 0x80)
            if (const char *why = fdi_lowlevel(d + offset, length)) return bad(why);
        offset += length;
    }
    return good(kFdi);
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
    if (starts_with(data, bytes, "Formatted Disk Image file")) {
        if (bytes > kMaxTrackDiskBytes) return bad("an FDI is larger than the core reads (3 MiB)");
        return fdi(data, bytes);
    }
    if (bytes != kPlainAdfBytes) return bad("a disk image must be exactly 901120 bytes, an extended ADF or an FDI");
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
