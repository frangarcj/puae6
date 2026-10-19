#include "cd_akiko.h"
#include <string.h>
namespace p6_cd {
Result akiko_toc(const Disc &disc, cd_toc_head *out)
{
    const auto &layout = disc.layout();
    if (!out || !layout.tracks) return Result::Invalid;
    cd_toc_head toc;
    memset(&toc, 0, sizeof toc);
    // Same ordering and fields as command_toc in pinned blkdev_cdimage.cpp.
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
Result akiko_qcode(const Disc &disc, uint32_t lba, uint8_t status, bool all,
                   void *out, size_t capacity)
{
    const size_t length = all ? SUB_CHANNEL_SIZE : SUBQ_SIZE;
    if (!out || capacity < length ||
        (status != AUDIO_STATUS_NOT_SUPPORTED && (status < AUDIO_STATUS_IN_PROGRESS || status > AUDIO_STATUS_NO_STATUS)))
        return Result::Invalid;
    uint8_t sector[kMaxReadBytes];
    const auto result = disc.read_sector(lba, 2448, sector, sizeof sector);
    if (result != Result::Ok) return result;
    if (all) memcpy(out, sector + 2352, length);
    else {
        uint8_t q[SUBQ_SIZE] = {0, status, 0, 12};
        memcpy(q + 4, sector + 2352 + SUB_ENTRY_SIZE, SUB_ENTRY_SIZE);
        memcpy(out, q, sizeof q);
    }
    return Result::Ok;
}
}
