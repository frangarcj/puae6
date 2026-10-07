// The frame around the native state (T18): what retro_serialize writes and retro_unserialize checks before it hands anything to p6_state_load.
//
// libretro asks for one size (retro_serialize_size) that never grows, writes states of exactly that size and loads blobs of the size they were
// written with (a file may be longer if somebody appended to it, and a frontend may hand over a bigger buffer). p6_state_load, on the other hand,
// wants the exact length it wrote and refuses anything else. The frame bridges the two without guessing:
//
//   [ header: 64 bytes ][ adapter section ][ native state ][ zero padding up to serialize_size ]
//
// The header names the version and the exact length of the section and of the native state, and holds the SHA-256 of the section (the native state
// carries its own checksums). Padding is part of the format: a frame whose padding is not all zero is refused, so a blob that was written to, cut and
// extended, or taken from the wrong place is never half-accepted. A blob larger than the frame needs is accepted only if what follows the native state
// is zero. Nothing here calls the engine; the adapter decides what the section holds and whether it fits the running image.
//
// The adapter section carries what the adapter keeps between two runs and the frontend has not seen the effect of yet: the disk tray (selection, open
// tray, requests not applied, the medium the drive holds, the media-event counter), the audio the frontend has not taken, and the geometry (and, for
// a frontend that cannot repeat a picture, the pixels) of the last picture, and the input memory (T17c: the Caps Lock latch and the mouse movement that did
// not fit a unit). All of it belongs to the saved timeline, so a rewind or a runahead branch leaves no input, audio or disk request of the abandoned
// future behind. What the frontend holds down is not carried: the adapter reads it afresh in every retro_run and the bridge's own edge state is in the
// native state. Every number is little-endian, whatever the host is.
#ifndef P6_LIBRETRO_STATE_FRAME_H
#define P6_LIBRETRO_STATE_FRAME_H

#include "disk_control.h"
#include "input_map.h"
#include "sha256.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <vector>

namespace p6_libretro {

constexpr size_t kStateHeaderBytes = 64;
constexpr uint32_t kStateVersion = 2;       // 2: the input memory joined the section (T17c)
constexpr size_t kSectionAlign = 16;
constexpr size_t kMaxImages = 16;                                   // P6_MAX_MEDIA: a playlist cannot name more

enum class FrameError { None, TooSmall, BadMagic, BadVersion, BadLayout, NonZeroPadding, BadChecksum };

inline const char *describe(FrameError error)
{
    switch (error) {
    case FrameError::TooSmall: return "the buffer is smaller than a state";
    case FrameError::BadMagic: return "this is not a state of this core";
    case FrameError::BadVersion: return "the state has a frame version this core does not know";
    case FrameError::BadLayout: return "the lengths in the state header do not fit the buffer";
    case FrameError::NonZeroPadding: return "the bytes after the state are not zero";
    case FrameError::BadChecksum: return "the adapter section of the state is damaged";
    default: return "";
    }
}

inline void put32(uint8_t *at, uint32_t value) { for (int i = 0; i < 4; ++i) at[i] = uint8_t(value >> (8 * i)); }
inline void put64(uint8_t *at, uint64_t value) { for (int i = 0; i < 8; ++i) at[i] = uint8_t(value >> (8 * i)); }
inline uint32_t get32(const uint8_t *at) { uint32_t value = 0; for (int i = 3; i >= 0; --i) value = value << 8 | at[i]; return value; }
inline uint64_t get64(const uint8_t *at) { uint64_t value = 0; for (int i = 7; i >= 0; --i) value = value << 8 | at[i]; return value; }

inline size_t align_up(size_t value, size_t align) { return (value + align - 1) / align * align; }

// The adapter's own part of a state. `picture` holds the pixels of the last picture only for a frontend that cannot repeat one (can_dupe false).
struct Section {
    DiskTray::Snapshot tray;
    uint64_t event_sequence = 0;
    std::vector<int16_t> pending;           // interleaved stereo
    uint32_t last_width = 0, last_height = 0;
    std::vector<uint8_t> picture;           // last_width * last_height * 4 bytes, or empty
    InputMemory input;
};

// The most a section can take for the given limits; the serialization size is built from it, so it must hold for every Section the adapter can make.
inline size_t section_bound(size_t pending_values, size_t picture_bytes)
{
    return align_up(4 + 4 * kMaxImages + 4 + 4 + 4 + 8 + 4 + 2 * pending_values + 4 + 4 + 4 + picture_bytes, kSectionAlign);
}

inline void encode_section(const Section &section, std::vector<uint8_t> *out)
{
    out->clear();
    auto u32 = [&](uint32_t value) { uint8_t b[4]; put32(b, value); out->insert(out->end(), b, b + 4); };
    u32(uint32_t(section.tray.media.size()));
    for (uint32_t medium : section.tray.media) u32(medium);
    u32(section.tray.selected);
    u32((section.tray.ejected ? 1u : 0u) | (section.tray.eject_seen ? 2u : 0u) | (section.tray.dirty ? 4u : 0u));
    u32(section.tray.in_drive);
    uint8_t sequence[8];
    put64(sequence, section.event_sequence);
    out->insert(out->end(), sequence, sequence + 8);
    u32(uint32_t(section.pending.size()));
    for (int16_t value : section.pending) { out->push_back(uint8_t(uint16_t(value))); out->push_back(uint8_t(uint16_t(value) >> 8)); }
    u32(section.last_width);
    u32(section.last_height);
    u32(uint32_t(section.picture.size()));
    out->insert(out->end(), section.picture.begin(), section.picture.end());
    u32((section.input.caps_latched ? 1u : 0u) | (section.input.caps_held ? 2u : 0u));
    u32(uint32_t(section.input.mouse_rx));
    u32(uint32_t(section.input.mouse_ry));
    out->resize(align_up(out->size(), kSectionAlign), 0);
}

// Reads a section back. Every count is checked against the bytes that remain and against the limits before anything is allocated; the padding
// must be exactly the zero bytes that finish the alignment. Returns false (with `why`) for anything else.
inline bool decode_section(const uint8_t *data, size_t size, size_t max_pending_values, uint32_t max_width, uint32_t max_height, Section *out,
                           const char **why)
{
    size_t at = 0;
    auto fail = [&](const char *text) { *why = text; return false; };
    auto room = [&](size_t bytes) { return size - at >= bytes; };
    *out = Section();
    if (!room(4)) return fail("the section is cut short");
    const uint32_t images = get32(data + at);
    at += 4;
    if (images > kMaxImages) return fail("too many images in the tray");
    if (!room(size_t(images) * 4 + 4 + 4 + 4 + 8 + 4)) return fail("the section is cut short");
    for (uint32_t i = 0; i < images; ++i, at += 4) out->tray.media.push_back(get32(data + at));
    out->tray.selected = get32(data + at);
    at += 4;
    const uint32_t flags = get32(data + at);
    at += 4;
    if (flags & ~7u) return fail("unknown tray flags");
    out->tray.ejected = flags & 1;
    out->tray.eject_seen = flags & 2;
    out->tray.dirty = flags & 4;
    out->tray.in_drive = get32(data + at);
    at += 4;
    out->event_sequence = get64(data + at);
    at += 8;
    const uint32_t pending = get32(data + at);
    at += 4;
    if (pending % 2 || pending > max_pending_values) return fail("the pending audio is not whole stereo frames within the limit");
    if (!room(size_t(pending) * 2 + 12)) return fail("the section is cut short");
    out->pending.resize(pending);
    for (uint32_t i = 0; i < pending; ++i, at += 2) out->pending[i] = int16_t(uint16_t(data[at] | data[at + 1] << 8));
    out->last_width = get32(data + at);
    out->last_height = get32(data + at + 4);
    const uint32_t picture = get32(data + at + 8);
    at += 12;
    if (out->last_width > max_width || out->last_height > max_height) return fail("the last picture is larger than the machine draws");
    if (picture && picture != uint64_t(out->last_width) * out->last_height * 4) return fail("the picture does not match its size");
    if (!room(picture)) return fail("the section is cut short");
    out->picture.assign(data + at, data + at + picture);
    at += picture;
    if (!room(12)) return fail("the section is cut short");
    const uint32_t input_flags = get32(data + at);
    const int32_t rx = int32_t(get32(data + at + 4)), ry = int32_t(get32(data + at + 8));
    at += 12;
    if (input_flags & ~3u) return fail("unknown input flags");
    if (rx < -kMouseBacklog || rx > kMouseBacklog || ry < -kMouseBacklog || ry > kMouseBacklog) return fail("the mouse movement owed is beyond the backlog");
    out->input.caps_latched = input_flags & 1;
    out->input.caps_held = input_flags & 2;
    out->input.mouse_rx = rx;
    out->input.mouse_ry = ry;
    if (align_up(at, kSectionAlign) != size) return fail("the section has bytes it does not describe");
    for (; at < size; ++at)
        if (data[at]) return fail("the section padding is not zero");
    return true;
}

inline void write_header(uint8_t *out, uint64_t native_bytes, const uint8_t *section, size_t section_bytes)
{
    memset(out, 0, kStateHeaderBytes);
    memcpy(out, "P6LRSTAT", 8);
    put32(out + 8, kStateVersion);
    put32(out + 12, uint32_t(kStateHeaderBytes));
    put64(out + 16, native_bytes);
    put32(out + 24, uint32_t(section_bytes));
    p6_sha256_hash(section, section_bytes, out + 32);
}

struct Frame {
    const uint8_t *section = nullptr;
    size_t section_bytes = 0;
    const uint8_t *native = nullptr;
    size_t native_bytes = 0;
};

// Checks the header, the lengths, the section checksum and the zero padding of a blob of `size` bytes. Overflow-safe: every length is compared with what
// remains of the buffer, never added up first.
inline FrameError parse_frame(const uint8_t *data, size_t size, size_t max_section_bytes, Frame *out)
{
    if (size < kStateHeaderBytes) return FrameError::TooSmall;
    if (memcmp(data, "P6LRSTAT", 8) != 0) return FrameError::BadMagic;
    if (get32(data + 8) != kStateVersion || get32(data + 12) != kStateHeaderBytes) return FrameError::BadVersion;
    for (size_t i = 28; i < 32; ++i)        // reserved
        if (data[i]) return FrameError::BadLayout;
    const uint64_t native = get64(data + 16);
    const uint32_t section = get32(data + 24);
    const size_t room = size - kStateHeaderBytes;
    if (section % kSectionAlign || section > max_section_bytes || section > room) return FrameError::BadLayout;
    if (native == 0 || native > room - section) return FrameError::BadLayout;
    uint8_t digest[32];
    p6_sha256_hash(data + kStateHeaderBytes, section, digest);
    if (memcmp(digest, data + 32, 32) != 0) return FrameError::BadChecksum;
    for (size_t at = kStateHeaderBytes + section + size_t(native); at < size; ++at)
        if (data[at]) return FrameError::NonZeroPadding;
    out->section = data + kStateHeaderBytes;
    out->section_bytes = section;
    out->native = data + kStateHeaderBytes + section;
    out->native_bytes = size_t(native);
    return FrameError::None;
}

}  // namespace p6_libretro

#endif
