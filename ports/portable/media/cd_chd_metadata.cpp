#include "cd_chd_metadata.h"
#include <libchdr/chd.h>
#include <initializer_list>
#include <string.h>
namespace p6_cd {
namespace {
struct Fields {
    const char *at, *end;
    bool text(const char *key, char *out, size_t capacity) {
        const auto n = strlen(key);
        if (size_t(end - at) <= n || memcmp(at, key, n) || at[n] != ':') return false;
        at += n + 1; const char *first = at;
        while (at < end && *at != ' ') ++at;
        const size_t bytes = at - first;
        if (!bytes || bytes >= capacity) return false;
        memcpy(out, first, bytes); out[bytes] = 0;
        if (at < end) ++at;
        return true;
    }
    bool number(const char *key, uint32_t &out) {
        char token[16]; if (!text(key, token, sizeof token)) return false;
        out = 0;
        for (const char *p = token; *p; ++p) {
            if (*p < '0' || *p > '9' || out > (UINT32_MAX - unsigned(*p - '0')) / 10) return false;
            out = out * 10 + unsigned(*p - '0');
        }
        return true;
    }
};
uint32_t data_size(const char *type) {
    static const struct { const char *type; uint32_t bytes; } modes[] = {
        {"MODE1",2048},{"MODE1/2048",2048},{"MODE1_RAW",2352},{"MODE1/2352",2352},
        {"MODE2",2336},{"MODE2/2336",2336},{"MODE2_FORM1",2048},{"MODE2/2048",2048},
        {"MODE2_FORM2",2324},{"MODE2/2324",2324},{"MODE2_FORM_MIX",2336},
        {"MODE2_RAW",2352},{"MODE2/2352",2352},{"CDI/2352",2352},{"AUDIO",2352}};
    for (const auto &m : modes) if (!strcmp(type, m.type)) return m.bytes;
    return 0;
}
bool sub_type(const char *s) { return !strcmp(s,"NONE") || !strcmp(s,"RW") || !strcmp(s,"RW_RAW"); }
Result record(const ChdReader &reader, uint32_t tag, unsigned index, char *text, uint32_t *length, uint8_t *flags, bool *found) {
    const auto r = reader.metadata(tag, index, text, 256, length, flags, found);
    return r == Result::Range ? Result::Unsupported : r;
}
}
Result chd_metadata(const ChdReader &reader, ChdMetadata *out)
{
    if (!out || !reader.bytes()) return Result::Invalid;
    uint32_t length = 0; uint8_t flags = 0; bool first = false, second = false;
    for (uint32_t tag : {CDROM_OLD_METADATA_TAG, GDROM_OLD_METADATA_TAG, GDROM_TRACK_METADATA_TAG, CHD_MAKE_TAG('D','V','D',' ')}) {
        const auto r = reader.metadata(tag, 0, nullptr, 0, &length, &flags, &first);
        if (r != Result::Ok) return r;
        if (first) return Result::Unsupported;
    }
    auto r = reader.metadata(CDROM_TRACK_METADATA_TAG, 0, nullptr, 0, &length, &flags, &first);
    if (r != Result::Ok) return r;
    r = reader.metadata(CDROM_TRACK_METADATA2_TAG, 0, nullptr, 0, &length, &flags, &second);
    if (r != Result::Ok) return r;
    if (first == second) return Result::Unsupported;
    const uint32_t tag = second ? CDROM_TRACK_METADATA2_TAG : CDROM_TRACK_METADATA_TAG;
    ChdMetadata candidate{}; const uint64_t units = reader.bytes() / 2448;
    for (unsigned n = 0; n <= 99; ++n) {
        char text[256]; bool found = false;
        r = record(reader, tag, n, text, &length, &flags, &found);
        if (r != Result::Ok) return r;
        if (!found) break;
        if (n == 99) return Result::Unsupported;
        if (length && !text[length - 1]) --length; // one optional terminator only
        if (!length || text[0] == ' ' || text[length - 1] == ' ') return Result::Invalid;
        for (unsigned i = 0; i < length; ++i) if (text[i] < 32 || text[i] > 126) return Result::Invalid;
        auto &t = candidate.track[n]; t.flags = flags;
        Fields f{text, text + length};
        if (!f.number("TRACK", t.number) || !f.text("TYPE", t.type, sizeof t.type) || !f.text("SUBTYPE", t.subtype, sizeof t.subtype) ||
            !f.number("FRAMES", t.frames)) return Result::Invalid;
        if (second) {
            if (!f.number("PREGAP", t.pregap) || !f.text("PGTYPE", t.pgtype, sizeof t.pgtype) || !f.text("PGSUB", t.pgsub, sizeof t.pgsub) ||
                !f.number("POSTGAP", t.postgap)) return Result::Invalid;
        } else { strcpy(t.pgtype,"MODE1"); strcpy(t.pgsub,"NONE"); }
        if (f.at != f.end || t.number != n + 1 || !t.frames) return Result::Invalid;
        t.stored_pregap = t.pgtype[0] == 'V';
        if (t.stored_pregap && (!t.pregap || t.pregap >= t.frames)) return Result::Invalid;
        t.data_bytes = data_size(t.type); t.pg_data_bytes = data_size(t.pgtype + t.stored_pregap);
        if (!t.data_bytes || !t.pg_data_bytes || !sub_type(t.subtype) || !sub_type(t.pgsub)) return Result::Unsupported;
        t.offset_frames = candidate.padded_frames; t.extra_frames = (0u - t.frames) & 3;
        const uint64_t padded = uint64_t(t.frames) + t.extra_frames;
        if (padded > units - candidate.padded_frames) return Result::Invalid;
        candidate.padded_frames += padded; ++candidate.tracks;
    }
    // ccecb1e libretro-glue.c:2719..2725: tracks consume FRAMES+extra_frames.
    // MacHawk CHD_format.cs:603..615 rounds their total to the allocated hunk.
    // Accept only the remaining suffix of that last hunk; never expose it as LBAs.
    const uint64_t per_hunk = reader.hunk_bytes() / 2448;
    const auto rounded = (candidate.padded_frames + per_hunk - 1) / per_hunk * per_hunk;
    if (units > rounded) return Result::Invalid;
    candidate.tail_frames = units - candidate.padded_frames;
    *out = candidate; return Result::Ok;
}
}
