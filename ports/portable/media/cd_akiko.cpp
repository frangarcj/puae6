#include "cd_akiko.h"
#include "cd_read.h"
#include <string.h>
namespace p6_cd {
Result akiko_toc(const Disc &disc, cd_toc_head *out)
{
    const auto &layout = disc.layout();
    if (!out || !layout.tracks) return Result::Invalid;
    cd_toc_head toc;
    memset(&toc, 0, sizeof toc);
    // Same legacy ordering/fields; multi-session projects all global tracks and
    // the final leadout. These aggregate A0/A1/A2 are not a physical lead-in.
    toc.first_track = toc.first_track_offset = 1;
    toc.last_track = toc.last_track_offset = toc.tracks = int(layout.tracks);
    toc.points = int(layout.tracks) + 3;
    toc.lastaddress = int(layout.leadout);
    uint8_t control = 4;
    for (uint32_t i = 0; i < layout.tracks; ++i)
        if (!(layout.track[i].control & 4)) control = 0;
    toc.toc[0].adr = 1; toc.toc[0].control = control;
    toc.toc[0].point = 0xa0; toc.toc[0].track = 1;
    for (uint32_t i = 0; i < layout.tracks; ++i) {
        auto &entry = toc.toc[i + 1];
        const auto &track = layout.track[i];
        entry.adr = track.adr; entry.control = track.control;
        entry.track = entry.point = uint8_t(i + 1);
        entry.paddress = int(track.index1);
    }
    for (unsigned i = 0; i < 2; ++i) {
        auto &entry = toc.toc[layout.tracks + 1 + i];
        entry.adr = 1; entry.control = control; entry.point = uint8_t(0xa1 + i);
        entry.paddress = int(layout.leadout);
        if (!i) entry.track = uint8_t(layout.tracks);
    }
    *out = toc;
    return Result::Ok;
}
Result akiko_session_toc(const Disc &disc, uint32_t ordinal, SessionToc *out)
{
    const auto &layout = disc.layout();
    if (!out || !layout.tracks) return Result::Invalid;
    if (!layout.sessions) return Result::Unsupported;
    if (!ordinal || ordinal > layout.sessions) return Result::Range;
    SessionToc next{}; next.session = layout.session[ordinal - 1];
    const auto &s = next.session; auto &toc = next.toc;
    const uint32_t count = uint32_t(s.last_track) - s.first_track + 1;
    toc.first_track = s.first_track; toc.last_track = s.last_track;
    toc.first_track_offset = 1; toc.last_track_offset = toc.tracks = int(count);
    toc.points = int(count) + 3;
    toc.firstaddress = int(layout.track[s.first_track - 1].index0); toc.lastaddress = int(s.a2);
    uint8_t control = 4;
    for (uint32_t n = s.first_track - 1; n < s.last_track; ++n)
        if (!(layout.track[n].control & 4)) control = 0;
    toc.toc[0].adr = 1; toc.toc[0].control = control;
    toc.toc[0].point = 0xa0; toc.toc[0].track = s.first_track;
    for (uint32_t n = 0; n < count; ++n) {
        const auto &track = layout.track[s.first_track - 1 + n]; auto &entry = toc.toc[n + 1];
        entry.adr = track.adr; entry.control = track.control;
        entry.track = entry.point = uint8_t(s.first_track + n); entry.paddress = int(track.index1);
    }
    for (uint32_t n = 0; n < 2; ++n) {
        auto &entry = toc.toc[count + 1 + n];
        entry.adr = 1; entry.control = control; entry.point = uint8_t(0xa1 + n);
        entry.paddress = int(s.a2); if (!n) entry.track = s.last_track;
    }
    *out = next; return Result::Ok;
}
Result akiko_qcode(const Disc &disc, uint32_t lba, uint8_t status, bool all,
                   void *out, size_t capacity)
{
    const size_t length = all ? SUB_CHANNEL_SIZE : SUBQ_SIZE;
    if (!out || capacity < length ||
        (status != AUDIO_STATUS_NOT_SUPPORTED && (status < AUDIO_STATUS_IN_PROGRESS || status > AUDIO_STATUS_NO_STATUS)))
        return Result::Invalid;
    uint8_t subchannels[SUB_CHANNEL_SIZE];
    const auto result = read_subchannel(disc, lba, subchannels, sizeof subchannels);
    if (result != Result::Ok) return result;
    if (all) memcpy(out, subchannels, length);
    else {
        uint8_t q[SUBQ_SIZE] = {0, status, 0, 12};
        memcpy(q + 4, subchannels + SUB_ENTRY_SIZE, SUB_ENTRY_SIZE);
        memcpy(out, q, sizeof q);
    }
    return Result::Ok;
}
}
