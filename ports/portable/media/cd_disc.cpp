#include "cd_disc.h"
#include "uae/cdrom.h" // encode_l2 of the pinned WinUAE source, not a substitute encoder
#include <limits.h>
#include <string.h>
namespace p6_cd {
namespace {
using R = Track::Regions;
bool mode_control(Mode mode, uint32_t control) {
    return (mode == Mode::Mode1_2048 || mode == Mode::Mode1Raw || mode == Mode::Mode2Raw || mode == Mode::Audio) &&
        control <= 15 && bool(control & 4) != (mode == Mode::Audio);
}
bool packing(Mode mode, uint32_t stride) { return mode == Mode::Mode1_2048 ? stride == 2048 : stride == 2352 || stride == 2448; }
bool regions(const Track &t) {
    const auto &r = t.regions; const auto f = r.flags;
    if (!f) return r.pregap_mode == Mode::Mode1_2048 && !r.pregap_control;
    if (!(f & R::Explicit) || (f & ~63u)) return false;
    const bool pregap = t.index1 > t.index0, stored = t.stored_pregap && t.index1-t.index0 > t.synthetic_pregap;
    if (pregap ? !mode_control(r.pregap_mode,r.pregap_control) || (stored && !packing(r.pregap_mode,t.stride)) :
        r.pregap_mode != Mode::Mode1_2048 || r.pregap_control) return false;
    if ((f & (R::ProgramSub96|R::PregapSub96|R::PostgapSub96)) && t.stride != 2448) return false;
    return !((f & R::PregapSub96) && !stored) && !((f & (R::PostgapStored|R::PostgapIndex2)) && !t.postgap) &&
        !((f & R::PostgapSub96) && !(f & R::PostgapStored));
}
bool sessions(const Layout &layout)
{
    if (layout.sessions > layout.tracks) return false;
    uint32_t first = 1, previous_a2 = 0;
    for (uint32_t n = 0; n < kMaxTracks; ++n) {
        const auto &s = layout.session[n];
        if (n >= layout.sessions) {
            if (s.first_track || s.last_track || s.format || s.cue0 || s.a2) return false;
            continue;
        }
        if (s.first_track != first || s.last_track < first || s.last_track > layout.tracks ||
            (s.format != 0 && s.format != 0x10 && s.format != 0x20) ||
            (n ? int64_t(s.cue0) < previous_a2 : s.cue0 > 0) ||
            int64_t(s.cue0) > layout.track[first - 1].index0 ||
            s.a2 != layout.track[s.last_track - 1].end) return false;
        first = uint32_t(s.last_track) + 1; previous_a2 = s.a2;
    }
    return !layout.sessions || (first == layout.tracks + 1 && previous_a2 == layout.leadout);
}
}

Result Disc::prepare(const Layout &layout, const Binding *bindings, uint32_t count)
{
    if (!bindings || !count || count > kMaxSources || !layout.tracks || layout.tracks > kMaxTracks ||
        !layout.leadout || layout.leadout > INT_MAX - 150 || !sessions(layout)) return Result::Invalid;
    for (uint32_t n = 0; n < count; ++n)
        if (sources_[n].bind(bindings[n].identity, bindings[n].read, bindings[n].context) != Result::Ok) return Result::Invalid;
    uint32_t next = 0, session = 0;
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const Track &t = layout.track[n];
        const bool boundary = n && layout.sessions && n + 1 == layout.session[session].first_track;
        if ((boundary ? t.index0 < next : t.index0 != next) || t.index1 < t.index0 || t.end <= t.index1 || t.end > layout.leadout || t.source >= count ||
            !mode_control(t.mode,t.control) || t.adr != 1 ||
            t.synthetic_pregap > t.index1 - t.index0 || t.postgap >= t.end - t.index1 ||
            (!t.stored_pregap && t.synthetic_pregap && t.synthetic_pregap != t.index1 - t.index0) ||
            !packing(t.mode,t.stride) || !regions(t)) return Result::Invalid;
        const auto bytes = sources_[t.source].identity().bytes;
        const uint64_t sectors = t.end - ((t.regions.flags & R::PostgapStored) ? 0 : t.postgap) - (t.stored_pregap ? t.index0 + t.synthetic_pregap : t.index1);
        if (t.offset > bytes || sectors > (bytes - t.offset) / t.stride) return Result::Range;
        next = t.end;
        if (layout.sessions && n + 1 == layout.session[session].last_track) ++session;
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
    if (count != count_ || layout.tracks != layout_.tracks || layout.leadout != layout_.leadout || layout.sessions != layout_.sessions ||
        memcmp(layout.content_id, layout_.content_id, sizeof layout.content_id)) return Result::Incompatible;
    for (uint32_t n = 0; n < layout.sessions; ++n) {
        const auto &a = layout.session[n], &b = layout_.session[n];
        if (a.first_track != b.first_track || a.last_track != b.last_track || a.format != b.format ||
            a.cue0 != b.cue0 || a.a2 != b.a2) return Result::Incompatible;
    }
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const Track &a = layout.track[n], &b = layout_.track[n];
        if (a.index0 != b.index0 || a.index1 != b.index1 || a.end != b.end || a.source != b.source || a.stride != b.stride ||
            a.offset != b.offset || a.mode != b.mode || a.adr != b.adr || a.control != b.control || a.stored_pregap != b.stored_pregap ||
            a.synthetic_pregap != b.synthetic_pregap || a.postgap != b.postgap ||
            a.regions.flags != b.regions.flags || a.regions.pregap_mode != b.regions.pregap_mode ||
            a.regions.pregap_control != b.regions.pregap_control) return Result::Incompatible;
    }
    for (uint32_t n = 0; n < count; ++n) {
        const auto &a = sources_[n].identity(), &b = bindings[n].identity;
        if (a.bytes != b.bytes || a.sector_bytes != b.sector_bytes || memcmp(a.content_id, b.content_id, sizeof a.content_id)) return Result::Incompatible;
    }
    *this = candidate;
    return Result::Ok;
}
Result Disc::describe(uint32_t lba, SectorInfo *out) const
{
    if (!count_ || !out) return Result::Invalid;
    if (lba >= layout_.leadout) return Result::Range;
    uint32_t n = 0; while (lba >= layout_.track[n].end) ++n;
    const auto &t = layout_.track[n]; const auto f = t.regions.flags;
    if (lba < t.index0) return Result::Range; // Inter-session leadout/lead-in is not a pregap or payload.
    const bool pregap = lba < t.index1, postgap = lba >= t.end-t.postgap;
    const uint32_t first = t.stored_pregap ? t.index0+t.synthetic_pregap : t.index1;
    const bool synthetic = lba < first || (postgap && !(f & R::PostgapStored));
    SectorInfo next{t,n+1,t.stride == 2448 && !synthetic,pregap || postgap,false,synthetic,0};
    if (!synthetic) next.source_offset = t.offset + uint64_t(lba-first)*t.stride;
    if (f & R::Explicit) {
        if (pregap) { next.effective.mode=t.regions.pregap_mode; next.effective.control=uint8_t(t.regions.pregap_control); }
        next.stored_subs = !synthetic && (f & (pregap ? R::PregapSub96 : postgap ? R::PostgapSub96 : R::ProgramSub96));
        next.audio_gap = synthetic && next.effective.mode == Mode::Audio;
    } else {
        bool first_in_session = false;
        for (uint32_t s = 1; s < layout_.sessions; ++s)
            if (n + 1 == layout_.session[s].first_track) first_in_session = true;
        const bool audio_prefix = n && !first_in_session && (t.control & 4) && layout_.track[n-1].mode == Mode::Audio && pregap && t.index1-lba > 150;
        if (audio_prefix) next.effective.control=layout_.track[n-1].control;
        next.audio_gap = synthetic && audio_prefix;
    }
    *out = next; return Result::Ok;
}
Result Disc::read_sector(uint32_t lba, uint32_t view, void *out, size_t capacity) const
{
    if (!count_ || !out || (view != 2048 && view != 2352 && view != 2448) || capacity < view) return Result::Invalid;
    SectorInfo info; const auto described = describe(lba,&info);
    if (described != Result::Ok) return described;
    const auto &t = info.effective;
    const bool cooked = t.mode == Mode::Mode1_2048, gap = info.synthetic;
    if ((view == 2048 && (t.mode == Mode::Audio || t.mode == Mode::Mode2Raw)) ||
        (view == 2448 && !info.stored_subs)) return Result::Unsupported;
    if (view == 2352 && (cooked || (gap && t.mode != Mode::Audio)) && lba > 100 * 60 * 75 - 151)
        return Result::Unsupported; // synthesized MSF has two BCD digits, validated before I/O
    uint8_t sector[kMaxReadBytes] = {};
    if (!gap) {
        const auto result = sources_[t.source].read_at(info.source_offset, sector + (cooked && view == 2352 ? 16 : 0), t.stride);
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
