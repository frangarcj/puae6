#include "cd_nrg_metadata.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
using namespace p6_cd;
struct Input { std::vector<uint8_t> tail; uint64_t base; int fault; unsigned calls = 0; };
static bool read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
{
    auto &s = *const_cast<Input *>(static_cast<const Input *>(ctx)); ++s.calls; *got = 0;
    if (length > 256 || offset < s.base || offset - s.base > s.tail.size() || length > s.tail.size() - (offset - s.base)) abort();
    if (s.fault == 1) return false;
    memcpy(out, s.tail.data() + (offset - s.base), length);
    *got = s.fault == 2 ? length - 1 : s.fault == 3 ? length + 1 : length; return true;
}
int main(int argc, char **argv)
{
    if (argc != 4) return 64;
    Input s{{}, strtoull(argv[2], nullptr, 10), atoi(argv[3])};
    FILE *f = fopen(argv[1], "rb"); if (!f) return 66;
    for (int c; (c = fgetc(f)) != EOF;) s.tail.push_back(uint8_t(c)); fclose(f);
    struct { uint64_t before; NrgMetadata metadata; uint64_t after; } output;
    memset(&output, 0xa5, sizeof output);
    const auto status = nrg_metadata(&s, read, s.base + s.tail.size(), &output.metadata);
    if (output.before != UINT64_C(0xa5a5a5a5a5a5a5a5) || output.after != output.before) return 70;
    if (status != Result::Ok) {
        for (auto *p = reinterpret_cast<const uint8_t *>(&output.metadata); p != reinterpret_cast<const uint8_t *>(&output.metadata + 1); ++p) if (*p != 0xa5) return 71;
        printf("error=%u\n", unsigned(status)); return 2;
    }
    const auto &m = output.metadata;
    printf("nrg %u %u %llu %d %d %u %u\n", m.version, m.tracks, (unsigned long long)m.directory, m.leadin, m.leadout, m.disk_type, m.session_hint);
    if (m.sessions > 99) return 72;
    for (unsigned n = 0; n < 99; ++n) {
        const auto &s = m.session[n]; const auto &i = s.info;
        if (n >= m.sessions) {
            if (i.first_track || i.last_track || i.format || i.cue0 || i.a2 || s.session_hint) return 73;
            for (auto value : s.catalog) if (value) return 73;
        } else {
            printf("session %u %u %u %u %d %u %u ", n + 1, i.first_track, i.last_track, i.format, i.cue0, i.a2, s.session_hint);
            for (auto value : s.catalog) printf("%02x", unsigned(uint8_t(value))); puts("");
        }
    }
    for (unsigned n = 0; n < m.tracks; ++n) {
        const auto &t = m.track[n];
        printf("track %llu %llu %llu %u %u %d %d %u %u %u\n", (unsigned long long)t.offset0, (unsigned long long)t.offset1,
               (unsigned long long)t.end, t.stride, t.mode, t.index0, t.index1, t.has_index0, t.adr, t.control);
    }
}
