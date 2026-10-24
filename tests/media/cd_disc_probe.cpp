#include "cd_disc.h"
#include "cd_akiko.h"
#include "cd_read.h"
#include "uae/cdrom.h"
#include "../../adapters/waterbox/cd_descriptor.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <initializer_list>
#include <utility>
using namespace p6_cd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static_assert(sizeof(Disc) < 16384, "bounded metadata, never a disc-sized allocation");
static_assert(sizeof(Session) == 12, "fixed private session metadata");
struct Provider { uint64_t bytes, offset, last = 0; uint32_t stride, mode, calls = 0; int fault = 0; uint32_t boundary = 0, prefix_mode = 0; };
static uint8_t byte(const Provider &p, uint64_t at)
{
    const auto pos = (at - p.offset) % p.stride;
    if (p.mode && pos == 15) return uint8_t((at-p.offset)/p.stride < p.boundary ? p.prefix_mode : p.mode);
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
    CHECK(canonical_read(mode2, 52, 2448, out, sizeof out) == Result::Ok && out[15] == 2 && filled(out + 16, 2336, 0));
    return 0;
}
static int mode2_gaps()
{
    for (unsigned stride : {2352u, 2448u}) {
        Provider p{17 + 2 * stride, 17, 0, stride, 2};
        Binding binding{{{1}, p.bytes, 1}, provide, &p};
        Layout layout{}; layout.tracks = 1; layout.leadout = 4504;
        layout.track[0] = {0, 4502, 4504, 0, stride, 17, Mode::Mode2Raw, 1, 4, false, 4502, 1};
        Disc disc; CHECK(disc.bind(layout, &binding, 1) == Result::Ok);
        uint8_t out[2464];
        for (uint32_t lba : {0u, 74u, 75u, 4349u, 4350u, 4501u, 4503u}) {
            memset(out, 0xa5, sizeof out);
            CHECK(canonical_read(disc, lba, 2448, out + 8, 2448) == Result::Ok && !p.calls);
            CHECK(filled(out, 8, 0xa5) && filled(out + 2456, 8, 0xa5) && filled(out + 24, 2336, 0));
            printf("gap %u %u ", stride, lba);
            for (unsigned n = 0; n < 16; ++n) printf("%02x", out[8 + n]);
            putchar(' '); for (unsigned n = 0; n < 96; ++n) printf("%02x", out[2360 + n]); puts("");
        }
        CHECK(canonical_read(disc, 4502, 2448, out, sizeof out) == Result::Ok);
        for (unsigned n = 0; n < 2352; ++n) CHECK(out[n] == byte(p, p.offset + n)); // Stored Form2 stays intact.
        for (int fault = 1; fault <= 3; ++fault) {
            p.fault = fault; const auto calls = p.calls;
            CHECK(canonical_read(disc, 4501, 2448, out, sizeof out) == Result::Ok && p.calls == calls);
            memset(out, 0xa5, sizeof out);
            CHECK(canonical_read(disc, 4502, 2448, out, sizeof out) == Result::ReadError && filled(out, sizeof out, 0xa5));
        }
        memset(out, 0xa5, sizeof out);
        CHECK(disc.read_sector(4501, 2048, out, sizeof out) == Result::Unsupported && filled(out, sizeof out, 0xa5));
        CHECK(disc.read_sector(4501, 2448, out, sizeof out) == Result::Unsupported && filled(out, sizeof out, 0xa5));
        layout.track[0].index1 = layout.track[0].synthetic_pregap = 449851;
        layout.leadout = layout.track[0].end = 449853;
        Disc limit; CHECK(limit.bind(layout, &binding, 1) == Result::Ok);
        const auto calls = p.calls;
        CHECK(limit.read_sector(449849, 2352, out, sizeof out) == Result::Ok && !memcmp(out + 12, "\x99\x59\x74\x02", 4));
        for (uint32_t lba : {449850u, 449852u}) {
            memset(out, 0xa5, sizeof out);
            CHECK(limit.read_sector(lba, 2352, out, sizeof out) == Result::Unsupported && filled(out, sizeof out, 0xa5));
            CHECK(canonical_read(limit, lba, 2448, out, sizeof out) == Result::Unsupported && filled(out, sizeof out, 0xa5));
        }
        CHECK(p.calls == calls);
    }
    return 0;
}

static int waterbox_v2_descriptor()
{
    static_assert(sizeof(p6w_cd_info)==2432 && sizeof(p6w_cd_track)==24 &&
        offsetof(p6w_cd_info,bytes)==40 && offsetof(p6w_cd_info,tracks)==56, "unchanged v1");
    static_assert(sizeof(p6w_cd_info_v2)==4808 && sizeof(p6w_cd_track_v2)==48 &&
        offsetof(p6w_cd_info_v2,bytes)==40 && offsetof(p6w_cd_info_v2,tracks)==56, "v2 header");
    static_assert(offsetof(p6w_cd_track_v2,index0)==0 && offsetof(p6w_cd_track_v2,index1)==4 &&
        offsetof(p6w_cd_track_v2,end)==8 && offsetof(p6w_cd_track_v2,mode)==12 &&
        offsetof(p6w_cd_track_v2,control)==16 && offsetof(p6w_cd_track_v2,reserved)==20 &&
        offsetof(p6w_cd_track_v2,stored_pregap)==24 && offsetof(p6w_cd_track_v2,synthetic_pregap)==28 &&
        offsetof(p6w_cd_track_v2,postgap)==32 && offsetof(p6w_cd_track_v2,flags)==36 &&
        offsetof(p6w_cd_track_v2,pregap_mode)==40 && offsetof(p6w_cd_track_v2,pregap_control)==44, "v2 track");
    using R=Track::Regions;
    p6w_cd_info_v2 wire{}; wire.version=2; wire.size=sizeof wire; wire.count=4; wire.leadout=20;
    wire.id[0]=0x71; wire.bytes=20*2448;
    wire.tracks[0]={0,0,3,0,0,0,0,0,0,0,0,0}; // Complete legacy policy remains expressible.
    wire.tracks[1]={3,5,9,1,4,0,1,1,2,R::Explicit|R::ProgramSub96|R::PregapSub96|R::PostgapIndex2,0,0};
    wire.tracks[2]={9,11,15,0,0,0,0,2,2,R::Explicit|R::PostgapStored|R::PostgapSub96|R::PostgapIndex2,2,4};
    wire.tracks[3]={15,17,20,2,4,0,1,0,0,R::Explicit|R::ProgramSub96,1,4};
    Provider p{wire.bytes,0,0,2448,1}; Binding binding{{{0x71},wire.bytes,1},provide,&p};
    struct Guarded { uint64_t before; Layout layout; uint64_t after; } guarded;
    memset(&guarded,0xa5,sizeof guarded);
    CHECK(p6_waterbox::cd_descriptor_v2(&wire,binding,&guarded.layout)==Result::Ok&&!p.calls);
    CHECK(guarded.before==UINT64_C(0xa5a5a5a5a5a5a5a5)&&guarded.after==guarded.before);
    const auto base=guarded.layout; CHECK(base.tracks==4&&base.leadout==20&&!memcmp(base.content_id,wire.id,32));
    const Mode modes[]={Mode::Audio,Mode::Mode1Raw,Mode::Audio,Mode::Mode2Raw};
    const Mode prefixes[]={Mode::Mode1_2048,Mode::Audio,Mode::Mode2Raw,Mode::Mode1Raw};
    for(unsigned n=0;n<4;++n) {
        const auto &t=base.track[n]; const auto &w=wire.tracks[n];
        CHECK(t.index0==w.index0&&t.index1==w.index1&&t.end==w.end&&t.source==0&&t.stride==2448&&t.adr==1);
        CHECK(t.mode==modes[n]&&t.control==w.control&&t.stored_pregap==bool(w.stored_pregap)&&
            t.synthetic_pregap==w.synthetic_pregap&&t.postgap==w.postgap&&t.regions.flags==w.flags&&
            t.regions.pregap_mode==prefixes[n]&&t.regions.pregap_control==w.pregap_control);
        CHECK(t.offset==uint64_t(n==0?0:n==1?4:n==2?11:15)*2448);
    }
    Disc disc; CHECK(disc.bind(base,&binding,1)==Result::Ok&&!p.calls);
    uint8_t out[2464];
    for(uint32_t lba=0;lba<20;++lba) {
        const bool synthetic=lba==3||lba==7||lba==8||lba==9||lba==10;
        const bool sub=!synthetic&&lba!=11&&lba!=12&&lba!=15&&lba!=16;
        const Mode mode=(lba<5||(lba>=11&&lba<15))?Mode::Audio:(lba<9||(lba>=15&&lba<17))?Mode::Mode1Raw:Mode::Mode2Raw;
        SectorInfo info; CHECK(disc.describe(lba,&info)==Result::Ok&&info.synthetic==synthetic&&info.stored_subs==sub&&info.effective.mode==mode);
        CHECK(info.effective.control==(mode==Mode::Audio?0:4)&&(synthetic||info.source_offset==uint64_t(lba)*2448));
        const auto calls=p.calls; memset(out,0xa5,sizeof out);
        CHECK(canonical_read(disc,lba,2448,out+8,2448)==Result::Ok&&filled(out,8,0xa5)&&filled(out+2456,8,0xa5));
        if(synthetic) CHECK(p.calls==calls&&(mode!=Mode::Audio||filled(out+8,2352,0)));
        else for(unsigned n=0;n<2352;++n) CHECK(out[8+n]==byte(p,uint64_t(lba)*2448+n));
        if(synthetic&&mode!=Mode::Audio) CHECK(out[23]==(mode==Mode::Mode1Raw?1:2)&&filled(out+24,2048,0));
        if(sub) for(unsigned n=0;n<96;++n) CHECK(out[2360+n]==byte(p,uint64_t(lba)*2448+2352+n));
        else { printf("wire %u ",lba); for(unsigned n=0;n<96;++n) printf("%02x",out[2360+n]); puts(""); }
        memset(out,0xa5,sizeof out);
        CHECK(disc.read_sector(lba,2448,out,2448)==(sub?Result::Ok:Result::Unsupported));
        if(!sub) CHECK(filled(out,sizeof out,0xa5));
    }
    for(unsigned bad=0;bad<30;++bad) {
        auto other=wire; auto b=binding; auto &t=other.tracks[1];
        switch(bad) {
        case 0: other.version=1; break; case 1: --other.size; break;
        case 2: other.count=0; break; case 3: other.count=100; break;
        case 4: ++other.bytes; break; case 5: --other.leadout; break;
        case 6: t.reserved=1; break; case 7: t.mode=3; break; case 8: t.control=260; break;
        case 9: t.stored_pregap=2; break; case 10: t.synthetic_pregap=3; break;
        case 11: t.flags|=64; break; case 12: t.flags&=~R::Explicit; break;
        case 13: t.pregap_mode=3; break; case 14: t.pregap_control=256; break;
        case 15: t.pregap_control=4; break; case 16: t.index0=4; break; case 17: t.end=t.index1; break;
        case 18: t.postgap=4; break; case 19: t.flags|=R::PostgapSub96; break;
        case 20: other.tracks[0].pregap_mode=1; break; case 21: other.tracks[0].pregap_control=2; break;
        case 22: other.tracks[98].synthetic_pregap=1; break; case 23: other.tracks[98].reserved=1; break;
        case 24: other.count=1; break; case 25: t.synthetic_pregap=UINT32_MAX; break;
        case 26: b.identity.sector_bytes=2048; break; case 27: b.identity.content_id[0]^=1; break;
        case 28: --b.identity.bytes; break; case 29: b.read=nullptr; break;
        }
        memset(&guarded,0xa5,sizeof guarded); const auto calls=p.calls;
        CHECK(p6_waterbox::cd_descriptor_v2(&other,b,&guarded.layout)==Result::Invalid);
        CHECK(filled(reinterpret_cast<uint8_t *>(&guarded),sizeof guarded,0xa5)&&p.calls==calls);
    }
    CHECK(p6_waterbox::cd_descriptor_v2(nullptr,binding,&guarded.layout)==Result::Invalid);
    CHECK(p6_waterbox::cd_descriptor_v2(&wire,binding,nullptr)==Result::Invalid);
    Provider fresh=p; fresh.calls=0; auto renamed=binding; renamed.context=&fresh;
    CHECK(p6_waterbox::cd_descriptor_v2(&wire,renamed,&guarded.layout)==Result::Ok);
    auto calls=p.calls; CHECK(disc.rebind(guarded.layout,&renamed,1)==Result::Ok&&!fresh.calls&&p.calls==calls);
    CHECK(disc.read_sector(4,2448,out,2448)==Result::Ok&&fresh.calls==1&&p.calls==calls);
    for(unsigned change=0;change<8;++change) {
        auto other=wire; auto b=renamed; auto &t=other.tracks[1];
        switch(change) {
        case 0: other.id[0]^=1; b.identity.content_id[0]^=1; break;
        case 1: t.pregap_control=2; break; case 2: other.tracks[3].control=5; break;
        case 3: t.synthetic_pregap=0; break; case 4: --t.postgap; break;
        case 5: t.pregap_mode=1; t.pregap_control=4; break;
        case 6: t.flags&=~R::PregapSub96; break; case 7: ++other.tracks[3].index1; break;
        }
        calls=fresh.calls;
        CHECK(p6_waterbox::cd_descriptor_v2(&other,b,&guarded.layout)==Result::Ok&&fresh.calls==calls);
        CHECK(disc.rebind(guarded.layout,&b,1)==Result::Incompatible&&fresh.calls==calls);
        CHECK(disc.read_sector(4,2448,out,2448)==Result::Ok&&fresh.calls==calls+1);
        for(unsigned n=0;n<2448;++n) CHECK(out[n]==byte(fresh,4*2448+n));
    }
    for(int fault : {1,2,3}) {
        fresh.fault=fault; memset(out,0xa5,sizeof out);
        CHECK(canonical_read(disc,4,2448,out+8,2448)==Result::ReadError&&filled(out,sizeof out,0xa5));
    }
    fresh.fault=0;
    auto maximum=wire; maximum.count=maximum.leadout=99; maximum.bytes=99*2448;
    for(unsigned n=0;n<99;++n) maximum.tracks[n]={n,n,n+1,1,4,0,0,0,0,0,0,0};
    auto b=renamed; b.identity.bytes=maximum.bytes; calls=fresh.calls;
    CHECK(p6_waterbox::cd_descriptor_v2(&maximum,b,&guarded.layout)==Result::Ok&&fresh.calls==calls&&guarded.layout.tracks==99);
    return 0;
}

static int waterbox_v3_descriptor()
{
    static_assert(sizeof(p6w_cd_session)==20 && offsetof(p6w_cd_session,first_track)==0 &&
        offsetof(p6w_cd_session,last_track)==4 && offsetof(p6w_cd_session,format)==8 &&
        offsetof(p6w_cd_session,cue0)==12 && offsetof(p6w_cd_session,a2)==16,"V3 session ABI");
    static_assert(sizeof(p6w_cd_info_v3)==6792 && alignof(p6w_cd_info_v3)==8 &&
        offsetof(p6w_cd_info_v3,bytes)==40 && offsetof(p6w_cd_info_v3,tracks)==56 &&
        offsetof(p6w_cd_info_v3,session_count)==4808 && offsetof(p6w_cd_info_v3,sessions)==4812,"V3 info ABI");
    p6w_cd_info_v3 wire{}; wire.version=3; wire.size=sizeof wire; wire.id[0]=0x73;
    wire.count=3; wire.leadout=39; wire.bytes=39*2448; wire.session_count=2;
    wire.tracks[0]={0,0,4,0,0,0,1,0,0,1,0,0};
    wire.tracks[1]={4,6,11,1,4,0,1,0,0,1,1,4};
    wire.tracks[2]={30,32,39,2,4,0,1,0,0,1,2,4};
    wire.sessions[0]={1,2,0,-150,11}; wire.sessions[1]={3,3,0x20,20,39};
    Provider p{wire.bytes,0,0,2448,0}; Binding binding{{{0x73},wire.bytes,1},provide,&p};
    struct Guarded { uint64_t before; Layout layout; uint64_t after; } guarded;
    memset(&guarded,0xa5,sizeof guarded);
    CHECK(p6_waterbox::cd_descriptor_v3(&wire,binding,&guarded.layout)==Result::Ok&&!p.calls);
    CHECK(guarded.before==UINT64_C(0xa5a5a5a5a5a5a5a5)&&guarded.after==guarded.before);
    const auto base=guarded.layout; CHECK(base.sessions==2&&base.tracks==3&&!memcmp(base.content_id,wire.id,32));
    for(unsigned n=0;n<99;++n) {
        const auto &s=base.session[n]; const auto &w=wire.sessions[n];
        CHECK(s.first_track==w.first_track&&s.last_track==w.last_track&&s.format==w.format&&s.cue0==w.cue0&&s.a2==w.a2);
    }
    Disc disc; CHECK(disc.bind(base,&binding,1)==Result::Ok&&!p.calls);
    cd_toc_head global; CHECK(akiko_toc(disc,&global)==Result::Ok&&global.tracks==3&&global.points==6&&
        global.toc[3].point==3&&global.toc[3].paddress==32&&global.toc[5].point==0xa2&&global.lastaddress==39);
    for(unsigned n=1;n<=2;++n) {
        SessionToc toc{}; CHECK(akiko_session_toc(disc,n,&toc)==Result::Ok);
        CHECK(toc.toc.first_track==int(n==1?1:3)&&toc.toc.last_track==int(n==1?2:3));
        CHECK(toc.toc.firstaddress==int(n==1?0:30)&&toc.toc.lastaddress==int(n==1?11:39));
    }
    CHECK(!p.calls);
    uint8_t out[2464];
    for(uint32_t lba : {11u,20u,29u}) {
        for(unsigned view : {2048u,2352u,2448u}) {
            memset(out,0xa5,sizeof out);
            CHECK(canonical_read(disc,lba,view,out+8,view)==Result::Range&&filled(out,sizeof out,0xa5));
        }
        CHECK(read_subchannel(disc,lba,out,sizeof out)==Result::Range&&filled(out,sizeof out,0xa5));
        CHECK(!p.calls);
    }
    for(uint32_t lba : {0u,3u,4u,5u,6u,10u,30u,31u,32u,38u}) {
        memset(out,0xa5,sizeof out);
        CHECK(canonical_read(disc,lba,2448,out+8,2448)==Result::Ok&&filled(out,8,0xa5)&&filled(out+2456,8,0xa5));
        for(unsigned n=0;n<2352;++n) CHECK(out[8+n]==byte(p,uint64_t(lba)*2448+n));
        const auto calls=p.calls; CHECK(read_subchannel(disc,lba,out,96)==Result::Ok&&p.calls==calls);
        printf("wire3 %u ",lba); for(unsigned n=0;n<96;++n) printf("%02x",out[n]); puts("");
    }
    for(unsigned bad=0;bad<30;++bad) {
        auto other=wire; auto b=binding;
        switch(bad) {
        case 0: other.version=2; break; case 1: --other.size; break;
        case 2: other.session_count=100; break; case 3: other.session_count=0; break;
        case 4: other.session_count=1; break; case 5: other.sessions[0].first_track=0; break;
        case 6: other.sessions[0].first_track=257; break; case 7: other.sessions[1].last_track=259; break;
        case 8: other.sessions[0].last_track=1; break; case 9: other.sessions[1].first_track=2; break;
        case 10: other.sessions[1].format=1; break; case 11: other.sessions[1].format=0x120; break;
        case 12: other.sessions[0].cue0=1; break; case 13: other.sessions[1].cue0=10; break;
        case 14: other.sessions[1].cue0=31; break; case 15: ++other.sessions[0].a2; break;
        case 16: --other.sessions[1].a2; break; case 17: other.sessions[2].first_track=1; break;
        case 18: other.sessions[98].last_track=1; break; case 19: other.sessions[98].format=0x10; break;
        case 20: other.sessions[98].cue0=INT32_MIN; break; case 21: other.sessions[98].a2=1; break;
        case 22: other.count=100; break; case 23: other.tracks[1].reserved=1; break;
        case 24: other.tracks[2].mode=3; break; case 25: other.tracks[98].end=1; break;
        case 26: ++b.identity.bytes; break; case 27: b.identity.sector_bytes=2048; break;
        case 28: b.identity.content_id[0]^=1; break; case 29: b.read=nullptr; break;
        }
        memset(&guarded,0xa5,sizeof guarded); const auto calls=p.calls;
        CHECK(p6_waterbox::cd_descriptor_v3(&other,b,&guarded.layout)==Result::Invalid);
        CHECK(filled(reinterpret_cast<uint8_t *>(&guarded),sizeof guarded,0xa5)&&p.calls==calls);
    }
    CHECK(p6_waterbox::cd_descriptor_v3(nullptr,binding,&guarded.layout)==Result::Invalid);
    CHECK(p6_waterbox::cd_descriptor_v3(&wire,binding,nullptr)==Result::Invalid);
    Provider fresh=p; fresh.calls=0; auto b=binding; b.context=&fresh;
    CHECK(p6_waterbox::cd_descriptor_v3(&wire,b,&guarded.layout)==Result::Ok&&!fresh.calls);
    CHECK(disc.rebind(guarded.layout,&b,1)==Result::Ok&&!fresh.calls); p.fault=2;
    for(unsigned change=0;change<5;++change) {
        auto other=wire; auto candidate=b;
        if(change==0) other.sessions[0].cue0=-149;
        if(change==1) other.sessions[1].cue0=21;
        if(change==2) other.sessions[0].format=0x10;
        if(change==3) other.sessions[1].format=0x10;
        if(change==4) { other.id[0]^=1; candidate.identity.content_id[0]^=1; }
        const auto calls=fresh.calls;
        CHECK(p6_waterbox::cd_descriptor_v3(&other,candidate,&guarded.layout)==Result::Ok&&fresh.calls==calls);
        CHECK(disc.rebind(guarded.layout,&candidate,1)==Result::Incompatible&&fresh.calls==calls);
        for(uint32_t lba : {0u,32u}) {
            CHECK(canonical_read(disc,lba,2352,out,sizeof out)==Result::Ok);
            for(unsigned n=0;n<2352;++n) CHECK(out[n]==byte(fresh,uint64_t(lba)*2448+n));
        }
    }
    for(int fault : {1,2,3}) for(uint32_t lba : {0u,32u}) {
        fresh.fault=fault; memset(out,0xa5,sizeof out);
        CHECK(canonical_read(disc,lba,2448,out+8,2448)==Result::ReadError&&filled(out,sizeof out,0xa5));
    }
    fresh.fault=0;
    auto single=wire; single.count=1; single.leadout=4; single.bytes=4*2448; single.session_count=0;
    memset(single.sessions,0,sizeof single.sessions); memset(single.tracks+1,0,sizeof single.tracks-sizeof single.tracks[0]);
    p6w_cd_info_v2 v2{}; v2.version=2; v2.size=sizeof v2; v2.count=1; v2.leadout=4; v2.bytes=single.bytes;
    memcpy(v2.id,single.id,32); memcpy(v2.tracks,single.tracks,sizeof v2.tracks);
    b.identity.bytes=single.bytes; Layout legacy; Disc one;
    CHECK(p6_waterbox::cd_descriptor_v2(&v2,b,&legacy)==Result::Ok&&one.bind(legacy,&b,1)==Result::Ok);
    CHECK(p6_waterbox::cd_descriptor_v3(&single,b,&guarded.layout)==Result::Ok&&!guarded.layout.sessions);
    CHECK(one.rebind(guarded.layout,&b,1)==Result::Ok); // Complete semantic parity, without comparing padding.
    auto maximum=wire; maximum.count=maximum.leadout=maximum.session_count=99; maximum.bytes=99*2448;
    for(unsigned n=0;n<99;++n) {
        maximum.tracks[n]={n,n,n+1,1,4,0,0,0,0,0,0,0};
        maximum.sessions[n]={n+1,n+1,0,n?int32_t(n):-150,n+1};
    }
    b.identity.bytes=maximum.bytes; const auto calls=fresh.calls;
    CHECK(p6_waterbox::cd_descriptor_v3(&maximum,b,&guarded.layout)==Result::Ok&&fresh.calls==calls&&guarded.layout.sessions==99);
    return 0;
}

static int explicit_regions()
{
    using R = Track::Regions;
    for (auto program : {Mode::Audio,Mode::Mode1Raw,Mode::Mode2Raw}) for (auto prefix : {Mode::Audio,Mode::Mode1Raw,Mode::Mode2Raw})
    for (unsigned stored = 0; stored < 3; ++stored) for (unsigned mask = 0; mask < 4; ++mask) for (unsigned post = 0; post < 2; ++post) {
        if (!stored && (mask&2)) continue;
        Provider p{11+7*2448,11,0,2448,unsigned(program)};
        p.boundary = stored == 1 ? 2 : stored == 2 ? 1 : 0; p.prefix_mode = unsigned(prefix);
        Binding binding{{{1},p.bytes,1},provide,&p}; Layout layout{}; layout.tracks=1; layout.leadout=7;
        auto &t=layout.track[0]; t={0,2,7,0,2448,11,program,1,uint8_t(program==Mode::Audio?0:4),stored!=0,stored==2?1u:0u,2};
        t.regions={R::Explicit|R::PostgapIndex2|(mask&1?R::ProgramSub96:0)|(mask&2?R::PregapSub96:0)|
            (post?R::PostgapStored:0)|(post&&(mask&1)?R::PostgapSub96:0),prefix,uint32_t(prefix==Mode::Audio?0:4)};
        Disc disc; CHECK(disc.bind(layout,&binding,1)==Result::Ok); uint8_t out[2464], q[96];
        for (uint32_t lba=0; lba<7; ++lba) {
            const bool pre=lba<2, tail=lba>=5, synthetic=(lba<(stored==1?0:stored==2?1:2))||(tail&&!post);
            const auto mode=pre?prefix:program; const auto control=mode==Mode::Audio?0:4;
            const bool has_sub=!synthetic&&(pre?mask&2:tail?post&&(mask&1):mask&1);
            const auto at=11+uint64_t(lba-(stored==1?0:stored==2?1:2))*2448;
            const auto calls=p.calls; SectorInfo info; CHECK(disc.describe(lba,&info)==Result::Ok);
            CHECK(info.effective.mode==mode && info.effective.control==control && info.pause==(pre||tail) &&
                info.synthetic==synthetic && info.stored_subs==has_sub && (synthetic||info.source_offset==at));
            CHECK(describe_sector(disc,lba,&info)==Result::Ok && p.calls==calls);
            for (unsigned view : {2048u,2352u,2448u}) {
                memset(out,0xa5,sizeof out); const bool reject=view==2048&&mode!=Mode::Mode1Raw;
                CHECK(canonical_read(disc,lba,view,out+8,view)==(reject?Result::Unsupported:Result::Ok));
                CHECK(filled(out,8,0xa5)&&filled(out+8+view,sizeof out-8-view,0xa5));
                if (reject) CHECK(filled(out,sizeof out,0xa5));
                else if (!synthetic) for (unsigned n=0;n<(view==2448?2352:view);++n) CHECK(out[8+n]==byte(p,at+(view==2048?16:0)+n));
                else if (view==2048||mode==Mode::Audio) CHECK(filled(out+8,view==2448?2352:view,0));
                else CHECK(out[23]==(mode==Mode::Mode1Raw?1:2)&&filled(out+24,2048,0));
            }
            memset(out,0xa5,sizeof out);
            CHECK(disc.read_sector(lba,2448,out,2448)==(has_sub?Result::Ok:Result::Unsupported));
            if (!has_sub) CHECK(filled(out,sizeof out,0xa5));
            CHECK(read_subchannel(disc,lba,q,sizeof q)==Result::Ok);
            if (has_sub) for(unsigned n=0;n<96;++n) CHECK(q[n]==byte(p,at+2352+n)); // Opaque Q, CRC and R-W/SBI survive.
            else {
                printf("region %u %u ",control,lba); for(auto value:q) printf("%02x",value); puts("");
                CHECK(q[14]==(pre?0:tail?2:1));
            }
            if (synthetic) CHECK(p.calls==calls);
            else for (int fault : {1,2,3}) {
                p.fault=fault; memset(out,0xa5,sizeof out);
                CHECK(canonical_read(disc,lba,2448,out,2448)==Result::ReadError&&filled(out,sizeof out,0xa5));
                p.fault=0;
            }
        }
    }
    Provider p{11+7*2448,11,0,2448,1}; Binding b{{{1},p.bytes,1},provide,&p};
    Layout base{}; base.tracks=1; base.leadout=7; base.track[0]={0,2,7,0,2448,11,Mode::Mode1Raw,1,4,true,0,2};
    base.track[0].regions={63,Mode::Mode1Raw,4}; Disc disc; CHECK(disc.bind(base,&b,1)==Result::Ok);
    uint8_t saved[2448],out[2448]; CHECK(canonical_read(disc,0,2448,saved,sizeof saved)==Result::Ok);
    for (unsigned field=0;field<7;++field) {
        auto other=base; auto &r=other.track[0].regions;
        if(field==0) r.flags^=R::ProgramSub96; if(field==1) r.flags^=R::PregapSub96;
        if(field==2) r.flags^=R::PostgapSub96; if(field==3) r.flags^=R::PostgapIndex2;
        if(field==4) r.flags&=~(R::PostgapStored|R::PostgapSub96);
        if(field==5) r.pregap_mode=Mode::Mode2Raw; if(field==6) r.pregap_control=6;
        CHECK(disc.rebind(other,&b,1)==Result::Incompatible);
        CHECK(canonical_read(disc,0,2448,out,sizeof out)==Result::Ok&&!memcmp(out,saved,sizeof out));
    }
    for(unsigned bad=0;bad<13;++bad) {
        auto other=base; auto &t=other.track[0]; auto &r=t.regions;
        if(bad==0) r.flags=64; if(bad==1) r.flags=R::ProgramSub96; if(bad==2) r.flags=0;
        if(bad==3) r.pregap_control=16; if(bad==4) r.pregap_mode=Mode::Audio;
        if(bad==5) r.pregap_mode=Mode::Mode1_2048; if(bad==6) r.pregap_mode=Mode(99);
        if(bad==7) t.stride=2352; if(bad==8) t.stored_pregap=false;
        if(bad==9) t.synthetic_pregap=2; if(bad==10) t.postgap=0;
        if(bad==11) r.flags&=~R::PostgapStored; if(bad==12) t.index1=0;
        Disc empty; CHECK(empty.bind(other,&b,1)==Result::Invalid&&!empty.layout().tracks);
        CHECK(disc.rebind(other,&b,1)==Result::Invalid);
    }
    Binding short_source=b; short_source.identity.bytes=11+5*2448;
    Disc short_disc; CHECK(short_disc.bind(base,&short_source,1)==Result::Range);
    base.track[0].regions.flags&=~(R::PostgapStored|R::PostgapSub96);
    CHECK(short_disc.bind(base,&short_source,1)==Result::Ok); // Synthesized tail consumes no source.
    base.track[0].regions.flags&=~R::PostgapIndex2; Disc index1;
    CHECK(index1.bind(base,&short_source,1)==Result::Ok&&read_subchannel(index1,5,out,sizeof out)==Result::Ok);
    CHECK(out[14]==1&&filled(out,12,0xff)); // Explicit policy without INDEX02 also keeps INDEX01.
    SectorInfo canary{}; canary.number=123; CHECK(short_disc.describe(7,&canary)==Result::Range&&canary.number==123);
    Disc empty; CHECK(empty.describe(0,&canary)==Result::Invalid&&canary.number==123);
    return 0;
}

static int sessions()
{
    // Explicit CUE0/A2 positions, not fixed 4500/6750/2250 session padding.
    // Session 2 has its own 200-sector data pregap after an inter-session hole.
    Provider p[2] = {{4*2352,0,0,2352,0}, {4*2352,0,0,2352,1}};
    Binding b[2] = {{{{1},p[0].bytes,1},provide,&p[0]}, {{{2},p[1].bytes,1},provide,&p[1]}};
    Layout layout{}; layout.content_id[0]=0x66; layout.tracks=2; layout.leadout=224;
    layout.track[0]={0,0,4,0,2352,0,Mode::Audio,1,0,true};
    layout.track[1]={20,220,224,1,2352,0,Mode::Mode1Raw,1,4,false,200};
    layout.sessions=2; layout.session[0]={1,1,0,-150,4}; layout.session[1]={2,2,0x20,10,224};
    Disc disc; CHECK(disc.bind(layout,b,2)==Result::Ok&&p[0].calls==0&&p[1].calls==0);
    SessionToc toc, unchanged; memset(&toc,0xa5,sizeof toc); memcpy(&unchanged,&toc,sizeof toc);
    cd_toc_head global; memset(&global,0xa5,sizeof global);
    CHECK(akiko_toc(disc,&global)==Result::Ok);
    cd_toc_head expected; memset(&expected,0,sizeof expected);
    expected.first_track=expected.first_track_offset=1;
    expected.last_track=expected.last_track_offset=expected.tracks=2;
    expected.points=5; expected.lastaddress=224;
    for(unsigned n=0;n<5;++n) expected.toc[n].adr=1;
    expected.toc[0].point=0xa0; expected.toc[0].track=1;
    expected.toc[1].track=expected.toc[1].point=1;
    expected.toc[2].track=expected.toc[2].point=2;
    expected.toc[2].control=4; expected.toc[2].paddress=220;
    expected.toc[3].point=0xa1; expected.toc[3].track=2; expected.toc[3].paddress=224;
    expected.toc[4].point=0xa2; expected.toc[4].paddress=224;
    CHECK(!memcmp(&global,&expected,sizeof global));
    for (uint32_t ordinal : {0u,3u}) {
        CHECK(akiko_session_toc(disc,ordinal,&toc)==Result::Range&&!memcmp(&toc,&unchanged,sizeof toc));
    }
    CHECK(akiko_session_toc(disc,1,nullptr)==Result::Invalid);
    for (uint32_t ordinal=1;ordinal<=2;++ordinal) {
        CHECK(akiko_session_toc(disc,ordinal,&toc)==Result::Ok);
        const auto &t=toc.toc; const auto &s=toc.session;
        CHECK(s.first_track==ordinal&&s.last_track==ordinal&&s.format==(ordinal==1?0:0x20));
        CHECK(s.cue0==(ordinal==1?-150:10)&&s.a2==(ordinal==1?4u:224u));
        CHECK(t.first_track==int(ordinal)&&t.last_track==int(ordinal));
        CHECK(t.first_track_offset==1&&t.last_track_offset==1&&t.tracks==1&&t.points==4);
        CHECK(t.firstaddress==(ordinal==1?0:20)&&t.lastaddress==(ordinal==1?4:224));
        CHECK(t.toc[0].point==0xa0&&t.toc[0].track==ordinal&&t.toc[0].paddress==0);
        CHECK(t.toc[1].track==ordinal&&t.toc[1].point==ordinal&&t.toc[1].paddress==(ordinal==1?0:220));
        CHECK(t.toc[2].point==0xa1&&t.toc[2].track==ordinal&&t.toc[2].paddress==t.lastaddress);
        CHECK(t.toc[3].point==0xa2&&t.toc[3].paddress==t.lastaddress);
        for(unsigned n=0;n<4;++n) CHECK(t.toc[n].adr==1&&t.toc[n].control==(ordinal==1?0:4));
    }
    CHECK(p[0].calls==0&&p[1].calls==0); // Queries do not read or select media.
    CHECK(akiko_toc(disc,&global)==Result::Ok&&!memcmp(&global,&expected,sizeof global));
    uint8_t out[2448], saved[2448]; SectorInfo info{}; info.number=123;
    for (uint32_t lba : {4u,9u,10u,19u}) {
        CHECK(disc.describe(lba,&info)==Result::Range&&info.number==123);
        for(unsigned view : {2048u,2352u,2448u}) {
            memset(out,0xa5,sizeof out);
            CHECK(canonical_read(disc,lba,view,out,sizeof out)==Result::Range&&filled(out,sizeof out,0xa5));
        }
        CHECK(read_subchannel(disc,lba,out,sizeof out)==Result::Range&&filled(out,sizeof out,0xa5));
        CHECK(akiko_qcode(disc,lba,AUDIO_STATUS_NO_STATUS,true,out,sizeof out)==Result::Range&&filled(out,sizeof out,0xa5));
        CHECK(p[0].calls==0&&p[1].calls==0);
    }
    for(uint32_t lba : {0u,3u,20u,219u,220u,223u}) {
        CHECK(read_subchannel(disc,lba,out,sizeof out)==Result::Ok);
        // Track 2 must never inherit the previous session's audio CONTROL.
        CHECK(out[12]==(lba<4?1:0x41)&&out[13]==(lba<4?1:2));
        printf("session-q %u ",lba); for(unsigned n=0;n<96;++n) printf("%02x",out[n]); puts("");
    }
    CHECK(p[0].calls==0&&p[1].calls==0);
    CHECK(canonical_read(disc,3,2352,out,sizeof out)==Result::Ok&&p[0].last==3*2352);
    CHECK(canonical_read(disc,20,2352,out,sizeof out)==Result::Ok&&p[1].calls==0);
    CHECK(canonical_read(disc,220,2352,saved,sizeof saved)==Result::Ok&&p[1].last==0);
    CHECK(canonical_read(disc,223,2352,out,sizeof out)==Result::Ok&&p[1].last==3*2352);
    const auto calls=p[0].calls+p[1].calls;
    for(unsigned bad=0;bad<15;++bad) {
        auto other=layout;
        if(bad==0) other.sessions=100;
        if(bad==1) other.sessions=0;
        if(bad==2) other.session[0].first_track=2;
        if(bad==3) other.session[0].last_track=2;
        if(bad==4) other.session[1].first_track=3;
        if(bad==5) other.session[1].last_track=1;
        if(bad==6) other.session[1].format=1;
        if(bad==7) other.session[0].cue0=1;
        if(bad==8) other.session[1].cue0=3;
        if(bad==9) other.session[1].cue0=21;
        if(bad==10) other.session[0].a2=5;
        if(bad==11) other.session[1].a2=223;
        if(bad==12) other.session[2].cue0=1;
        if(bad==13) { other.sessions=1; other.session[0]={1,2,0,-150,224}; other.session[1]={}; }
        if(bad==14) other.session[1].last_track=3;
        Disc empty; CHECK(empty.bind(other,b,2)==Result::Invalid&&!empty.layout().tracks);
        CHECK(disc.rebind(other,b,2)==Result::Invalid);
        CHECK(p[0].calls+p[1].calls==calls);
    }
    for(unsigned field=0;field<3;++field) {
        auto other=layout;
        if(field==0) other.session[0].cue0=-149;
        if(field==1) other.session[1].cue0=11;
        if(field==2) other.session[1].format=0x10;
        CHECK(disc.rebind(other,b,2)==Result::Incompatible&&p[0].calls+p[1].calls==calls);
    }
    CHECK(canonical_read(disc,220,2352,out,sizeof out)==Result::Ok&&!memcmp(out,saved,2352));
    Provider fresh[2]={p[0],p[1]}; Binding rebound[2]={b[0],b[1]};
    for(unsigned n=0;n<2;++n) rebound[n].context=&fresh[n];
    CHECK(disc.rebind(layout,rebound,2)==Result::Ok); p[0].fault=p[1].fault=2;
    CHECK(canonical_read(disc,220,2352,out,sizeof out)==Result::Ok&&!memcmp(out,saved,2352));
    Fixture f; Disc legacy; CHECK(legacy.bind(f.layout,f.bindings,4)==Result::Ok);
    cd_toc_head original, explicit_one;
    CHECK(akiko_toc(legacy,&original)==Result::Ok);
    memcpy(&toc,&unchanged,sizeof toc);
    CHECK(akiko_session_toc(legacy,1,&toc)==Result::Unsupported&&!memcmp(&toc,&unchanged,sizeof toc));
    f.layout.sessions=1; f.layout.session[0]={1,4,0,-150,13}; Disc one;
    CHECK(one.bind(f.layout,f.bindings,4)==Result::Ok&&akiko_toc(one,&explicit_one)==Result::Ok);
    CHECK(!memcmp(&original,&explicit_one,sizeof original));
    f.layout.sessions=2; f.layout.session[0]={1,1,0,-150,3}; f.layout.session[1]={2,4,0,3,13}; Disc two;
    CHECK(two.bind(f.layout,f.bindings,4)==Result::Ok);
    auto partition=f.layout; partition.session[0]={1,2,0,-150,7}; partition.session[1]={3,4,0,7,13};
    CHECK(two.rebind(partition,f.bindings,4)==Result::Incompatible);
    partition.sessions=1; partition.session[0]={1,4,0,-150,13}; partition.session[1]={};
    CHECK(two.rebind(partition,f.bindings,4)==Result::Incompatible);
    Disc empty; CHECK(akiko_session_toc(empty,1,&toc)==Result::Invalid);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "sessions")) return sessions();
    if (!strcmp(argv[1], "wire-v3")) return waterbox_v3_descriptor();
    if (!strcmp(argv[1], "wire-v2")) return waterbox_v2_descriptor();
    if (!strcmp(argv[1], "regions")) return explicit_regions();
    if (!strcmp(argv[1], "reads")) return reads();
    if (!strcmp(argv[1], "failures")) return failures_and_rebind();
    if (!strcmp(argv[1], "akiko")) return akiko();
    if (!strcmp(argv[1], "canonical")) return canonical();
    if (!strcmp(argv[1], "mode2-gaps")) return mode2_gaps();
    return 2;
}
