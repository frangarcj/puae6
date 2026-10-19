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
// The adapter section carries what the adapter keeps between two runs and the frontend has not seen the effect of yet: the disk tray (the list by medium, selection, open
// tray, requests not applied, the medium the drive holds, the media-event counter; since T17e the list may be shorter than the live one, see DiskTray::fits), the audio the frontend has not taken, and the geometry (and, for
// a frontend that cannot repeat a picture, the pixels, in the format the session presents: 4 or 2 bytes a pixel) of the last picture, and the input memory (T17c: the Caps Lock latch and the mouse movement that did
// not fit a unit; T31d: the mapper's, turbo's and stick mouse's memory, the D-Pad mode and the presentation toggles). All of it belongs to the saved timeline, so a rewind or a runahead branch leaves no input, audio or disk request of the abandoned
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
constexpr uint32_t kStateVersion = 7;       // 2: the input memory joined the section (T17c); 3: the list of the tray may end before the live one and hold slots with no disk (T17e); 4: the input memory grew the mapper's and the turbo's (T31d); 5: the statusbar's flag and the message of the last action (T31b); 6: the virtual keyboard's memory (T31a); 7: independent second mouse backlog/fractions (T70b)
constexpr size_t kSectionAlign = 16;
constexpr size_t kMaxImages = 16;                                   // P6_MAX_MEDIA: a playlist cannot name more
constexpr size_t kInputBytes = 14 * 4;                               // the input memory in the section: fourteen 32-bit words

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
    return align_up(4 + 4 * kMaxImages + 4 + 4 + 4 + 8 + 4 + 2 * pending_values + 4 + 4 + 4 + picture_bytes + kInputBytes, kSectionAlign);
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
    const InputMemory &in = section.input;
    u32((in.caps_latched ? 1u : 0u) | (in.caps_held ? 2u : 0u) | (in.dpad_mouse ? 4u : 0u) | (in.turbo_toggled ? 8u : 0u) | (in.crop_toggled ? 16u : 0u) |
        (in.aspect_toggled ? 32u : 0u) | (in.statusbar_shown ? 64u : 0u));
    u32(uint32_t(in.mouse_rx));
    u32(uint32_t(in.mouse_ry));
    u32(uint32_t(in.turbo_phase) | uint32_t(in.reset_hold) << 8);
    u32(in.held);
    u32(uint32_t(uint16_t(in.host_frac[0])) | uint32_t(uint16_t(in.host_frac[1])) << 16);
    u32(uint32_t(uint16_t(in.stick_frac[0][0])) | uint32_t(uint16_t(in.stick_frac[0][1])) << 16);
    u32(uint32_t(uint16_t(in.stick_frac[1][0])) | uint32_t(uint16_t(in.stick_frac[1][1])) << 16);
    u32(uint32_t(in.message) | uint32_t(in.message_left) << 8);
    u32((in.vkbd_open ? 1u : 0u) | (in.vkbd_opaque ? 2u : 0u) | uint32_t(in.vk_cursor) << 8 | uint32_t(in.vk_held) << 16);
    u32(uint32_t(in.vk_latch) | uint32_t(in.vk_repeat) << 8);
    u32(uint32_t(in.mouse2_rx));
    u32(uint32_t(in.mouse2_ry));
    u32(uint32_t(uint16_t(in.host2_frac[0])) | uint32_t(uint16_t(in.host2_frac[1])) << 16);
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
    if (picture && picture != uint64_t(out->last_width) * out->last_height * 4 && picture != uint64_t(out->last_width) * out->last_height * 2)
        return fail("the picture does not match its size");       // 4 bytes a pixel (XRGB8888) or 2 (RGB565, T31c)
    if (!room(picture)) return fail("the section is cut short");
    out->picture.assign(data + at, data + at + picture);
    at += picture;
    if (!room(kInputBytes)) return fail("the section is cut short");
    uint32_t word[14];
    for (uint32_t &w : word) {
        w = get32(data + at);
        at += 4;
    }
    if (word[0] & ~127u) return fail("unknown input flags");
    const int32_t rx = int32_t(word[1]), ry = int32_t(word[2]);
    if (rx < -kMouseBacklog || rx > kMouseBacklog || ry < -kMouseBacklog || ry > kMouseBacklog) return fail("the mouse movement owed is beyond the backlog");
    if ((word[3] & 0xFF) >= kTurboCycle || (word[3] >> 8) > kResetHoldUnits || (word[3] >> 16)) return fail("the turbo phase or the soft reset count is out of range");
    if (word[4] & ~(((UINT32_C(1) << kHotkeyCount) - 1) << kHotkeyBit | ((UINT32_C(1) << kMapperInputs) - 1))) return fail("unknown inputs held");
    auto fraction = [&](uint32_t half, int limit, int16_t *value) {
        const int v = int16_t(uint16_t(half));
        if (v < -limit || v > limit) return false;
        *value = int16_t(v);
        return true;
    };
    if (!fraction(word[5], 99, &out->input.host_frac[0]) || !fraction(word[5] >> 16, 99, &out->input.host_frac[1]) ||
        !fraction(word[6], 999, &out->input.stick_frac[0][0]) || !fraction(word[6] >> 16, 999, &out->input.stick_frac[0][1]) ||
        !fraction(word[7], 999, &out->input.stick_frac[1][0]) || !fraction(word[7] >> 16, 999, &out->input.stick_frac[1][1]))
        return fail("a fraction of a mouse count is out of range");
    if ((word[8] & 0xFF) > kMessageReset || ((word[8] >> 8) & 0xFF) > kMessageUnits || (word[8] >> 16) || (((word[8] & 0xFF) == 0) != (((word[8] >> 8) & 0xFF) == 0)))
        return fail("the message of the last action is out of range");
    const uint32_t cursor = (word[9] >> 8) & 0xFF, keyheld = (word[9] >> 16) & 0xFF, latch = word[10] & 0xFF;
    if ((word[9] & ~3u & 0xFF) || (word[9] >> 24) || cursor >= kVkbdKeys || (keyheld != kVkbdNoKey && vk_find(uint8_t(keyheld)) < 0) || latch >= (1u << kVkLatchCount) || (word[10] >> 16))
        return fail("the virtual keyboard's memory is out of range");
    if (!(word[9] & 1) && (keyheld != kVkbdNoKey || latch || ((word[10] >> 8) & 0xFF))) return fail("a closed virtual keyboard holds a key");
    out->input.vkbd_open = word[9] & 1;
    out->input.vkbd_opaque = word[9] & 2;
    out->input.vk_cursor = uint8_t(cursor);
    out->input.vk_held = uint8_t(keyheld);
    out->input.vk_latch = uint8_t(latch);
    out->input.vk_repeat = uint8_t((word[10] >> 8) & 0xFF);
    out->input.caps_latched = word[0] & 1;
    out->input.caps_held = word[0] & 2;
    out->input.dpad_mouse = word[0] & 4;
    out->input.turbo_toggled = word[0] & 8;
    out->input.crop_toggled = word[0] & 16;
    out->input.aspect_toggled = word[0] & 32;
    out->input.statusbar_shown = word[0] & 64;
    out->input.message = uint8_t(word[8] & 0xFF);
    out->input.message_left = uint8_t((word[8] >> 8) & 0xFF);
    out->input.mouse2_rx = int32_t(word[11]);
    out->input.mouse2_ry = int32_t(word[12]);
    if (out->input.mouse2_rx < -kMouseBacklog || out->input.mouse2_rx > kMouseBacklog ||
        out->input.mouse2_ry < -kMouseBacklog || out->input.mouse2_ry > kMouseBacklog)
        return fail("the second mouse movement owed is beyond the backlog");
    if (!fraction(word[13], 99, &out->input.host2_frac[0]) || !fraction(word[13] >> 16, 99, &out->input.host2_frac[1]))
        return fail("a fraction of a second mouse count is out of range");
    out->input.mouse_rx = rx;
    out->input.mouse_ry = ry;
    out->input.turbo_phase = uint8_t(word[3] & 0xFF);
    out->input.reset_hold = uint8_t(word[3] >> 8);
    out->input.held = word[4];
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
