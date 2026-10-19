#include "cd_disc.h"
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
    for (unsigned n = 0; n < 18; ++n) {
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
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "reads")) return reads();
    if (!strcmp(argv[1], "failures")) return failures_and_rebind();
    return 2;
}
