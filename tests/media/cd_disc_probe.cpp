#include "cd_disc.h"
#include "cd_akiko.h"
#include "cd_read.h"
#include "uae/cdrom.h"
#include <stdio.h>
#include <string.h>
#include <initializer_list>
#include <utility>
using namespace p6_cd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static_assert(sizeof(Disc) < 16384, "bounded metadata, never a disc-sized allocation");
struct Provider { uint64_t bytes, offset, last = 0; uint32_t stride, mode, calls = 0; int fault = 0; };
static uint8_t byte(const Provider &p, uint64_t at)
{
    const auto pos = (at - p.offset) % p.stride;
    if (p.mode && pos == 15) return uint8_t(p.mode);
    if (p.mode == 2 && pos == 18) return 0x20; // Mode2 Form2, never 2048-byte user data
    return uint8_t(at ^ (at >> 32) ^ (at >> 7));
}
static bool provide(const void *context, uint64_t offset, void *out, size_t length, size_t *got)
{
    auto &p = *const_cast<Provider *>(static_cast<const Provider *>(context));
    ++p.calls; p.last = offset;
    if (offset > p.bytes || length > p.bytes - offset) { *got = 0; return false; }
    for (size_t n = 0; n < length; ++n) static_cast<uint8_t *>(out)[n] = byte(p, offset + n);
    *got = p.fault == 1 ? length - 1 : p.fault == 3 ? length + 1 : length;
    return p.fault != 2;
}
static bool filled(const uint8_t *out, size_t length, uint8_t value)
{
    for (size_t n = 0; n < length; ++n) if (out[n] != value) return false;
    return true;
}
struct Fixture {
    Provider p[4] = {{(UINT64_C(1) << 32) + 20000, (UINT64_C(1) << 32) + 3, 0, 2048, 0},
                     {20000, 11, 0, 2352, 0}, {20480, 13, 0, 2448, 1}, {20000, 17, 0, 2352, 2}};
    Binding bindings[4]{};
    Layout layout{};
    Fixture() {
        for (unsigned n = 0; n < 4; ++n) bindings[n] = {{{uint8_t(n + 1)}, p[n].bytes, 1}, provide, &p[n]};
        layout.content_id[0] = 0x44; layout.tracks = 4; layout.leadout = 13;
        layout.track[0] = {0, 0, 3, 0, 2048, p[0].offset, Mode::Mode1_2048, 1, 4, false};
        layout.track[1] = {3, 5, 7, 1, 2352, p[1].offset, Mode::Audio, 1, 0, false};
        layout.track[2] = {7, 9, 11, 2, 2448, p[2].offset, Mode::Mode1Raw, 1, 4, true};
        layout.track[3] = {11, 11, 13, 3, 2352, p[3].offset, Mode::Mode2Raw, 1, 4, false};
    }
};
static int reads()
{
    Fixture f;
    Disc disc;
    CHECK(disc.bind(f.layout, f.bindings, 4) == Result::Ok);
    uint8_t out[2464], expected[2352] = {};
    for (unsigned n = 0; n < 2048; ++n) expected[16 + n] = byte(f.p[0], f.p[0].offset + 2 * 2048 + n);
    encode_l2(expected, 152);
    memset(out, 0xa5, sizeof out);
    CHECK(disc.read_sector(2, 2352, out + 8, 2448) == Result::Ok);
    CHECK(!memcmp(out + 8, expected, sizeof expected) && f.p[0].last > UINT32_MAX);
    CHECK(filled(out, 8, 0xa5) && filled(out + 8 + 2352, sizeof out - 8 - 2352, 0xa5));
    CHECK(out[8] == 0 && out[19] == 0 && filled(out + 9, 10, 0xff));
    CHECK(out[20] == 0 && out[21] == 2 && out[22] == 2 && out[23] == 1);
    uint32_t edc = 0;
    for (unsigned n = 0; n < 2064; ++n) {
        edc ^= out[8 + n];
        for (unsigned bit = 0; bit < 8; ++bit) edc = (edc >> 1) ^ ((edc & 1) ? 0xd8018001 : 0);
    }
    for (unsigned n = 0; n < 4; ++n) CHECK(out[8 + 2064 + n] == uint8_t(edc >> (8 * n)));
    CHECK(disc.read_sector(2, 2048, out, sizeof out) == Result::Ok && !memcmp(out, expected + 16, 2048));
    CHECK(disc.read_sector(3, 2352, out, sizeof out) == Result::Ok && filled(out, 2352, 0) && !f.p[1].calls);
    for (uint32_t lba : {5u, 6u, 7u, 10u, 11u, 12u}) {
        const unsigned source = lba < 7 ? 1 : lba < 11 ? 2 : 3;
        const auto &t = f.layout.track[source];
        const unsigned view = source == 2 ? 2448 : 2352;
        CHECK(disc.read_sector(lba, view, out, sizeof out) == Result::Ok);
        const uint64_t at = t.offset + uint64_t(lba - (t.stored_pregap ? t.index0 : t.index1)) * t.stride;
        for (unsigned n = 0; n < view; ++n) CHECK(out[n] == byte(f.p[source], at + n));
    }
    CHECK(disc.read_sector(7, 2048, out, sizeof out) == Result::Ok);
    for (unsigned n = 0; n < 2048; ++n) CHECK(out[n] == byte(f.p[2], f.p[2].offset + 16 + n));
    Disc gaps;
    f.layout.track[0].index1 = 1;
    f.layout.track[2].stored_pregap = false;
    const auto calls = f.p[0].calls + f.p[2].calls;
    CHECK(gaps.bind(f.layout, f.bindings, 4) == Result::Ok);
    for (uint32_t lba : {0u, 7u}) {
        CHECK(gaps.read_sector(lba, 2352, out, sizeof out) == Result::Ok && out[11] == 0 && out[15] == 1);
        CHECK(filled(out + 16, 2048, 0));
    }
    CHECK(calls == f.p[0].calls + f.p[2].calls);
    f.layout.track[2].stored_pregap = true;
    f.layout.track[2].synthetic_pregap = 1; f.layout.track[2].postgap = 1;
    f.layout.track[0].postgap = 1; f.layout.track[1].postgap = 1;
    Disc combined;
    CHECK(combined.bind(f.layout, f.bindings, 4) == Result::Ok);
    const auto before = f.p[0].calls + f.p[1].calls + f.p[2].calls;
    for (uint32_t lba : {0u, 2u, 3u, 6u, 7u, 10u}) {
        const unsigned view = lba < 3 || lba >= 7 ? 2048 : 2352;
        CHECK(combined.read_sector(lba, view, out, sizeof out) == Result::Ok && filled(out, view, 0));
    }
    CHECK(before == f.p[0].calls + f.p[1].calls + f.p[2].calls);
    CHECK(combined.read_sector(10, 2352, out, sizeof out) == Result::Ok && out[15] == 1 && filled(out + 16, 2048, 0));
    CHECK(combined.read_sector(8, 2448, out, sizeof out) == Result::Ok);
    for (unsigned n = 0; n < 2448; ++n) CHECK(out[n] == byte(f.p[2], f.p[2].offset + n));
    CHECK(combined.read_sector(9, 2448, out, sizeof out) == Result::Ok && f.p[2].last == f.p[2].offset + 2448);
    return 0;
}
static int failures_and_rebind()
{
    Fixture f, replacement;
    Disc disc;
    uint8_t out[2448];
    CHECK(disc.bind(f.layout, f.bindings, 4) == Result::Ok);
    for (auto args : {std::pair<uint32_t,uint32_t>{13, 2352}, {0, 2448}, {3, 2448}, {5, 2048}, {11, 2048}, {0, 96}}) {
        memset(out, 0xa5, sizeof out);
        CHECK(disc.read_sector(args.first, args.second, out, sizeof out) != Result::Ok && filled(out, sizeof out, 0xa5));
    }
    CHECK(!f.p[0].calls && !f.p[1].calls && !f.p[3].calls);
    for (int fault = 1; fault <= 3; ++fault) {
        f.p[2].fault = fault;
        CHECK(disc.read_sector(7, 2448, out, sizeof out) == Result::ReadError && filled(out, sizeof out, 0xa5));
    }
    f.p[2].fault = 0;
    for (unsigned n = 0; n < 22; ++n) {
        Layout bad = f.layout;
        Binding bindings[4]; memcpy(bindings, replacement.bindings, sizeof bindings);
        if (n == 0) bad.content_id[0] ^= 1;
        if (n == 1) ++bad.leadout;
        if (n == 2) ++bad.track[2].index0;
        if (n == 3) ++bad.track[2].index1;
        if (n == 4) ++bad.track[2].offset;
        if (n == 5) bad.track[2].control ^= 1;
        if (n == 6) bad.track[2].stored_pregap = false;
        if (n == 7) bindings[2].identity.content_id[0] ^= 1;
        if (n == 8) ++bindings[2].identity.bytes;
        if (n == 9) bindings[2].identity.sector_bytes = 2048;
        if (n == 10) bindings[2].read = nullptr;
        if (n == 11) bad.track[2].offset = UINT64_MAX - 1;
        if (n == 12) bad.track[2].mode = Mode::Mode2Raw;
        if (n == 13) bad.track[2].stride = 2352;
        if (n == 14) bad.track[2].source = 1;
        if (n == 15) bad.track[2].adr = 0;
        if (n == 16) --bad.track[2].end;
        if (n == 17) --bad.tracks;
        if (n == 18) bad.track[2].synthetic_pregap = 1;
        if (n == 19) bad.track[2].postgap = 1;
        if (n == 20) bad.track[2].synthetic_pregap = 3;
        if (n == 21) bad.track[2].postgap = 2;
        CHECK(disc.rebind(bad, bindings, 4) != Result::Ok);
        CHECK(disc.read_sector(7, 2352, out, sizeof out) == Result::Ok && !replacement.p[2].calls);
    }
    CHECK(disc.rebind(f.layout, replacement.bindings, 4) == Result::Ok);
    CHECK(disc.read_sector(7, 2352, out, sizeof out) == Result::Ok && replacement.p[2].calls == 1);
    Disc fresh;
    CHECK(fresh.bind(disc.layout(), replacement.bindings, 4) == Result::Ok);
    CHECK(fresh.read_sector(7, 2352, out, sizeof out) == Result::Ok);
    Source bytes;
    CHECK(bytes.bind({{1}, 17, 1}, provide, &f.p[1]) == Result::Ok);
    CHECK(bytes.read_sector(0, out, sizeof out) == Result::Invalid);
    CHECK(bytes.read_at(16, out, 1) == Result::Ok && out[0] == byte(f.p[1], 16));
    return 0;
}
static int akiko()
{
    Fixture f;
    Disc disc, absent;
    CHECK(disc.bind(f.layout, f.bindings, 4) == Result::Ok);
    struct { uint64_t before; cd_toc_head toc; uint64_t after; } result{};
    result.before = result.after = UINT64_C(0x123456789abcdef0);
    CHECK(akiko_toc(disc, &result.toc) == Result::Ok);
    const auto &toc = result.toc;
    CHECK(result.before == result.after && result.before == UINT64_C(0x123456789abcdef0));
    CHECK(toc.first_track == 1 && toc.last_track == 4 && toc.first_track_offset == 1 && toc.last_track_offset == 4);
    CHECK(toc.tracks == 4 && toc.points == 7 && toc.firstaddress == 0 && toc.lastaddress == 13);
    CHECK(toc.toc[0].point == 0xa0 && toc.toc[0].track == 1 && toc.toc[0].control == 0);
    for (unsigned i = 0; i < 4; ++i) {
        const auto &entry = toc.toc[i + 1];
        CHECK(entry.track == i + 1 && entry.point == i + 1 && entry.adr == 1);
        CHECK(entry.paddress == int(f.layout.track[i].index1) && entry.control == f.layout.track[i].control);
    }
    CHECK(toc.toc[5].point == 0xa1 && toc.toc[5].track == 4 && toc.toc[5].paddress == 13);
    CHECK(toc.toc[6].point == 0xa2 && toc.toc[6].paddress == 13 && !f.p[2].calls);
    const auto before = result.toc;
    CHECK(akiko_toc(absent, &result.toc) == Result::Invalid && !memcmp(&before, &result.toc, sizeof before));
    CHECK(akiko_toc(disc, nullptr) == Result::Invalid);
    uint8_t out[112]; memset(out, 0xa5, sizeof out);
    CHECK(akiko_qcode(disc, 7, AUDIO_STATUS_IN_PROGRESS, true, out + 8, 96) == Result::Ok);
    CHECK(filled(out, 8, 0xa5) && filled(out + 104, 8, 0xa5));
    for (unsigned i = 0; i < 96; ++i) CHECK(out[8 + i] == byte(f.p[2], f.p[2].offset + 2352 + i));
    memset(out, 0xa5, sizeof out);
    CHECK(akiko_qcode(disc, 7, AUDIO_STATUS_PAUSED, false, out + 8, 16) == Result::Ok);
    CHECK(filled(out, 8, 0xa5) && filled(out + 24, 88, 0xa5));
    CHECK(out[8] == 0 && out[9] == AUDIO_STATUS_PAUSED && out[10] == 0 && out[11] == 12);
    for (unsigned i = 0; i < 12; ++i) CHECK(out[12 + i] == byte(f.p[2], f.p[2].offset + 2352 + 12 + i));
    CHECK(akiko_qcode(disc, 0, AUDIO_STATUS_NO_STATUS, true, out, sizeof out) == Result::Ok);
    CHECK(out[12] == 0x41 && out[13] == 1 && out[14] == 1);
    for (int fault : {1, 2}) {
        f.p[2].fault = fault; memset(out, 0xa5, sizeof out);
        CHECK(akiko_qcode(disc, 7, AUDIO_STATUS_NO_STATUS, true, out, sizeof out) == Result::ReadError);
        CHECK(filled(out, sizeof out, 0xa5));
    }
    f.p[2].fault = 0;
    CHECK(akiko_qcode(disc, 7, 0xff, true, out, sizeof out) == Result::Invalid);
    CHECK(akiko_qcode(disc, 7, AUDIO_STATUS_NO_STATUS, true, out, 95) == Result::Invalid);
    CHECK(akiko_qcode(disc, 13, AUDIO_STATUS_NO_STATUS, false, out, sizeof out) == Result::Range);
    CHECK(filled(out, sizeof out, 0xa5));
    Layout maximum{}; maximum.tracks = maximum.leadout = 99;
    for (unsigned i = 0; i < 99; ++i) maximum.track[i] = {i, i, i + 1, 0, 2448, uint64_t(i) * 2448, Mode::Mode1Raw, 1, 4, true};
    Provider p{99 * 2448, 0, 0, 2448, 1};
    Binding binding{{{1}, p.bytes, 1}, provide, &p};
    Disc full;
    CHECK(full.bind(maximum, &binding, 1) == Result::Ok && akiko_toc(full, &result.toc) == Result::Ok);
    CHECK(toc.points == 102 && toc.toc[99].point == 99 && toc.toc[100].point == 0xa1 && toc.toc[101].point == 0xa2);
    CHECK(toc.toc[0].control == 4 && toc.toc[101].paddress == 99 && result.before == result.after);
    return 0;
}
static int canonical()
{
    Fixture f; Disc disc, absent;
    CHECK(disc.bind(f.layout, f.bindings, 4) == Result::Ok);
    uint8_t out[2464], main[2352], stored[2448];
    memset(out, 0xa5, sizeof out);
    CHECK(canonical_read(disc, 0, 2448, out + 8, 2448) == Result::Ok);
    CHECK(disc.read_sector(0, 2352, main, sizeof main) == Result::Ok && !memcmp(out + 8, main, sizeof main));
    const uint8_t q[] = {0x41,1,1,0,0,0,0,0,2,0,0x28,0x32};
    CHECK(filled(out + 2360, 12, 0) && !memcmp(out + 2372, q, sizeof q) && filled(out + 2384, 72, 0));
    CHECK(filled(out, 8, 0xa5) && filled(out + 2456, 8, 0xa5));
    CHECK(canonical_read(disc, 3, 2448, out, sizeof out) == Result::Ok);
    CHECK(filled(out, 2352, 0) && filled(out + 2352, 12, 0xff));
    CHECK(out[2364] == 1 && out[2365] == 2 && out[2366] == 0 && out[2369] == 2 && out[2373] == 3);
    CHECK(canonical_read(disc, 5, 2448, out, sizeof out) == Result::Ok && filled(out + 2352, 12, 0));
    for (size_t n = 0; n < 2352; ++n) CHECK(out[n] == byte(f.p[1], f.p[1].offset + n));
    CHECK(out[2365] == 2 && out[2366] == 1 && out[2369] == 0);
    CHECK(disc.read_sector(7, 2448, stored, sizeof stored) == Result::Ok);
    const auto calls = f.p[2].calls;
    CHECK(canonical_read(disc, 7, 2448, out, sizeof out) == Result::Ok && !memcmp(out, stored, 2448));
    CHECK(f.p[2].calls == calls + 1); // preserve all stored P-W/SBI with one read
    CHECK(read_subchannel(disc, 7, out, sizeof out) == Result::Ok && !memcmp(out, stored + 2352, 96));
    for (int fault : {1, 2, 3}) {
        f.p[2].fault = fault; memset(out, 0xa5, sizeof out);
        CHECK(read_subchannel(disc, 7, out, sizeof out) == Result::ReadError && filled(out, sizeof out, 0xa5));
        CHECK(canonical_read(disc, 7, 2448, out, sizeof out) == Result::ReadError && filled(out, sizeof out, 0xa5));
    }
    f.p[2].fault = 0;
    Layout layout{}; layout.tracks = 2; layout.leadout = 205;
    layout.track[0] = {0, 0, 2, 1, 2352, 0, Mode::Audio, 1, 2, false};
    layout.track[1] = {2, 202, 205, 0, 2048, 0, Mode::Mode1_2048, 1, 4, false, 200, 2};
    Disc gaps; CHECK(gaps.bind(layout, f.bindings, 4) == Result::Ok);
    CHECK(canonical_read(gaps, 51, 2448, out, sizeof out) == Result::Ok);
    CHECK(filled(out, 2352, 0) && out[2364] == 0x21 && out[2365] == 2 && out[2366] == 0); //151 before INDEX01
    CHECK(canonical_read(gaps, 52, 2448, out, sizeof out) == Result::Ok);
    CHECK(out[11] == 0 && out[15] == 1 && filled(out + 16, 2048, 0) && out[2364] == 0x41); //150 before INDEX01
    for (uint32_t lba : {202u, 203u, 204u}) {
        CHECK(canonical_read(gaps, lba, 2448, out, sizeof out) == Result::Ok);
        CHECK(out[2366] == 1 && out[2369] == lba - 202);
        CHECK(filled(out + 2352, 12, lba == 202 ? 0 : 0xff));
    }
    memset(out, 0xa5, sizeof out);
    CHECK(canonical_read(gaps, 51, 2048, out, sizeof out) == Result::Unsupported);
    CHECK(canonical_read(absent, 0, 2448, out, sizeof out) == Result::Invalid);
    CHECK(canonical_read(disc, 13, 2448, out, sizeof out) == Result::Range);
    CHECK(canonical_read(disc, 0, 2448, out, 2447) == Result::Invalid);
    CHECK(read_subchannel(disc, 0, out, 95) == Result::Invalid && filled(out, sizeof out, 0xa5));
    layout.track[1].mode = Mode::Mode2Raw; layout.track[1].stride = 2352;
    Disc mode2; CHECK(mode2.bind(layout, f.bindings, 4) == Result::Ok);
    CHECK(canonical_read(mode2, 52, 2448, out, sizeof out) == Result::Unsupported && filled(out, sizeof out, 0xa5));
    return 0;
}
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "reads")) return reads();
    if (!strcmp(argv[1], "failures")) return failures_and_rebind();
    if (!strcmp(argv[1], "akiko")) return akiko();
    if (!strcmp(argv[1], "canonical")) return canonical();
    return 2;
}
