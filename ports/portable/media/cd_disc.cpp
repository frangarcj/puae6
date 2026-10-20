#include "cd_disc.h"
#include "uae/cdrom.h" // encode_l2 of the pinned WinUAE source, not a substitute encoder
#include <limits.h>
#include <string.h>
namespace p6_cd {
Result Disc::prepare(const Layout &layout, const Binding *bindings, uint32_t count)
{
    if (!bindings || !count || count > kMaxSources || !layout.tracks || layout.tracks > kMaxTracks ||
        !layout.leadout || layout.leadout > INT_MAX - 150) return Result::Invalid;
    for (uint32_t n = 0; n < count; ++n)
        if (sources_[n].bind(bindings[n].identity, bindings[n].read, bindings[n].context) != Result::Ok) return Result::Invalid;
    uint32_t next = 0;
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const Track &t = layout.track[n];
        const bool cooked = t.mode == Mode::Mode1_2048, audio = t.mode == Mode::Audio;
        if (t.index0 != next || t.index1 < t.index0 || t.end <= t.index1 || t.end > layout.leadout || t.source >= count ||
            (t.mode != Mode::Mode1_2048 && t.mode != Mode::Mode1Raw && t.mode != Mode::Mode2Raw && !audio) ||
            t.adr != 1 || t.control > 15 || bool(t.control & 4) == audio ||
            t.synthetic_pregap > t.index1 - t.index0 || t.postgap >= t.end - t.index1 ||
            (!t.stored_pregap && t.synthetic_pregap && t.synthetic_pregap != t.index1 - t.index0) ||
            (cooked ? t.stride != 2048 : t.stride != 2352 && t.stride != 2448)) return Result::Invalid;
        const auto bytes = sources_[t.source].identity().bytes;
        const uint64_t sectors = t.end - t.postgap - (t.stored_pregap ? t.index0 + t.synthetic_pregap : t.index1);
        if (t.offset > bytes || sectors > (bytes - t.offset) / t.stride) return Result::Range;
        next = t.end;
    }
    if (next != layout.leadout) return Result::Invalid;
    layout_ = layout; count_ = count;
    return Result::Ok;
}
Result Disc::bind(const Layout &layout, const Binding *bindings, uint32_t count)
{
    if (count_) return Result::Invalid;
    // Value-init also zeroes padding in unused Source identities: Waterbox snapshots the object representation.
    Disc candidate{};
    const auto result = candidate.prepare(layout, bindings, count);
    if (result == Result::Ok) *this = candidate;
    return result;
}
Result Disc::rebind(const Layout &layout, const Binding *bindings, uint32_t count)
{
    if (!count_) return Result::Invalid;
    // Value-init also zeroes padding in unused Source identities: Waterbox snapshots the object representation.
    Disc candidate{};
    const auto result = candidate.prepare(layout, bindings, count);
    if (result != Result::Ok) return result;
    if (count != count_ || layout.tracks != layout_.tracks || layout.leadout != layout_.leadout ||
        memcmp(layout.content_id, layout_.content_id, sizeof layout.content_id)) return Result::Incompatible;
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const Track &a = layout.track[n], &b = layout_.track[n];
        if (a.index0 != b.index0 || a.index1 != b.index1 || a.end != b.end || a.source != b.source || a.stride != b.stride ||
            a.offset != b.offset || a.mode != b.mode || a.adr != b.adr || a.control != b.control || a.stored_pregap != b.stored_pregap ||
            a.synthetic_pregap != b.synthetic_pregap || a.postgap != b.postgap) return Result::Incompatible;
    }
    for (uint32_t n = 0; n < count; ++n) {
        const auto &a = sources_[n].identity(), &b = bindings[n].identity;
        if (a.bytes != b.bytes || a.sector_bytes != b.sector_bytes || memcmp(a.content_id, b.content_id, sizeof a.content_id)) return Result::Incompatible;
    }
    *this = candidate;
    return Result::Ok;
}
Result Disc::read_sector(uint32_t lba, uint32_t view, void *out, size_t capacity) const
{
    if (!count_ || !out || (view != 2048 && view != 2352 && view != 2448) || capacity < view) return Result::Invalid;
    if (lba >= layout_.leadout) return Result::Range;
    const Track *track = nullptr;
    for (uint32_t n = 0; n < layout_.tracks; ++n) if (lba < layout_.track[n].end) { track = &layout_.track[n]; break; }
    const auto &t = *track;
    const uint32_t first = t.stored_pregap ? t.index0 + t.synthetic_pregap : t.index1;
    const bool cooked = t.mode == Mode::Mode1_2048, gap = lba < first || lba >= t.end - t.postgap;
    if ((view == 2048 && (t.mode == Mode::Audio || t.mode == Mode::Mode2Raw)) ||
        (view == 2448 && (cooked || gap || t.stride != 2448))) return Result::Unsupported;
    if (view == 2352 && (cooked || (gap && t.mode != Mode::Audio)) && lba > 100 * 60 * 75 - 151)
        return Result::Unsupported; // synthesized MSF has two BCD digits, validated before I/O
    uint8_t sector[kMaxReadBytes] = {};
    if (!gap) {
        const uint64_t at = t.offset + uint64_t(lba - first) * t.stride;
        const auto result = sources_[t.source].read_at(at, sector + (cooked && view == 2352 ? 16 : 0), t.stride);
        if (result != Result::Ok) return result;
    }
    if (view == 2352 && (cooked || (gap && t.mode == Mode::Mode1Raw))) {
        encode_l2(sector, int(lba) + 150);
    }
    if (gap && t.mode == Mode::Mode2Raw) {
        // libMirage a9d6a204 sector.c:feed_data selects Form1 for empty Mode2Mixed.
        // Zero subheader/data yield zero EDC/P/Q; Form1 excludes header from ECC.
        const uint32_t absolute = lba + 150;
        auto bcd = [](uint32_t n) { return uint8_t(n / 10 * 16 + n % 10); };
        memset(sector + 1, 0xff, 10);
        sector[12] = bcd(absolute / 4500); sector[13] = bcd(absolute / 75 % 60);
        sector[14] = bcd(absolute % 75); sector[15] = 2;
    }
    const uint8_t *data = sector;
    if (view == 2048 && t.mode == Mode::Mode1Raw && !gap) {
        if (sector[15] != 1) return Result::ReadError;
        data += 16;
    }
    memcpy(out, data, view);
    return Result::Ok;
}
} // namespace p6_cd
