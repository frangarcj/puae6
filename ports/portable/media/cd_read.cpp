#include "cd_read.h"
#include "cd_subchannel.h"
#include <string.h>
namespace p6_cd {
Result describe_sector(const Disc &disc, uint32_t lba, SectorInfo *out)
{
    const auto &layout = disc.layout();
    if (!out || !layout.tracks) return Result::Invalid;
    if (lba >= layout.leadout) return Result::Range;
    uint32_t n = 0;
    while (lba >= layout.track[n].end) ++n;
    const auto &t = layout.track[n];
    const auto first = t.stored_pregap ? t.index0 + t.synthetic_pregap : t.index1;
    const bool gap = lba < first || lba >= t.end - t.postgap;
    const bool audio_prefix = n && (t.control & 4) && layout.track[n - 1].mode == Mode::Audio &&
                              lba < t.index1 && t.index1 - lba > 150;
    *out = {t, n + 1, t.stride == 2448 && !gap, lba < t.index1 || lba >= t.end - t.postgap, gap && audio_prefix};
    if (audio_prefix) out->effective.control = layout.track[n - 1].control;
    return Result::Ok;
}
Result read_subchannel(const Disc &disc, uint32_t lba, void *out, size_t capacity)
{
    if (!out || capacity < 96) return Result::Invalid;
    SectorInfo sector;
    auto result = describe_sector(disc, lba, &sector);
    if (result != Result::Ok) return result;
    if (sector.stored_subs) {
        uint8_t bytes[kMaxReadBytes];
        result = disc.read_sector(lba, 2448, bytes, sizeof bytes);
        if (result == Result::Ok) memcpy(out, bytes + 2352, 96);
        return result;
    }
    return synth_subchannel(sector.effective, sector.number, lba, sector.pause, out, capacity);
}
Result canonical_read(const Disc &disc, uint32_t lba, uint32_t view, void *out, size_t capacity)
{
    if (!out || capacity < view || (view != 2048 && view != 2352 && view != 2448)) return Result::Invalid;
    SectorInfo sector;
    auto result = describe_sector(disc, lba, &sector);
    if (result != Result::Ok) return result;
    if (sector.audio_gap && view == 2048) return Result::Unsupported;
    if (view != 2448 && !sector.audio_gap) return disc.read_sector(lba, view, out, capacity);
    if (sector.stored_subs) return disc.read_sector(lba, view, out, capacity);
    uint8_t bytes[kMaxReadBytes] = {};
    if (view == 2448) {
        result = read_subchannel(disc, lba, bytes + 2352, 96);
        if (result != Result::Ok) return result;
    }
    // The synthesized prefix of a long audio->data pregap is audio silence,
    // matching its effective CONTROL; the final150 frames use the data mode.
    if (!sector.audio_gap) result = disc.read_sector(lba, 2352, bytes, 2352);
    if (result == Result::Ok) memcpy(out, bytes, view);
    return result;
}
}
